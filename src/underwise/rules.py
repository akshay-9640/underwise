"""R1 to R5 and the overall status, in plain code. The model never decides a rule.

Rule names, the TIV threshold and the minimum loss history come from data/rules.json. Every rule reads
the effective value of its fields, so a reviewer's override is used once made,
and NOT_EXTRACTED is never treated as absent.
"""

import json
from functools import lru_cache
from pathlib import Path

from .models import Field, RuleResult

RULES_FILE = Path(__file__).resolve().parents[2] / "data" / "rules.json"


@lru_cache
def spec() -> dict:
    return {r["id"]: r for r in json.loads(RULES_FILE.read_text())["rules"]}


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _result(rule_id: str, status: str, reason: str, *fields: Field) -> RuleResult:
    values = {f.name: {"value": f.effective_value, "state": f.effective_state, "human_override": f.override is not None}
              for f in fields}
    return RuleResult(id=rule_id, name=spec()[rule_id]["name"], status=status, reason=reason, values=values,
                      evidence=[e for f in fields for e in (f.evidence + [a.evidence for a in f.alternatives])])


def _why(f: Field) -> str:
    """Plain words for a field that is not KNOWN."""
    label = f.name.replace("_", " ")
    return {
        "MISSING": f"{label} is absent",
        "CONFLICT": f"{label} conflicts between sources ({', '.join(f'{a.raw} in {a.evidence.source_file}' for a in f.alternatives)})",
        "INVALID": f"{label} is invalid" + (f": {f.note}" if f.note else ""),
        "NOT_EXTRACTED": f"{label} was not extracted (model unavailable)",
    }[f.effective_state]


def r1(name: Field, date: Field) -> RuleResult:
    fields = (name, date)
    absent = [f for f in fields if f.effective_state == "MISSING"]
    if absent:  # FLAG for an absent value takes precedence over UNKNOWN
        return _result("R1", "FLAG", _cap("; ".join(map(_why, absent))) + ".", *fields)
    unresolved = [f for f in fields if f.effective_state != "KNOWN"]
    if unresolved:
        return _result("R1", "UNKNOWN", _cap("; ".join(map(_why, unresolved))) + ".", *fields)
    return _result("R1", "PASS", "Business name and requested effective date are present, valid and consistent.", *fields)


def _count(f: Field) -> int | None:
    v = f.effective_value
    return v if f.effective_state == "KNOWN" and isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def r2(declared: Field, schedule: Field) -> RuleResult:
    d, s = _count(declared), _count(schedule)
    if d is None or s is None:
        bad = [f for f, v in ((declared, d), (schedule, s)) if v is None]
        why = "; ".join(_why(f) if f.effective_state != "KNOWN" else f"{f.name.replace('_', ' ')} is not a nonnegative integer" for f in bad)
        return _result("R2", "UNKNOWN", _cap(why) + ".", declared, schedule)
    if d == s:
        return _result("R2", "PASS", f"Declared count {d} equals the {s} locations on the schedule.", declared, schedule)
    return _result("R2", "FLAG", f"Declared count {d} differs from the {s} locations on the schedule.", declared, schedule)


def r3(tiv: Field) -> RuleResult:
    limit = spec()["R3"]["threshold_usd"]
    v = tiv.effective_value
    if tiv.effective_state != "KNOWN":
        return _result("R3", "UNKNOWN", _cap(_why(tiv)) + ".", tiv)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        return _result("R3", "UNKNOWN", f"Total insured value {v!r} is negative or not a dollar amount.", tiv)
    if v > limit:  # rules.json: operator greater_than, so exactly $10,000,000 passes
        return _result("R3", "FLAG", f"Total insured value ${v:,.0f} is above ${limit:,} and needs a senior referral.", tiv)
    return _result("R3", "PASS", f"Total insured value ${v:,.0f} is within ${limit:,}.", tiv)


def r4(sprinklered: Field) -> RuleResult:
    v = sprinklered.effective_value
    if sprinklered.effective_state != "KNOWN" or not isinstance(v, bool):
        return _result("R4", "UNKNOWN", _cap(_why(sprinklered) if sprinklered.effective_state != "KNOWN" else "Sprinkler answer is ambiguous") + ".", sprinklered)
    if v:
        return _result("R4", "PASS", "The applicant explicitly states the buildings are sprinklered.", sprinklered)
    return _result("R4", "FLAG", "The applicant explicitly answered No to automatic sprinklers.", sprinklered)


def r5(loss_runs: Field, years: Field) -> RuleResult:
    need = spec()["R5"]["min_years"]
    v = loss_runs.effective_value
    if loss_runs.effective_state == "KNOWN" and v is False:
        return _result("R5", "FLAG", "No loss runs report is attached. A promise in the email does not count.", loss_runs)
    if not (loss_runs.effective_state == "KNOWN" and v is True):
        return _result("R5", "UNKNOWN", loss_runs.note or _cap(_why(loss_runs)) + ".", loss_runs)
    n = _count(years)
    if n is None:
        return _result("R5", "UNKNOWN", f"A loss runs report is attached, but the years it covers are unclear: {_why(years) if years.effective_state != 'KNOWN' else 'not a whole number'}.", loss_runs, years)
    if n < need:
        return _result("R5", "FLAG", f"The loss runs report covers {n} year{'s' if n != 1 else ''}; at least {need} are required.", loss_runs, years)
    return _result("R5", "PASS", f"A readable loss runs report is attached and covers {n} years (at least {need} required).", loss_runs, years)


def evaluate(fields: dict[str, Field]) -> tuple[list[RuleResult], str]:
    f = fields
    rules = [r1(f["business_name"], f["requested_effective_date"]),
             r2(f["declared_location_count"], f["schedule_location_count"]),
             r3(f["total_insured_value"]), r4(f["sprinklered"]), r5(f["loss_runs_present"], f["loss_history_years"])]
    overall = "READY_FOR_REVIEW" if all(r.status == "PASS" for r in rules) else "NEEDS_REVIEW"
    return rules, overall
