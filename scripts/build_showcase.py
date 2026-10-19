"""Builds the static showcase in showcase/ from real recorded model output. Dev only:

    uv run --env-file .env python scripts/build_showcase.py

Each sample packet is processed with the model answers recorded in tests/fixtures/recorded
(a real live run), so nothing on the page is invented. Answers to the Ask panel's suggested
questions are recorded once with the live model and kept in showcase/results/*.ask.json;
delete those files to record them again.
"""

import copy
import json
import re
import shutil
import tempfile
from pathlib import Path

from underwise.ask import ask
from underwise.cli import report
from underwise.llm import from_env
from underwise.pipeline import process

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "underwise" / "web"
OUT = ROOT / "showcase"
PACKETS = {
    "P1-clean": "Everything consistent, with 5 years of loss runs.",
    "P2-missing-info": "Effective date to be confirmed, sprinkler answer blank, loss runs promised but not attached.",
    "P3-conflicts": "Email and application disagree on TIV and sprinklers; 5 locations on the schedule vs 4 declared.",
    "P4-edge-cases": "TIV exactly at the $10M threshold, a scanned loss runs PDF, and an instruction planted in the email.",
}


class Replay:
    """Returns the answers recorded from a real model run, by tag."""
    def __init__(self, name: str):
        self.answers = json.loads((ROOT / "tests" / "fixtures" / "recorded" / f"{name}.json").read_text())
        self.label = "claude-code: claude-opus-5-5"

    def json(self, *, system, user, schema, tag):
        return copy.deepcopy(self.answers[tag])


def main() -> None:
    results = OUT / "results"
    results.mkdir(parents=True, exist_ok=True)
    questions = json.loads(re.search(r"const ASK_SUGGESTIONS = (\[.*?\]);", (WEB / "app.js").read_text()).group(1))
    live, index = None, []
    for name, tests in PACKETS.items():
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for f in (ROOT / "data" / "sample_submissions" / name).iterdir():
                shutil.copy(f, folder / f.name)
            result = process(name, folder, Replay(name))
            (results / f"{name}.json").write_text(result.model_dump_json(indent=2))
            (results / f"{name}.report.txt").write_text(report(result))
            asked = results / f"{name}.ask.json"
            if not asked.exists():
                live = live or from_env()
                if live is None:
                    raise SystemExit("Recording Ask answers needs a model. Set UNDERWISE_LLM_PROVIDER (see .env.example).")
                answers = {q: ask(live, result, folder, q).model_dump() for q in questions}
                asked.write_text(json.dumps(answers, indent=2))
        index.append({"id": name, "business": result.fields["business_name"].value, "tests": tests,
                      "overall": result.overall_status, "rules": {r.id: r.status for r in result.rules}})
        print(f"{name}: {result.overall_status}")
    (results / "index.json").write_text(json.dumps(index, indent=2))
    (results / "health.json").write_text(json.dumps({"model": "claude-code: claude-opus-5-5", "model_error": None}))
    shutil.copy(WEB / "app.css", OUT / "app.css")
    shutil.copy(WEB / "app.js", OUT / "app.js")
    html = (WEB / "index.html").read_text()
    for old, new in [
        ('href="/static/app.css">', 'href="app.css">\n  <link rel="stylesheet" href="demo.css">'),
        ('<script src="/static/app.js" defer></script>', '<script src="demo.js" defer></script>\n  <script src="app.js" defer></script>'),
        ("Review a submission packet</h1>", "Submission review, with the evidence</h1>"),
        ("Add one packet: the broker email, the application PDF, the locations schedule CSV and any loss runs report. Fields are extracted with evidence, then five checks run in code.",
         "Pick a fictional packet to see its real recorded review: every value carries a quote verified in code, and five checks run in code, not in the model."),
        ('<form id="upload" class="card">', '<div id="picker" class="picker"></div>\n      <p class="hint demo-note">This page replays recorded output from Claude Opus 5.5. Uploads, corrections and free-form questions run in the local app: <a href="https://github.com/akshay-9640/underwise">see the code on GitHub</a>.</p>\n      <form id="upload" class="card" hidden>'),
    ]:
        assert old in html, old
        html = html.replace(old, new)
    (OUT / "index.html").write_text(html)


if __name__ == "__main__":
    main()
