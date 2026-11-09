"""Job discovery inside HireOS (it used to be OpenClaw's job-discovery skill + the `hireos` CLI).

One run for a user: job-alert emails -> ATS feeds and LinkedIn searches from their targets ->
prepare (fit analysis, then a tailored resume) for unscored jobs, at most PREPARE_MAX per run.
Resume cards then reach the user through the Telegram bot by themselves.

Targets are the user's `discovery_targets` setting (JSON):
  title_keywords, exclude_keywords, locations: lowercase substrings matched on title / location
  companies: {"greenhouse"|"lever"|"ashby": [board slug, ...]}
  workday: {company name: "https://<tenant>.wdN.myworkdayjobs.com/<site>"}, workday_searches: [query]
  linkedin_searches: [{keywords, location?, limit?}]   (public guest API, never a LinkedIn login)
"""
import asyncio
import json
import math
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from fastapi import BackgroundTasks, HTTPException
from loguru import logger
from pydantic import BaseModel

TARGETS_KEY = "discovery_targets"
PREPARE_MAX = 30      # a run adds ~20; 12 let the backlog grow
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
ATS_HOSTS = ["myworkdayjobs.com", "avature.net", "successfactors", "icims.com", "taleo.net", "greenhouse.io",
             "lever.co", "ashbyhq.com", "smartrecruiters.com", "oraclecloud.com", "jobvite.com", "bamboohr.com",
             "paylocity.com", "workable.com", "recruitee.com", "dayforcehcm.com", "ultipro.com", "adp.com",
             "breezy.hr", "rippling.com"]
# Where big employers' Workday jobs live, checked against the live boards 2026-11-09. LinkedIn hides
# the apply link from signed-out readers and tenants can't be guessed, so these are matched by name
# (whole words) after the user's own `workday` targets and the boards learned from pasted links.
# ponytail: hand-kept list; a board that moves just stops matching (the website walk still runs).
KNOWN_WORKDAY = {
    "TD": "https://td.wd3.myworkdayjobs.com/TD_Bank_Careers",
    "Toronto Dominion": "https://td.wd3.myworkdayjobs.com/TD_Bank_Careers",
    "RBC": "https://rbc.wd3.myworkdayjobs.com/RBCGLOBAL1",
    "Royal Bank of Canada": "https://rbc.wd3.myworkdayjobs.com/RBCGLOBAL1",
    "BMO": "https://bmo.wd3.myworkdayjobs.com/External",
    "Bank of Montreal": "https://bmo.wd3.myworkdayjobs.com/External",
    "CIBC": "https://cibc.wd3.myworkdayjobs.com/search",
    "Manulife": "https://manulife.wd3.myworkdayjobs.com/MFCJH_Jobs",
    "Sun Life": "https://sunlife.wd3.myworkdayjobs.com/Experienced-Jobs",
    "Intact": "https://intactfc.wd3.myworkdayjobs.com/intactfc",
    "Allstate": "https://allstate.wd5.myworkdayjobs.com/allstate_careers",
    "Aviva": "https://aviva.wd1.myworkdayjobs.com/External",
    "Desjardins": "https://desjardins.wd10.myworkdayjobs.com/Desjardins",
    "Interac": "https://interac.wd3.myworkdayjobs.com/Interac",
    "Fidelity Canada": "https://fil.wd3.myworkdayjobs.com/fidelitycanada",
    "AXIS Capital": "https://axiscapital.wd1.myworkdayjobs.com/axiscareers",
    "Capital One": "https://capitalone.wd12.myworkdayjobs.com/Capital_One",
    "Mastercard": "https://mastercard.wd1.myworkdayjobs.com/CorporateCareers",
    "Thomson Reuters": "https://thomsonreuters.wd5.myworkdayjobs.com/External_Career_Site",
    "Canadian Tire": "https://canadiantirecorporation.wd3.myworkdayjobs.com/Enterprise_External_Careers_Site",
    "Home Depot": "https://homedepot.wd5.myworkdayjobs.com/CareerDepotCanada",
    "BDO": "https://bdo.wd3.myworkdayjobs.com/BDO",
    "PwC": "https://pwc.wd3.myworkdayjobs.com/Global_Experienced_Careers",
    "Accenture": "https://accenture.wd103.myworkdayjobs.com/AccentureCareers",
    "Autodesk": "https://autodesk.wd1.myworkdayjobs.com/Ext",
    "NVIDIA": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
    "Cisco": "https://cisco.wd5.myworkdayjobs.com/Cisco_Careers",
    "Salesforce": "https://salesforce.wd12.myworkdayjobs.com/External_Career_Site",
    "Ciena": "https://ciena.wd5.myworkdayjobs.com/Careers",
    "Magna": "https://magna.wd3.myworkdayjobs.com/Magna",
}
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
    return out      # no model of its own: discovery uses the user's model (Settings › AI Models)


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


LIGHTPANDA = os.getenv("LIGHTPANDA_BIN") or shutil.which("lightpanda")


async def render(url: str, timeout: float = 30) -> str:
    """The page's HTML after its JavaScript ran (lightpanda, a light headless browser), iframes
    included; "" when lightpanda is missing, slow or fails. Only for careers pages whose ATS link
    a script draws: plain fetches stay the default (faster, and most pages need nothing more)."""
    if not LIGHTPANDA:
        return ""
    proc = await asyncio.create_subprocess_exec(
        LIGHTPANDA, "fetch", "--dump", "html", "--with-frames", "--wait-until", "networkalmostidle",
        "--wait-ms", "10000", url, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ""
    return out.decode("utf-8", "replace")


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
    p = urlparse(u)
    return any(a in (p.hostname or "") for a in ATS_HOSTS) and \
        not re.search(r"\.(css|js|png|jpe?g|svg|gif|woff2?|ico)$", p.path, re.I)


LEGAL = re.compile(r"\b(inc|ltd|llc|corp|corporation|co|the|limited|plc)\b")


def website_guesses(company: str) -> List[str]:
    """allstate.ca, allstate.com... for "Allstate Canada": tried when LinkedIn hides the website."""
    full = LEGAL.sub(" ", norm(company)).split()
    core = [w for w in COMPANY_NOISE.sub(" ", norm(company)).split() if w]
    bases = list(dict.fromkeys(b for b in ("".join(core), "".join(full), core[0] if core else "") if len(b) > 2))
    return [f"https://www.{b}.{tld}" for b in bases for tld in ("ca", "com")]


async def company_website(c: httpx.AsyncClient, job_page: dict, company: str, ask=None) -> Optional[str]:
    """The company's own website: LinkedIn's company page, else a domain guessed from the name
    (kept only when its page names the company), else ask(company) (the user's model)."""
    mo = re.search(r'href="(https://[a-z]+\.linkedin\.com/company/[^"?]+)', (job_page or {}).get("html") or "")
    if mo:
        about = (await get_page(c, mo.group(1)))["html"]       # 403 for bots, often
        rd = re.search(r'href="([^"]+)"[^>]*data-tracking-control-name="about_website"', about) or \
            re.search(r'data-tracking-control-name="about_website"[^>]*href="([^"]+)"', about)
        if rd:
            redir = rd.group(1).replace("&amp;", "&")
            return (parse_qs(urlparse(redir).query).get("url") or [redir])[0]
    name = " ".join(LEGAL.sub(" ", norm(company)).split())     # "home depot", not just "home"
    for url in website_guesses(company):
        try:
            page = await get_page(c, url)
        except httpx.HTTPError:
            continue
        if page["status"] < 400 and name and name in norm(page["html"]):
            return page["url"]
    if ask:
        try:
            url = await ask(company)
        except Exception as e:      # the model is a last resort; never fail the lookup over it
            logger.debug(f"[discovery] website of {company!r}: {e}")
            return None
        if url and re.match(r"https?://[^/\s]+\.[a-z]{2,}", url):
            return url
    return None


async def careers_site(c: httpx.AsyncClient, job_page: dict, location: str = "", company: str = "",
                       ask=None, render=None) -> Optional[dict]:
    """Company website -> its careers page (and the one for the job's country or city, e.g.
    /careers/canada/) -> the ATS it links to: {careers_url} or None. No search engine,
    no login. render(url) -> html runs the page's JavaScript (lightpanda), for pages whose ATS link
    is drawn by a script."""
    website = await company_website(c, job_page, company, ask)
    if not website:
        return None
    try:
        home = await get_page(c, website)
    except httpx.HTTPError:
        home = None
    if not home or home["status"] >= 400:
        return {"careers_url": website}                                  # bot-blocked: browse it

    renders = [2]       # lightpanda takes seconds a page: the home and the first careers page only

    async def links_of(url: str, html: str) -> List[str]:
        links = hrefs(url, html)
        if render and renders[0] and not any(is_ats(u) for u in links):
            renders[0] -= 1
            links += [u for u in hrefs(url, await render(url) or "") if u not in links]
        return links

    domain = ".".join((urlparse(home["url"]).hostname or "").split(".")[-2:])
    links = await links_of(home["url"], home["html"])
    ats = [u for u in links if is_ats(u)]
    careers = [u for u in links if re.search(r"career|carri[eè]re|/jobs|emploi|join-us|work-with-us", u, re.I)
               and (urlparse(u).hostname or "").endswith(domain)][:3]
    places = [w for w in re.split(r"[^a-z]+", (location or "").lower()) if len(w) > 3]
    seen = set(careers)
    for cu in careers:
        if ats:
            break
        try:
            links = await links_of(cu, (await get_page(c, cu))["html"])
        except httpx.HTTPError:
            continue
        ats = [u for u in links if is_ats(u)]
        # one level down: the careers page for the job's place, on the same site
        if len(careers) < 6:      # at most a few extra pages
            careers += [u for u in links if u not in seen and any(p in u.lower() for p in places)
                        and (urlparse(u).hostname or "").endswith(domain)][:2]
        seen.update(careers)
    ats.sort(key=lambda u: board_of(u) is None)       # a board we can search first
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
    if len(loose) == 1:
        return loose[0]
    # an office name instead of the city ("Canadian Head Office"): the one posting with this exact
    # title, in the same country
    country = norm((location or "").split(",")[-1])[:5]
    only = [p for p in posts if norm(p["title"]) == want]
    if len(only) == 1 and len(country) == 5 and country in norm(f"{only[0]['location']} {only[0]['url']}"):
        return only[0]
    return None


def board_of(url: str):
    """(platform, board) of an ATS posting URL: the Greenhouse/Lever/Ashby board slug, or the Workday
    career site (origin + site). None for anything else."""
    u = urlparse(url or "")
    host, parts = u.hostname or "", [p for p in u.path.split("/") if p]
    if "myworkdayjobs.com" in host:
        site = next((p for p in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)), None)
        return ("workday", f"{u.scheme}://{u.netloc}/{site}") if site else None
    for platform, h in (("greenhouse", "greenhouse.io"), ("lever", "lever.co"), ("ashby", "ashbyhq.com")):
        if host.endswith(h):
            q = parse_qs(u.query).get("for")      # boards.greenhouse.io/embed/job_board?for=<slug>
            slug = q[0] if q else next((p for p in parts if p not in ("embed", "job_app", "v1", "boards")), None)
            return (platform, slug) if slug else None
    return None


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
    li_slug = re.search(r'linkedin\.com/company/([a-z0-9-]+)', (li or {}).get("html") or "")
    slugs = list(dict.fromkeys(s for s in ("".join(words), "-".join(words), li_slug and li_slug.group(1).replace("-", ""),
                                            words[0] if words else "") if s))
    learned = board_of((t.get("learned") or {}).get(norm(job.company)) or "")   # from an apply link the user pasted
    if learned and learned[0] in FEEDS:
        try:
            hit = same_job(await FEEDS[learned[0]](c, learned[1]), job.title, job.location)
        except (httpx.HTTPError, ValueError, KeyError):
            hit = None
        if hit:
            return {"route": "found", "platform": learned[0], "url": hit["url"], "text": hit.get("text") or ""}
    for slug in slugs:
        for platform in ("greenhouse", "lever", "ashby"):
            try:
                posts = await FEEDS[platform](c, slug)
            except (httpx.HTTPError, ValueError, KeyError):
                continue
            hit = same_job(posts, job.title, job.location)
            if hit:
                return {"route": "found", "platform": platform, "url": hit["url"], "text": hit.get("text") or ""}
    # Workday tenants can't be guessed from the name: they come from the targets' `workday` list and
    # KNOWN_WORKDAY, then from the company's own website.
    co = set(norm(job.company).split())
    boards = list(dict.fromkeys(b for name, b in [*(t.get("workday") or {}).items(), *KNOWN_WORKDAY.items()]
                                if norm(name) and set(norm(name).split()) <= co))[:2]
    if learned and learned[0] == "workday":
        boards.insert(0, learned[1])
    async def on_board(board):
        try:
            hit = same_job(await workday(c, board, job.title), job.title, job.location)
        except (httpx.HTTPError, ValueError, KeyError):
            return None
        return hit and {"route": "found", "platform": "workday", "url": hit["url"]}
    for board in boards:
        if found := await on_board(board):
            return found
    # not on a known board: walk the company's own website to its careers page and ATS
    try:
        site = await careers_site(c, li, job.location or "", job.company or "", t.get("ask_website"), render)
    except httpx.HTTPError:
        site = None
    board = board_of((site or {}).get("careers_url") or "")
    if board and board[0] == "workday" and board[1] not in boards:
        if found := await on_board(board[1]):
            return found
    elif board and board[0] in FEEDS and board[1] not in slugs:
        try:
            hit = same_job(await FEEDS[board[0]](c, board[1]), job.title, job.location)
        except (httpx.HTTPError, ValueError, KeyError):
            hit = None
        if hit:
            return {"route": "found", "platform": board[0], "url": hit["url"], "text": hit.get("text") or ""}
    return {"route": "company_site", "careers_url": (site or {}).get("careers_url")}


# ── prepare: route, score, resume ──

def _note(job, text: str):
    job.notes = ((job.notes or "") + f"\n{text}").strip()


async def _run_endpoint(endpoint, job_id: int, payload: dict, db, user) -> None:
    """Call a HireOS endpoint that schedules a background task, then run that task to the end."""
    bt = BackgroundTasks()
    await endpoint(job_id, bt, payload, db, user)
    await bt()


class _Website(BaseModel):
    website: str = ""


async def ask_website(m, db, user_id: int, company: str) -> str:
    """The employer's homepage from the user's model: a last resort when LinkedIn hides it and no
    domain guess names the company. "" when the model isn't sure."""
    out = await asyncio.wait_for(m.get_llm_router(db, user_id).structured_complete(
        f'What is the official website (homepage URL) of the employer "{company}"? Reply as JSON '
        '{"website": "https://..."}, with "" if you are not sure which company this is.',
        _Website, llm=m.resolve_llm(db, user_id)), 60)
    return out.website.strip()


LINK_HIDDEN = re.compile(r"linkedin\.com/jobs/view|glassdoor\.|indeed\.")   # a board, not the employer's own posting


async def route_job(m, c: httpx.AsyncClient, db, user, job, t: dict) -> Dict[str, Any]:
    """Find where a LinkedIn/Glassdoor/Indeed job is really applied to and update the job: moved to
    the found posting, withdrawn when LinkedIn says closed, or a note (Easy Apply, careers page)."""
    route = await find_ats(c, job, {**t, "learned": json.loads(m._setting(db, user.id, "ats_boards") or "{}"),
                                    "ask_website": lambda company: ask_website(m, db, user.id, company)})
    if route["route"] == "closed":
        _note(job, "[find-ats: LinkedIn says no longer accepting applications]")
        old, job.status = job.status, "withdrawn"
        db.commit()
        m._log_event(db, job.id, "status_change", "Status → withdrawn", f"Moved from {old} to withdrawn (posting closed)",
                     old_value=old, new_value="withdrawn", source="agent", user_id=user.id)
        return route
    if route["route"] == "easy_apply":
        if "Easy Apply only" not in (job.notes or ""):
            _note(job, "[find-ats: LinkedIn Easy Apply only — needs a LinkedIn login]")
    elif route["route"] == "found":
        _note(job, f"[find-ats: was {job.url}]")
        job.url, job.platform = route["url"], route["platform"]
        if (job.meta or {}).get("partial_jd") and len(route.get("text") or "") > len(job.job_description or ""):
            # an alert email's snippet: score the real posting instead
            job.job_description = route["text"]
            job.meta = {k: v for k, v in job.meta.items() if k != "partial_jd"}
    elif route.get("careers_url") and route["careers_url"] not in (job.notes or ""):
        _note(job, f"[careers: {route['careers_url']}]")
    db.commit()
    return route


async def prepare(m, c: httpx.AsyncClient, db, user, job_id: int, t: dict, step=None) -> str:
    """One job: find its own posting (LinkedIn), score it, tailor a resume when it clears min_score.
    Returns a short outcome for the run summary."""
    job = m.get_owned_job(db, job_id, user)
    step = step or (lambda text: None)          # what it's doing now, for the progress message
    payload = {}                     # the user's model
    route = None
    if LINK_HIDDEN.search(job.url or ""):
        step(f"#{job.id} {job.company}: finding where to apply")
        route = await route_job(m, c, db, user, job, t)
        if route["route"] == "closed":
            return "closed"
    if job.match_score is None:
        step(f"#{job.id} {job.company}: scoring the fit")
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
        step(f"#{job.id} {job.company}: scored {job.match_score:g}, writing the resume (then review + ATS score)")
        await _run_endpoint(m.start_generate_resume, job_id, payload, db, user)
        db.expire_all()
        task = db.query(m.LLMTaskStatus).filter_by(job_id=job_id, task_type="resume").first()
        if not task or task.status != "completed":
            raise RuntimeError(f"resume generation failed: {task.error_message if task else 'no task'}")
    return "resume ready"


async def _prepare_all(m, c, db, user, job_ids: List[int], t: dict, out: dict, progress=None, label=""):
    """prepare() each job; one failing never stops the rest. progress(done, total, outcome, now) as it
    goes: now = the step under way, outcome = how the job ended (after each job)."""
    for i, jid in enumerate(job_ids, 1):
        step = (lambda text, i=i: progress(i - 1, len(job_ids), None, text)) if progress else None
        try:
            outcome = await prepare(m, c, db, user, jid, t, step)
            out["prepared"].append((jid, outcome))
        except Exception as e:
            db.rollback()
            outcome = "error"
            out["errors"].append(f"{label}#{jid}: {str(e)[:200]}")
        if progress:
            job = db.get(m.Job, jid)
            detail = f"#{jid} {job.company if job else ''}" + (
                f" — {job.match_score:g}" if job is not None and job.match_score is not None else "")
            progress(i, len(job_ids), outcome, None, detail)


async def score(m, user_id: int, job_ids: List[int], progress=None) -> Dict[str, Any]:
    """prepare() the named jobs now, outside a run (the chat's "score my jobs")."""
    db = m.SessionLocal()
    out: Dict[str, Any] = {"prepared": [], "errors": []}
    try:
        user = db.get(m.User, user_id)
        t = targets(m, db, user_id) or {}
        async with httpx.AsyncClient(headers=UA, follow_redirects=True, timeout=12) as c:
            await _prepare_all(m, c, db, user, job_ids, t, out, progress)
        return out
    finally:
        db.close()


RELINK_GAP = 3       # seconds between jobs: LinkedIn rate-limits a burst of guest reads


async def relink(m, user_id: int, job_ids: List[int], progress=None, gap: float = RELINK_GAP,
                 transport: httpx.AsyncBaseTransport = None) -> Dict[str, Any]:
    """route_job() for jobs already in HireOS (the chat's "find apply links"): only the link lookup,
    no scoring or resumes. A job waiting on the user that turns out to be on a form the agent fills
    goes back to it, as when the user replies with the link."""
    import apply_queue
    db = m.SessionLocal()
    out: Dict[str, Any] = {"prepared": [], "errors": []}
    try:
        user = db.get(m.User, user_id)
        t = targets(m, db, user_id) or {}
        async with httpx.AsyncClient(headers=UA, follow_redirects=True, timeout=12, transport=transport) as c:
            for i, jid in enumerate(job_ids, 1):
                if progress:
                    progress(i - 1, len(job_ids), None, f"#{jid}: finding where to apply")
                try:
                    job = m.get_owned_job(db, jid, user)
                    route = await route_job(m, c, db, user, job, t)
                    if route["route"] == "found":
                        outcome = f"found on {route['platform'].title()}"
                        if job.status == "needs_human" and route["platform"] in apply_queue.WORKER_FILLS:
                            m.approve_job(job.id, db, user)
                            outcome += ", back to the agent"
                    else:
                        outcome = {"closed": "closed", "easy_apply": "Easy Apply only"}.get(route["route"], "no link found")
                    out["prepared"].append((jid, outcome))
                except Exception as e:
                    db.rollback()
                    outcome = "error"
                    out["errors"].append(f"#{jid}: {str(e)[:200]}")
                if progress:
                    job = db.get(m.Job, jid)
                    progress(i, len(job_ids), outcome, None, f"#{jid} {job.company if job else ''}")
                if i < len(job_ids):
                    await asyncio.sleep(gap)
        return out
    finally:
        db.close()


async def run(m, user_id: int, transport: httpx.AsyncBaseTransport = None, progress=None) -> Dict[str, Any]:
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
        async with httpx.AsyncClient(headers=UA, follow_redirects=True, timeout=12, transport=transport) as c:
            say = (lambda text: progress(0, 0, None, text)) if progress else (lambda text: None)
            # 1. job-alert emails (fresh as the posting-age window)
            say("reading job-alert emails")
            try:
                r = await m.scan_mail({"days": math.ceil(max_age / 24), "limit": 10}, db=db, current_user=user)
                summary["added"] += len(r.get("added") or [])
            except HTTPException as e:
                summary["errors"].append(f"alert emails: {e.detail}")
            # 2. ATS feeds + LinkedIn searches
            say(f"checking company job boards ({summary['added']} new so far)")
            feeds = await scan_feeds(c, t, max_age, datetime.now(timezone.utc))
            summary["errors"] += feeds["errors"]
            for j in feeds["found"]:
                try:
                    r = await m.track_jd_text({"url": j["url"],
                                               "text": f"{j['title']}\n{j['company']}\n{j['location']}\n\n{j['text']}"[:20000]},
                                              db=db, current_user=user)
                    summary["added"] += 0 if r.get("duplicate") else 1
                except HTTPException as e:
                    summary["errors"].append(f"{j['url']}: {e.detail}")
            for s in t.get("linkedin_searches") or []:
                say(f"LinkedIn search: {s.get('keywords')} ({summary['added']} new so far)")
                try:
                    r = await m.linkedin_search({"limit": 5, **s, "posted_within": f"{int(max_age)}h"}, db=db, current_user=user)
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
            await _prepare_all(m, c, db, user, todo[:PREPARE_MAX], t, summary, progress, "prepare ")
        return summary
    finally:
        db.close()
