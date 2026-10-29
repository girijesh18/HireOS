"""Job discovery inside HireOS (it used to be OpenClaw's job-discovery skill + the `hireos` CLI).

One run for a user: job-alert emails -> ATS feeds and LinkedIn searches from their targets ->
prepare (fit analysis, then a tailored resume) for unscored jobs, at most PREPARE_MAX per run.
Resume cards then reach the user through the Telegram bot by themselves.

Targets are the user's `discovery_targets` setting (JSON):
  title_keywords, exclude_keywords, locations: lowercase substrings matched on title / location
  companies: {"greenhouse"|"lever"|"ashby": [board slug, ...]}
  workday: {company name: "https://<tenant>.wdN.myworkdayjobs.com/<site>"}, workday_searches: [query]
  linkedin_searches: [{keywords, location?, limit?}]   (public guest API, never a LinkedIn login)
  llm: model for extraction / scoring / resumes (optional; else the user's default)
"""
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from fastapi import BackgroundTasks, HTTPException
from loguru import logger

TARGETS_KEY = "discovery_targets"
PREPARE_MAX = 30      # a run adds ~20; 12 let the backlog grow
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
ATS_HOSTS = ["myworkdayjobs.com", "avature.net", "successfactors", "icims.com", "taleo.net", "greenhouse.io",
             "lever.co", "ashbyhq.com", "smartrecruiters.com", "oraclecloud.com", "jobvite.com", "bamboohr.com",
             "paylocity.com", "workable.com", "recruitee.com", "dayforcehcm.com", "ultipro.com", "adp.com",
             "breezy.hr", "rippling.com"]
COMPANY_NOISE = re.compile(r"\b(inc|ltd|llc|corp|corporation|canada|group|technologies|the)\b")


def strip_html(h: Optional[str]) -> str:
    s = str(h or "")
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&amp;", "&")):
        s = s.replace(a, b)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\s*\n\s*", "\n", s).strip()


def norm(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def targets(m, db, user_id: int) -> Optional[dict]:
    raw = m._setting(db, user_id, TARGETS_KEY)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


LIST_KEYS = ("title_keywords", "exclude_keywords", "locations", "workday_searches")


def clean_targets(t: Any) -> dict:
    """Validated targets (what the API stores). Raises ValueError with what is wrong."""
    if not isinstance(t, dict):
        raise ValueError("targets must be a JSON object")
    out: Dict[str, Any] = {}
    for k in LIST_KEYS:
        v = t.get(k) or []
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            raise ValueError(f"{k} must be a list of words")
        out[k] = [x.strip().lower() for x in v][:50]
    comp = t.get("companies") or {}
    if not isinstance(comp, dict) or any(k not in FEEDS or not isinstance(v, list) for k, v in comp.items()):
        raise ValueError(f"companies must map one of {sorted(FEEDS)} to a list of board slugs")
    for slugs in comp.values():
        if not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", x) for x in slugs):
            raise ValueError("a board slug is the part of the careers URL after the ATS host, e.g. 'acme'")
    out["companies"] = {k: v[:100] for k, v in comp.items()}
    wd = t.get("workday") or {}
    if not isinstance(wd, dict) or not all(isinstance(v, str) and re.fullmatch(r"https://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/[^/?#\s]+/?", v)
                                           for v in wd.values()):
        raise ValueError("workday must map a company name to https://<tenant>.wdN.myworkdayjobs.com/<site>")
    out["workday"] = {str(k)[:100]: v.rstrip("/") for k, v in list(wd.items())[:200]}
    li = t.get("linkedin_searches") or []
    if not isinstance(li, list) or not all(isinstance(x, dict) and isinstance(x.get("keywords"), str) and x["keywords"].strip() for x in li):
        raise ValueError("linkedin_searches must be a list of {keywords, location?, limit?}")
    out["linkedin_searches"] = [{"keywords": x["keywords"].strip()[:100], "location": str(x.get("location") or "")[:100],
                                 "limit": max(1, min(int(x.get("limit") or 5), 10))} for x in li[:20]]
    if t.get("llm"):
        out["llm"] = str(t["llm"])[:60]
    return out


# ── ATS feeds: public, keyless. Each returns [{url, title, location, text, postedAt|postedOn}] ──

async def greenhouse(c: httpx.AsyncClient, board: str) -> List[dict]:
    r = await c.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", params={"content": "true"})
    if r.status_code != 200:
        raise ValueError(f"greenhouse {board}: HTTP {r.status_code}")
    return [{"url": j["absolute_url"], "title": j["title"], "location": (j.get("location") or {}).get("name") or "",
             "text": strip_html(j.get("content")), "postedAt": j.get("first_published")} for j in r.json()["jobs"]]


async def lever(c: httpx.AsyncClient, company: str) -> List[dict]:
    r = await c.get(f"https://api.lever.co/v0/postings/{company}", params={"mode": "json"})
    if r.status_code != 200:
        raise ValueError(f"lever {company}: HTTP {r.status_code}")
    return [{"url": j["hostedUrl"], "title": j["text"], "location": (j.get("categories") or {}).get("location") or "",
             "text": j.get("descriptionPlain") or strip_html(j.get("description")),
             "postedAt": datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc).isoformat() if j.get("createdAt") else None}
            for j in r.json()]


async def ashby(c: httpx.AsyncClient, org: str) -> List[dict]:
    r = await c.get(f"https://api.ashbyhq.com/posting-api/job-board/{org}")
    if r.status_code != 200:
        raise ValueError(f"ashby {org}: HTTP {r.status_code}")
    return [{"url": j["jobUrl"], "title": j["title"], "location": j.get("location") or "",
             "text": j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")), "postedAt": j.get("publishedAt")}
            for j in r.json()["jobs"] if j.get("isListed") is not False]


def workday_parts(board: str) -> tuple:
    u = urlparse(board)
    return f"{u.scheme}://{u.netloc}", u.netloc.split(".")[0], [p for p in u.path.split("/") if p][-1]


async def workday(c: httpx.AsyncClient, board: str, search: str = "") -> List[dict]:
    """The career site's own JSON API, 20 per page max."""
    origin, tenant, site = workday_parts(board)
    r = await c.post(f"{origin}/wday/cxs/{tenant}/{site}/jobs", headers={"Accept": "application/json"},
                     json={"limit": 20, "offset": 0, "searchText": search, "appliedFacets": {}})
    if r.status_code != 200:
        raise ValueError(f"workday {tenant}/{site}: HTTP {r.status_code}")
    return [{"url": f"{origin}/en-US/{site}{j['externalPath']}", "title": j["title"],
             "location": j.get("locationsText") or "", "text": "", "postedOn": j.get("postedOn")}
            for j in r.json().get("jobPostings", []) if j.get("externalPath") and j.get("title")]   # malformed rows skipped


FEEDS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


def age_hours(j: dict, now: datetime) -> Optional[float]:
    """Hours since a posting went up; None when the board doesn't say (those are kept)."""
    if j.get("postedAt"):
        try:
            t = datetime.fromisoformat(str(j["postedAt"]).replace("Z", "+00:00"))
            return (now - (t if t.tzinfo else t.replace(tzinfo=timezone.utc))).total_seconds() / 3600
        except ValueError:
            return None
    mo = re.search(r"Posted\s+(Today|Yesterday|(\d+)\+?\s+Days?\s+Ago)", j.get("postedOn") or "", re.I)
    if not mo:
        return None
    w = mo.group(1).lower()
    return 0 if w == "today" else 24 if w == "yesterday" else int(mo.group(2)) * 24


def wd_place_ok(where: str, places: List[str]) -> bool:
    """Workday lists every office of a posting. A place counts when it is one of the target cities,
    or remote together with Canada; "US, CA, Remote" or on-site Vancouver does not."""
    if not places:   # no locations set: no place filter, as for the ATS feeds
        return True
    w = where.lower()
    cities = [p for p in places if p not in ("remote", "canada")]
    return any(p in w for p in cities) or bool(re.search(r"\bon, can\b|, on\b", w)) or \
        ("remote" in places and bool(re.search(r"canada[^;]{0,25}remote|remote[^;]{0,25}canada", w)))


async def scan_feeds(c: httpx.AsyncClient, t: dict, max_age: float, now: datetime) -> Dict[str, Any]:
    """Matching fresh postings from the target companies' boards: {found: [...], too_old, errors}."""
    want = [k.lower() for k in t.get("title_keywords") or []]
    avoid = [k.lower() for k in t.get("exclude_keywords") or []]
    places = [k.lower() for k in t.get("locations") or []]
    found, errors, too_old = [], [], 0

    def fresh(j):
        nonlocal too_old
        a = age_hours(j, now)
        if a is not None and a > max_age:
            too_old += 1
            return False
        return True

    for ats, slugs in (t.get("companies") or {}).items():
        if ats not in FEEDS:
            errors.append(f"unknown ATS {ats!r} in targets")
            continue
        for slug in slugs:
            try:
                for j in await FEEDS[ats](c, slug):
                    title = j["title"].lower()
                    if want and not any(k in title for k in want):
                        continue
                    if any(k in title for k in avoid):
                        continue
                    if places and not any(p in (j["location"] or "").lower() for p in places):
                        continue
                    if fresh(j):
                        found.append({"ats": ats, "company": slug, **j})
            except (httpx.HTTPError, ValueError, KeyError) as e:
                errors.append(str(e))

    # Workday: one keyword search per board, then each job's own detail call for the description
    # and, for "N Locations", the real places.
    seen = {j["url"] for j in found}
    for company, board in (t.get("workday") or {}).items():
        if board in seen:
            continue
        seen.add(board)
        origin, tenant, site = workday_parts(board)
        for q in t.get("workday_searches") or ["data scientist"]:
            try:
                for j in await workday(c, board, q):
                    title = j["title"].lower()
                    if j["url"] in seen or (want and not any(k in title for k in want)) or any(k in title for k in avoid):
                        continue
                    if not fresh(j):
                        continue
                    seen.add(j["url"])
                    path = urlparse(j["url"]).path.replace(f"/en-US/{site}", "", 1)
                    r = await c.get(f"{origin}/wday/cxs/{tenant}/{site}{path}", headers={"Accept": "application/json"})
                    info = (r.json().get("jobPostingInfo") or {}) if r.status_code == 200 else {}
                    where = " ; ".join([info.get("location") or "", *(info.get("additionalLocations") or []), j["location"]])
                    if not wd_place_ok(where, places):
                        continue
                    found.append({"ats": "workday", "company": company, **j, "location": where,
                                  "text": strip_html(info.get("jobDescription"))})
            except (httpx.HTTPError, ValueError, KeyError) as e:
                errors.append(str(e))
    return {"found": found, "too_old": too_old, "errors": errors}


# ── find-ats: a LinkedIn posting -> the company's own posting ──

async def get_page(c: httpx.AsyncClient, url: str) -> dict:
    r = await c.get(url)
    return {"url": str(r.url), "status": r.status_code, "html": r.text}


async def linkedin_page(c: httpx.AsyncClient, url: str) -> Optional[dict]:
    """How a LinkedIn posting is applied to, from its public page: route 'company_site', 'easy_apply'
    (LinkedIn's own form, needs a LinkedIn login), 'closed' or 'unknown'. None for other URLs."""
    mo = re.search(r"linkedin\.com/jobs/view/(?:[^/]*-)?(\d+)", url or "")
    if not mo:
        return None
    page = await get_page(c, f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{mo.group(1)}")
    html = page["html"]
    route = ("unknown" if page["status"] != 200 or "topcard__title" not in html      # rate-limited / error page
             else "closed" if re.search(r"No longer accepting applications", html, re.I)
             else "company_site" if "offsite-apply-icon" in html else "easy_apply")
    return {**page, "route": route}


def hrefs(base: str, html: str) -> List[str]:
    out = []
    for h in re.findall(r"href=[\"']([^\"'#]+)", html):
        try:
            u = urljoin(base, h.replace("&amp;", "&"))
        except ValueError:
            continue
        if u.startswith("http") and u not in out:
            out.append(u)
    return out


def is_ats(u: str) -> bool:
    host = urlparse(u).hostname or ""
    return any(a in host for a in ATS_HOSTS)


async def careers_site(c: httpx.AsyncClient, job_page: dict) -> Optional[dict]:
    """Company website (from LinkedIn's public company page) -> its careers page -> the ATS it links
    to: {careers_url, workday?} or None. No search engine, no login."""
    mo = re.search(r'href="(https://[a-z]+\.linkedin\.com/company/[^"?]+)', job_page["html"])
    if not mo:
        return None
    about = (await get_page(c, mo.group(1)))["html"]
    rd = re.search(r'href="([^"]+)"[^>]*data-tracking-control-name="about_website"', about) or \
        re.search(r'data-tracking-control-name="about_website"[^>]*href="([^"]+)"', about)
    if not rd:
        return None
    redir = rd.group(1).replace("&amp;", "&")
    website = (parse_qs(urlparse(redir).query).get("url") or [redir])[0]
    try:
        home = await get_page(c, website)
    except httpx.HTTPError:
        home = None
    if not home or home["status"] >= 400 or len(home["html"]) < 2000:
        return {"careers_url": website}                                  # bot-blocked: browse it
    domain = ".".join((urlparse(home["url"]).hostname or "").split(".")[-2:])
    links = hrefs(home["url"], home["html"])
    ats = [u for u in links if is_ats(u)]
    careers = [u for u in links if re.search(r"career|carri[eè]re|/jobs|emploi|join-us|work-with-us", u, re.I)
               and (urlparse(u).hostname or "").endswith(domain)][:3]
    for cu in careers:
        if ats:
            break
        try:
            ats = [u for u in hrefs(cu, (await get_page(c, cu))["html"]) if is_ats(u)]
        except httpx.HTTPError:
            pass
    wd = next((u for u in ats if "myworkdayjobs.com" in u), None)
    if wd:   # board = origin + site (first path segment that isn't a locale)
        u = urlparse(wd)
        site = next((p for p in u.path.split("/") if p and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)), None)
        if site:
            return {"careers_url": f"{u.scheme}://{u.netloc}/{site}", "workday": f"{u.scheme}://{u.netloc}/{site}"}
    return {"careers_url": (ats or careers or [website])[0]}


def same_job(posts: List[dict], title: str, location: str) -> Optional[dict]:
    """Exact title in the same city (unless remote); a looser title match only when it is the single
    candidate there -- big employers post one title in many countries."""
    want, city = norm(title), norm((location or "").split(",")[0])
    remote = "remote" in norm(location)
    here = lambda p: not city or remote or city in norm(f"{p['location']} {p['url']}")
    exact = [p for p in posts if norm(p["title"]) == want and here(p)]
    if exact:
        return exact[0]
    loose = [p for p in posts if (norm(p["title"]) in want or want in norm(p["title"])) and here(p)]
    return loose[0] if len(loose) == 1 else None


async def find_ats(c: httpx.AsyncClient, job, t: dict) -> Dict[str, Any]:
    """Where to apply for a LinkedIn job. Returns {route: closed|easy_apply|found|company_site,
    platform?, url?, careers_url?}; the caller updates the job."""
    li = None
    try:
        li = await linkedin_page(c, job.url)
    except httpx.HTTPError:
        pass
    if li and li["route"] in ("closed", "easy_apply"):
        return {"route": li["route"]}
    words = [w for w in COMPANY_NOISE.sub(" ", norm(job.company)).split() if w]
    slugs = list(dict.fromkeys(s for s in ("".join(words), "-".join(words), words[0] if words else "") if s))
    for slug in slugs:
        for platform in ("greenhouse", "lever", "ashby"):
            try:
                posts = await FEEDS[platform](c, slug)
            except (httpx.HTTPError, ValueError, KeyError):
                continue
            hit = same_job(posts, job.title, job.location)
            if hit:
                return {"route": "found", "platform": platform, "url": hit["url"]}
    # Workday tenants can't be guessed from the name: they come from the targets' `workday` list,
    # then from the company's own website.
    boards = [b for name, b in (t.get("workday") or {}).items()
              if norm(name) and (norm(name) in norm(job.company) or norm(job.company) in norm(name))][:1]
    site = None
    if li:
        try:
            site = await careers_site(c, li)
        except httpx.HTTPError:
            site = None
    if site and site.get("workday"):
        boards.append(site["workday"])
    for board in boards:
        try:
            hit = same_job(await workday(c, board, job.title), job.title, job.location)
        except (httpx.HTTPError, ValueError, KeyError):
            hit = None
        if hit:
            return {"route": "found", "platform": "workday", "url": hit["url"]}
    return {"route": "company_site", "careers_url": (site or {}).get("careers_url")}


# ── prepare: route, score, resume ──

def _note(job, text: str):
    job.notes = ((job.notes or "") + f"\n{text}").strip()


async def _run_endpoint(endpoint, job_id: int, payload: dict, db, user) -> None:
    """Call a HireOS endpoint that schedules a background task, then run that task to the end."""
    bt = BackgroundTasks()
    await endpoint(job_id, bt, payload, db, user)
    await bt()


async def prepare(m, c: httpx.AsyncClient, db, user, job_id: int, t: dict) -> str:
    """One job: find its own posting (LinkedIn), score it, tailor a resume when it clears min_score.
    Returns a short outcome for the run summary."""
    job = m.get_owned_job(db, job_id, user)
    payload = {"llm": t["llm"]} if t.get("llm") else {}
    route = None
    if re.search(r"linkedin\.com/jobs/view|glassdoor\.", job.url or ""):
        route = await find_ats(c, job, t)
        if route["route"] == "closed":
            _note(job, "[find-ats: LinkedIn says no longer accepting applications]")
            old, job.status = job.status, "withdrawn"
            db.commit()
            m._log_event(db, job.id, "status_change", "Status → withdrawn", f"Moved from {old} to withdrawn (posting closed)",
                         old_value=old, new_value="withdrawn", source="agent", user_id=user.id)
            return "closed"
        if route["route"] == "easy_apply":
            _note(job, "[find-ats: LinkedIn Easy Apply only — needs a LinkedIn login]")
        elif route["route"] == "found":
            _note(job, f"[find-ats: was {job.url}]")
            job.url, job.platform = route["url"], route["platform"]
        elif route.get("careers_url") and route["careers_url"] not in (job.notes or ""):
            _note(job, f"[careers: {route['careers_url']}]")
        db.commit()
    if job.match_score is None:
        await _run_endpoint(m.analyze_job, job_id, payload, db, user)
        db.expire_all()
        task = db.query(m.LLMTaskStatus).filter_by(job_id=job_id, task_type="analyze").first()
        if not task or task.status != "completed":
            raise RuntimeError(f"fit analysis failed: {task.error_message if task else 'no task'}")
        job = m.get_owned_job(db, job_id, user)
    if job.match_score < m._apply_config(db, user.id)["min_score"]:
        return "below min score"
    if route and route["route"] == "easy_apply":
        return "Easy Apply only"
    if not db.query(m.ResumeVersion).filter_by(job_id=job_id).first():
        await _run_endpoint(m.start_generate_resume, job_id, payload, db, user)
        db.expire_all()
        task = db.query(m.LLMTaskStatus).filter_by(job_id=job_id, task_type="resume").first()
        if not task or task.status != "completed":
            raise RuntimeError(f"resume generation failed: {task.error_message if task else 'no task'}")
    return "resume ready"


async def score(m, user_id: int, job_ids: List[int]) -> Dict[str, Any]:
    """prepare() the named jobs now, outside a run (the chat's "score my jobs")."""
    db = m.SessionLocal()
    out: Dict[str, Any] = {"prepared": [], "errors": []}
    try:
        user = db.get(m.User, user_id)
        t = targets(m, db, user_id) or {}
        async with httpx.AsyncClient(headers=UA, follow_redirects=True, timeout=12) as c:
            for jid in job_ids:
                try:
                    out["prepared"].append((jid, await prepare(m, c, db, user, jid, t)))
                except Exception as e:                       # one job must not stop the rest
                    db.rollback()
                    out["errors"].append(f"#{jid}: {str(e)[:200]}")
        return out
    finally:
        db.close()


async def run(m, user_id: int, transport: httpx.AsyncBaseTransport = None) -> Dict[str, Any]:
    """A discovery run for one user. Never raises for one source failing: errors are collected."""
    db = m.SessionLocal()
    summary: Dict[str, Any] = {"added": 0, "prepared": [], "waiting": 0, "errors": []}
    try:
        user = db.get(m.User, user_id)
        t = targets(m, db, user_id)
        if not user or t is None:
            summary["errors"].append("no discovery targets")
            return summary
        cfg = m._apply_config(db, user_id)
        max_age = cfg["max_posting_age_hours"]
        llm = {"llm": t["llm"]} if t.get("llm") else {}
        async with httpx.AsyncClient(headers=UA, follow_redirects=True, timeout=12, transport=transport) as c:
            # 1. job-alert emails (fresh as the posting-age window)
            try:
                r = await m.scan_mail({"days": math.ceil(max_age / 24), "limit": 10, **llm}, db=db, current_user=user)
                summary["added"] += len(r.get("added") or [])
            except HTTPException as e:
                summary["errors"].append(f"alert emails: {e.detail}")
            # 2. ATS feeds + LinkedIn searches
            feeds = await scan_feeds(c, t, max_age, datetime.now(timezone.utc))
            summary["errors"] += feeds["errors"]
            for j in feeds["found"]:
                try:
                    r = await m.track_jd_text({"url": j["url"], **llm,
                                               "text": f"{j['title']}\n{j['company']}\n{j['location']}\n\n{j['text']}"[:20000]},
                                              db=db, current_user=user)
                    summary["added"] += 0 if r.get("duplicate") else 1
                except HTTPException as e:
                    summary["errors"].append(f"{j['url']}: {e.detail}")
            for s in t.get("linkedin_searches") or []:
                try:
                    r = await m.linkedin_search({"limit": 5, **s, "posted_within": f"{int(max_age)}h", **llm}, db=db, current_user=user)
                    summary["added"] += len(r["added"])
                    summary["errors"] += [f"linkedin {f['url']}: {f['error']}" for f in r["failed"]]
                except HTTPException as e:
                    if e.status_code == 429:
                        summary["errors"].append("linkedin rate-limited — skipped the rest this run")
                        break
                    summary["errors"].append(f"linkedin {s.get('keywords')!r}: {e.detail}")
            # 3. prepare unscored jobs, newest first: oldest first starved new postings behind a
            # backlog that grew faster than PREPARE_MAX a run (the chat's score_jobs reaches older ones)
            todo = [j.id for j in db.query(m.Job).filter(m.Job.user_id == user_id, m.Job.status == "found",
                                                         m.Job.match_score.is_(None)).order_by(m.Job.id.desc())]
            summary["waiting"] = max(0, len(todo) - PREPARE_MAX)
            for jid in todo[:PREPARE_MAX]:
                try:
                    summary["prepared"].append((jid, await prepare(m, c, db, user, jid, t)))
                except Exception as e:                       # one job must not stop the rest
                    db.rollback()
                    summary["errors"].append(f"prepare #{jid}: {str(e)[:200]}")
        return summary
    finally:
        db.close()
