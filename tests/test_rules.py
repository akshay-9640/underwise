"""Rule logic, with fields built directly: no files and no model."""

import pytest

from underwise.models import Alternative, Evidence, Field, Override
from underwise.rules import evaluate, r1, r2, r3, r4, r5

EV = Evidence(source_file="broker_email.txt", locator="email body", excerpt_or_observation="x")


def known(name, value):
    return Field(name=name, value=value, state="KNOWN", evidence=[EV])


def state(name, s, note=None):
    return Field(name=name, state=s, note=note)


def conflict(name, a, b):
    return Field(name=name, state="CONFLICT", alternatives=[
        Alternative(value=a, raw=str(a), evidence=EV),
        Alternative(value=b, raw=str(b), evidence=EV.model_copy(update={"source_file": "application.pdf"}))])


NAME, DATE = known("business_name", "Example Co LLC"), known("requested_effective_date", "2027-01-01")


# R1
def test_r1_pass_flag_unknown():
    assert r1(NAME, DATE).status == "PASS"
    assert r1(NAME, state("requested_effective_date", "MISSING")).status == "FLAG"
    assert r1(NAME, state("requested_effective_date", "INVALID")).status == "UNKNOWN"
    assert r1(conflict("business_name", "A LLC", "B LLC"), DATE).status == "UNKNOWN"


def test_r1_absent_takes_precedence_over_conflict():
    r = r1(conflict("business_name", "A LLC", "B LLC"), state("requested_effective_date", "MISSING"))
    assert r.status == "FLAG" and "absent" in r.reason


def test_r1_not_extracted_is_unknown_not_absent():
    assert r1(state("business_name", "NOT_EXTRACTED"), state("requested_effective_date", "NOT_EXTRACTED")).status == "UNKNOWN"


# R2
def test_r2_counts():
    sched = lambda n: known("schedule_location_count", n)
    assert r2(known("declared_location_count", 3), sched(3)).status == "PASS"
    r = r2(known("declared_location_count", 4), sched(5))
    assert r.status == "FLAG" and "4" in r.reason and "5" in r.reason
    assert r2(state("declared_location_count", "MISSING"), sched(3)).status == "UNKNOWN"
    assert r2(conflict("declared_location_count", 3, 4), sched(3)).status == "UNKNOWN"
    assert r2(known("declared_location_count", 3), state("schedule_location_count", "INVALID", "the header is wrong")).status == "UNKNOWN"


# R3
@pytest.mark.parametrize("tiv,status", [(10_000_000, "PASS"), (10_000_000.0, "PASS"), (9_999_999.99, "PASS"), (0, "PASS"),
                                        (10_000_000.01, "FLAG"), (11_200_000, "FLAG")])
def test_r3_threshold(tiv, status):
    assert r3(known("total_insured_value", tiv)).status == status


def test_r3_unresolved_is_unknown_before_threshold():
    assert r3(conflict("total_insured_value", 9_800_000, 11_200_000)).status == "UNKNOWN"
    assert r3(state("total_insured_value", "MISSING")).status == "UNKNOWN"
    assert r3(known("total_insured_value", -5)).status == "UNKNOWN"


# R4
def test_r4_sprinklered():
    assert r4(known("sprinklered", True)).status == "PASS"
    assert r4(known("sprinklered", False)).status == "FLAG"
    assert r4(state("sprinklered", "MISSING")).status == "UNKNOWN"  # silence is not No
    assert r4(state("sprinklered", "INVALID", "answer was 'partially'")).status == "UNKNOWN"
    assert r4(conflict("sprinklered", True, False)).status == "UNKNOWN"


# R5
def test_r5_loss_history():
    present, years = known("loss_runs_present", True), lambda n: known("loss_history_years", n)
    assert r5(present, years(5)).status == "PASS"
    assert r5(present, years(3)).status == "PASS"
    r = r5(present, years(2))
    assert r.status == "FLAG" and "2 years" in r.reason
    assert r5(present, state("loss_history_years", "MISSING")).status == "UNKNOWN"
    assert r5(known("loss_runs_present", False), state("loss_history_years", "MISSING")).status == "FLAG"
    r = r5(state("loss_runs_present", "INVALID", "scan.pdf contains no selectable text"), state("loss_history_years", "MISSING"))
    assert r.status == "UNKNOWN" and "scan.pdf" in r.reason


# Overrides and overall status
def test_override_resolves_a_conflict_and_keeps_the_original():
    f = conflict("total_insured_value", 9_800_000, 11_200_000)
    f.override = Override(value=9_800_000, reason="Broker confirmed by phone", original_value=None, original_state="CONFLICT")
    r = r3(f)
    assert r.status == "PASS" and r.values["total_insured_value"]["human_override"] is True
    assert len(r.evidence) == 2  # both original alternatives stay in the evidence


def test_overall_status():
    fields = {"business_name": NAME, "requested_effective_date": DATE,
              "declared_location_count": known("declared_location_count", 3), "schedule_location_count": known("schedule_location_count", 3),
              "total_insured_value": known("total_insured_value", 8_400_000), "sprinklered": known("sprinklered", True),
              "loss_runs_present": known("loss_runs_present", True), "loss_history_years": known("loss_history_years", 5)}
    rules, overall = evaluate(fields)
    assert [r.id for r in rules] == ["R1", "R2", "R3", "R4", "R5"] and overall == "READY_FOR_REVIEW"
    fields["loss_runs_present"] = known("loss_runs_present", False)
    assert evaluate(fields)[1] == "NEEDS_REVIEW"
