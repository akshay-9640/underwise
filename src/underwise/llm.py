"""One interface for every model: send a system prompt and a user prompt, get a JSON object back.

Two adapters, chosen by environment variables (see .env.example):
- openai: any OpenAI-compatible chat endpoint. Covers a local Ollama or LM Studio
  server, OpenAI, and Anthropic's OpenAI-compatible endpoint. Standard library only.
- claude-code: the Claude Code CLI (`claude -p`), which runs on a Claude subscription.
No key is ever stored in code. With no provider configured, from_env() returns None
and the app runs only its deterministic parts.
"""

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from typing import Protocol


class LLMError(RuntimeError):
    """A model call failed. The message is safe to show to the user (it never contains the key)."""


class LLM(Protocol):
    label: str

    def json(self, *, system: str, user: str, schema: dict, tag: str) -> dict: ...


def parse_json(text: str) -> dict:
    """The JSON object in a model reply, tolerating code fences or a sentence around it."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise LLMError("The model did not return JSON.")
    try:
        out = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"The model returned malformed JSON ({e.msg}).")
    if not isinstance(out, dict):
        raise LLMError("The model returned JSON that is not an object.")
    return out


class OpenAICompatible:
    def __init__(self, base_url: str, model: str, api_key: str | None, timeout: float):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.key, self.timeout = model, api_key, timeout
        self.label = f"openai-compatible: {model}"

    def json(self, *, system: str, user: str, schema: dict, tag: str) -> dict:
        body = {"model": self.model, "temperature": 0, "messages": [
            {"role": "system", "content": system + "\n\nReply with one JSON object matching this JSON Schema, and nothing else:\n" + json.dumps(schema)},
            {"role": "user", "content": user}]}
        headers = {"Content-Type": "application/json"} | ({"Authorization": f"Bearer {self.key}"} if self.key else {})
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                reply = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise LLMError(f"The model endpoint returned HTTP {e.code}.")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LLMError(f"Could not reach the model endpoint ({getattr(e, 'reason', e)}).")
        try:
            return parse_json(reply["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            raise LLMError("The model endpoint returned an unexpected response shape.")


class ClaudeCode:
    def __init__(self, binary: str, model: str, timeout: float):
        self.binary, self.model, self.timeout = binary, model, timeout
        self.label = f"claude-code: {model}"

    def json(self, *, system: str, user: str, schema: dict, tag: str) -> dict:
        cmd = [self.binary, "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
               "--system-prompt", system, "--model", self.model, "--tools", "",
               "--setting-sources", "", "--no-session-persistence"]
        try:
            with tempfile.TemporaryDirectory() as cwd:  # keeps local project files out of the model's context
                proc = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=self.timeout, cwd=cwd)
        except subprocess.TimeoutExpired:
            raise LLMError(f"The model did not answer within {self.timeout:.0f} seconds.")
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise LLMError(f"The Claude Code CLI failed: {(proc.stderr or proc.stdout).strip()[:200]}")
        if out.get("is_error") or not isinstance(out.get("structured_output"), dict):
            raise LLMError(f"The Claude Code CLI returned no structured output: {str(out.get('result'))[:200]}")
        return out["structured_output"]


def from_env() -> LLM | None:
    provider = os.environ.get("UNDERWISE_LLM_PROVIDER") or ("openai" if os.environ.get("UNDERWISE_LLM_BASE_URL") else "none")
    timeout = float(os.environ.get("UNDERWISE_LLM_TIMEOUT", "180"))
    if provider == "none":
        return None
    if provider == "openai":
        url, model = os.environ.get("UNDERWISE_LLM_BASE_URL"), os.environ.get("UNDERWISE_LLM_MODEL")
        if not url or not model:
            raise LLMError("UNDERWISE_LLM_PROVIDER=openai needs UNDERWISE_LLM_BASE_URL and UNDERWISE_LLM_MODEL.")
        return OpenAICompatible(url, model, os.environ.get("UNDERWISE_LLM_API_KEY") or None, timeout)
    if provider == "claude-code":
        binary = os.environ.get("UNDERWISE_CLAUDE_BIN") or shutil.which("claude")
        if not binary:
            raise LLMError("Claude Code CLI not found. Set UNDERWISE_CLAUDE_BIN to its path.")
        return ClaudeCode(binary, os.environ.get("UNDERWISE_LLM_MODEL", "claude-opus-5-5"), timeout)
    raise LLMError(f"Unknown UNDERWISE_LLM_PROVIDER {provider!r}. Use openai, claude-code or none.")
