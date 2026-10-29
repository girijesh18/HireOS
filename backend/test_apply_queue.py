"""Apply queue + local LLM provider tests.

Run: cd backend && python test_apply_queue.py
"""
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///./test_apply_queue.db"
os.environ["LOG_DIR"] = "./logs"
os.environ["OUTPUT_DIR"] = "./test_apply_output"

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import apply_queue as aq
import main
from database import Base, Job, ResumeVersion, SessionLocal, User, engine, init_db
from fastapi import HTTPException
from llm_router import LLMRouter


def fresh_db():
    Base.metadata.drop_all(bind=engine)
    init_db()
    return SessionLocal()


def make_user(db, email="a@b.com"):
    u = User(email=email, password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def make_job(db, user, *, status="approved", score=80.0, platform="greenhouse",
             url=None, resume=True, company="Acme", title="Data Scientist"):
    job = Job(company=company, title=title, url=url or f"https://boards.greenhouse.io/acme/jobs/{id(object())}",
              status=status, match_score=score, platform=platform, user_id=user.id)
    db.add(job)
    db.commit()
    db.refresh(job)
    if resume:
        out = Path(os.environ["OUTPUT_DIR"]) / str(job.id)
        out.mkdir(parents=True, exist_ok=True)
        pdf = out / "resume_v1.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        db.add(ResumeVersion(job_id=job.id, user_id=user.id, version=1, pdf_path=str(pdf)))
        db.commit()
    return job


def set_config(db, user, **cfg):
    return main.put_apply_config(cfg, db=db, current_user=user)


def claim(db, user, **payload):
    return main.apply_next(payload, db=db, current_user=user)


# Auto mode's pace (weekday work hours, one claim per AUTO_GAP) has its own test below;
# no other test may depend on when it runs.
REAL_AUTO_CLAIM_ALLOWED = aq.auto_claim_allowed
aq.auto_claim_allowed = lambda *a: True


# ── Pure helpers ───────────────────────────────────────────────────────────────

def test_detect_platform():
    cases = {
        "https://www.linkedin.com/jobs/view/4012345678/": "linkedin",
        "https://ca.indeed.com/viewjob?jk=abc123": "indeed",
        "https://boards.greenhouse.io/acme/jobs/123": "greenhouse",
        "https://job-boards.greenhouse.io/acme/jobs/123": "greenhouse",
        "https://jobs.lever.co/acme/uuid": "lever",
        "https://jobs.ashbyhq.com/acme/uuid": "ashby",
        "https://acme.wd3.myworkdayjobs.com/en-US/careers/job/x": "workday",
        "https://careers.acme.com/jobs/1": "other",
        "": "other",
    }
    for url, want in cases.items():
        assert aq.detect_platform(url) == want, (url, aq.detect_platform(url))


def test_normalize_url_dedupes_linkedin_and_tracking_params():
    """The same posting reached from two alert emails must collapse to one key."""
    a = aq.normalize_url("https://www.linkedin.com/jobs/view/4012345678/?refId=abc&trackingId=xyz")
    b = aq.normalize_url("https://linkedin.com/jobs/search/?currentJobId=4012345678&keywords=ds")
    c = aq.normalize_url("https://www.linkedin.com/jobs/view/senior-data-scientist-at-acme-4012345678")
    assert a == b == c == "linkedin.com/jobs/view/4012345678", (a, b, c)
    g1 = aq.normalize_url("https://boards.greenhouse.io/acme/jobs/123?gh_src=email&utm_source=x")
    g2 = aq.normalize_url("boards.greenhouse.io/acme/jobs/123/")
    assert g1 == g2, (g1, g2)
    assert aq.normalize_url("https://ca.indeed.com/viewjob?jk=abc&from=alert") == aq.normalize_url(
        "https://www.indeed.com/rc/clk?jk=abc&tk=1")


def test_range_cap_is_stable_within_a_day_and_bounded():
    caps = aq.parse_caps(None)
    day = date(2026, 10, 20)
    first = aq.daily_cap(caps, "linkedin", 1, day)
    assert all(aq.daily_cap(caps, "linkedin", 1, day) == first for _ in range(5))
    seen = {aq.daily_cap(caps, "linkedin", 1, day + timedelta(days=i)) for i in range(60)}
    assert min(seen) >= 10 and max(seen) <= 20, seen
    assert len(seen) > 1, "a range cap should vary across days"


def test_bad_caps_json_falls_back_to_defaults():
    assert aq.parse_caps("{not json") == aq.DEFAULT_CAPS
    assert aq.parse_caps('{"LinkedIn": 5}')["linkedin"] == 5


def test_day_bounds_follow_user_timezone():
    # 03:00 UTC on Oct 21 is still Oct 20 in Toronto (UTC-4).
    day, start, end = aq.day_bounds_utc("America/Toronto", datetime(2026, 10, 21, 3, 0))
    assert day == date(2026, 10, 20), day
    assert start == datetime(2026, 10, 20, 4, 0) and end - start == timedelta(days=1), (start, end)


# ── Queue endpoints ────────────────────────────────────────────────────────────

def test_manual_mode_only_hands_out_approved_jobs():
    db = fresh_db()
    u = make_user(db)
    make_job(db, u, status="pending", score=99)
    assert claim(db, u).status_code == 204, "pending jobs need approval when auto-apply is off"
    job = make_job(db, u, status="approved", score=60)
    got = claim(db, u)
    assert got["job_id"] == job.id and got["resume_pdf_url"].endswith("/resume.pdf"), got
    db.refresh(job)
    assert job.status == "applying" and job.meta["apply_attempts"] == 1


def test_auto_apply_approves_only_at_or_above_min_score():
    db = fresh_db()
    u = make_user(db)
    set_config(db, u, auto_apply=True, min_score=80)
    main.put_apply_config({"auto_min_score": 80}, db=db, current_user=u)   # auto mode's own bar
    low = make_job(db, u, status="pending", score=79.9)
    high = make_job(db, u, status="pending", score=80)
    assert claim(db, u)["job_id"] == high.id
    assert claim(db, u).status_code == 204, "the 79.9 job must stay in pending"
    db.refresh(low)
    assert low.status == "pending"


def test_job_without_resume_pdf_is_skipped():
    db = fresh_db()
    u = make_user(db)
    make_job(db, u, resume=False)
    assert claim(db, u).status_code == 204


def test_daily_cap_counts_applied_and_in_flight_claims():
    db = fresh_db()
    u = make_user(db)
    set_config(db, u, caps={"greenhouse": 2})
    for i in range(4):
        make_job(db, u, company=f"Co{i}")
    first = claim(db, u)
    main.apply_result(first["job_id"], {"outcome": "submitted"}, db=db, current_user=u)
    claim(db, u)  # in flight, not reported yet
    try:
        claim(db, u)
        assert False, "third claim should hit the cap"
    except HTTPException as e:
        assert e.status_code == 429, e.status_code
    status = main.apply_status(db=db, current_user=u)
    assert status["today"]["greenhouse"] == {"used": 2, "cap": 2}, status["today"]


def test_cap_on_one_platform_still_serves_another():
    db = fresh_db()
    u = make_user(db)
    set_config(db, u, caps={"linkedin": 0})
    make_job(db, u, platform="linkedin", url="https://www.linkedin.com/jobs/view/1")
    lever = make_job(db, u, platform="lever", url="https://jobs.lever.co/acme/1")
    assert claim(db, u)["job_id"] == lever.id


def test_platform_filter():
    db = fresh_db()
    u = make_user(db)
    make_job(db, u, platform="greenhouse", score=90)
    lever = make_job(db, u, platform="lever", url="https://jobs.lever.co/acme/1", score=70)
    assert claim(db, u, platforms=["lever"])["job_id"] == lever.id


def test_job_id_claims_only_that_job():
    db = fresh_db()
    u = make_user(db)
    make_job(db, u, platform="greenhouse", score=90)
    low = make_job(db, u, platform="lever", url="https://jobs.lever.co/acme/1", score=70)
    assert claim(db, u, job_id=low.id)["job_id"] == low.id


def test_stale_claim_returns_to_queue():
    db = fresh_db()
    u = make_user(db)
    job = make_job(db, u)
    claim(db, u)
    assert claim(db, u).status_code == 204, "a live claim must not be handed out twice"
    db.refresh(job)
    job.meta = {**job.meta, "apply_claimed_at": (datetime.utcnow() - aq.CLAIM_TTL - timedelta(minutes=1)).isoformat()}
    db.commit()
    assert claim(db, u)["job_id"] == job.id


def test_failed_twice_goes_to_needs_human():
    db = fresh_db()
    u = make_user(db)
    job = make_job(db, u)
    claim(db, u)
    assert main.apply_result(job.id, {"outcome": "failed"}, db=db, current_user=u)["status"] == "approved"
    claim(db, u)
    assert main.apply_result(job.id, {"outcome": "failed"}, db=db, current_user=u)["status"] == "needs_human"
    assert claim(db, u).status_code == 204


def test_submitted_sets_applied_and_saves_screenshot_and_answers():
    import base64
    db = fresh_db()
    u = make_user(db)
    job = make_job(db, u)
    claim(db, u)
    shot = base64.b64encode(b"\x89PNG fake").decode()
    main.apply_result(job.id, {"outcome": "submitted", "notes": "ok",
                               "answers": [{"label": "Visa", "answer": "No sponsorship needed"}],
                               "screenshot_b64": "data:image/png;base64," + shot}, db=db, current_user=u)
    db.refresh(job)
    assert job.status == "applied" and job.applied_at is not None
    assert job.meta["apply_answers"][0]["label"] == "Visa"
    name = job.meta["apply_screenshots"][0]
    assert (Path(os.environ["OUTPUT_DIR"]) / str(job.id) / name).read_bytes() == b"\x89PNG fake"
    assert "apply_claimed_at" not in job.meta


def test_skipped_is_not_handed_out_again_by_auto_apply():
    db = fresh_db()
    u = make_user(db)
    set_config(db, u, auto_apply=True, min_score=50)
    job = make_job(db, u, status="pending", score=90)
    claim(db, u)
    main.apply_result(job.id, {"outcome": "skipped", "notes": "role is contract"}, db=db, current_user=u)
    assert claim(db, u).status_code == 204


def test_bad_outcome_and_config_are_rejected():
    db = fresh_db()
    u = make_user(db)
    job = make_job(db, u)
    for bad in ({"outcome": "maybe"}, {}):
        try:
            main.apply_result(job.id, bad, db=db, current_user=u)
            assert False
        except HTTPException as e:
            assert e.status_code == 400
    for bad in ({"min_score": 150}, {"timezone": "Mars/Base"}, {"caps": [1, 2]}):
        try:
            set_config(db, u, **bad)
            assert False, bad
        except HTTPException as e:
            assert e.status_code == 400


def test_other_users_jobs_are_invisible():
    db = fresh_db()
    a, b = make_user(db, "a@x.com"), make_user(db, "b@x.com")
    job = make_job(db, a)
    assert claim(db, b).status_code == 204
    try:
        main.apply_result(job.id, {"outcome": "submitted"}, db=db, current_user=b)
        assert False
    except HTTPException as e:
        assert e.status_code == 404


def test_duplicate_detection_by_url_and_by_company_title():
    db = fresh_db()
    u = make_user(db)
    job = make_job(db, u, url="https://www.linkedin.com/jobs/view/4012345678/?trk=x", platform="linkedin")
    assert main._find_duplicate_job(db, u.id, url="https://linkedin.com/jobs/view/4012345678") .id == job.id
    assert main._find_duplicate_job(db, u.id, company="ACME", title="data scientist").id == job.id
    assert main._find_duplicate_job(db, u.id, url="https://linkedin.com/jobs/view/999") is None


def test_approve_endpoint():
    db = fresh_db()
    u = make_user(db)
    job = make_job(db, u, status="pending")
    assert main.approve_job(job.id, db=db, current_user=u)["status"] == "approved"
    job.status = "applied"
    db.commit()
    try:
        main.approve_job(job.id, db=db, current_user=u)
        assert False
    except HTTPException as e:
        assert e.status_code == 409


def test_reapproving_needs_human_job_resets_attempts():
    db = fresh_db()
    u = make_user(db)
    job = make_job(db, u)
    for _ in range(2):
        claim(db, u)
        main.apply_result(job.id, {"outcome": "failed"}, db=db, current_user=u)
    assert claim(db, u).status_code == 204
    main.approve_job(job.id, db=db, current_user=u)
    assert claim(db, u)["attempt"] == 1


def test_pause_blocks_every_claim():
    db = fresh_db()
    u = make_user(db)
    make_job(db, u)
    set_config(db, u, paused=True)
    try:
        claim(db, u)
        assert False
    except HTTPException as e:
        assert e.status_code == 423
    set_config(db, u, paused=False)
    assert claim(db, u)["job_id"]


def test_max_attempts_and_claim_ttl_are_configurable():
    db = fresh_db()
    u = make_user(db)
    job = make_job(db, u)
    set_config(db, u, max_attempts=1, claim_ttl_minutes=30)
    claim(db, u)
    db.refresh(job)
    job.meta = {**job.meta, "apply_claimed_at": (datetime.utcnow() - timedelta(minutes=31)).isoformat()}
    db.commit()
    assert claim(db, u).status_code == 204, "stale after 30 min, but its one attempt is used up"
    set_config(db, u, max_attempts=3)
    assert claim(db, u)["attempt"] == 2
    assert main.apply_result(job.id, {"outcome": "failed"}, db=db, current_user=u)["status"] == "approved"
    for bad in ({"max_attempts": 0}, {"claim_ttl_minutes": 5}, {"max_attempts": "x"}):
        try:
            set_config(db, u, **bad)
            assert False, bad
        except HTTPException as e:
            assert e.status_code == 400


def test_application_profile_feeds_answers_and_drafts_are_flagged():
    import asyncio
    db = fresh_db()
    u = make_user(db)
    job = make_job(db, u)
    main.put_application_profile({"salary_expectation": "CAD 160,000", "eeo_gender": "",
                                  "custom_qa": [{"q": "Over 18?", "a": "Yes"}], "_allow_drafts": True},
                                 db=db, current_user=u)
    seen = {}

    class FakeRouter:
        async def structured_complete(self, prompt, schema, llm=None, system=None):
            seen.update(prompt=prompt, system=system)
            return schema(answers=[{"id": "s", "answer": "CAD 160,000"},
                                   {"id": "w", "answer": "I like the mission", "drafted": True},
                                   {"id": "o", "answer": None, "drafted": True}])

    orig = main.get_llm_router, main.resolve_llm
    main.get_llm_router, main.resolve_llm = (lambda db, uid: FakeRouter()), (lambda db, uid, llm: "local")
    try:
        qs = [{"id": "s", "label": "Salary?"}, {"id": "w", "label": "Why us?", "type": "textarea"},
              {"id": "o", "label": "Other"}]
        out = asyncio.run(main.answer_questions({"job_id": job.id, "questions": qs}, db=db, current_user=u))
    finally:
        main.get_llm_router, main.resolve_llm = orig
    assert "salary expectation: CAD 160,000" in seen["prompt"] and "Q: Over 18?\nA: Yes" in seen["prompt"]
    assert "eeo gender" not in seen["prompt"], "blank fields stay out of the prompt"
    assert "_allow_drafts" not in seen["prompt"] and "DRAFTING ALLOWED" in seen["system"]
    by_id = {a["id"]: a for a in out["answers"]}
    assert by_id["w"]["drafted"] and not by_id["s"]["drafted"] and not by_id["o"]["drafted"]
    ev = db.query(main.ApplicationEvent).filter_by(job_id=job.id).order_by(main.ApplicationEvent.id.desc()).first()
    assert "drafted (review): Why us?" in ev.description

    main.put_application_profile({}, db=db, current_user=u)
    assert main.get_application_profile(db=db, current_user=u) == {}


def test_flags_and_activity():
    db = fresh_db()
    u = make_user(db)
    stuck, human, done = make_job(db, u, company="Stuck"), make_job(db, u, company="Human"), make_job(db, u, company="Done")
    for j in (stuck, human, done):
        db.refresh(j)
    assert [claim(db, u)["job_id"] for _ in range(3)] == [stuck.id, human.id, done.id]
    main.apply_result(human.id, {"outcome": "needs_human", "notes": "CAPTCHA"}, db=db, current_user=u)
    main.apply_result(done.id, {"outcome": "submitted"}, db=db, current_user=u)
    db.refresh(stuck)
    stuck.meta = {**stuck.meta, "apply_claimed_at": (datetime.utcnow() - timedelta(hours=3)).isoformat()}
    db.commit()
    set_config(db, u, paused=True, caps={"greenhouse": 1})

    kinds = {f["kind"]: f for f in main.flags(db=db, current_user=u)}
    assert {"paused", "stuck", "needs_human", "cap_reached", "no_screenshot"} <= set(kinds), kinds
    assert kinds["needs_human"]["message"] == "CAPTCHA"
    assert main.flags(db=db, current_user=u)[0]["severity"] == "high", "most severe first"

    main.dismiss_flag({"id": kinds["needs_human"]["id"]}, db=db, current_user=u)
    assert "needs_human" not in {f["kind"] for f in main.flags(db=db, current_user=u)}

    feed = main.activity(db=db, current_user=u)
    titles = [e["event_title"] for e in feed]
    assert "Apply agent: claimed" in titles and "Apply agent: needs human" in titles
    assert all(e["company"] for e in feed)
    assert all(e["source"] == "agent" for e in main.activity(source="agent", db=db, current_user=u))
    other = make_user(db, "x@y.com")
    assert main.activity(db=db, current_user=other) == [] and main.flags(db=db, current_user=other) == []


# ── Local LLM provider ─────────────────────────────────────────────────────────

def test_local_provider_offered_only_when_configured():
    assert "local" not in LLMRouter(keys={}, allow_env=False).available_providers()
    r = LLMRouter(keys={"local_url": "http://localhost:8080/v1"}, allow_env=False)
    assert "local" in r.available_providers()
    assert r.default_llm() == "local", "the only configured provider should be the default"


def test_local_provider_calls_openai_compatible_server():
    import asyncio
    seen = {}

    class FakeCompletions:
        async def create(self, **kw):
            seen.update(kw)
            msg = type("M", (), {"content": "hello from llama"})()
            choice = type("C", (), {"message": msg, "finish_reason": "stop"})()
            return type("R", (), {"choices": [choice], "usage": None})()

    class FakeClient:
        def __init__(self, **kw):
            seen["base_url"] = kw["base_url"]
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    import openai
    original = openai.AsyncOpenAI
    openai.AsyncOpenAI = FakeClient
    try:
        r = LLMRouter(keys={"local_url": "http://host.docker.internal:8080/v1/"}, allow_env=False)
        out = asyncio.run(r.complete("hi", llm="local", fallback=False))
    finally:
        openai.AsyncOpenAI = original
    assert out == "hello from llama", out
    assert seen["base_url"] == "http://host.docker.internal:8080/v1" and seen["model"] == "local", seen
    assert seen["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False, seen


def test_local_llm_url_setting_maps_to_router_key():
    db = fresh_db()
    u = make_user(db)
    main._put_setting(db, u.id, "local_llm_url", "http://localhost:8080/v1")
    db.commit()
    assert "local" in main.get_llm_router(db, u.id).available_providers()


def test_auto_mode_pace():
    ok = REAL_AUTO_CLAIM_ALLOWED
    tue_10 = datetime(2026, 10, 27, 14, 0)                       # 10:00 in Toronto (EDT)
    assert ok("America/Toronto", tue_10, None)
    assert not ok("America/Toronto", tue_10, (tue_10 - timedelta(minutes=5)).isoformat())   # inside the gap
    assert ok("America/Toronto", tue_10, (tue_10 - timedelta(minutes=21)).isoformat())
    assert not ok("America/Toronto", datetime(2026, 10, 28, 1, 0), None)    # 21:00 the evening before
    assert not ok("America/Toronto", datetime(2026, 10, 25, 14, 0), None)   # Sunday
    assert ok("Bad/Zone", tue_10, "garbage")                                # bad tz / bad stamp: defaults

    db = fresh_db()
    u = make_user(db)
    set_config(db, u, auto_apply=True, min_score=80)
    pending = make_job(db, u, status="pending", score=95, company="Auto")
    approved = make_job(db, u, status="approved", score=60, company="Manual")
    aq.auto_claim_allowed = lambda *a: False
    try:
        assert claim(db, u)["job_id"] == approved.id, "a job the user approved is never held by the pace"
        assert claim(db, u).status_code == 204, "outside the pace auto mode waits"
        assert claim(db, u, job_id=pending.id)["job_id"] == pending.id, "naming the job overrides the pace"
    finally:
        aq.auto_claim_allowed = lambda *a: True
    assert main._setting(db, u.id, aq.AUTO_LAST_KEY), "an auto claim starts the gap"


def test_worker_star_takes_every_platform():
    db = fresh_db()
    u = make_user(db)
    gh = make_job(db, u, platform="greenhouse", company="G")
    other = make_job(db, u, platform="icims", company="I", score=70)
    main._put_setting(db, u.id, "worker_platforms", "*")
    db.commit()
    assert claim(db, u).status_code == 204, "with '*' nothing is left for OpenClaw"
    assert claim(db, u, worker=True)["job_id"] == gh.id
    assert claim(db, u, worker=True)["job_id"] == other.id


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except Exception as e:
                failures += 1
                print(f"  FAIL  {name}: {e.__class__.__name__}: {e}")
    print("FAILED" if failures else "all passed")
    sys.exit(1 if failures else 0)


def test_handoff_cause_from_agent_notes():
    cases = {
        "Workday: 'Apply Manually' step 1 requires creating a Workday account": "workday",
        "Amazon apply flow requires sign-in/account creation (passport.amazon.jobs)": "account",
        "Autodesk careers site returns 'Access Denied' bot check": "bot",
        "LinkedIn Easy Apply only — needs LinkedIn login": "easy_apply",
        "no public apply route — LinkedIn apply behind sign-in": "not_found",
        "SuccessFactors requires sign-in OR account creation plus a 'not a robot' check": "account",
        "Required field 'How did you hear?' - HireOS returned null, no grounded answer": "answers",
    }
    for note, cause in cases.items():
        assert main.handoff_cause(note) == cause, note


def test_reanalyze_keeps_handoff_status(monkeypatch):
    import asyncio
    class FakeFit:
        async def analyze(self, **kw):
            return {"match_score": 80, "strengths": [], "gaps": [], "action_items": []}
    monkeypatch.setattr(main, "get_agent", lambda *a, **k: FakeFit())
    monkeypatch.setattr(main, "get_master_resume", lambda *a, **k: "resume")
    db = SessionLocal()
    user = db.query(User).first() or User(email="reanalyze@test", hashed_password="x")
    db.add(user); db.commit()
    job = Job(user_id=user.id, company="C", title="T", status="needs_human", job_description="jd")
    db.add(job); db.commit()
    asyncio.run(main._bg_analyze(job.id, "local", user.id))
    db.refresh(job)
    assert job.status == "needs_human" and job.match_score == 80


def test_company_key_matches_variants():
    k = aq.company_key
    assert k("WSP in Canada") == k("WSP") == k("wsp")
    assert k("Manulife (John Hancock)") == k("Manulife")
    assert k("Canadian Tire Corporation") == k("Canadian Tire")
    assert k("EvenUp") != k("Even")


def test_second_job_at_same_company_is_held():
    db = fresh_db()
    u = make_user(db)
    first, second = make_job(db, u, company="WSP"), make_job(db, u, company="WSP in Canada", score=70.0)
    assert claim(db, u)["job_id"] == first.id
    main.apply_result(first.id, {"outcome": "submitted"}, db=db, current_user=u)
    claim(db, u)   # nothing else is ready
    db.refresh(second)
    assert second.status == "pending"
    # naming the job is the user's override
    second.status, second.meta = "approved", {}
    db.commit()
    assert main.apply_next({"job_id": second.id}, db=db, current_user=u)["job_id"] == second.id


def test_waiting_job_is_never_handed_out_again():
    from datetime import datetime, timedelta
    db = fresh_db()
    u = make_user(db)
    job = make_job(db, u)
    assert claim(db, u)["job_id"] == job.id
    main.apply_wait(job.id, {"reason": "CAPTCHA"}, db=db, current_user=u)
    # hours later the claim would have expired -- but a waiting job is not a claim
    job.meta = {**job.meta, "apply_claimed_at": (datetime.utcnow() - timedelta(hours=5)).isoformat()}
    db.commit()
    other = make_job(db, u, company="Other")
    assert claim(db, u)["job_id"] == other.id          # the queue moves on, never back to the waiting one
    db.refresh(job)
    assert job.status == "waiting_human"
    # the user answers: the same job is claimed again by the run that waited
    assert main.apply_continue(job.id, db=db, current_user=u)["status"] == "applying"
    main.apply_result(job.id, {"outcome": "submitted"}, db=db, current_user=u)
    db.refresh(job)
    assert job.status == "applied"


def test_waiting_job_counts_toward_daily_cap():
    db = fresh_db()
    u = make_user(db)
    set_config(db, u, caps={"greenhouse": 1})
    job = make_job(db, u, company="A")
    make_job(db, u, company="B")
    claim(db, u)
    main.apply_wait(job.id, {"reason": "email code"}, db=db, current_user=u)
    try:
        claim(db, u)
        assert False, "the waiting job holds today's only greenhouse slot"
    except HTTPException as e:
        assert e.status_code == 429


def test_resume_rules_store_and_remove():
    db = fresh_db()
    u = make_user(db)
    main.add_resume_rule({"kind": "style", "text": "Summary opens with 'Lead data scientist'"}, db=db, current_user=u)
    main.add_resume_rule({"kind": "exclude", "text": "Strategic navigation"}, db=db, current_user=u)
    main.add_resume_rule({"kind": "exclude", "text": "strategic navigation"}, db=db, current_user=u)   # duplicate ignored
    r = main.add_resume_rule({"kind": "ban", "text": "spearheaded"}, db=db, current_user=u)
    assert r == {"style": ["Summary opens with 'Lead data scientist'"], "exclude": ["Strategic navigation"],
                 "ban": ["spearheaded"]}
    assert main.remove_resume_rule("exclude", 1, db=db, current_user=u)["exclude"] == []


def test_mode_switch_is_auto_apply():
    db = fresh_db()
    u = make_user(db)
    assert main._apply_config(db, u.id)["mode"] == "review"                       # default: nothing without approval
    cfg = main.put_apply_config({"mode": "auto", "max_posting_age_hours": 48}, db=db, current_user=u)
    assert cfg["mode"] == "auto" and cfg["auto_apply"] is True and cfg["max_posting_age_hours"] == 48
    assert main.put_apply_config({"mode": "review"}, db=db, current_user=u)["auto_apply"] is False
    try:
        main.put_apply_config({"mode": "yolo"}, db=db, current_user=u)
        assert False
    except HTTPException as e:
        assert e.status_code == 400


def test_ops_reports_every_apply_outcome_once():
    db = fresh_db()
    u = make_user(db)
    a, b = make_job(db, u, company="A"), make_job(db, u, company="B")
    t0 = main.ops_errors(since="", db=db, current_user=u)["now"]
    claim(db, u)
    main.apply_result(a.id, {"outcome": "submitted", "notes": "confirmation page"}, db=db, current_user=u)
    claim(db, u)
    main.apply_wait(b.id, {"reason": "CAPTCHA"}, db=db, current_user=u)
    r = main.ops_errors(since=t0, db=db, current_user=u)
    assert [(x["job_id"], x["outcome"]) for x in r["results"]] == [(a.id, "submitted"), (b.id, "waiting for you")]
    assert main.ops_errors(since=r["now"], db=db, current_user=u)["results"] == []   # not repeated
    assert r["mode"] == "review" and r["ready"] == 0


def test_linkedin_posted_within_hours():
    import asyncio, linkedin_guest
    seen = {}

    class Resp:
        status_code, text = 200, ""
        def raise_for_status(self): pass

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, params=None, **k):
            seen.update(params or {})
            return Resp()
    orig = linkedin_guest.httpx.AsyncClient
    linkedin_guest.httpx.AsyncClient = Client
    try:
        asyncio.run(linkedin_guest.search_ids("data scientist", "Toronto", "72h", limit=1))
    except Exception:
        pass
    finally:
        linkedin_guest.httpx.AsyncClient = orig
    assert seen.get("f_TPR") == "r259200"


def test_auto_mode_uses_its_own_higher_bar():
    db = fresh_db()
    u = make_user(db)
    main.put_apply_config({"mode": "auto", "min_score": 75}, db=db, current_user=u)
    assert main._apply_config(db, u.id)["auto_min_score"] == 85               # default
    make_job(db, u, status="pending", score=80.0, company="A")               # resume-worthy, not auto-applied
    good = make_job(db, u, status="pending", score=90.0, company="B")
    assert claim(db, u)["job_id"] == good.id
    assert claim(db, u).status_code == 204, "80 < 85: auto mode must not take it"
    main.put_apply_config({"auto_min_score": 78}, db=db, current_user=u)
    assert claim(db, u)["company"] == "A"
    # never below the resume bar
    assert main.put_apply_config({"auto_min_score": 50}, db=db, current_user=u)["auto_min_score"] == 75


def test_mail_updates_move_status_forward_once(monkeypatch=None):
    import asyncio
    import mail_updates as mu
    db = fresh_db()
    u = make_user(db)
    applied = make_job(db, u, status="applied", company="OpenText", title="Senior AI Developer")
    found = make_job(db, u, status="found", company="BDO", title="AI Engineer")
    mails = [  # newest first, as IMAP returns them
        {"id": "<2>", "from": "msoos@bdo.ca", "subject": "BDO Canada - Teams Video Interview - AI Scientist",
         "date": "d2", "body": "interview for AI Scientist", "job": {"id": found.id, "company": "BDO", "title": "AI Engineer", "status": "found"}},
        {"id": "<1>", "from": "schedule@invite.opentext.com", "subject": "Interview Invitation - Senior AI Developer",
         "date": "d1", "body": "", "job": {"id": applied.id, "company": "OpenText", "title": "Senior AI Developer", "status": "applied"}},
    ]
    seen_arg = []
    orig_fetch, orig_classify = mu.fetch, mu.classify
    mu.fetch = lambda days, seen, jobs: (seen_arg.append(set(seen)), [m for m in mails if m["id"] not in seen])[1]
    async def kind(router, m, loads=None, llm=None):
        return "interview"
    mu.classify = kind
    try:
        out = asyncio.run(main.agent_scan_updates({"days": 3}, db=db, current_user=u))
        assert [x["job_id"] for x in out["updates"]] == [applied.id, found.id]          # oldest first
        db.refresh(applied); db.refresh(found)
        assert applied.status == "screening"
        assert found.status == "found", "company-only match must not change status"
        assert out["updates"][1]["title_matched"] is False
        again = asyncio.run(main.agent_scan_updates({"days": 3}, db=db, current_user=u))
        assert again["updates"] == [] and seen_arg[-1] == {"<1>", "<2>"}, "each email is handled once"
    finally:
        mu.fetch, mu.classify = orig_fetch, orig_classify


def test_exclusion_rule_reaches_open_resumes_only():
    from fastapi import BackgroundTasks
    db = fresh_db()
    u = make_user(db)
    md = "## Leadership\n- **Strategic navigation:** relocated (2021)\n- Led a team of 6\n"
    jobs = {s: make_job(db, u, status=s, company=s) for s in ("pending", "approved", "applied", "applying", "withdrawn")}
    for j in jobs.values():
        db.query(ResumeVersion).filter_by(job_id=j.id).delete()
        db.add(ResumeVersion(job_id=j.id, user_id=u.id, version=1, content_md=md))
    db.commit()
    bt = BackgroundTasks()
    r = main.add_resume_rule({"kind": "exclude", "text": "Strategic navigation"}, background_tasks=bt, db=db, current_user=u)
    assert sorted(r["updating"]) == sorted([jobs["pending"].id, jobs["approved"].id]), r["updating"]
    assert len(bt.tasks) == 1
    # nothing left to do once they're clean
    for jid in r["updating"]:
        db.add(ResumeVersion(job_id=jid, user_id=u.id, version=2, content_md="## Leadership\n- Led a team of 6\n"))
    db.commit()
    assert main.apply_resume_rules(BackgroundTasks(), db=db, current_user=u)["updating"] == []


def test_company_cooldown_holds_once_and_approval_overrides():
    from database import ApplicationEvent
    db = fresh_db()
    u = make_user(db)
    done = make_job(db, u, status="applied", company="Acme Corp")
    done.applied_at = datetime.utcnow() - timedelta(days=3)
    db.commit()
    waiting = make_job(db, u, status="pending", score=95, company="ACME CORP")   # auto off: never claimable
    again = make_job(db, u, status="approved", score=90, company="Acme Corp.")
    held = lambda j: db.query(ApplicationEvent).filter(ApplicationEvent.job_id == j.id,
                                                       ApplicationEvent.title.like("Held%")).count()
    for _ in range(3):                                   # the worker polls every minute
        assert claim(db, u).status_code == 204
    assert held(waiting) == 0                            # a job that wasn't going to be claimed isn't touched
    assert held(again) == 1 and db.get(Job, again.id).status == "pending"   # held once, not every poll
    main.approve_job(again.id, db, u)                    # the user chooses to apply anyway (Retry / Approve)
    assert claim(db, u)["job_id"] == again.id
    db.close()


def test_bad_caps_never_break_applying_and_unknown_companies_dont_collide():
    from fastapi import HTTPException
    db = fresh_db()
    u = make_user(db)
    try:
        set_config(db, u, caps={"linkedin": ["10", "x"]})
        raise AssertionError("bad caps accepted")
    except HTTPException as e:
        assert e.status_code == 400 and "linkedin" in e.detail
    assert aq.daily_cap({"linkedin": ["10", "x"]}, "linkedin", 1, date(2026, 10, 27)) == 0   # an old bad value
    main._put_setting(db, u.id, aq.CAPS_KEY, json.dumps({"greenhouse": [3, "x"]}))         # stored before validation
    db.commit()
    assert main.get_apply_config(db, u)                                                     # no 500: the fix is reachable
    assert aq.company_key("Unknown") == aq.company_key("") == aq.company_key(None) == ""
    done = make_job(db, u, status="applied", company="Unknown")
    done.applied_at = datetime.utcnow()
    db.commit()
    other = make_job(db, u, status="approved", company="Unknown", platform="lever")
    assert claim(db, u)["job_id"] == other.id                                               # not held by a stranger
    db.close()
