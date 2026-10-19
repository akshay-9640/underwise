"""Reads a submission packet: the broker email, PDF text page by page, and the
locations schedule CSV. Everything here is deterministic code, no model calls."""

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

from .models import Attachment, Evidence, Field

CSV_HEADER = ["location_id", "address", "city", "state", "zip", "construction"]
ALLOWED = {".txt": "email", ".eml": "email", ".pdf": "pdf", ".csv": "csv"}
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES = 12


class PacketError(ValueError):
    """The packet itself is unusable (no files, too many emails, bad file type). Shown to the user as is."""


@dataclass
class Source:
    """A text source the model reads: the email (one 'page') or a PDF (one entry per page)."""
    file: str
    kind: str
    pages: list[str]

    def locator(self, page: int) -> str:
        return "email body" if self.kind == "email" else f"PDF page {page}"


@dataclass
class Packet:
    folder: Path
    attachments: list[Attachment] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)   # readable email and PDFs
    schedule: Field | None = None


def check_upload(names: list[str], sizes: list[int], has_pasted_email: bool) -> None:
    """Validates an upload before anything is stored."""
    if not names and not has_pasted_email:
        raise PacketError("Add the packet's files or paste the broker email.")
    if len(names) > MAX_FILES:
        raise PacketError(f"A packet can have at most {MAX_FILES} files.")
    kinds = []
    for name, size in zip(names, sizes):
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED:
            raise PacketError(f"{name}: only .txt or .eml (email), .pdf and .csv files are supported.")
        if size > MAX_FILE_BYTES:
            raise PacketError(f"{name} is larger than 10 MB.")
        kinds.append(ALLOWED[ext])
    if kinds.count("email") + has_pasted_email > 1:
        raise PacketError("A packet has one broker email: upload it or paste it, not both.")
    if kinds.count("csv") > 1:
        raise PacketError("A packet has one locations schedule CSV.")
    if len(set(map(str.lower, names))) != len(names):
        raise PacketError("Two files have the same name.")


def safe_name(name: str) -> str:
    """Keeps only the base name and plain characters, so an upload cannot write outside its packet folder."""
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name).strip(" .")
    return base or "file"


def read_pdf(path: Path) -> tuple[list[str] | None, str | None]:
    try:
        pages = [p.extract_text() or "" for p in PdfReader(path).pages]
    except Exception as e:  # pypdf raises many types for damaged files; any of them means unreadable
        return None, f"could not be read as a PDF ({type(e).__name__})"
    if not any(p.strip() for p in pages):
        return None, "contains no selectable text"
    return pages, None


def count_schedule(path: Path) -> Field:
    """schedule_location_count: nonblank data rows after the header. Null when the CSV cannot be parsed."""
    name = path.name
    try:
        text = path.read_bytes().decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
    except (UnicodeDecodeError, csv.Error) as e:
        return _unparsable(name, f"could not be parsed as CSV ({e})")
    data = [(i, r) for i, r in enumerate(rows) if any(c.strip() for c in r)]
    if not data:
        return _unparsable(name, "the file is empty")
    header = [c.strip().lower() for c in data[0][1]]
    if header != CSV_HEADER:
        return _unparsable(name, f"the header is {','.join(data[0][1])!r}, expected {','.join(CSV_HEADER)!r}")
    records = data[1:]
    for n, (_, r) in enumerate(records, 1):
        if len(r) != len(CSV_HEADER):
            return _unparsable(name, f"data row {n} has {len(r)} columns, expected {len(CSV_HEADER)}")
    count = len(records)
    rows_text = f"CSV data rows 1-{count} (header excluded)" if count else "CSV data rows: none (header only)"
    return Field(name="schedule_location_count", value=count, state="KNOWN", evidence=[Evidence(
        source_file=name, locator=rows_text, kind="observation",
        excerpt_or_observation=f"{count} nonblank data row{'s' if count != 1 else ''} after the header {','.join(CSV_HEADER)}"
                               + (f"; location_ids {', '.join(r[0] for _, r in records)}" if count else ""))])


def _unparsable(name: str, why: str) -> Field:
    return Field(name="schedule_location_count", state="INVALID", note=f"The locations schedule {why}.",
                 evidence=[Evidence(source_file=name, locator="whole file", kind="observation",
                                    excerpt_or_observation=f"Not counted: the locations schedule {why}.")])


def load_packet(folder: Path) -> Packet:
    """Reads every supported file in the folder. Unsupported files are listed as unreadable, never guessed at."""
    packet = Packet(folder=folder)
    csvs = []
    for path in sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")):
        kind = ALLOWED.get(path.suffix.lower())
        if kind == "email":
            text = path.read_text(encoding="utf-8", errors="replace")
            packet.attachments.append(Attachment(file=path.name, kind="email", readable=True))
            packet.sources.append(Source(path.name, "email", [text]))
        elif kind == "pdf":
            pages, error = read_pdf(path)
            packet.attachments.append(Attachment(file=path.name, kind="pdf", readable=pages is not None,
                                                 pages=len(pages) if pages else None, error=error))
            if pages:
                packet.sources.append(Source(path.name, "pdf", pages))
        elif kind == "csv":
            csvs.append((path, Attachment(file=path.name, kind="csv", readable=True)))
            packet.attachments.append(csvs[-1][1])
        elif path.name != "result.json":
            raise PacketError(f"{path.name}: unsupported file type.")
    if len(csvs) == 1:
        path, attachment = csvs[0]
        packet.schedule = count_schedule(path)
        attachment.readable = packet.schedule.state == "KNOWN"
        attachment.error = packet.schedule.note
    else:
        inspected = ", ".join(a.file for a in packet.attachments) or "no files"
        why = "No locations schedule CSV was supplied." if not csvs else "More than one CSV was supplied."
        packet.schedule = Field(name="schedule_location_count", state="MISSING" if not csvs else "INVALID", note=why,
                                evidence=[Evidence(source_file=inspected, locator="uploaded attachment inventory",
                                                   kind="observation", excerpt_or_observation=why)])
    return packet
