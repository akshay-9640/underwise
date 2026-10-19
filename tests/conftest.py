"""Test helpers. No test calls a live model: ReplayLLM returns answers recorded from a
real model run (`underwise check-samples --record tests/fixtures/recorded`), and FakeLLM
returns hand-written answers for edge cases."""

import copy
import json
import shutil
from pathlib import Path

import pytest

from underwise.llm import LLMError

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "sample_submissions"
FIXTURES = ROOT / "tests" / "fixtures"


class ReplayLLM:
    label = "replay: recorded"

    def __init__(self, sample: str):
        self.answers = json.loads((FIXTURES / "recorded" / f"{sample}.json").read_text())

    def json(self, *, system, user, schema, tag):
        return copy.deepcopy(self.answers[tag])


class FakeLLM:
    """Answers by tag (file name or "summary"). An Exception value is raised instead."""
    label = "fake"

    def __init__(self, answers: dict):
        self.answers, self.prompts = answers, {}

    def json(self, *, system, user, schema, tag):
        self.prompts[tag] = (system, user)
        a = self.answers.get(tag, {"summary": "Fake summary."} if tag == "summary" else None)
        if isinstance(a, Exception):
            raise a
        if a is None:
            raise LLMError(f"no fake answer for {tag}")
        return copy.deepcopy(a)


def doc_answer(doc_type="broker_email", type_excerpt=None, **fields):
    """A model answer: fields given as name=(value, excerpt); unspecified fields are not mentioned."""
    from underwise.models import EXTRACTED_FIELDS
    return {"document_type": doc_type, "document_type_excerpt": type_excerpt,
            "fields": {f: dict(zip(("value", "excerpt"), fields.get(f, (None, None)))) for f in EXTRACTED_FIELDS}}


@pytest.fixture
def make_packet(tmp_path):
    """make_packet(sample="P1-clean", files={"name": text_or_bytes}, drop=["file"]) -> folder."""
    def build(sample: str | None = None, files: dict | None = None, drop: tuple = ()) -> Path:
        folder = tmp_path / "packet"
        folder.mkdir(exist_ok=True)
        if sample:
            for f in (SAMPLES / sample).iterdir():
                if f.name not in drop:
                    shutil.copy(f, folder / f.name)
        for name, content in (files or {}).items():
            (folder / name).write_bytes(content if isinstance(content, bytes) else content.encode())
        return folder
    return build
