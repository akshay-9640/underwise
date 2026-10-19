"""One folder per packet under UNDERWISE_DATA_DIR (default ./runs): its uploaded files and result.json.
A new packet always gets a new folder, so one applicant's data never reaches another's results."""

import os
import re
import uuid
from pathlib import Path

from .models import PacketResult

ID = re.compile(r"[0-9a-f]{12}")


def root() -> Path:
    return Path(os.environ.get("UNDERWISE_DATA_DIR", "runs")).resolve()


def new_packet() -> tuple[str, Path]:
    pid = uuid.uuid4().hex[:12]
    folder = root() / pid
    folder.mkdir(parents=True)
    return pid, folder


def folder(pid: str) -> Path:
    if not ID.fullmatch(pid) or not (root() / pid).is_dir():
        raise KeyError(pid)
    return root() / pid


def save(result: PacketResult) -> None:
    # shortcut: last write wins if two reviewers correct one packet at once; add a version check if it becomes multi-user.
    path = folder(result.packet_id) / "result.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(result.model_dump_json(indent=2))
    tmp.replace(path)  # atomic, so a crash never leaves a half-written result


def load(pid: str) -> PacketResult:
    path = folder(pid) / "result.json"
    if not path.exists():
        raise KeyError(pid)
    return PacketResult.model_validate_json(path.read_text())
