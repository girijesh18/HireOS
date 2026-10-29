"""Apply queue: which approved job an apply agent (OpenClaw) works on next.

The daily caps live here, server-side, so a misbehaving or looping agent can't
apply more than the user allowed -- the agent asks for work, it never decides
the volume. Everything here is pure (no DB) so it can be tested directly:

    python test_apply_queue.py
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Settings keys (per user, in the settings table).
AUTO_APPLY_KEY = "auto_apply_enabled"        # "true" -> skip manual approval
MIN_SCORE_KEY = "auto_apply_min_score"       # auto-approve only at/above this match score
CAPS_KEY = "apply_daily_caps"                # JSON {platform: n | [lo, hi]}
TIMEZONE_KEY = "apply_timezone"              # IANA name; "today" for the caps
PAUSED_KEY = "apply_paused"                  # "true" -> agent gets no work at all
MAX_ATTEMPTS_KEY = "apply_max_attempts"      # failed tries before a job goes to needs_human
CLAIM_TTL_KEY = "apply_claim_ttl_minutes"    # minutes before a silent claim is reclaimed
AUTO_MIN_SCORE_KEY = "auto_mode_min_score"  # auto mode applies without asking at/above this (>= min_score)
DEFAULT_AUTO_MIN_SCORE = 85
MAX_AGE_KEY = "discovery_max_age_hours"      # discovery skips postings older than this (unknown age is kept)
DEFAULT_MAX_AGE_HOURS = 72

DEFAULT_MIN_SCORE = 75
DEFAULT_TIMEZONE = "America/Toronto"
# [lo, hi] draws a fresh cap each day, so the daily volume isn't a constant.
DEFAULT_CAPS: Dict[str, object] = {
    "linkedin": [10, 20],
    "indeed": [10, 20],
    "greenhouse": 15,
    "lever": 15,
    "ashby": 15,
    "default": 10,
}

# A claim older than this is assumed dead (agent crashed mid-application) and
# the job goes back in the queue.
CLAIM_TTL = timedelta(hours=2)

OUTCOMES = ("submitted", "needs_human", "failed", "skipped")
# After this many failed attempts a job stops being retried automatically.
MAX_ATTEMPTS = 2

_PLATFORM_HOSTS = [
    ("linkedin", r"(^|\.)linkedin\.com$"),
    ("indeed", r"(^|\.)indeed\.(com|ca|co\.uk|co\.in)$"),
    ("glassdoor", r"(^|\.)glassdoor\.(com|ca)$"),
    ("greenhouse", r"(^|\.)greenhouse\.io$"),
    ("lever", r"(^|\.)lever\.co$"),
    ("ashby", r"(^|\.)ashbyhq\.com$"),
    ("workday", r"(^|\.)myworkdayjobs\.com$|(^|\.)workday\.com$"),
    ("smartrecruiters", r"(^|\.)smartrecruiters\.com$"),
    ("icims", r"(^|\.)icims\.com$"),
    ("taleo", r"(^|\.)taleo\.net$"),
    ("successfactors", r"(^|\.)successfactors\.(com|eu)$|(^|\.)sapsf\.com$"),
]

# What the apply worker fills (apply_worker.SUPPORTED); everything else is applied to by the user.
WORKER_FILLS = ("greenhouse", "lever", "ashby", "workday")
_ATS_NAMES = {"icims": "iCIMS", "taleo": "Taleo", "successfactors": "SAP SuccessFactors",
              "smartrecruiters": "SmartRecruiters", "avature": "Avature", "oraclecloud": "Oracle",
              "jobvite": "Jobvite", "bamboohr": "BambooHR", "workable": "Workable", "dayforcehcm": "Dayforce",
              "ultipro": "UKG", "adp": "ADP", "paylocity": "Paylocity", "recruitee": "Recruitee",
              "breezy": "Breezy", "rippling": "Rippling"}


def manual_reason(job) -> Optional[str]:
    """Why the apply worker can't fill this job, in plain words, or None when it can.
    Shown on the resume card before approving and in the hand-off after."""
    platform = detect_platform(job.url)
    if platform in WORKER_FILLS or (job.platform or "").lower() in ("greenhouse", "lever", "ashby"):
        return None
    notes = job.notes or ""
    if "LinkedIn Easy Apply only" in notes:
        return "LinkedIn Easy Apply only: it needs your LinkedIn sign-in, and the agent never signs in to LinkedIn."
    careers = re.search(r"\[careers: (\S+?)\]", notes)
    if careers:
        host = (urlsplit(careers.group(1)).hostname or "").lower()
        ats = next((name for key, name in _ATS_NAMES.items() if key in host), None)
        if ats:
            return (f"the application is on {ats}, which the agent can't fill yet ({ats} usually needs an "
                    "account first, and the agent only creates accounts on Workday).")
        return (f"the application is on the company's own careers site ({host}); the agent only fills "
                "Greenhouse, Lever, Ashby and Workday forms.")
    if platform == "linkedin":
        return ("LinkedIn hides this posting's apply link behind its sign-in, and no Greenhouse, Lever, Ashby "
                "or Workday posting was found for it.")
    host = (urlsplit(job.url or "").hostname or "no link").lower()
    name = _ATS_NAMES.get(platform) or host
    return f"it's on {name}, which the agent can't fill; it only fills Greenhouse, Lever, Ashby and Workday forms."


# Query params that only track where a click came from; dropping them lets the
# same posting reached from two alert emails dedupe to one job.
_TRACKING_PARAMS = re.compile(r"^(utm_.*|ref|refid|trk|trackingid|src|source|from|gh_src|lipi|eBP|alid|tk)$", re.I)


def detect_platform(url: Optional[str]) -> str:
    """ATS / job board behind a URL, or "other"."""
    host = (urlsplit(url or "").hostname or "").lower()
    for name, pattern in _PLATFORM_HOSTS:
        if re.search(pattern, host):
            return name
    return "other"


@functools.lru_cache(maxsize=20000)   # pure; the duplicate check normalizes every stored URL per insert
def normalize_url(url: Optional[str]) -> str:
    """Canonical form of a job URL for duplicate detection ("" if none)."""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url if "://" in url else "https://" + url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    # LinkedIn has many URL shapes for one posting; the numeric job id is the identity.
    if host.endswith("linkedin.com"):
        m = re.search(r"/jobs/view/(?:[^/]*?-)?(\d+)", parts.path)
        job_id = m.group(1) if m else dict(parse_qsl(parts.query)).get("currentJobId")
        if job_id:
            return f"linkedin.com/jobs/view/{job_id}"
    if "indeed." in host:
        jk = dict(parse_qsl(parts.query)).get("jk") or dict(parse_qsl(parts.query)).get("vjk")
        if jk:
            return f"indeed/{jk}"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAMS.match(k)))
    return urlunsplit(("", host, parts.path.rstrip("/"), query, "")).lstrip("/")


def parse_caps(raw: Optional[str]) -> Dict[str, object]:
    """User caps merged over the defaults; bad JSON falls back to defaults."""
    caps = dict(DEFAULT_CAPS)
    if raw:
        try:
            user = json.loads(raw)
            if isinstance(user, dict):
                caps.update({str(k).lower(): v for k, v in user.items()})
        except (ValueError, TypeError):
            pass
    return caps


def daily_cap(caps: Dict[str, object], platform: str, user_id: int, day: date) -> int:
    """Today's cap for a platform. A [lo, hi] range resolves to one number per
    (user, platform, day) -- stable across calls, different across days."""
    spec = caps.get(platform, caps.get("default", 10))
    try:   # a bad stored value means "don't apply there", never a 500 on every apply endpoint
        if isinstance(spec, (list, tuple)) and len(spec) == 2:
            lo, hi = sorted(max(0, int(x)) for x in spec)
            seed = hashlib.sha256(f"{user_id}:{platform}:{day.isoformat()}".encode()).digest()
            return lo + int.from_bytes(seed[:4], "big") % (hi - lo + 1)
        return max(0, int(spec))
    except (TypeError, ValueError):
        return 0


def valid_cap(spec) -> bool:
    """A cap is a whole number >= 0 or a [lo, hi] pair of them."""
    ok = lambda x: isinstance(x, int) and not isinstance(x, bool) and x >= 0
    return ok(spec) or (isinstance(spec, (list, tuple)) and len(spec) == 2 and all(ok(x) for x in spec))


def day_bounds_utc(tz_name: Optional[str], now_utc: datetime) -> Tuple[date, datetime, datetime]:
    """(local date, start, end) of the user's current day, as naive UTC datetimes
    to compare with the DB's utcnow() timestamps."""
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(tz_name or DEFAULT_TIMEZONE)
    except Exception:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return local.date(), start, start + timedelta(days=1)


def is_auto_approvable(auto_enabled: bool, min_score: float, status: str, score: Optional[float]) -> bool:
    """A scored, not-yet-approved job that auto-approval may push into the queue."""
    return bool(auto_enabled) and status == "pending" and score is not None and score >= min_score


# Auto mode applies on its own only on weekdays in work hours (user's timezone), one job per
# AUTO_GAP: a burst of applications at 3 a.m. looks like a bot, and leaves the user no time to stop it.
AUTO_HOURS = (8, 20)
AUTO_GAP = timedelta(minutes=20)
AUTO_LAST_KEY = "auto_last_claim"


def auto_claim_allowed(tz_name: Optional[str], now_utc: datetime, last_claim: Optional[str]) -> bool:
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(tz_name or DEFAULT_TIMEZONE)
    except Exception:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    if local.weekday() >= 5 or not AUTO_HOURS[0] <= local.hour < AUTO_HOURS[1]:
        return False
    return claim_is_stale(last_claim, now_utc, AUTO_GAP)


def claim_is_stale(claimed_at: Optional[str], now_utc: datetime, ttl: timedelta = CLAIM_TTL) -> bool:
    if not claimed_at:
        return True
    try:
        return now_utc - datetime.fromisoformat(claimed_at) > ttl
    except ValueError:
        return True


# One application per company per 90 days: a second one in the same window reads as
# spam to the recruiter (Ashby/Greenhouse cap and blocklist candidates) and Huntr's
# data shows repeat applications to one company convert at about a third of the rate.
COMPANY_COOLDOWN_DAYS = 90


def company_key(name: Optional[str]) -> str:
    """'WSP in Canada' / 'WSP' and 'Manulife (John Hancock)' / 'Manulife' compare equal.
    "" when the company isn't known: unknown companies never match each other."""
    if (name or "").strip().lower() in ("", "unknown", "n/a", "none"):
        return ""
    n = re.sub(r"\(.*?\)", " ", (name or "").lower())
    n = re.sub(r"\b(in canada|canada|inc|ltd|llc|corp|corporation|limited|co)\b\.?", " ", n)
    return re.sub(r"[^a-z0-9]+", "", n)
