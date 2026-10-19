"""Command line:

    underwise run data/sample_submissions/P3-conflicts  process one packet folder, write result.json and report.md
    underwise serve                                     the web app on http://127.0.0.1:8000
    underwise check-samples [--runs N] [--report F]     run every sample packet with the live model, compare each
                                                        field and rule to tests/fixtures/expected.json, and re-check
                                                        that every quote is on the page it claims
"""

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from . import store
from .llm import LLMError, from_env
from .models import PacketResult
from .pipeline import process

ROOT = Path(__file__).resolve().parents[2]


def report(r: PacketResult) -> str:
    """The human-readable version of a result, as Markdown."""
    lines = [f"# Submission review {r.packet_id}", "", f"**Overall: {r.overall_status}**. {r.disclaimer}", "",
             f"Model: {r.model or 'none configured'}. Extraction: {r.extraction.status}"
             + (f" ({r.extraction.detail})" if r.extraction.status != "OK" else "") + ".", "", "## Rules", ""]
    for rule in r.rules:
        lines.append(f"- **{rule.id} {rule.name}: {rule.status}**. {rule.reason}")
    lines += ["", "## Fields", ""]
    for f in r.fields.values():
        shown = f.effective_value if f.effective_state == "KNOWN" else f.effective_state
        lines.append(f"- **{f.name}**: {shown}" + (f" (human override, was {f.override.original_state}: {f.override.reason})" if f.override else ""))
        for a in f.alternatives:
            lines.append(f"  - {a.raw!r} in {a.evidence.source_file}, {a.evidence.locator}: \"{a.evidence.excerpt_or_observation}\"")
        for e in f.evidence:
            quote = f"\"{e.excerpt_or_observation}\"" if e.kind == "excerpt" else e.excerpt_or_observation
            lines.append(f"  - {e.source_file}, {e.locator}: {' '.join(quote.split())}")
        if f.note and f.state != "KNOWN":
            lines.append(f"  - Note: {f.note}")
    lines += ["", "## Underwriter summary (AI)", "", r.summary.detail or r.summary.status, "",
              "## Draft follow-up email (not sent)", "", f"To: {r.email_draft['to']}", f"Subject: {r.email_draft['subject']}", "",
              r.email_draft["body"], ""]
    return "\n".join(lines)


def run(folder: Path, out: Path | None) -> PacketResult:
    llm = from_env()
    pid, packet_dir = store.new_packet()
    for f in folder.iterdir():  # a copy, so every run is independent and the source folder is never written to
        if f.is_file() and not f.name.startswith("."):
            shutil.copy(f, packet_dir / f.name)
    result = process(pid, packet_dir, llm)
    store.save(result)
    target = out or packet_dir
    target.mkdir(parents=True, exist_ok=True)
    (target / "result.json").write_text(result.model_dump_json(indent=2))
    (target / "report.md").write_text(report(result))
    return result


class Recorder:
    """Wraps a live model and keeps every answer by tag, to record offline test fixtures."""
    def __init__(self, llm):
        self.llm, self.label, self.calls = llm, llm.label, {}

    def json(self, **kw):
        out = self.llm.json(**kw)
        self.calls[kw["tag"]] = out
        return out


def recheck_excerpts(result: PacketResult, folder: Path) -> tuple[int, int]:
    """Independently reopens each file and confirms every quoted excerpt is on the page its locator names."""
    from .documents import load_packet
    from .extract import locate
    sources = {s.file: s for s in load_packet(folder).sources}
    evidence = [e for f in result.fields.values() for e in f.evidence + [a.evidence for a in f.alternatives]]
    evidence += [a.doc_type_evidence for a in result.attachments if a.doc_type_evidence]
    total = ok = 0
    for e in evidence:
        if e.kind != "excerpt":
            continue
        total += 1
        src = sources.get(e.source_file)
        page = 1 if e.locator == "email body" else int(e.locator.rsplit(" ", 1)[-1]) if e.locator.startswith("PDF page ") else None
        ok += bool(src and page and locate(e.excerpt_or_observation, src) == page)
    return ok, total


def check_samples(record: Path | None, runs: int = 1, report_path: Path | None = None) -> int:
    llm = from_env()
    if llm is None:
        print("check-samples needs a model. Set UNDERWISE_LLM_PROVIDER (see .env.example).")
        return 2
    expected = json.loads((ROOT / "tests" / "fixtures" / "expected.json").read_text())
    rows, misses_all = [], []
    for run in range(1, runs + 1):
        for name, want in expected.items():
            rec = Recorder(llm)
            pid, packet_dir = store.new_packet()
            for f in (ROOT / "data" / "sample_submissions" / name).iterdir():
                shutil.copy(f, packet_dir / f.name)
            start = time.monotonic()
            result = process(pid, packet_dir, rec)
            seconds = time.monotonic() - start
            store.save(result)
            got = {"fields": {n: {"state": f.state, "value": f.value, "alternatives": sorted(map(str, (a.value for a in f.alternatives)))}
                              for n, f in result.fields.items()},
                   "rules": {r.id: r.status for r in result.rules}, "overall": result.overall_status}
            field_miss = [f"{n}: expected {w}, got {got['fields'][n]}" for n, w in want["fields"].items()
                          if any(got["fields"][n][k] != v for k, v in w.items())]
            checks = sum(len(w) for w in want["fields"].values())
            field_ok = checks - sum(sum(got["fields"][n][k] != v for k, v in w.items()) for n, w in want["fields"].items())
            rule_miss = [f"{rid}: expected {st}, got {got['rules'][rid]}" for rid, st in want["rules"].items() if got["rules"][rid] != st]
            overall_ok = got["overall"] == want["overall"]
            ex_ok, ex_total = recheck_excerpts(result, packet_dir)
            misses = field_miss + rule_miss + ([f"overall: expected {want['overall']}, got {got['overall']}"] if not overall_ok else [])
            misses += [f"summary: {result.summary.detail}"] if result.summary.status != "OK" else []
            misses += [f"{ex_total - ex_ok} excerpts not found on their stated page"] if ex_ok != ex_total else []
            misses_all += [f"run {run}, {name}: {m}" for m in misses]
            rows.append({"run": run, "sample": name, "field_ok": field_ok, "field_checks": checks,
                         "rules_ok": 5 - len(rule_miss), "overall_ok": overall_ok, "excerpts_ok": ex_ok, "excerpts": ex_total,
                         "summary": result.summary.status, "seconds": round(seconds, 1)})
            print(f"run {run} {name}: {'OK' if not misses else 'MISMATCH'}  fields {field_ok}/{checks}  rules "
                  f"{' '.join(f'{k}={v}' for k, v in got['rules'].items())}  excerpts {ex_ok}/{ex_total}  {seconds:.1f}s")
            for m in misses:
                print(f"   - {m}")
            if record and run == runs:
                record.mkdir(parents=True, exist_ok=True)
                (record / f"{name}.json").write_text(json.dumps(rec.calls, indent=2))
    print("All samples match." if not misses_all else f"{len(misses_all)} mismatches.")
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(accuracy_report(rows, misses_all, llm.label, runs))
        print(f"Report written to {report_path}")
    return 1 if misses_all else 0


def accuracy_report(rows: list[dict], misses: list[str], model: str, runs: int) -> str:
    tot = lambda k: sum(r[k] for r in rows)
    lines = ["# Extraction accuracy on the sample packets", "",
             f"Generated by `underwise check-samples --runs {runs} --report evals/accuracy.md` on {datetime.now().date().isoformat()} "
             f"with {model}. Each run processes every sample packet from scratch through the live model.", "",
             "What is compared: each field's state and value (and, for conflicts, both alternative values) against "
             "`tests/fixtures/expected.json`, which was written by hand from `data/rules.json`, not from model output; "
             "the five rule statuses; the overall status. Every quoted excerpt is then re-checked independently: the file "
             "is reopened and the excerpt must appear on the exact page its locator names.", "",
             "| Run | Sample | Field checks | Rules | Overall | Excerpts on stated page | AI summary | Seconds |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['run']} | {r['sample']} | {r['field_ok']}/{r['field_checks']} | {r['rules_ok']}/5 | "
                     f"{'match' if r['overall_ok'] else 'MISMATCH'} | {r['excerpts_ok']}/{r['excerpts']} | {r['summary']} | {r['seconds']} |")
    lines += ["", f"**Totals over {runs} run{'s' if runs != 1 else ''}:** field checks {tot('field_ok')}/{tot('field_checks')}, "
              f"rules {tot('rules_ok')}/{5 * len(rows)}, overall status {sum(r['overall_ok'] for r in rows)}/{len(rows)}, "
              f"excerpts on their stated page {tot('excerpts_ok')}/{tot('excerpts')}.", ""]
    lines += ["Mismatches:", ""] + [f"- {m}" for m in misses] if misses else ["No mismatches."]
    lines += ["", "Scope: four fictional packets is a small set. These results show the required cases work and stay "
              "consistent across runs; they do not measure accuracy on unseen layouts. The offline tests lock the same "
              "expectations in using recorded model answers.", ""]
    return "\n".join(lines)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="underwise", description="Commercial property submission review.")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="process one packet folder")
    r.add_argument("folder", type=Path)
    r.add_argument("--out", type=Path, help="where to write result.json and report.md (default: the packet's run folder)")
    s = sub.add_parser("serve", help="start the web app")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--host", default="127.0.0.1")
    c = sub.add_parser("check-samples", help="run the sample packets with the live model and compare to expectations")
    c.add_argument("--record", type=Path, help="also save the model's answers (from the last run) as offline test fixtures")
    c.add_argument("--runs", type=int, default=1, help="repeat the whole check this many times to measure consistency")
    c.add_argument("--report", type=Path, help="write the results as a Markdown accuracy report")
    args = p.parse_args(argv)
    try:
        if args.cmd == "run":
            if not args.folder.is_dir():
                sys.exit(f"Not a folder: {args.folder}")
            result = run(args.folder, args.out)
            print(report(result))
        elif args.cmd == "serve":
            import uvicorn
            from .app import create_app
            print(f"Underwise submission review on http://{args.host}:{args.port}")
            uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
        else:
            sys.exit(check_samples(args.record, max(1, args.runs), args.report))
    except LLMError as e:  # bad model configuration: say what to fix, no traceback
        sys.exit(f"Model configuration error: {e}")
