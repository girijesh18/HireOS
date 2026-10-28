"""
Job links from LinkedIn / Indeed alert emails in a Gmail inbox (IMAP, read-only).

No model reads the email: links are pulled out with regexes, so alert content
can't steer anything. LinkedIn postings are then fetched through the guest API;
Indeed pages sit behind Cloudflare, so an Indeed job carries only the text of
its block in the alert email (title, company, location, snippet).

Needs GMAIL_ADDRESS and GMAIL_APP_PASSWORD (a Google app password, not the
account password) in the backend env.
"""
import email
import email.header
import email.utils
import html
import imaplib
import os
import re
from datetime import datetime, timedelta
from email.message import Message
from typing import Dict, List
from urllib.parse import parse_qsl, urlsplit

import httpx

import linkedin_guest

SENDERS = ("linkedin.com", "indeed.com", "glassdoor.com")
INVISIBLE = re.compile(r"[\u200b\u2003\u2007\u034f\xad]+")
HREF = re.compile(r'href=["\']([^"\']+)["\']', re.I)


def _html_part(msg: Message) -> str:
    """HTML body if there is one, else the plain-text body."""
    plain = ""
    for part in msg.walk():
        if part.get_content_type() in ("text/html", "text/plain") and not part.get_filename():
            body = part.get_payload(decode=True) or b""
            text = body.decode(part.get_content_charset() or "utf-8", "replace")
            if part.get_content_type() == "text/html":
                return text
            plain = plain or text
    return plain


def _text(fragment: str) -> str:
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", fragment, flags=re.S | re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "\n", t))
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", t)).strip()


def indeed_jk(url: str) -> str:
    """Indeed job key from a job link; follows one tracking redirect (cts.indeed.com) if needed."""
    q = dict(parse_qsl(urlsplit(html.unescape(url)).query))
    if q.get("jk") or q.get("vjk"):
        return q.get("jk") or q.get("vjk")
    if "indeed." not in (urlsplit(url).hostname or ""):
        return ""
    try:
        loc = httpx.get(html.unescape(url), follow_redirects=False, timeout=10, headers=linkedin_guest.HEADERS).headers.get("location", "")
    except httpx.HTTPError:
        return ""
    q = dict(parse_qsl(urlsplit(loc).query))
    return q.get("jk") or q.get("vjk") or ""


# Glassdoor's "jobs for you" mixes in unrelated roles (product designer, manufacturing...).
# ponytail: fixed keyword filter; read the user's target roles from settings if they change field
GLASSDOOR_ROLE = re.compile(r"\b(data|machine learning|ml|ai|scien\w*|analytics|llm|nlp|deep learning|mlops|genai|applied)\b", re.I)


def glassdoor_job(url: str, segment: str) -> Dict[str, str]:
    """{source, url, company, title, location, text} for one Glassdoor alert link, or {}.
    The segment after the link reads: company, [rating ★], title, location, salary..."""
    jl = dict(parse_qsl(urlsplit(url).query)).get("jobListingId")
    lines = [l.strip() for l in segment.split("\n") if l.strip() and "★" not in l]
    if not jl or len(lines) < 3 or not GLASSDOOR_ROLE.search(lines[1]):
        return {}
    return {"source": "glassdoor", "url": f"https://www.glassdoor.ca/job-listing/?jl={jl}",
            "company": lines[0], "title": lines[1], "location": lines[2],
            "text": f"{lines[1]} at {lines[0]} — {lines[2]}\n" + "\n".join(lines[3:])[:500]}


def jobs_in_email(body: str) -> List[Dict[str, str]]:
    """[{source, url, text?}] for each distinct job linked in one alert email."""
    links = [(m.start(), html.unescape(m.group(1))) for m in HREF.finditer(body)]
    jobs, seen, indeed = [], set(), {}
    for i, (pos, url) in enumerate(links):
        li = linkedin_guest.job_id_from_url(url)
        if "glassdoor." in (urlsplit(url).hostname or "") and "jobListing" in url:
            end = links[i + 1][0] if i + 1 < len(links) else len(body)
            g = glassdoor_job(url, INVISIBLE.sub("", _text(body[body.find(">", pos) + 1:end])))
            if g and g["url"] not in seen:
                seen.add(g["url"])
                jobs.append(g)
        elif li and li not in seen:
            seen.add(li)
            jobs.append({"source": "linkedin", "url": linkedin_guest.canonical_url(li)})
        elif "indeed." in (urlsplit(url).hostname or "") and ("jk=" in url or "/rc/clk" in url or "cts.indeed" in url):
            jk = indeed_jk(url)
            if not jk:
                continue
            # A job has several opaque links (title, "View job", ...); the title link is
            # followed by title/company/location/salary/preview, so keep the longest text.
            end = links[i + 1][0] if i + 1 < len(links) else len(body)
            text = INVISIBLE.sub("", _text(body[body.find(">", pos) + 1:end]))[:3000]
            if jk not in indeed:
                indeed[jk] = {"source": "indeed", "url": f"https://ca.indeed.com/viewjob?jk={jk}", "text": text}
                jobs.append(indeed[jk])
            elif len(text) > len(indeed[jk]["text"]):
                indeed[jk]["text"] = text
    return jobs


def fetch_alert_jobs(days: int = 2, max_emails: int = 30) -> List[Dict[str, str]]:
    """Jobs from alert emails received in the last `days`, newest email first."""
    user, pw = os.getenv("GMAIL_ADDRESS"), os.getenv("GMAIL_APP_PASSWORD")
    if not (user and pw):
        raise ValueError("GMAIL_ADDRESS and GMAIL_APP_PASSWORD are not set in the backend environment.")
    since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(user, pw.replace(" ", ""))
        imap.select("INBOX", readonly=True)  # never marks mail as read
        uids = []
        for sender in SENDERS:
            _, data = imap.uid("search", None, f'(SINCE {since} FROM "{sender}")')
            uids += data[0].split()
        jobs, seen = [], set()
        for uid in sorted(set(uids), key=int, reverse=True)[:max_emails]:
            _, data = imap.uid("fetch", uid, "(BODY.PEEK[])")
            for job in jobs_in_email(_html_part(email.message_from_bytes(data[0][1]))):
                if job["url"] not in seen:
                    seen.add(job["url"])
                    jobs.append(job)
        return jobs
    finally:
        try:
            imap.logout()
        except Exception:
            pass


VERIFY_WORDS = ("verif", "activat", "confirm")


def workday_verify_link(body: str, host: str = "") -> str:
    """The account-verification link in one Workday email ('' if none). Only *.myworkdayjobs.com
    links count, optionally only for one tenant host, so nothing else in the inbox can be opened."""
    for m in HREF.finditer(body):
        url = html.unescape(m.group(1))
        h = urlsplit(url).hostname or ""
        if h.endswith(".myworkdayjobs.com") and (not host or h == host) and any(w in url.lower() for w in VERIFY_WORDS):
            return url
    return ""


def fetch_workday_verify_link(host: str = "", minutes: int = 30) -> str:
    """Newest Workday verification link received in the last `minutes` (read-only IMAP)."""
    user, pw = os.getenv("GMAIL_ADDRESS"), os.getenv("GMAIL_APP_PASSWORD")
    if not (user and pw):
        raise ValueError("GMAIL_ADDRESS and GMAIL_APP_PASSWORD are not set in the backend environment.")
    since = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
    cutoff = datetime.now().astimezone() - timedelta(minutes=minutes)
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(user, pw.replace(" ", ""))
        imap.select("INBOX", readonly=True)
        _, data = imap.uid("search", None, f'(SINCE {since} FROM "myworkday")')
        for uid in sorted(data[0].split(), key=int, reverse=True)[:10]:
            _, d = imap.uid("fetch", uid, "(BODY.PEEK[])")
            msg = email.message_from_bytes(d[0][1])
            if email.utils.parsedate_to_datetime(msg["Date"]) < cutoff:
                break
            link = workday_verify_link(_html_part(msg), host)
            if link:
                return link
        return ""
    finally:
        try:
            imap.logout()
        except Exception:
            pass


# Senders allowed for one-time codes: job-application systems only, never Google, banks, etc.
ATS_SENDERS = ("oraclecloud.com", "oracle.com", "greenhouse.io", "greenhouse-mail.io", "myworkday.com",
               "lever.co", "ashbyhq.com", "icims.com", "smartrecruiters.com", "successfactors.com",
               "taleo.net", "jobvite.com", "workable.com", "bamboohr.com")
CODE_NEAR = re.compile(r"(?:code|passcode|pin|otp)\D{0,80}?\b(\d{4,8})\b"
                       r"|\b(\d{4,8})\b\D{0,40}?(?:is your|verification|security|one-time)", re.I)


def _sender_domain(from_header: str) -> str:
    addr = email.utils.parseaddr(from_header or "")[1].lower()
    return addr.rsplit("@", 1)[-1] if "@" in addr else ""


def _is_ats(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in ATS_SENDERS)


def ats_code(from_header: str, body: str) -> str:
    """The one-time code in one email from an application system ('' if none or any other sender)."""
    if not _is_ats(_sender_domain(from_header)):
        return ""
    m = CODE_NEAR.search(INVISIBLE.sub("", _text(body)))
    return (m.group(1) or m.group(2)) if m else ""


def fetch_ats_code(minutes: int = 15) -> Dict[str, str]:
    """Newest one-time code from an application system in the last `minutes` (read-only IMAP).
    Only headers are fetched for other mail, so nothing else in the inbox is downloaded.
    Returns {code, sender} or {}."""
    user, pw = os.getenv("GMAIL_ADDRESS"), os.getenv("GMAIL_APP_PASSWORD")
    if not (user and pw):
        raise ValueError("GMAIL_ADDRESS and GMAIL_APP_PASSWORD are not set in the backend environment.")
    since = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
    cutoff = datetime.now().astimezone() - timedelta(minutes=max(1, min(int(minutes), 60)))
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(user, pw.replace(" ", ""))
        imap.select("INBOX", readonly=True)
        _, data = imap.uid("search", None, f"(SINCE {since})")
        for uid in sorted(data[0].split(), key=int, reverse=True)[:15]:
            _, h = imap.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM DATE)])")
            head = email.message_from_bytes(h[0][1])
            if email.utils.parsedate_to_datetime(head["Date"]) < cutoff:
                break
            if not _is_ats(_sender_domain(head["From"])):
                continue
            _, d = imap.uid("fetch", uid, "(BODY.PEEK[])")
            code = ats_code(head["From"], _html_part(email.message_from_bytes(d[0][1])))
            if code:
                return {"code": code, "sender": _sender_domain(head["From"])}
        return {}
    finally:
        try:
            imap.logout()
        except Exception:
            pass


CONFIRM_QUERY = ('subject:(received OR applying OR applied OR application OR "thank you") '
                 '-from:jobalerts-noreply@linkedin.com -from:em.linkedin.com -from:alert@indeed.com')


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def confirms(company: str, title: str, sender: str, subject: str) -> str:
    """Why this email confirms an application to `company`/`title` ('' if it doesn't).
    Workday mails often name only the title (web@myworkday.com), or the company in the sender."""
    co = _key(re.sub(r"\b(inc|ltd|llc|corp|corporation|canada|in canada|group)\b\.?", "", (company or "").lower()))
    local, _, domain = sender.lower().partition("@")
    if co and len(co) >= 3 and (co in _key(subject) or co in _key(local) or co in _key(domain.split(".")[0])):
        return "company"
    if title and len(_key(title)) >= 8 and _key(title) in _key(subject):
        return "title"
    return ""


def fetch_confirmations(company: str, title: str = "", days: int = 14) -> List[Dict[str, str]]:
    """Recent emails confirming an application to this company/title (read-only IMAP).
    Only From/Subject/Date headers are fetched; no message body is downloaded."""
    user, pw = os.getenv("GMAIL_ADDRESS"), os.getenv("GMAIL_APP_PASSWORD")
    if not (user and pw):
        raise ValueError("GMAIL_ADDRESS and GMAIL_APP_PASSWORD are not set in the backend environment.")
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(user, pw.replace(" ", ""))
        imap.select("INBOX", readonly=True)
        imap.literal = f"newer_than:{max(1, min(int(days), 60))}d {CONFIRM_QUERY}".encode()
        _, data = imap.uid("search", "CHARSET", "UTF-8", "X-GM-RAW")
        found = []
        for uid in sorted(data[0].split(), key=int, reverse=True)[:60]:   # ponytail: newest 60 confirmations is plenty
            _, h = imap.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            head = email.message_from_bytes(h[0][1])
            sender = email.utils.parseaddr(head["From"] or "")[1]
            subject = " ".join(str(email.header.make_header(email.header.decode_header(head["Subject"] or ""))).split())
            why = confirms(company, title, sender, subject)
            if why:
                found.append({"from": sender, "subject": subject[:200], "date": head["Date"] or "", "matched_on": why})
                if len(found) == 5:
                    break
        return found
    finally:
        try:
            imap.logout()
        except Exception:
            pass

if __name__ == "__main__":
    assert ats_code("WSP <no-reply@emit.fa.ca3.oraclecloud.com>", "<p>Your verification code is <b>935955</b>.</p>") == "935955"
    assert ats_code("Greenhouse <no-reply@us.greenhouse-mail.io>", "Copy and paste this code into the field: 48213077") == "48213077"
    assert ats_code("Google <no-reply@accounts.google.com>", "Your code is 123456") == ""
    assert ats_code("x <a@evil-oraclecloud.com>", "code 123456") == ""
    assert ats_code("WSP <a@oraclecloud.com>", "Thanks for applying to job 91916") == ""
    gd = ('<a href="https://www.glassdoor.ca/partner/jobListing.htm?pos=101&amp;jobListingId=1010302515013&amp;guid=x">'
          'First National Financial<br>3.7 ★<br>AI Engineer<br>Toronto<br>$103K - $140K</a>'
          '<a href="https://www.glassdoor.ca/partner/jobListing.htm?pos=102&amp;jobListingId=2">Tigercat<br>Product Designer<br>Brant</a>')
    g = jobs_in_email(gd)
    assert len(g) == 1 and g[0]["company"] == "First National Financial" and g[0]["title"] == "AI Engineer" \
        and g[0]["url"] == "https://www.glassdoor.ca/job-listing/?jl=1010302515013", g
    assert confirms("Newfold Digital", "Senior AI Engineer", "web@myworkday.com",
                    "Thank You, Girijesh ! We've Received Your Application For Senior AI Engineer .") == "title"
    assert confirms("SunLife", "Senior Data Scientist", "sunlife@myworkday.com", "We've received your application!") == "company"
    assert confirms("WSP in Canada", "AI Developer", "no-reply@wsp.com", "Thank you for applying") == "company"
    assert confirms("Newfold Digital", "Senior AI Engineer", "axiscapital@myworkday.com", "Thank you for applying") == ""
    li ='<a href="https://www.linkedin.com/comm/jobs/view/4476106173/?trackingId=x&amp;otpToken=y">AI Engineer</a>'
    ind = ('<table><tr><td><a href="https://ca.indeed.com/rc/clk/dl?jk=abc123&amp;from=ja">ML Scientist</a>'
           '<p>Acme Corp - Toronto, ON</p><p>Build ranking models.</p></td></tr></table>'
           '<table><tr><td><a href="https://ca.indeed.com/rc/clk/dl?jk=def456">Data Scientist</a><p>Beta Inc</p></td></tr></table>')
    jobs = jobs_in_email(li + li + ind)
    assert [j["url"] for j in jobs] == ["https://www.linkedin.com/jobs/view/4476106173/",
                                        "https://ca.indeed.com/viewjob?jk=abc123",
                                        "https://ca.indeed.com/viewjob?jk=def456"], jobs
    assert "Acme Corp" in jobs[1]["text"] and "Build ranking" in jobs[1]["text"] and "Beta Inc" not in jobs[1]["text"], jobs[1]
    two = ('<a href="https://ca.indeed.com/rc/clk?jk=zz9">View job</a><a href="https://x.com/bad">bad match</a>'
           '<a href="https://ca.indeed.com/rc/clk?jk=zz9">ML Lead</a><p>Gamma - Toronto</p><p>Salary $1</p>')
    assert len(jobs_in_email(two)) == 1 and "Gamma" in jobs_in_email(two)[0]["text"], jobs_in_email(two)
    wd = ('<a href="https://www.myworkday.com/help">Help</a>'
          '<a href="https://manulife.wd3.myworkdayjobs.com/MFCJH_Jobs/activate/abc123?redirect=x">Verify Account</a>')
    assert workday_verify_link(wd) == "https://manulife.wd3.myworkdayjobs.com/MFCJH_Jobs/activate/abc123?redirect=x"
    assert workday_verify_link(wd, "td.wd3.myworkdayjobs.com") == ""
    assert workday_verify_link('<a href="https://evil.example/verify?x=myworkdayjobs.com">x</a>') == ""
    print("ok")
