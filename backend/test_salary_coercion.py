"""LLM-extracted salaries must land in the Integer columns as ints.

Floats (e.g. "$117,923.72 per annum") get stored as REAL by SQLite and then
break JobOut validation, which 500s GET /api/jobs for the whole account.
"""
from main import _as_int
from schemas import JobOut


def test_as_int():
    assert _as_int(117923.72) == 117923
    assert _as_int("138547.50") == 138547
    assert _as_int(150000) == 150000
    assert _as_int(None) is None
    assert _as_int("competitive") is None
    assert _as_int(True) is None


def test_jobout_rejects_float_salary():
    """Guards the reason _as_int exists: a REAL salary kills the list response."""
    from pydantic import ValidationError
    row = dict(
        id=1, company="X", title="Y", url=None, job_description=None, location=None,
        salary_max=None, remote=False, listed_at=None, platform=None, status="found",
        match_score=None, strengths=None, gaps=None, action_items=None, archetype=None,
        posting_legitimacy=None, priority="medium", starred=False, recruiter_name=None,
        recruiter_email=None, recruiter_linkedin=None, applied_at=None,
        last_contact_at=None, follow_up_due=None, offer_amount=None,
        rejection_reason=None, notes=None,
        created_at="2026-07-28T00:00:00", updated_at="2026-07-28T00:00:00",
    )
    try:
        JobOut(**row, salary_min=117923.72)
        raise AssertionError("expected JobOut to reject a fractional salary")
    except ValidationError:
        pass
    assert JobOut(**row, salary_min=_as_int(117923.72)).salary_min == 117923


if __name__ == "__main__":
    test_as_int()
    test_jobout_rejects_float_salary()
    print("ok")
