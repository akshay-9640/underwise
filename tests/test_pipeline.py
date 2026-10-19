"""End to end on the four samples with recorded model answers, plus no-model and model-failure runs."""

import json

import pytest

from underwise.documents import load_packet
from underwise.llm import LLMError
from underwise.pipeline import clear_override, process, rerun, set_override

from conftest import FIXTURES, FakeLLM, ReplayLLM

EXPECTED = json.loads((FIXTURES / "expected.json").read_text())


@pytest.mark.parametrize("sample", list(EXPECTED))
def test_samples_match_expected_outcomes(make_packet, sample):
    r = process("a" * 12, make_packet(sample), ReplayLLM(sample))
    want = EXPECTED[sample]
    assert {x.id: x.status for x in r.rules} == want["rules"] and r.overall_status == want["overall"]
    for name, w in want["fields"].items():
        f = r.fields[name]
        assert f.state == w["state"], name
        if "value" in w:
            assert f.value == w["value"], name
    assert r.extraction.status == r.summary.status == "OK"
    for f in r.fields.values():  # every extracted or derived value carries evidence
        assert f.evidence or f.alternatives, f.name


def test_conflicts_draft_asks_about_each_problem(make_packet):
    r = process("c" * 12, make_packet("P3-conflicts"), ReplayLLM("P3-conflicts"))
    body = r.email_draft["body"]
    assert r.email_draft["to"] == "Lee Morgan <lee.morgan@example.com>" and r.email_draft["subject"].startswith("Re: ")
    for needle in ("declares 4 locations", "lists 5", "total insured value", "9,800,000", "11,200,000", "sprinkler", "covers 2 years"):
        assert needle in body, needle


def test_ready_packet_draft_asks_for_nothing(make_packet):
    r = process("a" * 12, make_packet("P1-clean"), ReplayLLM("P1-clean"))
    assert "1." not in r.email_draft["body"] and "complete" in r.email_draft["body"]


def test_no_model_runs_deterministic_parts(make_packet):
    r = process("b" * 12, make_packet("P2-missing-info"), None)
    assert r.extraction.status == r.summary.status == "SKIPPED" and r.model is None
    assert r.fields["schedule_location_count"].value == 2
    assert all(x.status == "UNKNOWN" for x in r.rules)  # nothing claimed absent, nothing passed


def test_no_model_and_no_pdfs_still_flags_missing_loss_runs(make_packet):
    r = process("b" * 12, make_packet("P2-missing-info", drop=("application.pdf",)), None)
    assert {x.id: x.status for x in r.rules}["R5"] == "FLAG"


def test_summary_failure_keeps_rule_results(make_packet):
    llm = ReplayLLM("P3-conflicts")
    r = process("c" * 12, make_packet("P3-conflicts"), FakeLLM(llm.answers | {"summary": LLMError("HTTP 500")}))
    assert r.summary.status == "FAILED" and "HTTP 500" in r.summary.detail
    assert {x.id: x.status for x in r.rules} == EXPECTED["P3-conflicts"]["rules"]


def test_correction_reruns_rules_and_keeps_original_evidence(make_packet):
    r = process("c" * 12, make_packet("P3-conflicts"), ReplayLLM("P3-conflicts"))
    r = rerun(set_override(r, "total_insured_value", "$9,800,000", "Broker confirmed the TIV by phone"), None)
    f = r.fields["total_insured_value"]
    assert f.state == "CONFLICT" and len(f.alternatives) == 2  # original extraction kept
    assert f.override.value == 9_800_000 and f.override.original_state == "CONFLICT"
    rule = next(x for x in r.rules if x.id == "R3")
    assert rule.status == "PASS" and rule.values["total_insured_value"]["human_override"]
    r = rerun(clear_override(r, "total_insured_value"), None)
    assert next(x for x in r.rules if x.id == "R3").status == "UNKNOWN"


@pytest.mark.parametrize("name,value,reason,msg", [
    ("total_insured_value", "a lot", "x", "not a dollar amount"), ("sprinklered", "maybe", "x", "Yes or No"),
    ("total_insured_value", "9800000", " ", "reason"), ("nope", "1", "x", "Unknown field"),
])
def test_invalid_corrections_are_rejected(make_packet, name, value, reason, msg):
    r = process("c" * 12, make_packet("P3-conflicts"), ReplayLLM("P3-conflicts"))
    with pytest.raises(ValueError, match=msg):
        set_override(r, name, value, reason)


def test_packets_are_independent(make_packet, tmp_path):
    a = process("a" * 12, make_packet("P1-clean"), ReplayLLM("P1-clean"))
    folder_c = tmp_path / "c"
    folder_c.mkdir()
    for f in (FIXTURES.parents[1] / "data" / "sample_submissions" / "P2-missing-info").iterdir():
        (folder_c / f.name).write_bytes(f.read_bytes())
    c = process("c" * 12, folder_c, ReplayLLM("P2-missing-info"))
    assert a.fields["business_name"].value != c.fields["business_name"].value
    assert "loss_runs.pdf" not in {x.file for x in load_packet(folder_c).attachments}


def test_independent_excerpt_recheck_catches_a_wrong_page(make_packet):
    from underwise.cli import accuracy_report, recheck_excerpts
    folder = make_packet("P1-clean")
    r = process("a" * 12, folder, ReplayLLM("P1-clean"))
    ok, total = recheck_excerpts(r, folder)
    assert total > 10 and ok == total
    r.fields["business_name"].evidence[0].locator = "PDF page 3"  # no file has a third page
    assert recheck_excerpts(r, folder)[0] == ok - 1
    md = accuracy_report([{"run": 1, "sample": "P1-clean", "field_ok": 11, "field_checks": 11, "rules_ok": 5, "overall_ok": True,
                           "excerpts_ok": ok, "excerpts": total, "summary": "OK", "seconds": 9.0}], [], "fake", 1)
    assert "| 1 | P1-clean | 11/11 | 5/5 | match |" in md and "No mismatches." in md
