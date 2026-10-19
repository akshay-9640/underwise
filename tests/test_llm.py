"""The model adapters and configuration, against a local stand-in server (no real model)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from underwise.llm import LLMError, OpenAICompatible, from_env, parse_json


@pytest.fixture
def server():
    """A stand-in OpenAI-compatible endpoint. Set server.reply / server.status per test."""
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"], seen["auth"] = self.path, self.headers.get("Authorization")
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(srv.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(srv.reply).encode())

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    srv.status, srv.reply, srv.seen = 200, {}, seen
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def chat(content):
    return {"choices": [{"message": {"content": content}}]}


def test_openai_compatible_request_and_reply(server):
    server.reply = chat('Here you go:\n```json\n{"summary": "ok"}\n```')
    llm = OpenAICompatible(f"http://127.0.0.1:{server.server_port}/v1", "local-model", "secret-key", timeout=5)
    assert llm.json(system="sys", user="doc", schema={"type": "object"}, tag="t") == {"summary": "ok"}
    body = server.seen["body"]
    assert server.seen["path"] == "/v1/chat/completions" and server.seen["auth"] == "Bearer secret-key"
    assert body["model"] == "local-model" and body["temperature"] == 0 and body["messages"][1]["content"] == "doc"


def test_no_key_sends_no_auth_header(server):
    server.reply = chat('{"a": 1}')
    OpenAICompatible(f"http://127.0.0.1:{server.server_port}/v1", "m", None, timeout=5).json(system="s", user="u", schema={}, tag="t")
    assert server.seen["auth"] is None


def test_http_errors_and_bad_replies_become_llm_errors(server):
    llm = OpenAICompatible(f"http://127.0.0.1:{server.server_port}/v1", "m", "secret-key", timeout=5)
    server.status, server.reply = 401, {"error": "bad key"}
    with pytest.raises(LLMError, match="HTTP 401") as e:
        llm.json(system="s", user="u", schema={}, tag="t")
    assert "secret-key" not in str(e.value)
    server.status, server.reply = 200, {"unexpected": True}
    with pytest.raises(LLMError, match="unexpected response shape"):
        llm.json(system="s", user="u", schema={}, tag="t")
    server.reply = chat("I cannot help with that.")
    with pytest.raises(LLMError, match="did not return JSON"):
        llm.json(system="s", user="u", schema={}, tag="t")


def test_unreachable_endpoint():
    with pytest.raises(LLMError, match="Could not reach"):
        OpenAICompatible("http://127.0.0.1:9/v1", "m", None, timeout=2).json(system="s", user="u", schema={}, tag="t")


def test_parse_json():
    assert parse_json('{"a": {"b": 1}}') == {"a": {"b": 1}}
    for bad in ("[1, 2]", "{not json}", ""):
        with pytest.raises(LLMError):
            parse_json(bad)


def test_from_env(monkeypatch):
    for k in ("UNDERWISE_LLM_PROVIDER", "UNDERWISE_LLM_BASE_URL", "UNDERWISE_LLM_MODEL", "UNDERWISE_LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert from_env() is None  # nothing configured: deterministic mode
    monkeypatch.setenv("UNDERWISE_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("UNDERWISE_LLM_MODEL", "llama3.2")
    assert from_env().label == "openai-compatible: llama3.2"
    monkeypatch.setenv("UNDERWISE_LLM_PROVIDER", "magic")
    with pytest.raises(LLMError, match="Unknown"):
        from_env()
    monkeypatch.setenv("UNDERWISE_LLM_PROVIDER", "claude-code")
    monkeypatch.setenv("UNDERWISE_CLAUDE_BIN", "/bin/echo")
    assert from_env().label.startswith("claude-code")
