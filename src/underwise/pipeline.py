"""Processing a packet end to end, and rerunning the checks after a reviewer's correction."""

import time
from datetime import datetime, timezone
from pathlib import Path

from .documents import load_packet
from .extract import NORMALIZERS, extract, norm_count, norm_yes_no
from .llm import LLM
from .log import log
from .models import Field, Override, PacketResult
from .narrative import draft_email, email_header, summarize
from .rules import evaluate

# Corrections are typed by a reviewer, so they go through the same normalizers as extracted values.
OVERRIDE_NORMALIZERS = NORMALIZERS | {"loss_runs_present": norm_yes_no, "schedule_location_count": norm_count}


def process(packet_id: str, folder: Path, llm: LLM | None) -> PacketResult:
    start = time.monotonic()
    packet = load_packet(folder)
    fields, extraction = extract(packet, llm)
    email = next((s.pages[0] for s in packet.sources if s.kind == "email"), None)
    business = fields["business_name"].value if fields["business_name"].state == "KNOWN" else None
    to, subject = email_header(email, business)
    result = PacketResult(
        packet_id=packet_id, created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        model=llm.label if llm else None, attachments=packet.attachments, extraction=extraction, fields=fields,
        rules=[], overall_status="NEEDS_REVIEW", summary=extraction, email_draft={"to": to, "subject": subject, "body": ""})
    result = rerun(result, llm)
    log("process", packet=packet_id, extraction=extraction.status, summary=result.summary.status,
        overall=result.overall_status, ms=int((time.monotonic() - start) * 1000))
    return result


def rerun(result: PacketResult, llm: LLM | None) -> PacketResult:
    """Recomputes rules, the draft and the summary from the current fields. Extraction is not repeated."""
    rules, overall = evaluate(result.fields)
    draft = draft_email(result.fields, rules, result.email_draft["to"], result.email_draft["subject"])
    summary = summarize(llm, result.fields, rules, overall, [a.file for a in result.attachments])
    return result.model_copy(update={"rules": rules, "overall_status": overall, "email_draft": draft, "summary": summary})


def set_override(result: PacketResult, name: str, raw: str, reason: str) -> PacketResult:
    """Applies a reviewer's correction. The original value, state, evidence and alternatives stay on the field."""
    if name not in OVERRIDE_NORMALIZERS:
        raise ValueError(f"Unknown field {name!r}.")
    if not reason.strip():
        raise ValueError("Give a reason for the correction.")
    value, why = OVERRIDE_NORMALIZERS[name](str(raw))
    if why:
        raise ValueError(f"Invalid value: {why}.")
    field: Field = result.fields[name]
    field.override = Override(value=value, reason=reason.strip(), original_value=field.value, original_state=field.state)
    return result


def clear_override(result: PacketResult, name: str) -> PacketResult:
    if name not in result.fields:
        raise ValueError(f"Unknown field {name!r}.")
    result.fields[name].override = None
    return result
