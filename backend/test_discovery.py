"""Discovery (discovery.py): feeds, freshness, Workday places, find-ats, prepare, a whole run. No network.

Run: cd backend && python test_discovery.py
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./test_discovery.db"
os.environ["LOG_DIR"] = "./logs"

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException

import discovery as d
import main
from database import Base, Job, LLMTaskStatus, ResumeVersion, SessionLocal, User, engine, init_db

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
WD = "https://acme.wd3.myworkdayjobs.com/External"
LI_OPEN = '<h2 class="topcard__title">Data Scientist</h2><a class="offsite-apply-icon"></a>'


def gh_job(title, loc, days_old, url):
    return {"absolute_url": url, "title": title, "location": {"name": loc}, "content": "&lt;p&gt;Build models&lt;/p&gt;",
            "first_published": (NOW - timedelta(days=days_old)).isoformat()}


def transport(routes: dict, log: list = None):
    """routes: (method, url without query) -> (status, json or text)."""
    def handle(req: httpx.Request):
        key = (req.method, str(req.url).split("?")[0])
        if log is not None:
            log.append(key)
        status, body = routes.get(key, (404, "nope"))
        return httpx.Response(status, json=body) if isinstance(body, (dict, list)) else httpx.Response(status, text=body)
    return httpx.MockTransport(handle)


def client(routes, log=None):
    return httpx.AsyncClient(transport=transport(routes, log), follow_redirects=True)


def test_helpers():
    assert d.strip_html("<p>A &amp; B</p>\n  <b>c</b>") == "A & B\nc"
    assert d.age_hours({"postedAt": (NOW - timedelta(hours=5)).isoformat()}, NOW) == 5
    assert d.age_hours({"postedAt": "2026-10-27T10:00:00Z"}, NOW) == 2
    assert d.age_hours({"postedOn": "Posted 3 Days Ago"}, NOW) == 72
    assert d.age_hours({"postedOn": "Posted 30+ Days Ago"}, NOW) == 720
    assert d.age_hours({"postedOn": "Posted Yesterday"}, NOW) == 24
    assert d.age_hours({"postedOn": "whenever"}, NOW) is None                     # unknown age: kept
    places = ["toronto", "remote", "canada"]
    assert d.wd_place_ok("Toronto, ON ; New York", places)
    assert d.wd_place_ok("Waterloo, ON", places)                                   # Ontario counts
    assert d.wd_place_ok("Canada - Remote", places)
    assert not d.wd_place_ok("US, CA, Remote", places)
    assert not d.wd_place_ok("Vancouver, BC", places)
    posts = [{"title": "Data Scientist", "location": "London", "url": "u1"},
             {"title": "Data Scientist", "location": "Toronto", "url": "u2"},
             {"title": "Senior Data Scientist, Risk", "location": "Toronto", "url": "u3"}]
    assert d.same_job(posts, "Data Scientist", "Toronto, ON")["url"] == "u2"      # exact title, same city
    assert d.same_job([posts[0], posts[2]], "Senior Data Scientist", "Toronto, ON")["url"] == "u3"   # single loose match here
    assert d.same_job(posts, "Senior Data Scientist", "Toronto, ON") is None        # "Data Scientist" is loose too: two
    assert d.same_job(posts, "Scientist", "Toronto, ON") is None                    # two loose: never guess


def test_scan_feeds():
    t = {"title_keywords": ["data scientist"], "exclude_keywords": ["intern"], "locations": ["toronto"],
         "companies": {"greenhouse": ["acme"], "bogus": ["x"]}, "workday": {"Acme": WD}, "workday_searches": ["data"]}
    routes = {
        ("GET", "https://boards-api.greenhouse.io/v1/boards/acme/jobs"): (200, {"jobs": [
            gh_job("Data Scientist", "Toronto", 1, "https://gh/1"),
            gh_job("Data Scientist Intern", "Toronto", 1, "https://gh/2"),        # excluded word
            gh_job("Data Scientist", "Berlin", 1, "https://gh/3"),                # wrong place
            gh_job("Data Scientist", "Toronto", 9, "https://gh/4"),               # too old
            gh_job("Product Manager", "Toronto", 1, "https://gh/5")]}),
        ("POST", "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/External/jobs"): (200, {"jobPostings": [
            {"title": "Lead Data Scientist", "externalPath": "/job/Toronto/Lead-DS_R1", "locationsText": "2 Locations", "postedOn": "Posted Today"},
            {"title": "Data Scientist", "externalPath": "/job/Van/DS_R2", "locationsText": "Vancouver, BC", "postedOn": "Posted Today"}]}),
        ("GET", "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/External/job/Toronto/Lead-DS_R1"):
            (200, {"jobPostingInfo": {"location": "Toronto, ON", "jobDescription": "<p>Lead</p>"}}),
        ("GET", "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/External/job/Van/DS_R2"):
            (200, {"jobPostingInfo": {"location": "Vancouver, BC"}}),
    }

    async def go():
        async with client(routes) as c:
            return await d.scan_feeds(c, t, 72, NOW)
    r = asyncio.run(go())
    assert [(j["ats"], j["url"]) for j in r["found"]] == [
        ("greenhouse", "https://gh/1"), ("workday", f"{WD.rsplit('/', 1)[0]}/en-US/External/job/Toronto/Lead-DS_R1")], r["found"]
    assert r["found"][0]["text"] == "Build models" and r["found"][1]["text"] == "Lead"
    assert r["too_old"] == 1 and r["errors"] == ["unknown ATS 'bogus' in targets"]

    # No title keywords and no locations: no filter, on Workday as on the ATS feeds (was: every
    # Workday job dropped, silently).
    open_t = {"workday": {"Acme": WD}, "workday_searches": ["data"]}

    async def go_open():
        async with client(routes) as c:
            return await d.scan_feeds(c, open_t, 72, NOW)
    assert len([j for j in asyncio.run(go_open())["found"] if j["ats"] == "workday"]) == 2
    assert d.wd_place_ok("Vancouver, BC", [])


def test_find_ats():
    job = Job(company="Acme Technologies Inc.", title="Data Scientist", location="Toronto, ON",
              url="https://www.linkedin.com/jobs/view/data-scientist-at-acme-123")
    li = ("GET", "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/123")

    async def go(routes, t=None):
        async with client(routes) as c:
            return await d.find_ats(c, job, t or {})
    assert asyncio.run(go({li: (200, '<h2 class="topcard__title">x</h2> No longer accepting applications')})) == {"route": "closed"}
    assert asyncio.run(go({li: (200, '<h2 class="topcard__title">x</h2>')})) == {"route": "easy_apply"}
    gh = ("GET", "https://boards-api.greenhouse.io/v1/boards/acme/jobs")
    hit = asyncio.run(go({li: (200, LI_OPEN), gh: (200, {"jobs": [gh_job("Data Scientist", "Toronto", 1, "https://gh/9")]})}))
    assert hit == {"route": "found", "platform": "greenhouse", "url": "https://gh/9"}
    wd = ("POST", "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/External/jobs")
    hit = asyncio.run(go({li: (429, "slow down"), wd: (200, {"jobPostings": [
        {"title": "Data Scientist", "externalPath": "/job/Toronto-ON/DS_R7", "locationsText": "Toronto, ON"}]})}, {"workday": {"Acme": WD}}))
    assert hit == {"route": "found", "platform": "workday", "url": "https://acme.wd3.myworkdayjobs.com/en-US/External/job/Toronto-ON/DS_R7"}
    assert asyncio.run(go({li: (200, LI_OPEN)})) == {"route": "company_site", "careers_url": None}


def _db_user():
    Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()
    u = User(email="a@b.com", password_hash="x")
    db.add(u)
    db.commit()
    return db, u


def _fake_llm(scores: dict, calls: list):
    """Stand-ins for the background tasks: analysis sets the score, resume adds version 1."""
    async def analyze(job_id, llm, user_id):
        calls.append(("analyze", job_id))       # the model is resolve_llm's choice, not checked here
        db = SessionLocal()
        db.add(LLMTaskStatus(job_id=job_id, task_type="analyze", status="completed", user_id=user_id))
        j = db.get(Job, job_id)
        j.match_score, j.status = scores[job_id], "pending"
        db.commit()
        db.close()

    async def resume(job_id, llm, feedback, user_id, critic_llm="x"):
        calls.append(("resume", job_id))
        db = SessionLocal()
        ok = scores[job_id] != 99                          # 99: the model fails
        db.add(LLMTaskStatus(job_id=job_id, task_type="resume", status="completed" if ok else "failed",
                             error_message=None if ok else "model down", user_id=user_id))
        if ok:
            db.add(ResumeVersion(job_id=job_id, user_id=user_id, version=1, content_md="# R"))
        db.commit()
        db.close()
    return analyze, resume


def test_prepare():
    db, u = _db_user()
    main._put_setting(db, u.id, main.aq.MIN_SCORE_KEY, "70")
    db.commit()
    li = lambda n: f"https://www.linkedin.com/jobs/view/{n}"
    jobs = [Job(user_id=u.id, company="Acme", title="Data Scientist", location="Toronto", url=li(1), status="found"),
            Job(user_id=u.id, company="Gone", title="DS", url=li(2), status="found"),
            Job(user_id=u.id, company="Low", title="DS", url="https://x/low", status="found"),
            Job(user_id=u.id, company="Easy", title="DS", url=li(4), status="found"),
            Job(user_id=u.id, company="Broken", title="DS", url="https://x/broken", status="found")]
    db.add_all(jobs)
    db.commit()
    acme, gone, low, easy, broken = jobs
    routes = {("GET", "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/1"): (200, LI_OPEN),
              ("GET", "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/2"): (200, '<h2 class="topcard__title">x</h2>No longer accepting applications'),
              ("GET", "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4"): (200, '<h2 class="topcard__title">x</h2>'),
              ("GET", "https://boards-api.greenhouse.io/v1/boards/acme/jobs"): (200, {"jobs": [gh_job("Data Scientist", "Toronto", 1, "https://gh/a")]})}
    calls = []
    real = (main._bg_analyze, main._bg_resume)
    main._bg_analyze, main._bg_resume = _fake_llm({acme.id: 85, low.id: 40, easy.id: 90, broken.id: 99}, calls)
    t = {"llm": "local"}

    async def go(jid):
        async with client(routes) as c:
            return await d.prepare(main, c, db, u, jid, t)
    try:
        cfg_min = main._apply_config(db, u.id)["min_score"]
        assert asyncio.run(go(acme.id)) == "resume ready"
        db.refresh(acme)
        assert (acme.url, acme.platform) == ("https://gh/a", "greenhouse") and f"[find-ats: was {li(1)}]" in acme.notes
        assert asyncio.run(go(gone.id)) == "closed"
        db.refresh(gone)
        assert gone.status == "withdrawn" and ("analyze", gone.id) not in calls     # closed: no LLM spent
        assert asyncio.run(go(low.id)) == "below min score" and 40 < cfg_min
        assert asyncio.run(go(easy.id)) == "Easy Apply only"
        assert [c for c in calls if c[0] == "resume"] == [("resume", acme.id)], calls    # only the one that qualifies
        try:
            asyncio.run(go(broken.id))
            raise AssertionError("a failed resume must raise")
        except RuntimeError as e:
            assert "model down" in str(e)
        calls.clear()
        assert asyncio.run(go(acme.id)) == "resume ready" and calls == []                  # idempotent: nothing redone
    finally:
        main._bg_analyze, main._bg_resume = real
        db.close()


def test_run():
    db, u = _db_user()
    t = {"title_keywords": ["data scientist"], "locations": ["toronto"], "companies": {"greenhouse": ["acme"]},
         "linkedin_searches": [{"keywords": "data scientist"}, {"keywords": "ml"}], "llm": "local"}
    main._put_setting(db, u.id, d.TARGETS_KEY, json.dumps(t))
    old = Job(user_id=u.id, company="Old", title="DS", url="https://x/old", status="found")    # left from an earlier run
    db.add(old)
    db.commit()
    routes = {("GET", "https://boards-api.greenhouse.io/v1/boards/acme/jobs"): (200, {"jobs": [
        gh_job("Data Scientist", "Toronto", 0, "https://gh/new")]})}
    seen = {}

    async def scan_mail(payload, db, current_user):
        seen["mail"] = payload
        raise HTTPException(400, "Gmail not configured")

    async def track_jd_text(payload, db, current_user):
        seen.setdefault("tracked", []).append(payload)
        j = Job(user_id=current_user.id, company="Acme", title="Data Scientist", url=payload["url"], status="found")
        db.add(j)
        db.commit()
        return {"job_id": j.id}

    async def linkedin_search(payload, db, current_user):
        seen.setdefault("li", []).append(payload)
        raise HTTPException(429, "rate limited")

    calls = []
    real = (main.scan_mail, main.track_jd_text, main.linkedin_search, main._bg_analyze, main._bg_resume)
    main.scan_mail, main.track_jd_text, main.linkedin_search = scan_mail, track_jd_text, linkedin_search
    main._bg_analyze, main._bg_resume = _fake_llm({old.id: 90, old.id + 1: 30}, calls)
    try:
        s = asyncio.run(d.run(main, u.id, transport(routes)))
    finally:
        (main.scan_mail, main.track_jd_text, main.linkedin_search, main._bg_analyze, main._bg_resume) = real
    assert seen["mail"] == {"days": 3, "limit": 10, "llm": "local"}                  # 72 h window -> 3 days of mail
    assert seen["tracked"][0]["url"] == "https://gh/new" and seen["tracked"][0]["text"].startswith("Data Scientist\nacme\nToronto")
    assert len(seen["li"]) == 1 and seen["li"][0]["posted_within"] == "72h"         # 429: rest of LinkedIn skipped
    assert s["added"] == 1
    assert s["prepared"] == [(old.id, "resume ready"), (old.id + 1, "below min score")]   # oldest first
    assert s["errors"] == ["alert emails: Gmail not configured", "linkedin rate-limited — skipped the rest this run"]
    assert asyncio.run(d.run(main, 999))["errors"] == ["no discovery targets"]
    db.close()


def test_targets_validation_and_api():
    good = {"title_keywords": ["Data Scientist"], "companies": {"greenhouse": ["acme"]},
            "workday": {"Acme": WD + "/"}, "linkedin_searches": [{"keywords": "ml", "limit": 50}], "junk": 1}
    c = d.clean_targets(good)
    assert c["title_keywords"] == ["data scientist"] and c["workday"] == {"Acme": WD} and "junk" not in c
    assert c["linkedin_searches"] == [{"keywords": "ml", "location": "", "limit": 10}]
    for bad in ([], {"companies": {"taleo": ["x"]}}, {"companies": {"lever": ["../etc"]}},
                {"workday": {"X": "http://evil.com/a"}}, {"title_keywords": "ds"}, {"linkedin_searches": [{"keywords": ""}]}):
        try:
            d.clean_targets(bad)
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass
    db, u = _db_user()
    assert main.get_discovery_targets(db=db, current_user=u)["targets"] is None
    try:
        main.put_discovery_targets({"targets": {"workday": {"X": "nope"}}}, db=db, current_user=u)
        raise AssertionError("bad targets saved")
    except HTTPException as e:
        assert e.status_code == 400
    main.put_discovery_targets({"targets": good}, db=db, current_user=u)
    assert main.get_discovery_targets(db=db, current_user=u)["targets"] == c
    try:
        main.run_discovery(db=db, current_user=u)
        raise AssertionError("ran without the scheduler")
    except HTTPException as e:
        assert e.status_code == 409
    main.put_discovery_targets({"targets": {}}, db=db, current_user=u)
    assert main.get_discovery_targets(db=db, current_user=u)["targets"] is None
    db.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)


def test_targets_say_where_discovery_runs():
    """The hosted app (no scheduler) tells the panel discovery doesn't run there."""
    import scheduler
    db = main.SessionLocal()
    u = db.query(main.User).first() or main.User(email="r@h.com", password_hash="x")
    if not u.id:
        db.add(u)
        db.commit()
    real = scheduler.CURRENT
    try:
        scheduler.CURRENT = None
        assert main.get_discovery_targets(db, u)["runs_here"] is False
        scheduler.CURRENT = object()
        assert main.get_discovery_targets(db, u)["runs_here"] is True
    finally:
        scheduler.CURRENT = real
        db.close()
