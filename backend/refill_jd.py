"""One-off: re-fetch job descriptions for jobs restored after the 2026-10-24 DB wipe.
Only fills rows whose job_description is empty. Run inside the hireos container:
    python refill_jd.py
"""
import asyncio, re, sqlite3
from urllib.parse import urlparse

import httpx
import linkedin_guest

DB = "/data/swarm.db"


def strip_html(s: str) -> str:
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s or "")
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"<[^>]+>", " ", s)).strip()


async def workday(client, url):
    u = urlparse(url)
    m = re.match(r"/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)(/job/.+)", u.path)
    if not m:
        return ""
    tenant = u.hostname.split(".")[0]
    r = await client.get(f"{u.scheme}://{u.hostname}/wday/cxs/{tenant}/{m.group(1)}{m.group(2)}",
                         headers={"Accept": "application/json"})
    r.raise_for_status()
    return strip_html(r.json()["jobPostingInfo"]["jobDescription"])


async def greenhouse(client, url):
    m = re.search(r"greenhouse\.io/([^/]+)/jobs/(\d+)", url)
    if not m:
        return ""
    r = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
    r.raise_for_status()
    import html
    return strip_html(html.unescape(r.json()["content"]))


async def main():
    db = sqlite3.connect(DB)
    rows = db.execute("select id,url from jobs where (job_description is null or job_description='') "
                      "and url is not null and url!=''").fetchall()
    done, failed = [], []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for jid, url in rows:
            try:
                if "myworkdayjobs.com" in url:
                    text = await workday(client, url)
                elif "greenhouse.io" in url:
                    text = await greenhouse(client, url)
                elif "linkedin.com" in url:
                    lid = linkedin_guest.job_id_from_url(url)
                    text = linkedin_guest.as_jd_text(await linkedin_guest.fetch_job(lid)) if lid else ""
                    await asyncio.sleep(2)   # LinkedIn burst limit
                else:
                    text = ""
            except Exception as e:  # one dead posting must not stop the rest
                failed.append((jid, f"{type(e).__name__}: {str(e)[:80]}"))
                continue
            if len(text) < 200:
                failed.append((jid, "no description found"))
                continue
            db.execute("update jobs set job_description=?, notes=replace(notes, "
                       "'; job description missing -- re-fetch', '; job description re-fetched') where id=?",
                       (text, jid))
            db.commit()
            done.append(jid)
    print(f"filled {len(done)}: {done}")
    print(f"not filled {len(failed)}:")
    for f in failed:
        print("  ", f)


asyncio.run(main())
