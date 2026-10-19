"""Data shapes shared by the pipeline, the API, and the JSON output.

Field names and states follow data/field_specification.json. One state is added:
NOT_EXTRACTED, used only when the model did not run or failed. It keeps
"we could not look" separate from MISSING ("we looked and it is not there"),
so a model outage never makes a rule claim that a value is absent.
"""

from typing import Any, Literal

from pydantic import BaseModel

FieldState = Literal["KNOWN", "MISSING", "CONFLICT", "INVALID", "NOT_EXTRACTED"]
RuleStatus = Literal["PASS", "FLAG", "UNKNOWN"]

FIELD_NAMES = [
    "business_name", "requested_effective_date", "property_state", "occupancy_description",
    "declared_location_count", "total_insured_value", "sprinklered", "year_built",
    "loss_history_years", "loss_runs_present", "schedule_location_count",
]
# Fields the model reads: the first eight from the email and application, loss_history_years
# from the loss runs report. The last two are derived in code.
EXTRACTED_FIELDS = FIELD_NAMES[:9]


class Evidence(BaseModel):
    source_file: str
    locator: str                    # "email body", "PDF page 1", "CSV data rows 1-3 (header excluded)", ...
    excerpt_or_observation: str
    kind: Literal["excerpt", "observation"] = "excerpt"   # excerpts are verbatim from the file; observations describe what code saw


class Alternative(BaseModel):
    """One source's explicit value, kept when sources disagree or a value is invalid."""
    value: Any
    raw: str
    evidence: Evidence


class Override(BaseModel):
    value: Any
    reason: str
    original_value: Any
    original_state: FieldState


class Field(BaseModel):
    name: str
    value: Any = None
    state: FieldState
    evidence: list[Evidence] = []
    alternatives: list[Alternative] = []
    note: str | None = None
    override: Override | None = None

    @property
    def effective_value(self) -> Any:
        return self.override.value if self.override else self.value

    @property
    def effective_state(self) -> FieldState:
        if self.override:
            return "MISSING" if self.override.value is None else "KNOWN"
        return self.state


class RuleResult(BaseModel):
    id: str
    name: str
    status: RuleStatus
    reason: str
    values: dict[str, dict]          # field -> {"value", "state", "human_override"}
    evidence: list[Evidence]


class Attachment(BaseModel):
    file: str
    kind: Literal["email", "pdf", "csv"]
    readable: bool
    pages: int | None = None
    doc_type: str | None = None      # pdf only: application, loss_runs, other, or None when not determined
    doc_type_evidence: Evidence | None = None
    error: str | None = None


class StepStatus(BaseModel):
    status: Literal["OK", "SKIPPED", "FAILED"]
    detail: str | None = None


class PacketResult(BaseModel):
    packet_id: str
    created_at: str
    model: str | None                # "provider: model", or None when no model is configured
    attachments: list[Attachment]
    extraction: StepStatus
    fields: dict[str, Field]
    rules: list[RuleResult]
    overall_status: Literal["NEEDS_REVIEW", "READY_FOR_REVIEW"]
    summary: StepStatus              # detail holds the AI summary text when status is OK
    email_draft: dict[str, str]      # to, subject, body; a draft only, never sent
    disclaimer: str = "Readiness for human review only. This is not a coverage, pricing or binding decision."
