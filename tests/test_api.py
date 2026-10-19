"""The HTTP API, with recorded model answers and a temporary data folder."""

import pytest
from fastapi.testclient import TestClient

from underwise.app import create_app
from underwise.llm import LLMError

from conftest import SAMPLES, FakeLLM, ReplayLLM


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UNDERWISE_DATA_DIR", str(tmp_path / "runs"))


def upload(client, sample, drop=(), email_text=""):
    files = [("files", (f.name, f.read_bytes())) for f in sorted((SAMPLES / sample).iterdir()) if f.name not in drop]
    return client.post("/api/packets", files=files, data={"email_text": email_text})


def test_upload_correct_rerun_and_reload():
    client = TestClient(create_app(ReplayLLM("P3-conflicts")))
    r = upload(client, "P3-conflicts")
    assert r.status_code == 200
    body = r.json()
    pid = body["packet_id"]
    assert body["overall_status"] == "NEEDS_REVIEW" and body["fields"]["total_insured_value"]["state"] == "CONFLICT"

    r = client.put(f"/api/packets/{pid}/fields/total_insured_value/override", json={"value": "$9,800,000", "reason": "Confirmed with broker"})
    rules = {x["id"]: x["status"] for x in r.json()["rules"]}
    assert r.status_code == 200 and rules["R3"] == "PASS"
    field = r.json()["fields"]["total_insured_value"]
    assert field["override"]["value"] == 9_800_000 and len(field["alternatives"]) == 2

    assert client.get(f"/api/packets/{pid}").json()["fields"]["total_insured_value"]["override"]["reason"] == "Confirmed with broker"
    r = client.delete(f"/api/packets/{pid}/fields/total_insured_value/override")
    assert {x["id"]: x["status"] for x in r.json()["rules"]}["R3"] == "UNKNOWN"
    assert "Submission review" in client.get(f"/api/packets/{pid}/report").text


def test_pasted_email_is_a_source():
    email = (SAMPLES / "P2-missing-info" / "broker_email.txt").read_text()
    llm = ReplayLLM("P2-missing-info")
    llm.answers["pasted_email.txt"] = llm.answers.pop("broker_email.txt")
    r = upload(TestClient(create_app(llm)), "P2-missing-info", drop=("broker_email.txt",), email_text=email)
    assert r.status_code == 200 and r.json()["fields"]["business_name"]["value"] == "Example Northfield Storage Inc"
    assert any(a["file"] == "pasted_email.txt" for a in r.json()["attachments"])


def test_bad_uploads_are_rejected_with_a_message():
    client = TestClient(create_app(None, use_env=False))
    assert client.post("/api/packets", data={"email_text": ""}).status_code == 400
    r = client.post("/api/packets", files=[("files", ("notes.docx", b"x"))])
    assert r.status_code == 400 and "only" in r.json()["detail"]
    r = client.post("/api/packets", files=[("files", ("a.txt", b"x"))], data={"email_text": "pasted"})
    assert r.status_code == 400 and "one broker email" in r.json()["detail"]


def test_invalid_correction_and_unknown_packet():
    client = TestClient(create_app(ReplayLLM("P3-conflicts")))
    pid = upload(client, "P3-conflicts").json()["packet_id"]
    r = client.put(f"/api/packets/{pid}/fields/sprinklered/override", json={"value": "maybe", "reason": "x"})
    assert r.status_code == 422 and "Yes or No" in r.json()["detail"]
    assert client.get("/api/packets/000000000000").status_code == 404
    assert client.get("/api/packets/..%2F..%2Fetc").status_code == 404


def test_each_upload_is_a_new_packet():
    client = TestClient(create_app(ReplayLLM("P1-clean")))
    a = upload(client, "P1-clean").json()
    b = upload(TestClient(create_app(ReplayLLM("P2-missing-info"))), "P2-missing-info").json()
    assert a["packet_id"] != b["packet_id"]
    assert b["fields"]["business_name"]["value"] == "Example Northfield Storage Inc"
    assert "loss_runs.pdf" not in {x["file"] for x in b["attachments"]}


def test_model_failure_returns_deterministic_results_and_retry_works():
    failing = FakeLLM({"application.pdf": LLMError("Could not reach the model endpoint"), "broker_email.txt": LLMError("x")})
    client = TestClient(create_app(failing))
    r = upload(client, "P2-missing-info").json()
    assert r["extraction"]["status"] == "FAILED" and r["fields"]["schedule_location_count"]["value"] == 2
    assert r["fields"]["business_name"]["state"] == "NOT_EXTRACTED"
    failing.answers = ReplayLLM("P2-missing-info").answers  # the model is back
    r2 = client.post(f"/api/packets/{r['packet_id']}/process").json()
    assert r2["extraction"]["status"] == "OK" and r2["fields"]["business_name"]["state"] == "KNOWN"


def test_health_reports_model_and_config_errors(monkeypatch):
    assert TestClient(create_app(ReplayLLM("P1-clean"))).get("/api/health").json()["model"] == "replay: recorded"
    monkeypatch.setenv("UNDERWISE_LLM_PROVIDER", "openai")
    monkeypatch.delenv("UNDERWISE_LLM_BASE_URL", raising=False)
    h = TestClient(create_app()).get("/api/health").json()
    assert h["model"] is None and "UNDERWISE_LLM_BASE_URL" in h["model_error"]
