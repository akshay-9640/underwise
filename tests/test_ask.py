"""Ask about this packet: answers grounded in the packet, with every quote checked in code."""

import pytest
from fastapi.testclient import TestClient

from underwise.app import create_app
from underwise.ask import Turn, ask
from underwise.llm import LLMError
from underwise.pipeline import process, rerun, set_override

from conftest import SAMPLES, FakeLLM, ReplayLLM


def answer(*statements, answerable=True):
    return {"answerable": answerable, "statements": [{"text": t, "citations": [{"source": s, "quote": q} for s, q in cites]} for t, cites in statements]}


@pytest.fixture
def p3(make_packet):
    folder = make_packet("P3-conflicts")
    return process("c" * 12, folder, ReplayLLM("P3-conflicts")), folder


def test_verified_citations_get_locators(p3):
    result, folder = p3
    llm = FakeLLM({"ask": answer(
        ("The email says loss runs are attached.", [("broker_email.txt", "Application, schedule and loss runs attached.")]),
        ("R2 is flagged.", [("checks", "R2 Location count matches the schedule: FLAG")]),
        ("The schedule lists L5.", [("locations.csv", "L5,8 Fictional Dr,Princeton,NJ,08540,Frame")]),
        ("The application says No to sprinklers.", [("application.pdf", "Automatic sprinkler system (Yes/No): No")]))})
    a = ask(llm, result, folder, "What should I know?")
    locs = [s.citations[0].locator for s in a.statements]
    assert all(s.supported for s in a.statements)
    assert locs == ["email body", "check results computed by code", "CSV file", "PDF page 2"]


def test_invented_quote_or_unknown_source_is_unsupported(p3):
    result, folder = p3
    llm = FakeLLM({"ask": answer(
        ("Loss runs will follow.", [("broker_email.txt", "Loss runs have been requested from the current carrier")]),  # P2's email, not this one
        ("TIV is $50,000.", [("other_packet.pdf", "Total insured value: $50,000")]))})
    a = ask(llm, result, folder, "Anything?")
    assert not any(s.supported for s in a.statements)
    assert all(c.locator is None and not c.verified for s in a.statements for c in s.citations)


def test_checks_source_reflects_corrections(p3):
    result, folder = p3
    result = rerun(set_override(result, "total_insured_value", "$9,800,000", "Confirmed with broker"), None)
    llm = FakeLLM({"ask": answer(("TIV was corrected.", [("checks", "total_insured_value was corrected by a reviewer to 9800000")]))})
    assert ask(llm, result, folder, "Was anything corrected?").statements[0].supported


def test_prompt_keeps_documents_as_data_and_includes_history(p3):
    result, folder = p3
    llm = FakeLLM({"ask": answer(("x", []))})
    ask(llm, result, folder, "And the PDF?", [Turn(question="What does the email say?", answer="It says $9,800,000.")])
    system, user = llm.prompts["ask"]
    assert "untrusted data" in system and '<source id="broker_email.txt" kind="email">' in user
    assert "<earlier_question>What does the email say?</earlier_question>" in user and user.rstrip().endswith("And the PDF?\n</question>")
    assert "loss_runs_scan.pdf" not in user  # only this packet's files


def test_no_model_raises(p3):
    result, folder = p3
    with pytest.raises(LLMError, match="No model"):
        ask(None, result, folder, "Hello?")


def test_ask_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("UNDERWISE_DATA_DIR", str(tmp_path / "runs"))
    llm = ReplayLLM("P3-conflicts")
    llm.answers["ask"] = answer(("The email states $9,800,000.", [("broker_email.txt", "with a total insured value of $9,800,000")]))
    client = TestClient(create_app(llm))
    files = [("files", (f.name, f.read_bytes())) for f in (SAMPLES / "P3-conflicts").iterdir()]
    pid = client.post("/api/packets", files=files).json()["packet_id"]
    r = client.post(f"/api/packets/{pid}/ask", json={"question": "What TIV does the email state?"})
    assert r.status_code == 200 and r.json()["statements"][0]["citations"][0]["verified"] is True
    assert client.post(f"/api/packets/{pid}/ask", json={"question": "x"}).status_code == 422
    assert client.post("/api/packets/000000000000/ask", json={"question": "Hello there"}).status_code == 404
    no_model = TestClient(create_app(None, use_env=False))
    assert no_model.post(f"/api/packets/{pid}/ask", json={"question": "Hello there"}).status_code == 503
