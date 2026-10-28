"""
LinkedIn guest API — job search and job details without logging in.

The public job pages are backed by two unauthenticated endpoints:
  search: /jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=..&location=..&start=N  (10 cards/page)
  detail: /jobs-guest/jobs/api/jobPosting/{job_id}
No account is involved, so nothing here can get the user's account restricted.
LinkedIn rate-limits bursts (HTTP 429), so callers should keep volumes small.
"""
import asyncio
import html
import re
from typing import Dict, List, Optional
from urllib.parse import parse_qsl, urlsplit

import httpx

BASE = "https://www.linkedin.com/jobs-guest/jobs/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
# LinkedIn's f_TPR filter: seconds since posting
POSTED_WITHIN = {"day": "r86400", "week": "r604800", "month": "r2592000"}


def job_id_from_url(url: Optional[str]) -> Optional[str]:
    """Numeric LinkedIn job id from any job URL shape (/jobs/view/<slug>-<id>, ?currentJobId=<id>)."""
    parts = urlsplit((url or "").strip())
    if not (parts.hostname or "").endswith("linkedin.com"):
        return None
    m = re.search(r"/jobs/view/(?:[^/]*?-)?(\d+)", parts.path)
    return m.group(1) if m else dict(parse_qsl(parts.query)).get("currentJobId")


def canonical_url(job_id: str) -> str:
    # Drops tracking params and email login tokens (otpToken) — never store or forward those.
    return f"https://www.linkedin.com/jobs/view/{job_id}/"


def _text(fragment: str) -> str:
    t = re.sub(r"<(script|style|code)[^>]*>.*?</\1>", " ", fragment, flags=re.S)
    t = re.sub(r"<br\s*/?>|</(p|li|div|h\d|ul)>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def _field(page: str, cls: str) -> str:
    m = re.search(rf'class="[^"]*\b{re.escape(cls)}\b[^"]*"[^>]*>(.*?)</', page, flags=re.S)
    return _text(m.group(1)) if m else ""


def parse_job(page: str) -> Dict[str, str]:
    """Header fields plus clean description text (sign-in prompts dropped)."""
    start = page.rfind("<", 0, page.find('class="description__text'))
    return {
        "title": _field(page, "topcard__title"),
        "company": _field(page, "topcard__org-name-link"),
        "location": _field(page, "topcard__flavor--bullet"),
        "posted": _field(page, "posted-time-ago__text"),
        "applicants": _field(page, "num-applicants__caption"),
        "description": _text(page[start:]) if start >= 0 else _text(page),
    }


def as_jd_text(job: Dict[str, str]) -> str:
    head = [f"{k.title()}: {job[k]}" for k in ("title", "company", "location", "posted", "applicants") if job.get(k)]
    return "\n".join(head) + "\n\n" + job["description"]


async def fetch_job(job_id: str) -> Dict[str, str]:
    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0) as c:
        resp = await c.get(f"{BASE}/jobPosting/{job_id}")
    if resp.status_code == 429:
        raise ValueError("LinkedIn is rate-limiting requests — try again in a few minutes.")
    if resp.status_code != 200:
        raise ValueError(f"LinkedIn job {job_id} not available (HTTP {resp.status_code}) — it may be closed.")
    job = parse_job(resp.text)
    if not job["title"] or len(job["description"]) < 200:
        raise ValueError(f"LinkedIn job {job_id} returned no description — it may be closed.")
    return job


async def search_ids(keywords: str, location: str = "", posted_within: str = "week",
                     limit: int = 25, delay: float = 1.5) -> List[str]:
    """Job ids for a search, newest pages first, de-duplicated. Stops at the first empty page."""
    ids: List[str] = []
    params = {"keywords": keywords, "location": location}
    if posted_within in POSTED_WITHIN:
        params["f_TPR"] = POSTED_WITHIN[posted_within]
    elif re.fullmatch(r"\d+h", posted_within or ""):   # "72h" -> posted in the last 72 hours
        params["f_TPR"] = f"r{int(posted_within[:-1]) * 3600}"
    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0) as c:
        start = 0
        while len(ids) < limit:
            resp = await c.get(f"{BASE}/seeMoreJobPostings/search", params={**params, "start": start})
            if resp.status_code == 429:
                if ids:
                    break  # keep what we have
                raise ValueError("LinkedIn is rate-limiting requests — try again in a few minutes.")
            page_ids = re.findall(r'urn:li:jobPosting:(\d+)', resp.text) if resp.status_code == 200 else []
            if not page_ids:
                break
            ids += [i for i in dict.fromkeys(page_ids) if i not in ids]
            start += len(page_ids)
            await asyncio.sleep(delay)
    return ids[:limit]


if __name__ == "__main__":
    assert job_id_from_url("https://www.linkedin.com/comm/jobs/view/4476106173/?trackingId=x&otpToken=y") == "4476106173"
    assert job_id_from_url("https://ca.linkedin.com/jobs/view/ai-engineer-at-bell-4476106173") == "4476106173"
    assert job_id_from_url("https://www.linkedin.com/jobs/search/?currentJobId=123&keywords=ml") == "123"
    assert job_id_from_url("https://example.com/jobs/view/123") is None
    sample = ('<h2 class="top-card-layout__title topcard__title">AI Engineer</h2>'
              '<a class="topcard__org-name-link topcard__flavor--black-link"> Bell </a>'
              '<span class="topcard__flavor topcard__flavor--bullet">\n Toronto, ON\n</span>'
              '<p>Sign in</p><div class="description__text description__text--rich"><p>Build agents &amp; RAG</p></div>')
    j = parse_job(sample)
    assert (j["title"], j["company"], j["location"]) == ("AI Engineer", "Bell", "Toronto, ON"), j
    assert j["description"] == "Build agents & RAG" and "Sign in" not in j["description"], j
    print("ok")
