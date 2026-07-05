"""
HireOS — FastAPI Backend
Includes all CRUD endpoints + live agent endpoints.
"""
from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from typing import List, Optional, Any, Dict
import os
import re
import csv
import io
import json
import asyncio
from pathlib import Path
from urllib.parse import urlencode
import shutil
import httpx
from jose import JWTError, jwt
import bcrypt as _bcrypt
from pydantic import BaseModel as _BaseModel

from database import (
    init_db, get_db, SessionLocal, Job, ApplicationEvent, ResumeVersion,
    CoverLetterVersion, Settings, LLMComparison,
    EvaluationReport, StoryBankEntry, FollowUpLog,
    MasterResumeComponent, ChatInteraction, User,
    LLMTaskStatus, ResearchReport, LinkedInOutreachReport, InterviewPrepReport,
)
from schemas import (
    JobCreate, JobUpdate, JobOut,
    EventCreate, EventOut,
    ResumeVersionOut, CoverLetterVersionOut,
    ChatMessage, ChatResponse,
    SettingItem, SettingsUpdate,
    CompareRequest, CompareResponse, CompareResult,
    DashboardStats,
    EvaluationReportOut, StoryBankEntryCreate, StoryBankEntryOut,
    FollowUpLogCreate, FollowUpLogOut, FollowUpCadenceOut,
    PatternAnalyticsOut,
    ResumeComponentOut, ResumeComponentCreate, ResumeComponentUpdate,
    ChatInteractionOut, InsightsOut,
)
import chat_store
from search import router as search_router
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# ── Auth config ───────────────────────────────────────────────────────────────
JWT_SECRET = os.getenv("JWT_SECRET", "hireos-dev-secret-change-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30
_bearer = HTTPBearer(auto_error=False)

# ── SSO / OAuth config ──────────────────────────────────────────────────────────
# Public base URL the browser is redirected back to. Must match the redirect URIs
# registered with each provider, e.g. https://hireos.girijesh.ca
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")

OAUTH_PROVIDERS = {
    "google": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://www.googleapis.com/oauth2/v3/userinfo",
        "scope": "openid email profile",
    },
    "github": {
        "client_id": os.getenv("GITHUB_CLIENT_ID"),
        "client_secret": os.getenv("GITHUB_CLIENT_SECRET"),
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "userinfo_url": "https://api.github.com/user",
        "emails_url": "https://api.github.com/user/emails",
        "scope": "read:user user:email",
    },
}


def _oauth_redirect_uri(provider: str) -> str:
    return f"{APP_BASE_URL}/auth/oauth/{provider}/callback"


def _make_oauth_state(provider: str) -> str:
    """Signed, short-lived CSRF state — stateless, no server-side session store."""
    payload = {
        "p": provider,
        "typ": "oauth_state",
        "exp": datetime.utcnow() + timedelta(minutes=10),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _verify_oauth_state(state: str, provider: str) -> bool:
    if not state:
        return False
    try:
        payload = jwt.decode(state, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return False
    return payload.get("typ") == "oauth_state" and payload.get("p") == provider

def _hash_pw(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()

def _verify_pw(password: str, hashed: str) -> bool:
    return _bcrypt.checkpw(password.encode(), hashed.encode())

def _create_token(email: str) -> str:
    exp = datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS)
    return jwt.encode({"sub": email, "exp": exp}, JWT_SECRET, algorithm=JWT_ALGORITHM)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(_bearer), db: Session = Depends(get_db)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        email = payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

app = FastAPI(title="HireOS API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_AUTH_EXEMPT = {"/api/health"}

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if (
        path.startswith("/auth/")
        or not path.startswith("/api/")
        or path in _AUTH_EXEMPT
    ):
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    try:
        jwt.decode(auth[7:], JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return JSONResponse({"detail": "Invalid token"}, status_code=401)
    return await call_next(request)

app.include_router(search_router)


# ── Auth endpoints ─────────────────────────────────────────────────────────────

class _AuthBody(_BaseModel):
    email: str
    password: str


@app.post("/auth/signup")
def signup(data: _AuthBody, db: Session = Depends(get_db)):
    try:
        email = data.email.lower().strip()
        password = data.password
        if not email or not password:
            raise HTTPException(400, "Email and password required")
        if len(password) < 6:
            raise HTTPException(400, "Password must be at least 6 characters")
        if db.query(User).filter(User.email == email).first():
            raise HTTPException(400, "Email already registered")
        hashed = _hash_pw(password)
        user = User(email=email, password_hash=hashed)
        db.add(user)
        db.commit()
        return {"token": _create_token(email), "email": email}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Signup error: {type(e).__name__}: {e}")
        raise HTTPException(500, f"Signup failed: {type(e).__name__}: {e}")


@app.post("/auth/login")
def login(data: _AuthBody, db: Session = Depends(get_db)):
    email = data.email.lower().strip()
    password = data.password
    user = db.query(User).filter(User.email == email).first()
    if not user or not user.password_hash:
        # No local password set (SSO-only account, or unknown email).
        raise HTTPException(401, "Invalid email or password")
    if not _verify_pw(password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    return {"token": _create_token(email), "email": email}


@app.post("/auth/reset-password")
def reset_password(data: _AuthBody, db: Session = Depends(get_db)):
    email = data.email.lower().strip()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(404, "User not found")
    user.password_hash = _hash_pw(data.password)
    db.commit()
    return {"status": "ok"}


@app.get("/auth/me")
def me(current_user: User = Depends(get_current_user)):
    return {"email": current_user.email}


# ── SSO / OAuth endpoints ───────────────────────────────────────────────────────

def _upsert_oauth_user(db: Session, email: str, provider: str, name: str = None, avatar_url: str = None) -> User:
    """Link-by-email: log into the existing account for this email, or create a new
    SSO account if none exists. Never overwrites an existing local password."""
    email = email.lower().strip()
    user = db.query(User).filter(User.email == email).first()
    if user:
        changed = False
        if name and not getattr(user, "name", None):
            user.name = name; changed = True
        if avatar_url and not getattr(user, "avatar_url", None):
            user.avatar_url = avatar_url; changed = True
        if not user.password_hash and not user.oauth_provider:
            user.oauth_provider = provider; changed = True
        if changed:
            db.commit()
        return user
    user = User(email=email, password_hash="", oauth_provider=provider, name=name, avatar_url=avatar_url)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _fail_redirect(reason: str) -> RedirectResponse:
    return RedirectResponse(f"{APP_BASE_URL}/?auth_error={reason}")


@app.get("/auth/oauth/{provider}/login")
def oauth_login(provider: str):
    cfg = OAUTH_PROVIDERS.get(provider)
    if not cfg:
        raise HTTPException(404, "Unknown provider")
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise HTTPException(503, f"{provider} SSO is not configured")
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": _oauth_redirect_uri(provider),
        "scope": cfg["scope"],
        "state": _make_oauth_state(provider),
        "response_type": "code",
    }
    if provider == "google":
        params["access_type"] = "online"
        params["prompt"] = "select_account"
    return RedirectResponse(f"{cfg['authorize_url']}?{urlencode(params)}")


@app.get("/auth/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    code: str = None,
    state: str = None,
    error: str = None,
    db: Session = Depends(get_db),
):
    cfg = OAUTH_PROVIDERS.get(provider)
    if not cfg:
        raise HTTPException(404, "Unknown provider")
    if error:
        return _fail_redirect(error)
    if not code or not _verify_oauth_state(state, provider):
        return _fail_redirect("invalid_state")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_resp = await client.post(
                cfg["token_url"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "redirect_uri": _oauth_redirect_uri(provider),
                },
                headers={"Accept": "application/json"},
            )
            token_resp.raise_for_status()
            access_token = token_resp.json().get("access_token")
            if not access_token:
                return _fail_redirect("token_exchange_failed")

            auth_header = {"Authorization": f"Bearer {access_token}"}

            if provider == "google":
                ui = await client.get(cfg["userinfo_url"], headers=auth_header)
                ui.raise_for_status()
                info = ui.json()
                email = info.get("email")
                if not email or not info.get("email_verified", False):
                    return _fail_redirect("email_unverified")
                name = info.get("name")
                avatar_url = info.get("picture")
            else:  # github
                gh_headers = {**auth_header, "Accept": "application/vnd.github+json"}
                ui = await client.get(cfg["userinfo_url"], headers=gh_headers)
                ui.raise_for_status()
                info = ui.json()
                name = info.get("name") or info.get("login")
                avatar_url = info.get("avatar_url")
                # GitHub may hide the primary email on /user — fetch verified emails.
                em = await client.get(cfg["emails_url"], headers=gh_headers)
                em.raise_for_status()
                emails = em.json()
                primary = next(
                    (e for e in emails if e.get("primary") and e.get("verified")), None
                ) or next((e for e in emails if e.get("verified")), None)
                email = primary["email"] if primary else None
                if not email:
                    return _fail_redirect("email_unverified")
    except httpx.HTTPError:
        return _fail_redirect("oauth_failed")

    user = _upsert_oauth_user(db, email, provider, name=name, avatar_url=avatar_url)
    token = _create_token(user.email)
    return RedirectResponse(f"{APP_BASE_URL}/?auth_token={token}")

# ── Per-user (BYOK) LLM routing ────────────────────────────────────────────────
# Each user supplies their own provider keys in Settings; the router uses the
# requesting user's keys (os.environ only as a server-level fallback for local dev).

_USER_KEY_MAP = {
    "gemini_api_key": "gemini",
    "groq_api_key": "groq",
    "openrouter_api_key": "openrouter",
    "together_api_key": "together",
    "anthropic_api_key": "anthropic",
    "nvidia_api_key": "nvidia",
    "ollama_base_url": "ollama_url",
    "github_token": "github_token",
    "github_username": "github_username",
}

_AGENT_CLASSES = {}


def _load_agent_classes():
    global _AGENT_CLASSES
    if not _AGENT_CLASSES:
        from agents import (
            JobDiscoveryAgent, FitAssessmentAgent,
            ResumeTailoringAgent, CoverLetterAgent, ChatDispatcherAgent,
            LinkedInOutreachAgent, DeepResearchAgent, InterviewPrepAgent,
            ResumeCriticAgent, InsightsAgent,
        )
        _AGENT_CLASSES = {
            "discovery": JobDiscoveryAgent,
            "fit": FitAssessmentAgent,
            "resume": ResumeTailoringAgent,
            "critic": ResumeCriticAgent,
            "cover": CoverLetterAgent,
            "chat": ChatDispatcherAgent,
            "linkedin": LinkedInOutreachAgent,
            "deep_research": DeepResearchAgent,
            "interview_prep": InterviewPrepAgent,
            "insights": InsightsAgent,
        }
    return _AGENT_CLASSES


def get_llm_router(db: Session = None, user_id: int = None):
    """Router scoped strictly to a user's BYOK keys when a user is given (no env fallback,
    so one user's generations never use another user's or a shared server key).
    With no user, returns a server-fallback router (health/diagnostics only)."""
    from llm_router import LLMRouter
    if db is not None and user_id is not None:
        keys = {}
        rows = db.query(Settings).filter(Settings.user_id == user_id).all()
        for r in rows:
            if r.key in _USER_KEY_MAP and r.value and "•" not in r.value:
                keys[_USER_KEY_MAP[r.key]] = r.value
        return LLMRouter(keys=keys, allow_env=False)
    return LLMRouter(allow_env=True)


def get_agent(name: str, db: Session = None, user_id: int = None):
    """Build a fresh agent bound to the requesting user's BYOK router."""
    classes = _load_agent_classes()
    router = get_llm_router(db, user_id)
    return classes[name](router)


def resolve_llm(db: Session, user_id: int, requested=None) -> str:
    """The model to use: the caller's explicit choice, else the user's first
    configured provider (not a hardcoded gemini)."""
    if requested:
        return requested
    return get_llm_router(db, user_id).default_llm()


def get_owned_job(db: Session, job_id: int, user) -> Job:
    """Fetch a job that belongs to the user, else 404 (prevents cross-user access)."""
    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _owns_job(db: Session, job_id: int, user_id: int) -> bool:
    return db.query(Job.id).filter(Job.id == job_id, Job.user_id == user_id).first() is not None


def _strip_html(text: str) -> str:
    """Generic HTML tag stripper (used outside resume pipeline)."""
    import html as html_lib
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(p|div|li|h[1-6]|section|tr)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = html_lib.unescape(text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _html_resume_to_markdown(html: str) -> str:
    """Convert resume HTML (with header-container divs) to structured markdown.
    Preserves company/date/title structure so the LLM can read it properly.
    """
    import html as html_lib

    def clean(s: str) -> str:
        return html_lib.unescape(re.sub(r'<[^>]+>', '', s)).strip()

    text = html

    # Remove style/script blocks
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)

    # h1 → # Name
    text = re.sub(
        r'<h1[^>]*>(.*?)</h1>',
        lambda m: f'# {clean(m.group(1))}\n',
        text, flags=re.IGNORECASE | re.DOTALL
    )

    # h2 → ## SECTION
    text = re.sub(
        r'<h2[^>]*>(.*?)</h2>',
        lambda m: f'\n## {clean(m.group(1))}\n',
        text, flags=re.IGNORECASE | re.DOTALL
    )

    # contact-info div → plain contact line
    text = re.sub(
        r'<div[^>]+class="contact-info"[^>]*>(.*?)</div>',
        lambda m: clean(m.group(1)) + '\n',
        text, flags=re.DOTALL
    )

    # header-container sub (job title + date) — process BEFORE main header
    def _sub_header(m):
        inner = m.group(1)
        title_m = re.search(r'<div[^>]+class="job-title"[^>]*>(.*?)</div>', inner, re.DOTALL)
        # date is the last div in the block — its closing </div> may have been consumed by the outer match
        date_m  = re.search(r'<div[^>]+class="date"[^>]*>(.*?)(?:</div>|$)', inner, re.DOTALL)
        title = clean(title_m.group(1)) if title_m else ''
        date  = clean(date_m.group(1))  if date_m  else ''
        if title and date:
            return f'**{title}** ({date})\n'
        return f'**{title}**\n' if title else '\n'

    text = re.sub(
        r'<div[^>]+class="header-container sub"[^>]*>(.*?)</div>\s*</div>',
        _sub_header, text, flags=re.DOTALL
    )
    # Also catch without trailing </div>
    text = re.sub(
        r'<div[^>]+class="header-container sub"[^>]*>(.*?)</div>',
        _sub_header, text, flags=re.DOTALL
    )

    # header-container (company + date)
    def _company_header(m):
        inner = m.group(1)
        company_m = re.search(r'<div[^>]+class="company"[^>]*>(.*?)</div>', inner, re.DOTALL)
        # education blocks use job-title instead of company in a header-container
        title_m   = re.search(r'<div[^>]+class="job-title"[^>]*>(.*?)</div>', inner, re.DOTALL)
        # date is the last div in the block — its closing </div> may have been consumed by the outer match
        date_m    = re.search(r'<div[^>]+class="date"[^>]*>(.*?)(?:</div>|$)', inner, re.DOTALL)
        company = clean(company_m.group(1)) if company_m else (clean(title_m.group(1)) if title_m else '')
        date    = clean(date_m.group(1))    if date_m    else ''
        if company and date:
            return f'\n### {company} || {date}\n'
        return f'\n### {company}\n' if company else '\n'

    text = re.sub(
        r'<div[^>]+class="header-container"[^>]*>(.*?)</div>\s*</div>',
        _company_header, text, flags=re.DOTALL
    )
    text = re.sub(
        r'<div[^>]+class="header-container"[^>]*>(.*?)</div>',
        _company_header, text, flags=re.DOTALL
    )

    # Bold / italic inline
    text = re.sub(r'<strong>(.*?)</strong>', r'**\1**', text, flags=re.DOTALL)
    text = re.sub(r'<b>(.*?)</b>',           r'**\1**', text, flags=re.DOTALL)
    text = re.sub(r'<em>(.*?)</em>',          r'*\1*',  text, flags=re.DOTALL)
    text = re.sub(r'<i>(.*?)</i>',            r'*\1*',  text, flags=re.DOTALL)

    # List items
    text = re.sub(
        r'<li[^>]*>(.*?)</li>',
        lambda m: f'- {clean(m.group(1))}\n',
        text, flags=re.DOTALL
    )
    text = re.sub(r'<[uo]l[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</[uo]l>', '\n', text, flags=re.IGNORECASE)

    # Anchors — keep text only
    text = re.sub(r'<a[^>]+>(.*?)</a>', r'\1', text, flags=re.DOTALL)

    # Block closers → newline
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(p|div|tr|td|section)>', '\n', text, flags=re.IGNORECASE)

    # Strip remaining tags
    text = re.sub(r'<[^>]+>', '', text)

    # Decode entities
    text = html_lib.unescape(text)

    # Clean whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_contact_info(raw: str) -> dict:
    """Extract verified contact facts from raw resume content (HTML or plain text).
    Uses regex so results are guaranteed correct — no LLM involved.
    """
    import html as html_lib

    decoded = html_lib.unescape(raw)
    plain = re.sub(r'<[^>]+>', ' ', decoded)
    plain = re.sub(r'\s+', ' ', plain)

    # Name from h1 tag
    name_m = re.search(r'<h1[^>]*>(.*?)</h1>', raw, re.IGNORECASE | re.DOTALL)
    if name_m:
        name = re.sub(r'<[^>]+>', '', name_m.group(1)).strip()
    else:
        lines = [l.strip() for l in decoded.split('\n') if l.strip()]
        # First line that looks like a name (not a tag or URL)
        name = next((l for l in lines if l and not l.startswith('<') and not l.startswith('#') and len(l) < 60), '')
        name = re.sub(r'^#+\s*', '', name)

    # Email
    email_m = re.search(r'[\w.+-]+@[\w.-]+\.\w{2,}', plain)
    email = email_m.group(0) if email_m else ''

    # Phone — North American format
    phone_m = re.search(r'(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}', plain)
    phone = phone_m.group(0).strip() if phone_m else ''

    # Location — search inside contact-info div first (avoids matching the name)
    contact_div_m = re.search(r'class="contact-info"[^>]*>(.*?)</div>', raw, re.DOTALL | re.IGNORECASE)
    loc_search_text = re.sub(r'<[^>]+>', ' ', contact_div_m.group(1)) if contact_div_m else plain
    loc_search_text = html_lib.unescape(loc_search_text)
    loc_m = re.search(
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?,\s*'
        r'(?:Ontario|British Columbia|BC|Alberta|Quebec|Manitoba|Saskatchewan|'
        r'Nova Scotia|New Brunswick|PEI|Newfoundland|Yukon|NWT|Nunavut|'
        r'California|New York|Texas|Washington|Florida|Illinois|[A-Z]{2})'
        r'(?:,\s*(?:Canada|USA?))?)',
        loc_search_text
    )
    location = loc_m.group(1).strip() if loc_m else ''

    # LinkedIn — prefer href attribute (full URL)
    li_href = re.search(r'href=["\']([^"\']*linkedin\.com/in/[^\s"\']+)["\']', raw, re.IGNORECASE)
    if li_href:
        linkedin = li_href.group(1)
    else:
        li_m = re.search(r'(?:https?://)?(?:www\.)?linkedin\.com/in/[\w\-]+', plain)
        linkedin = li_m.group(0) if li_m else ''
        if linkedin and not linkedin.startswith('http'):
            linkedin = 'https://' + linkedin

    # GitHub — prefer href attribute
    gh_href = re.search(r'href=["\']([^"\']*github\.com/[\w\-][^\s"\']*)["\']', raw, re.IGNORECASE)
    if gh_href:
        github = gh_href.group(1)
    else:
        gh_m = re.search(r'(?:https?://)?(?:www\.)?github\.com/[\w\-]+', plain)
        github = gh_m.group(0) if gh_m else ''
        if github and not github.startswith('http'):
            github = 'https://' + github

    return {
        'name': name,
        'email': email,
        'phone': phone,
        'location': location,
        'linkedin': linkedin,
        'github': github,
    }


def _classify_component(name: str, content: str) -> str:
    """Decide whether a master-resume component is actual resume content or noise
    (a Q&A/questionnaire doc or a style-guide page). Only 'resume' components feed
    the tailoring prompt — joining questionnaires/CSS bloats and corrupts generation.
    """
    n = (name or "").lower()
    cl = (content or "").lower()

    # Explicit style-guide component → formatting reference, not resume text
    if "style guide" in n or "styleguide" in n or "style_guide" in n:
        return "style"

    # Q&A / questionnaire / intake doc → not resume content
    if ("needs your input" in cl
            or "section 1: positioning" in cl
            or n.endswith("_qa") or "questionnaire" in n or "intake" in n
            or len(re.findall(r'\*\*q\d', cl)) >= 3
            or cl.count("?") > 40):
        return "qa"

    # Full standalone HTML page (template/style) with no resume sections
    if "<!doctype" in cl and "professional summary" not in cl and "experience" not in cl:
        return "style"

    return "resume"


def _resume_components(db: Session, user_id: int):
    """Active resume-content components for a user (questionnaires/style guides filtered out).
    Falls back to all active components if the filter would remove everything."""
    components = db.query(MasterResumeComponent).filter(
        MasterResumeComponent.is_active == True,
        MasterResumeComponent.user_id == user_id,
    ).order_by(MasterResumeComponent.order.asc()).all()
    if not components:
        return []
    resume_only = [c for c in components if _classify_component(c.name, c.content_text or "") == "resume"]
    if not resume_only:
        logger.warning("[MasterResume] No component classified as resume — falling back to all active components")
        return components
    skipped = [c.name for c in components if c not in resume_only]
    if skipped:
        logger.info(f"[MasterResume] Skipped non-resume components: {skipped}")
    return resume_only


def get_master_resume(db: Session, user_id: int) -> str:
    """Load a user's master resume — joins resume-content components as structured markdown."""
    components = _resume_components(db, user_id)

    if components:
        parts = []
        for c in components:
            raw = c.content_text or ""
            is_html = bool(re.search(r'<(?:h[1-6]|div|ul|li|p)\b', raw, re.IGNORECASE))
            text = _html_resume_to_markdown(raw) if is_html else raw
            parts.append(text)
        result = "\n\n".join(parts)
        logger.info(f"[MasterResume] {len(components)} resume component(s), {len(result)} chars")
        return result

    # Legacy fallback (per-user)
    row = db.query(Settings).filter(Settings.key == "master_resume", Settings.user_id == user_id).first()
    if row and row.value:
        return row.value
    return ""


def get_raw_resume_content(db: Session, user_id: int) -> str:
    """Return raw (un-processed) resume-component content for contact info extraction."""
    components = _resume_components(db, user_id)
    if components:
        return "\n".join(c.content_text or "" for c in components)
    row = db.query(Settings).filter(Settings.key == "master_resume", Settings.user_id == user_id).first()
    return row.value if row and row.value else ""


@app.on_event("startup")
def startup():
    init_db()
    os.makedirs(os.getenv("OUTPUT_DIR", "./output"), exist_ok=True)
    # API keys are per-user (BYOK), read from each user's Settings at request time.
    # No global os.environ syncing — that would leak one user's key process-wide.
    # Init ChromaDB and purge interactions older than 90 days
    try:
        chat_store._get_collection()
        purged = chat_store.purge_old(days=90)
        if purged:
            logger.info(f"[ChatStore] Purged {purged} old interactions on startup")
    except Exception as e:
        logger.warning(f"[ChatStore] Startup init failed (non-fatal): {e}")
    logger.info("HireOS API started ✓")




# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def root():
    router = get_llm_router()
    return {
        "status": "running",
        "service": "HireOS API",
        "version": "2.0.0",
        "available_llms": router.available_providers(),
    }


# ── Dashboard Stats ────────────────────────────────────────────────────────────

@app.get("/api/stats", response_model=DashboardStats)
def get_stats(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    uid = current_user.id
    total = db.query(func.count(Job.id)).filter(Job.user_id == uid).scalar()
    week_ago = datetime.utcnow() - timedelta(days=7)

    by_status = {
        row.status: row.count
        for row in db.query(Job.status, func.count(Job.id).label("count")).filter(Job.user_id == uid).group_by(Job.status).all()
    }
    applied_this_week = db.query(func.count(Job.id)).filter(Job.user_id == uid, Job.applied_at >= week_ago).scalar()
    now = datetime.utcnow()
    follow_ups_due = db.query(func.count(Job.id)).filter(
        Job.user_id == uid,
        Job.follow_up_due <= now,
        Job.status.notin_(["applied", "rejected", "withdrawn", "offer"])
    ).scalar()

    return DashboardStats(
        total=total or 0,
        by_status=by_status,
        applied_this_week=applied_this_week or 0,
        pending_review=by_status.get("pending", 0),
        interviews_scheduled=by_status.get("interview_1", 0) + by_status.get("interview_2", 0),
        offers=by_status.get("offer", 0),
        follow_ups_due=follow_ups_due or 0,
    )


# ── Jobs CRUD ──────────────────────────────────────────────────────────────────

@app.get("/api/jobs", response_model=List[JobOut])
def list_jobs(
    status: Optional[str] = None,
    platform: Optional[str] = None,
    starred: Optional[bool] = None,
    priority: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Job).filter(Job.user_id == current_user.id)
    if status:
        q = q.filter(Job.status == status)
    if platform:
        q = q.filter(Job.platform == platform)
    if starred is not None:
        q = q.filter(Job.starred == starred)
    if priority:
        q = q.filter(Job.priority == priority)
    return q.order_by(Job.created_at.desc()).offset(skip).limit(limit).all()


@app.post("/api/jobs", response_model=JobOut)
def create_job(job: JobCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db_job = Job(**job.model_dump(), user_id=current_user.id)
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    _log_event(db, db_job.id, "status_change", "Job Added",
               f"Tracking started for {db_job.title} at {db_job.company}",
               new_value="found", source="user", user_id=current_user.id)
    return db_job


@app.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return get_owned_job(db, job_id, current_user)


@app.patch("/api/jobs/{job_id}", response_model=JobOut)
def update_job(job_id: int, updates: JobUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = get_owned_job(db, job_id, current_user)

    update_data = updates.model_dump(exclude_unset=True)
    old_status = job.status

    for key, val in update_data.items():
        setattr(job, key, val)
    job.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(job)

    if "status" in update_data and old_status != job.status:
        _log_event(db, job_id, "status_change", f"Status → {job.status}",
                   f"Moved from {old_status} to {job.status}",
                   old_value=old_status, new_value=job.status, source="user",
                   user_id=current_user.id)
    return job


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = get_owned_job(db, job_id, current_user)
    db.delete(job)
    db.commit()
    return {"status": "deleted"}


@app.get("/api/jobs/{job_id}/tasks")
def get_job_tasks(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Get status of all async LLM tasks for a job."""
    get_owned_job(db, job_id, current_user)
    return db.query(LLMTaskStatus).filter(
        LLMTaskStatus.job_id == job_id
    ).all()


# ── Application Timeline Events ────────────────────────────────────────────────

@app.get("/api/jobs/{job_id}/events", response_model=List[EventOut])
def get_events(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Get all events and follow-up logs for a job, unified and sorted."""
    get_owned_job(db, job_id, current_user)
    events = db.query(ApplicationEvent).filter(ApplicationEvent.job_id == job_id).all()
    fups = db.query(FollowUpLog).filter(FollowUpLog.job_id == job_id).all()
    
    unified = []
    for e in events:
        unified.append({
            "id": e.id,
            "job_id": e.job_id,
            "event_type": e.event_type,
            "title": e.title,
            "description": e.description,
            "old_value": e.old_value,
            "new_value": e.new_value,
            "source": e.source,
            "scheduled_at": e.scheduled_at,
            "completed_at": e.completed_at,
            "created_at": e.created_at
        })
    
    for f in fups:
        unified.append({
            "id": 1000000 + f.id,
            "job_id": f.job_id,
            "event_type": "follow_up_sent",
            "title": f"Follow-up #{f.follow_up_number} ({f.channel})",
            "description": f"Contacted {f.contact_name or 'N/A'}. Notes: {f.notes[:100] if f.notes else 'No notes'}",
            "old_value": None,
            "new_value": None,
            "source": "user",
            "scheduled_at": None,
            "completed_at": None,
            "created_at": f.sent_at
        })

    unified.sort(key=lambda x: x["created_at"], reverse=True)
    return unified


@app.post("/api/jobs/{job_id}/events", response_model=EventOut)
def add_event(job_id: int, event: EventCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_owned_job(db, job_id, current_user)
    db_event = ApplicationEvent(job_id=job_id, user_id=current_user.id, **event.model_dump())
    db.add(db_event)
    db.commit()
    db.refresh(db_event)
    return db_event


def _log_event(db, job_id, event_type, title, description=None,
               old_value=None, new_value=None, source="agent", user_id=None):
    ev = ApplicationEvent(
        job_id=job_id, user_id=user_id, event_type=event_type, title=title,
        description=description, old_value=old_value,
        new_value=new_value, source=source,
    )
    db.add(ev)
    db.commit()


# ── Resumes & Cover Letters ────────────────────────────────────────────────────

@app.get("/api/jobs/{job_id}/resumes", response_model=List[ResumeVersionOut])
def get_resumes(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_owned_job(db, job_id, current_user)
    return db.query(ResumeVersion).filter(ResumeVersion.job_id == job_id).order_by(ResumeVersion.created_at.desc()).all()


@app.get("/api/jobs/{job_id}/cover-letters", response_model=List[CoverLetterVersionOut])
def get_cover_letters(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_owned_job(db, job_id, current_user)
    return db.query(CoverLetterVersion).filter(CoverLetterVersion.job_id == job_id).order_by(CoverLetterVersion.created_at.desc()).all()


@app.patch("/api/jobs/{job_id}/resumes/{resume_id}")
def update_resume_name(job_id: int, resume_id: int, payload: Dict[str, Any] = {}, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_owned_job(db, job_id, current_user)
    rv = db.query(ResumeVersion).filter_by(id=resume_id, job_id=job_id).first()
    if not rv:
        raise HTTPException(status_code=404, detail="Resume not found")
    if "name" in payload:
        rv.name = payload["name"]
    db.commit()
    return {"status": "success"}


@app.patch("/api/jobs/{job_id}/cover-letters/{cl_id}")
def update_cover_letter_name(job_id: int, cl_id: int, payload: Dict[str, Any] = {}, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_owned_job(db, job_id, current_user)
    cl = db.query(CoverLetterVersion).filter_by(id=cl_id, job_id=job_id).first()
    if not cl:
        raise HTTPException(status_code=404, detail="Cover letter not found")
    if "name" in payload:
        cl.name = payload["name"]
    db.commit()
    return {"status": "success"}


@app.get("/api/download/{job_id}/{filename}")
def download_file(job_id: int, filename: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Download a generated document file (only for jobs the user owns)."""
    get_owned_job(db, job_id, current_user)
    # Guard against path traversal in filename
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    output_dir = Path(os.getenv("OUTPUT_DIR", "./output"))
    file_path = output_dir / str(job_id) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path), filename=filename)


# ── Settings (per-user) ─────────────────────────────────────────────────────────

@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = db.query(Settings).filter(Settings.user_id == current_user.id).all()
    # Skip internal caches (e.g. the insights narrative blob) — not user settings.
    result = {r.key: r.value for r in rows if r.key != _INSIGHTS_CACHE_KEY}
    # Mask secrets in response
    for k in result:
        if any(s in k for s in ["key", "token", "secret", "password"]):
            if result[k]:
                result[k] = "••••••••" + result[k][-4:] if len(result[k]) > 4 else "••••••••"
    return result


@app.post("/api/settings")
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    for item in payload.settings:
        # Don't save masked values back
        if item.value and "••••" in item.value:
            continue
        row = db.query(Settings).filter(
            Settings.key == item.key, Settings.user_id == current_user.id
        ).first()
        if row:
            row.value = item.value
            row.updated_at = datetime.utcnow()
        else:
            db.add(Settings(key=item.key, value=item.value, user_id=current_user.id))
    db.commit()
    return {"status": "saved"}


# ── Master Resume Components ──────────────────────────────────────────────────

@app.get("/api/settings/resume-components", response_model=List[ResumeComponentOut])
def list_resume_components(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """List the user's master resume components."""
    return db.query(MasterResumeComponent).filter(
        MasterResumeComponent.user_id == current_user.id
    ).order_by(MasterResumeComponent.order.asc()).all()


@app.post("/api/settings/resume-components/text", response_model=ResumeComponentOut)
def create_text_component(payload: ResumeComponentCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Add a manual text block to the master resume."""
    component = MasterResumeComponent(
        name=payload.name,
        type="text",
        content_text=payload.content_text,
        is_active=payload.is_active,
        order=payload.order,
        user_id=current_user.id,
    )
    db.add(component)
    db.commit()
    db.refresh(component)
    return component


@app.post("/api/settings/resume-components/file", response_model=ResumeComponentOut)
async def upload_resume_file(
    name: str = Query(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a PDF or Markdown file as a resume component."""
    from pdf_utils import extract_text_from_pdf
    
    file_content = await file.read()
    filename = file.filename
    content_text = ""
    
    # Extract text based on extension
    if filename.lower().endswith(".pdf"):
        content_text = extract_text_from_pdf(file_content)
    elif filename.lower().endswith(".html") or filename.lower().endswith(".htm"):
        raw = file_content.decode("utf-8", errors="ignore")
        content_text = _strip_html(raw)
    elif filename.lower().endswith(".md") or filename.lower().endswith(".txt"):
        raw = file_content.decode("utf-8", errors="ignore")
        content_text = _strip_html(raw) if ('<html' in raw.lower() or '<div' in raw.lower()) else raw
    else:
        raise HTTPException(status_code=400, detail="Unsupported file type. Upload PDF, HTML, Markdown, or TXT.")

    if not content_text:
        raise HTTPException(status_code=400, detail="Could not extract text from file.")

    # Save file to disk
    upload_dir = Path(os.getenv("OUTPUT_DIR", "./output")) / "master_resumes"
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / f"{datetime.utcnow().timestamp()}_{filename}"
    with open(file_path, "wb") as f:
        f.write(file_content)

    component = MasterResumeComponent(
        name=name,
        type="file",
        content_text=content_text,
        original_filename=filename,
        file_path=str(file_path),
        is_active=True,
        order=0,
        user_id=current_user.id,
    )
    db.add(component)
    db.commit()
    db.refresh(component)
    return component


@app.patch("/api/settings/resume-components/{id}", response_model=ResumeComponentOut)
def update_resume_component(id: int, payload: ResumeComponentUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Update a resume component (toggle active, change name, etc)."""
    component = db.query(MasterResumeComponent).filter(
        MasterResumeComponent.id == id, MasterResumeComponent.user_id == current_user.id
    ).first()
    if not component:
        raise HTTPException(status_code=404, detail="Component not found")

    if payload.name is not None: component.name = payload.name
    if payload.is_active is not None: component.is_active = payload.is_active
    if payload.order is not None: component.order = payload.order
    if payload.content_text is not None: component.content_text = payload.content_text

    db.commit()
    db.refresh(component)
    return component


@app.delete("/api/settings/resume-components/{id}")
def delete_resume_component(id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Delete a resume component."""
    component = db.query(MasterResumeComponent).filter(
        MasterResumeComponent.id == id, MasterResumeComponent.user_id == current_user.id
    ).first()
    if not component:
        raise HTTPException(status_code=404, detail="Component not found")

    # If it was a file, delete it from disk
    if component.type == "file" and component.file_path:
        p = Path(component.file_path)
        if p.exists():
            p.unlink()
            
    db.delete(component)
    db.commit()
    return {"status": "deleted"}


# ── Agent: Track Job from URL ──────────────────────────────────────────────────

@app.post("/api/agent/track-url")
async def track_url(payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Fetch a job URL, extract structured data using AI, and save it to DB.
    Body: { "url": "...", "llm": "gemini" }
    """
    url = payload.get("url", "").strip()
    llm = resolve_llm(db, current_user.id, payload.get("llm"))
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    try:
        # Retrieve additional settings for scraping (per-user)
        captcha_key = db.query(Settings).filter(Settings.key == "twocaptcha_api_key", Settings.user_id == current_user.id).first()
        proxy_url = db.query(Settings).filter(Settings.key == "proxy_url", Settings.user_id == current_user.id).first()

        agent = get_agent("discovery", db, current_user.id)
        data = await agent.analyze_url(
            url, 
            llm=llm, 
            captcha_key=captcha_key.value if captcha_key else None,
            proxy_url=proxy_url.value if proxy_url else None
        )
    except ValueError as e:
        # Known scraping/validation error (e.g. login wall, bot check)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Unexpected crash
        raise HTTPException(status_code=500, detail=f"Job discovery failed: {e}")

    # Map extracted data to DB fields
    job = Job(
        company=data.get("company", "Unknown"),
        title=data.get("title", "Unknown"),
        url=data.get("url") or url,
        job_description=data.get("job_description", ""),
        location=data.get("location", ""),
        remote=data.get("remote", False),
        salary_min=data.get("salary_min"),
        salary_max=data.get("salary_max"),
        platform=data.get("platform", "direct"),
        status="found",
        meta={"tech_stack": data.get("tech_stack", []), "seniority": data.get("seniority")},
        user_id=current_user.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    _log_event(db, job.id, "agent_action", "Job discovered via URL",
               f"Auto-extracted: {job.title} at {job.company}", source="agent", user_id=current_user.id)

    return {"job_id": job.id, "job": {
        "id": job.id, "company": job.company, "title": job.title,
        "status": job.status, "platform": job.platform, "remote": job.remote,
    }}




@app.post("/api/agent/track-jd")
async def track_jd_text(payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Extract job data from pasted JD text (for LinkedIn and login-walled sites).
    Body: { "text": "...", "llm": "gemini" }
    """
    text = payload.get("text", "").strip()
    llm = resolve_llm(db, current_user.id, payload.get("llm"))
    if not text or len(text) < 50:
        raise HTTPException(status_code=400, detail="Please paste the full job description text (at least 50 characters).")

    try:
        agent = get_agent("discovery", db, current_user.id)
        data = await agent.analyze_text(text, url="", llm=llm)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Job extraction failed: {e}")

    job = Job(
        company=data.get("company", "Unknown"),
        title=data.get("title", "Unknown"),
        url=data.get("url") or "",
        job_description=data.get("job_description") or text,
        location=data.get("location", ""),
        remote=data.get("remote", False),
        salary_min=data.get("salary_min"),
        salary_max=data.get("salary_max"),
        platform=data.get("platform", "linkedin"),
        status="found",
        meta={"tech_stack": data.get("tech_stack", []), "seniority": data.get("seniority")},
        user_id=current_user.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    _log_event(db, job.id, "agent_action", "Job added via pasted JD",
               f"Extracted: {job.title} at {job.company}", source="agent", user_id=current_user.id)

    return {"job_id": job.id, "job": {
        "id": job.id, "company": job.company, "title": job.title,
        "status": job.status, "platform": job.platform, "remote": job.remote,
    }}

# ── Agent: Fit Assessment ──────────────────────────────────────────────────────

async def _bg_analyze(job_id: int, llm: str, user_id: int):
    db = SessionLocal()
    try:
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="analyze").first()
        if not task:
            task = LLMTaskStatus(job_id=job_id, task_type="analyze", status="processing", user_id=user_id)
            db.add(task)
        else:
            task.status = "processing"
            task.error_message = None
        db.commit()

        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
        if not job: raise ValueError("Job not found")
        master_resume = get_master_resume(db, user_id)
        if not master_resume: raise ValueError("Master resume not found. Upload it in Settings → Resume Intelligence.")

        job.status = "analyzing"
        db.commit()

        agent = get_agent("fit", db, user_id)
        result = await agent.analyze(
            job_description=job.job_description or "",
            master_resume=master_resume,
            company=job.company,
            title=job.title,
            llm=llm,
        )

        job.match_score = result.get("match_score")
        job.strengths = result.get("strengths", [])
        job.gaps = result.get("gaps", [])
        job.action_items = result.get("action_items", [])
        job.status = "pending"

        db.add(ApplicationEvent(
            job_id=job_id, user_id=user_id, event_type="agent_action",
            title=f"Gap Analysis complete — {job.match_score}% match",
            description=f"{len(job.gaps or [])} gaps found", source="agent"
        ))

        try:
            cid = chat_store.log_interaction(
                user_input=f"Run gap analysis for {job.title} at {job.company}",
                agent_output=f"Score: {job.match_score}%, {len(job.gaps or [])} gaps found",
                action_type="gap_analysis", job_id=job_id, llm_used=llm, user_id=user_id,
                company=job.company, title=job.title,
            )
            db.add(ChatInteraction(chroma_id=cid, user_id=user_id, action_type="gap_analysis", job_id=job_id,
                                   company=job.company, title=job.title, llm_used=llm))
        except Exception as ce:
            logger.warning(f"[ChatStore] gap_analysis log failed: {ce}")

        task.status = "completed"
        db.commit()
    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)
        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
        if job and job.status == "analyzing":
            job.status = "found"
        db.commit()
    finally:
        db.close()


@app.post("/api/agent/analyze/{job_id}")
async def analyze_job(job_id: int, background_tasks: BackgroundTasks, payload: Dict[str, Any] = {}, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_owned_job(db, job_id, current_user)
    llm = resolve_llm(db, current_user.id, payload.get("llm"))
    background_tasks.add_task(_bg_analyze, job_id, llm, current_user.id)
    return {"status": "processing"}


# ── Agent: Generate Resume ─────────────────────────────────────────────────────

async def _bg_resume(job_id: int, llm: str, feedback: str, user_id: int, critic_llm: str = "gemini"):
    from doc_generator import save_resume
    import json as _json
    db = SessionLocal()
    try:
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="resume").first()
        if not task:
            task = LLMTaskStatus(job_id=job_id, task_type="resume", status="processing", user_id=user_id)
            db.add(task)
        else:
            task.status = "processing"
            task.error_message = None
        db.commit()

        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
        if not job:
            raise ValueError("Job not found")

        # ── Step 1: Extract contact facts from RAW content (no LLM, no hallucination) ──
        raw_content = get_raw_resume_content(db, user_id)
        if not raw_content:
            raise ValueError("Master resume not configured. Go to Settings → Resume Intelligence and upload your master resume.")
        contact_facts = extract_contact_info(raw_content)
        logger.info(f"[Resume] Contact facts extracted: {contact_facts}")

        # ── Step 2: Get structured markdown of full resume ──
        master_resume = get_master_resume(db, user_id)
        logger.info(f"[Resume] Master resume: {len(master_resume)} chars")

        # ── Step 3: Optional GitHub context (per-user keys) ──
        agent_resume = get_agent("resume", db, user_id)
        gh_token = agent_resume.router.github_token
        gh_user  = agent_resume.router.github_username
        github_ctx = ""
        if gh_token and gh_user:
            github_ctx = await agent_resume.fetch_github_context(gh_user, gh_token)

        # ── Step 4: Load user style guide and PDF style ──
        style_row = db.query(Settings).filter(Settings.key == "resume_style_guide", Settings.user_id == user_id).first()
        design_rules = style_row.value if style_row and style_row.value else ""

        pdf_style_row = db.query(Settings).filter(Settings.key == "resume_pdf_style", Settings.user_id == user_id).first()
        pdf_style = {}
        if pdf_style_row and pdf_style_row.value:
            try:
                pdf_style = _json.loads(pdf_style_row.value)
            except Exception:
                pass

        # ── Step 5: Single focused tailor call ──
        resume_md = await agent_resume.tailor(
            job_description=job.job_description or "",
            master_resume=master_resume,
            contact_facts=contact_facts,
            company=job.company,
            title=job.title,
            gaps=job.gaps or [],
            action_items=job.action_items or [],
            github_context=github_ctx,
            feedback=feedback,
            design_rules=design_rules,
            llm=llm,
        )

        # ── Step 6: Enforce header correctness post-LLM ──
        resume_md = agent_resume.enforce_header(resume_md, contact_facts)

        # ── Step 7: Style-guide enforcement pass (only if user provided rules) ──
        if design_rules.strip():
            resume_md = await agent_resume.validate_design(resume_md, design_rules, llm=llm)
            resume_md = agent_resume.enforce_header(resume_md, contact_facts)

        # ── Step 8: Save ──
        version = db.query(func.count(ResumeVersion.id)).filter(ResumeVersion.job_id == job_id).scalar() + 1
        paths = save_resume(job_id, version, resume_md, pdf_style=pdf_style)

        rv = ResumeVersion(
            job_id=job_id, user_id=user_id, version=version,
            content_md=resume_md,
            pdf_path=paths.get("pdf"),
            docx_path=paths.get("docx"),
            llm_used=llm,
            critic_notes=None,
        )
        db.add(rv)

        _log_event(db, job_id, "document_generated",
                   f"Resume v{version} generated",
                   f"Tailored via {llm}",
                   source="agent", user_id=user_id)

        try:
            cid = chat_store.log_interaction(
                user_input=f"Generate resume for {job.title} at {job.company}",
                agent_output=f"Resume v{version} generated via {llm}",
                action_type="resume", job_id=job_id, llm_used=llm, user_id=user_id,
                company=job.company, title=job.title,
            )
            db.add(ChatInteraction(chroma_id=cid, user_id=user_id, action_type="resume", job_id=job_id,
                                   company=job.company, title=job.title, llm_used=llm))
        except Exception as ce:
            logger.warning(f"[ChatStore] resume log failed: {ce}")

        task.status = "completed"
        db.commit()

        # ── Step 9: Auto ATS scoring (best-effort, runs after resume is ready) ──
        # Resume is already marked completed above, so the UI shows it immediately;
        # the ATS score fills in a bit later once the vendored scorer returns.
        try:
            from ats_score import score_resume_pdf
            pdf_path = paths.get("pdf")
            if pdf_path:
                _r = agent_resume.router
                ats = await asyncio.to_thread(
                    score_resume_pdf, pdf_path, llm,
                    _r.gemini_key, _r.nvidia_key, _r.github_token,
                )
                if ats:
                    rv.ats_score = ats
                    db.commit()
                    _log_event(db, job_id, "ats_scored",
                               f"Resume v{version} ATS score: {ats.get('total')}/100",
                               "Auto-evaluated via hiring-agent",
                               source="agent", user_id=user_id)
        except Exception as ats_err:
            logger.warning(f"[ATS] scoring failed for job {job_id}: {ats_err}")

    except Exception as e:
        logger.error(f"[ResumeGen] Failed for job {job_id}: {e}")
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="resume").first()
        if task:
            task.status = "failed"
            task.error_message = str(e)
            db.commit()
    finally:
        db.close()


@app.post("/api/agent/resume/chat")
async def resume_chat(payload: Dict[str, Any] = {}, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # NOTE: must be declared before the "/api/agent/resume/{job_id}" route below,
    # otherwise FastAPI matches "chat" as job_id and 422s on int parsing.
    job_id = payload.get("job_id")
    current_md = payload.get("current_md")
    instruction = payload.get("instruction")
    llm = resolve_llm(db, current_user.id, payload.get("llm"))

    if not job_id or not current_md or not instruction:
        raise HTTPException(status_code=400, detail="Missing required fields: job_id, current_md, instruction")

    get_owned_job(db, job_id, current_user)
    agent = get_agent("resume", db, current_user.id)

    try:
        updated_md = await agent.chat_edit(current_md, instruction, llm=llm)
        return {"updated_md": updated_md}
    except Exception as e:
        logger.error(f"[ResumeChat] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/agent/resume/{job_id}")
async def start_generate_resume(
    job_id: int,
    background_tasks: BackgroundTasks,
    payload: Dict[str, Any] = {},
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_job(db, job_id, current_user)
    llm = resolve_llm(db, current_user.id, payload.get("llm"))
    feedback = payload.get("feedback", "")
    critic_llm = payload.get("critic_llm", "claude")
    background_tasks.add_task(_bg_resume, job_id, llm, feedback, current_user.id, critic_llm)
    return {"status": "processing"}


@app.get("/api/agent/resume/{job_id}")
def get_resume_status(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_owned_job(db, job_id, current_user)
    task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="resume").first()
    latest = db.query(ResumeVersion).filter_by(job_id=job_id).order_by(ResumeVersion.version.desc()).first()

    if task and task.status == "processing":
        return {"status": "processing"}
    if latest:
        return {
            "status": "completed",
            "version": latest.version,
            "resume_md": latest.content_md,
            "pdf_url": f"/api/download/{job_id}/resume_v{latest.version}.pdf" if latest.pdf_path else None,
            "docx_url": f"/api/download/{job_id}/resume_v{latest.version}.docx" if latest.docx_path else None,
            "critic_notes": latest.critic_notes,
            "ats_score": latest.ats_score,
            "llm_used": latest.llm_used,
        }
    if task and task.status == "failed":
        return {"status": "failed", "error": task.error_message}
    return {"status": "none"}


@app.post("/api/agent/resume/{job_id}/save")
async def save_chat_resume(
    job_id: int,
    background_tasks: BackgroundTasks,
    payload: Dict[str, Any] = {},
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Saves the final chat-edited markdown to the DB and generates the PDF."""
    job = get_owned_job(db, job_id, current_user)
    final_md = payload.get("final_md")
    llm = payload.get("llm", "chat-editor")
    if not final_md:
        raise HTTPException(status_code=400, detail="Missing final_md")
        
    def _save_pdf():
        import json as _json
        db_session = SessionLocal()
        try:
            from doc_generator import save_resume
            latest = db_session.query(ResumeVersion).filter_by(job_id=job_id).order_by(ResumeVersion.version.desc()).first()
            version = 1 if not latest else latest.version + 1
            
            pdf_style_row = db_session.query(Settings).filter(Settings.key == "resume_pdf_style", Settings.user_id == current_user.id).first()
            pdf_style = {}
            if pdf_style_row and pdf_style_row.value:
                try:
                    pdf_style = _json.loads(pdf_style_row.value)
                except:
                    pass
                    
            pdf_path, docx_path = save_resume(job_id, version, final_md, pdf_style)
            
            rv = ResumeVersion(
                job_id=job_id, version=version, content_md=final_md,
                pdf_path=pdf_path, docx_path=docx_path, llm_used=llm
            )
            db_session.add(rv)
            db_session.commit()
            
            _log_event(db_session, job_id, "document_generated",
                       f"Resume v{version} saved via Chat Editor",
                       f"Saved by user", source="user", user_id=current_user.id)
        except Exception as e:
            logger.error(f"[ResumeChatSave] Failed to save chat resume: {e}")
        finally:
            db_session.close()

    background_tasks.add_task(_save_pdf)
    return {"status": "saving"}


# ── Agent: Generate Cover Letter ───────────────────────────────────────────────

async def _bg_cover_letter(job_id: int, llm: str, feedback: str, resume_version: int, user_id: int):
    db = SessionLocal()
    try:
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="cover_letter").first()
        if not task:
            task = LLMTaskStatus(job_id=job_id, task_type="cover_letter", status="processing", user_id=user_id)
            db.add(task)
        else:
            task.status = "processing"
            task.error_message = None
        db.commit()

        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
        if not job: raise ValueError("Job not found")

        q = db.query(ResumeVersion).filter(ResumeVersion.job_id == job_id)
        if resume_version: q = q.filter(ResumeVersion.version == resume_version)
        rv = q.order_by(ResumeVersion.created_at.desc()).first()

        master_resume = get_master_resume(db, user_id)
        if not rv and not master_resume:
            raise ValueError("Generate a resume first or upload your master resume in Settings → Resume Intelligence")

        agent_cover = get_agent("cover", db, user_id)
        cl_md = await agent_cover.generate(
            job_description=job.job_description or "",
            tailored_resume=rv.content_md if rv else master_resume,
            company=job.company,
            title=job.title,
            feedback=feedback,
            llm=llm,
        )

        from doc_generator import save_cover_letter
        from sqlalchemy import func
        version = db.query(func.count(CoverLetterVersion.id)).filter(CoverLetterVersion.job_id == job_id).scalar() + 1
        paths = save_cover_letter(job_id, version, cl_md)

        clv = CoverLetterVersion(
            job_id=job_id, user_id=user_id, version=version,
            content_md=cl_md, pdf_path=paths.get("pdf"),
            llm_used=llm
        )
        db.add(clv)

        db.add(ApplicationEvent(
            job_id=job_id, user_id=user_id, event_type="document_generated",
            title=f"Cover Letter v{version} generated", source="agent"
        ))

        try:
            cid = chat_store.log_interaction(
                user_input=f"Generate cover letter for {job.title} at {job.company}",
                agent_output=f"Cover letter v{version} generated",
                action_type="cover_letter", job_id=job_id, llm_used=llm, user_id=user_id,
                company=job.company, title=job.title,
            )
            db.add(ChatInteraction(chroma_id=cid, user_id=user_id, action_type="cover_letter", job_id=job_id,
                                   company=job.company, title=job.title,
                                   llm_used=llm))
        except Exception as ce:
            logger.warning(f"[ChatStore] cover_letter log failed: {ce}")

        task.status = "completed"
        db.commit()
    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)
        db.commit()
    finally:
        db.close()


@app.post("/api/agent/cover-letter/{job_id}")
async def generate_cover_letter(job_id: int, background_tasks: BackgroundTasks, payload: Dict[str, Any] = {}, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_owned_job(db, job_id, current_user)
    llm = resolve_llm(db, current_user.id, payload.get("llm"))
    feedback = payload.get("feedback", "")
    resume_version = payload.get("resume_version", None)
    background_tasks.add_task(_bg_cover_letter, job_id, llm, feedback, resume_version, current_user.id)
    return {"status": "processing"}


# ── Agent: LLM Comparison ──────────────────────────────────────────────────────

@app.post("/api/agent/compare/{job_id}")
async def compare_llms(job_id: int, payload: Dict[str, Any] = {}, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Fan out resume or cover letter generation to multiple LLMs.
    Body: { "task": "resume|cover_letter", "providers": ["gemini", "groq"] }
    """
    job = get_owned_job(db, job_id, current_user)

    task = (payload or {}).get("task", "resume")
    providers = (payload or {}).get("providers", ["gemini", "groq"])
    master_resume = get_master_resume(db, current_user.id)

    router = get_llm_router(db, current_user.id)

    if task == "resume":
        from agents import SYSTEM_RESUME_AGENT
        prompt = f"""Tailor this resume for: {job.title} at {job.company}

Job Description:
{(job.job_description or '')[:2500]}

Master Resume:
{master_resume[:2500]}

Gaps to address: {json.dumps(job.gaps or [])}
Action items: {json.dumps(job.action_items or [])}

Return a tailored resume in markdown."""
        system = SYSTEM_RESUME_AGENT
    else:
        # Cover letter
        rv = db.query(ResumeVersion).filter(ResumeVersion.job_id == job_id).order_by(ResumeVersion.created_at.desc()).first()
        from agents import SYSTEM_COVER_LETTER_AGENT
        prompt = f"""Write a cover letter for: {job.title} at {job.company}

Job: {(job.job_description or '')[:2000]}
Resume: {rv.content_md[:2000] if rv else master_resume[:2000]}

Return cover letter markdown only."""
        system = SYSTEM_COVER_LETTER_AGENT

    results = await router.compare(prompt, providers=providers, system=system)

    # Save comparison
    comp = LLMComparison(job_id=job_id, user_id=current_user.id, prompt_type=task, results=results)
    db.add(comp)
    db.commit()

    _log_event(db, job_id, "agent_action",
               f"LLM Comparison: {task}",
               f"Compared {len(providers)} providers: {', '.join(providers)}", source="agent",
               user_id=current_user.id)

    return {"task": task, "results": results}


# ── Agent: Floating Chat ───────────────────────────────────────────────────────

@app.post("/api/chat")
async def chat(msg: ChatMessage, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Global floating chat — routes natural language to agents (scoped to the current user).
    """
    llm = getattr(msg, "llm", "gemini") or "gemini"
    context = getattr(msg, "context", None)
    uid = current_user.id

    try:
        dispatcher = get_agent("chat", db, uid)
        action = await dispatcher.dispatch(msg.message, context=context, llm=llm)
    except Exception as e:
        logger.warning(f"[Chat] Dispatch failed: {e}")
        return ChatResponse(reply=f"Sorry, I hit an error: {e}")

    action_type = action.get("action", "general_reply")
    result_data = None

    def _owned(jid):
        return bool(jid) and _owns_job(db, jid, uid)

    # Execute the dispatched action
    try:
        if action_type == "track_job":
            url = action.get("url", "")
            if url:
                result_data = await track_url({"url": url, "llm": llm}, db=db, current_user=current_user)
                reply = f"✅ Added **{result_data['job']['title']}** at **{result_data['job']['company']}** to your pipeline! (Job #{result_data['job_id']})"
            else:
                reply = "I need the job URL. Paste it and I'll add it for you!"

        elif action_type == "analyze_job":
            job_id = action.get("job_id")
            if _owned(job_id):
                asyncio.create_task(_bg_analyze(job_id, llm, uid))
                reply = f"🤖 Gap analysis running for job #{job_id} — check back in a moment or watch the timeline."
            elif job_id:
                reply = "I couldn't find that job in your pipeline."
            else:
                reply = "Which job should I analyze? Share the job ID or company name."

        elif action_type == "generate_resume":
            job_id = action.get("job_id")
            if _owned(job_id):
                asyncio.create_task(_bg_resume(job_id, llm, "", uid, "claude"))
                reply = f"📄 Resume generation started for job #{job_id} — check the Resumes tab shortly."
            elif job_id:
                reply = "I couldn't find that job in your pipeline."
            else:
                reply = "Which job? Give me the job ID or company name."

        elif action_type == "generate_cover_letter":
            job_id = action.get("job_id")
            if _owned(job_id):
                asyncio.create_task(_bg_cover_letter(job_id, llm, "", 0, uid))
                reply = f"✉️ Cover letter generation started for job #{job_id} — check the Cover Letters tab shortly."
            elif job_id:
                reply = "I couldn't find that job in your pipeline."
            else:
                reply = "Which job? Give me the job ID."

        elif action_type == "evaluate_job_structured":
            job_id = action.get("job_id")
            if _owned(job_id):
                asyncio.create_task(_bg_evaluate(job_id, llm, uid))
                reply = f"⚡ A-G Evaluation running for job #{job_id} — the full 7-block report will appear in the Evaluation tab."
            elif job_id:
                reply = "I couldn't find that job in your pipeline."
            else:
                reply = "Which job should I evaluate?"

        elif action_type == "linkedin_outreach":
            job_id = action.get("job_id")
            contact_type = action.get("contact_type", "recruiter")
            if _owned(job_id):
                asyncio.create_task(_bg_linkedin(job_id, contact_type, llm, uid))
                reply = f"💼 LinkedIn outreach generation started for job #{job_id} (targeting: {contact_type}) — check the LinkedIn tab."
            elif job_id:
                reply = "I couldn't find that job in your pipeline."
            else:
                reply = "Which job do you need LinkedIn outreach for?"

        elif action_type == "deep_research":
            job_id = action.get("job_id")
            if _owned(job_id):
                asyncio.create_task(_bg_deep_research(job_id, llm, uid))
                reply = f"🔍 Deep research running for job #{job_id} — results will appear in the Research tab."
            elif job_id:
                reply = "I couldn't find that job in your pipeline."
            else:
                reply = "Which company/job should I research?"

        elif action_type == "interview_prep":
            job_id = action.get("job_id")
            if _owned(job_id):
                asyncio.create_task(_bg_interview_prep(job_id, llm, uid))
                reply = f"🎤 Interview prep running for job #{job_id} — STAR stories will appear in the Interview Prep tab."
            elif job_id:
                reply = "I couldn't find that job in your pipeline."
            else:
                reply = "Which job are you prepping for?"

        elif action_type == "get_stats":
            stats = get_stats(db=db, current_user=current_user)
            reply = f"📊 You have **{stats.total}** jobs tracked — {stats.by_status.get('applied', 0)} applied, {stats.interviews_scheduled} interviews, {stats.offers} offers. {stats.follow_ups_due} follow-ups due!"

        elif action_type == "list_jobs":
            filt = action.get("filter")
            jobs = list_jobs(status=filt, limit=5, db=db, current_user=current_user)
            if jobs:
                lines = "\n".join(f"• **{j.company}** — {j.title} (#{j.id}, {j.status})" for j in jobs)
                reply = f"Here are your {'recent' if not filt else filt} jobs:\n\n{lines}"
            else:
                reply = f"No jobs found{' with status ' + filt if filt else ''}."

        elif action_type == "apply_job":
            job_id = action.get("job_id")
            if _owned(job_id):
                reply = f"🚀 To protect you, the Application Agent requires you to click **Approve & Apply** in the Job Detail view (Job #{job_id}). Open it now?"
            elif job_id:
                reply = "I couldn't find that job in your pipeline."
            else:
                reply = "Which job should I apply to? Share the job ID."

        else:
            reply = action.get("text", "How can I help you with your job search? I can track jobs, run gap analysis, generate resumes and cover letters, or apply to jobs.")

    except HTTPException as e:
        reply = f"⚠️ {e.detail}"
    except Exception as e:
        logger.error(f"[Chat] Action execution failed: {e}")
        reply = f"Something went wrong: {e}"

    # Log to ChromaDB + SQLite (scoped to user)
    try:
        job_id_for_log = action.get("job_id")
        job_for_log = db.query(Job).filter(Job.id == job_id_for_log, Job.user_id == uid).first() if job_id_for_log else None
        chroma_id = chat_store.log_interaction(
            user_input=msg.message,
            agent_output=reply,
            action_type=action_type if action_type != "general_reply" else "chat",
            job_id=job_id_for_log,
            llm_used=llm,
            company=job_for_log.company if job_for_log else "",
            title=job_for_log.title if job_for_log else "",
            user_id=uid,
        )
        db.add(ChatInteraction(
            chroma_id=chroma_id,
            user_id=uid,
            action_type=action_type if action_type != "general_reply" else "chat",
            job_id=job_id_for_log,
            company=job_for_log.company if job_for_log else "",
            title=job_for_log.title if job_for_log else "",
            llm_used=llm,
        ))
        db.commit()
    except Exception as e:
        logger.warning(f"[ChatStore] Chat log failed (non-fatal): {e}")

    return ChatResponse(reply=reply, action_taken=action_type)


# ── Export ─────────────────────────────────────────────────────────────────────

@app.get("/api/export/csv")
def export_csv(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    jobs = db.query(Job).filter(Job.user_id == current_user.id).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Company", "Title", "Status", "Platform", "Location",
        "Remote", "Salary Min", "Salary Max", "Match Score",
        "Listed At", "Applied At", "Last Contact", "Follow Up Due",
        "Recruiter", "Priority", "URL", "Created At"
    ])
    for j in jobs:
        writer.writerow([
            j.id, j.company, j.title, j.status, j.platform, j.location,
            j.remote, j.salary_min, j.salary_max, j.match_score,
            j.listed_at, j.applied_at, j.last_contact_at, j.follow_up_due,
            j.recruiter_name, j.priority, j.url, j.created_at
        ])
    output.seek(0)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=job_applications.csv"}
    )


# ── LLM Providers Status ───────────────────────────────────────────────────────

@app.get("/api/llm/providers")
def get_providers(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    router = get_llm_router(db, current_user.id)
    return {"available": router.available_providers()}


# ══════════════════════════════════════════════════════════════════════════════
# CAREER-OPS ENHANCED ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

# ── Full A-G Structured Evaluation ─────────────────────────────────────────────

async def _bg_evaluate(job_id: int, llm: str, user_id: int):
    db = SessionLocal()
    try:
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="evaluate").first()
        if not task:
            task = LLMTaskStatus(job_id=job_id, task_type="evaluate", status="processing", user_id=user_id)
            db.add(task)
        else:
            task.status = "processing"
            task.error_message = None
        db.commit()

        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
        if not job:
            raise ValueError("Job not found")

        master_resume = get_master_resume(db, user_id)
        if not master_resume:
            raise ValueError("Set your master resume in Settings → Resume Intelligence first")

        agent = get_agent("fit", db, user_id)
        result = await agent.analyze(
            job_description=job.job_description or "",
            master_resume=master_resume,
            company=job.company,
            title=job.title,
            llm=llm,
        )

        # Save evaluation report to DB
        report = EvaluationReport(
            job_id=job_id,
            user_id=user_id,
            block_a_role_summary=result.get("block_a_role_summary"),
            block_b_cv_match=result.get("block_b_cv_match"),
            block_c_level_strategy=result.get("block_c_level_strategy"),
            block_d_comp_demand=result.get("block_d_comp_demand"),
            block_e_personalization=result.get("block_e_personalization"),
            block_f_interview_prep=result.get("block_f_interview_prep"),
            block_g_legitimacy=result.get("block_g_legitimacy"),
            global_score=result.get("global_score"),
            llm_used=llm,
        )
        db.add(report)

        # Update job fields
        job.match_score = result.get("match_score", job.match_score)
        job.strengths = result.get("strengths", job.strengths)
        job.gaps = result.get("gaps", job.gaps)
        job.action_items = result.get("action_items", job.action_items)
        job.status = "analyzing" if job.status == "found" else job.status

        # Set archetype and legitimacy from evaluation
        block_a = result.get("block_a_role_summary", {})
        if block_a:
            job.archetype = block_a.get("archetype")
        block_g = result.get("block_g_legitimacy", {})
        if block_g:
            job.posting_legitimacy = block_g.get("tier")

        # Auto-save STAR stories to story bank
        block_f = result.get("block_f_interview_prep", {})
        if block_f and "star_stories" in block_f:
            for story in block_f["star_stories"]:
                entry = StoryBankEntry(
                    job_id=job_id,
                    user_id=user_id,
                    title=story.get("title", "Untitled"),
                    jd_requirement=story.get("jd_requirement"),
                    situation=story.get("situation"),
                    task=story.get("task"),
                    action=story.get("action"),
                    result=story.get("result"),
                    reflection=story.get("reflection"),
                    tags=story.get("tags", []),
                )
                db.add(entry)

        # Log event
        db.add(ApplicationEvent(
            job_id=job_id,
            user_id=user_id,
            event_type="agent_action",
            title="Full A-G Evaluation Completed",
            description=f"Global score: {result.get('global_score', 'N/A')}/100 | Archetype: {block_a.get('archetype', 'N/A')}",
            source="agent",
        ))

        try:
            cid = chat_store.log_interaction(
                user_input=f"A-G evaluation for {job.title} at {job.company}",
                agent_output=f"Score: {result.get('global_score', '?')}/100 | Archetype: {block_a.get('archetype', '?')}",
                action_type="evaluation", job_id=job_id, llm_used=llm, user_id=user_id,
                company=job.company, title=job.title,
            )
            db.add(ChatInteraction(chroma_id=cid, user_id=user_id, action_type="evaluation", job_id=job_id,
                                   company=job.company, title=job.title,
                                   llm_used=llm))
        except Exception as ce:
            logger.warning(f"[ChatStore] evaluation log failed: {ce}")

        task.status = "completed"
        db.commit()
    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)
        db.commit()
    finally:
        db.close()


@app.post("/api/agent/evaluate/{job_id}")
async def start_evaluate_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    llm: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_job(db, job_id, current_user)
    llm = resolve_llm(db, current_user.id, llm)
    background_tasks.add_task(_bg_evaluate, job_id, llm, current_user.id)
    return {"status": "processing"}


@app.get("/api/evaluations/{job_id}", response_model=List[EvaluationReportOut])
def get_evaluations(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Get all evaluation reports for a job."""
    get_owned_job(db, job_id, current_user)
    return db.query(EvaluationReport).filter(
        EvaluationReport.job_id == job_id
    ).order_by(EvaluationReport.created_at.desc()).all()


# ── LinkedIn Outreach ──────────────────────────────────────────────────────────

async def _bg_linkedin(job_id: int, contact_type: str, llm: str, user_id: int):
    db = SessionLocal()
    try:
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="linkedin").first()
        if not task:
            task = LLMTaskStatus(job_id=job_id, task_type="linkedin", status="processing", user_id=user_id)
            db.add(task)
        else:
            task.status = "processing"
            task.error_message = None
        db.commit()

        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
        if not job:
            raise ValueError("Job not found")

        master_resume = get_master_resume(db, user_id)
        if not master_resume:
            raise ValueError("Set your master resume in Settings → Resume Intelligence first")

        agent = get_agent("linkedin", db, user_id)
        result = await agent.generate(
            job_description=job.job_description or "",
            master_resume=master_resume,
            company=job.company,
            title=job.title,
            contact_type=contact_type,
            llm=llm,
        )

        db.add(ApplicationEvent(
            job_id=job_id,
            user_id=user_id,
            event_type="agent_action",
            title="LinkedIn Outreach Generated",
            description=f"Contact type: {contact_type}",
            source="agent",
        ))

        report = db.query(LinkedInOutreachReport).filter_by(job_id=job_id).first()
        if not report:
            report = LinkedInOutreachReport(job_id=job_id, user_id=user_id)
            db.add(report)
        report.outreach_data = result
        report.llm_used = llm

        try:
            cid = chat_store.log_interaction(
                user_input=f"LinkedIn outreach ({contact_type}) for {job.title} at {job.company}",
                agent_output=f"Messages generated for {contact_type}",
                action_type="linkedin", job_id=job_id, llm_used=llm, user_id=user_id,
                company=job.company, title=job.title,
            )
            db.add(ChatInteraction(chroma_id=cid, user_id=user_id, action_type="linkedin", job_id=job_id,
                                   company=job.company, title=job.title,
                                   llm_used=llm))
        except Exception as ce:
            logger.warning(f"[ChatStore] linkedin log failed: {ce}")

        task.status = "completed"
        db.commit()
    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)
        db.commit()
    finally:
        db.close()


@app.post("/api/agent/linkedin/{job_id}")
async def start_linkedin_outreach(
    job_id: int,
    background_tasks: BackgroundTasks,
    contact_type: str = "hiring_manager",
    payload: Dict[str, Any] = {},
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_job(db, job_id, current_user)
    llm = resolve_llm(db, current_user.id, payload.get("llm"))
    if "contact_type" in payload:
        contact_type = payload["contact_type"]
    background_tasks.add_task(_bg_linkedin, job_id, contact_type, llm, current_user.id)
    return {"status": "processing"}


@app.get("/api/agent/linkedin/{job_id}")
def get_linkedin_outreach(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_owned_job(db, job_id, current_user)
    task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="linkedin").first()
    report = db.query(LinkedInOutreachReport).filter_by(job_id=job_id).first()
    
    if task and task.status == "processing":
        return {"status": "processing"}
    if report:
        return {"status": "completed", "outreach": report.outreach_data, "llm_used": report.llm_used}
    if task and task.status == "failed":
        return {"status": "failed", "error": task.error_message}
        
    return {"status": "none"}


# ── Deep Company Research ──────────────────────────────────────────────────────

async def _bg_deep_research(job_id: int, llm: str, user_id: int):
    db = SessionLocal()
    try:
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="research").first()
        if not task:
            task = LLMTaskStatus(job_id=job_id, task_type="research", status="processing", user_id=user_id)
            db.add(task)
        else:
            task.status = "processing"
            task.error_message = None
        db.commit()

        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
        if not job:
            raise ValueError("Job not found")

        master_resume = get_master_resume(db, user_id)

        agent = get_agent("deep_research", db, user_id)
        result = await agent.research(
            company=job.company,
            title=job.title,
            job_description=job.job_description or "",
            master_resume=master_resume or "",
            llm=llm,
        )

        db.add(ApplicationEvent(
            job_id=job_id,
            user_id=user_id,
            event_type="agent_action",
            title="Deep Company Research Completed",
            description=f"6-axis research for {job.company}",
            source="agent",
        ))

        report = db.query(ResearchReport).filter_by(job_id=job_id).first()
        if not report:
            report = ResearchReport(job_id=job_id, user_id=user_id)
            db.add(report)
        report.research_data = result
        report.llm_used = llm

        try:
            cid = chat_store.log_interaction(
                user_input=f"Deep research for {job.title} at {job.company}",
                agent_output=f"6-axis research completed for {job.company}",
                action_type="research", job_id=job_id, llm_used=llm, user_id=user_id,
                company=job.company, title=job.title,
            )
            db.add(ChatInteraction(chroma_id=cid, user_id=user_id, action_type="research", job_id=job_id,
                                   company=job.company, title=job.title, llm_used=llm))
        except Exception as ce:
            logger.warning(f"[ChatStore] research log failed: {ce}")

        task.status = "completed"
        db.commit()
    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)
        db.commit()
    finally:
        db.close()


@app.post("/api/agent/deep-research/{job_id}")
async def start_deep_research(
    job_id: int,
    background_tasks: BackgroundTasks,
    payload: Dict[str, Any] = {},
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_job(db, job_id, current_user)
    llm = resolve_llm(db, current_user.id, payload.get("llm"))
    background_tasks.add_task(_bg_deep_research, job_id, llm, current_user.id)
    return {"status": "processing"}


@app.get("/api/agent/deep-research/{job_id}")
def get_deep_research(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_owned_job(db, job_id, current_user)
    task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="research").first()
    report = db.query(ResearchReport).filter_by(job_id=job_id).first()
    
    if task and task.status == "processing":
        return {"status": "processing"}
    if report:
        return {"status": "completed", "research": report.research_data, "llm_used": report.llm_used}
    if task and task.status == "failed":
        return {"status": "failed", "error": task.error_message}
        
    return {"status": "none"}


# ── Interview Prep ─────────────────────────────────────────────────────────────

async def _bg_interview_prep(job_id: int, llm: str, user_id: int):
    db = SessionLocal()
    try:
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="interview_prep").first()
        if not task:
            task = LLMTaskStatus(job_id=job_id, task_type="interview_prep", status="processing", user_id=user_id)
            db.add(task)
        else:
            task.status = "processing"
            task.error_message = None
        db.commit()

        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
        if not job:
            raise ValueError("Job not found")

        master_resume = get_master_resume(db, user_id)
        if not master_resume:
            raise ValueError("Set your master resume in Settings → Resume Intelligence first")

        existing = db.query(StoryBankEntry).filter(StoryBankEntry.job_id == job_id).all()
        existing_dicts = [{"title": s.title, "jd_requirement": s.jd_requirement} for s in existing]

        agent = get_agent("interview_prep", db, user_id)
        result = await agent.generate_stories(
            job_description=job.job_description or "",
            master_resume=master_resume,
            company=job.company,
            title=job.title,
            existing_stories=existing_dicts,
            llm=llm,
        )

        saved_count = 0
        if "stories" in result:
            for story in result["stories"]:
                entry = StoryBankEntry(
                    job_id=job_id,
                    user_id=user_id,
                    title=story.get("title", "Untitled"),
                    jd_requirement=story.get("jd_requirement"),
                    situation=story.get("situation"),
                    task=story.get("task"),
                    action=story.get("action"),
                    result=story.get("result"),
                    reflection=story.get("reflection"),
                    tags=story.get("tags", []),
                )
                db.add(entry)
                saved_count += 1

        db.add(ApplicationEvent(
            job_id=job_id,
            user_id=user_id,
            event_type="agent_action",
            title="Interview Prep Generated",
            description=f"{saved_count} STAR+Reflection stories created",
            source="agent",
        ))

        report = db.query(InterviewPrepReport).filter_by(job_id=job_id).first()
        if not report:
            report = InterviewPrepReport(job_id=job_id, user_id=user_id)
            db.add(report)
        report.prep_data = result
        report.llm_used = llm

        try:
            cid = chat_store.log_interaction(
                user_input=f"Interview prep for {job.title} at {job.company}",
                agent_output=f"{saved_count} STAR stories generated",
                action_type="interview_prep", job_id=job_id, llm_used=llm, user_id=user_id,
                company=job.company, title=job.title,
            )
            db.add(ChatInteraction(chroma_id=cid, user_id=user_id, action_type="interview_prep", job_id=job_id,
                                   company=job.company, title=job.title,
                                   llm_used=llm))
        except Exception as ce:
            logger.warning(f"[ChatStore] interview_prep log failed: {ce}")

        task.status = "completed"
        db.commit()
    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)
        db.commit()
    finally:
        db.close()

@app.post("/api/agent/interview-prep/{job_id}")
async def start_interview_prep(
    job_id: int,
    background_tasks: BackgroundTasks,
    payload: Dict[str, Any] = {},
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_job(db, job_id, current_user)
    llm = resolve_llm(db, current_user.id, payload.get("llm"))
    background_tasks.add_task(_bg_interview_prep, job_id, llm, current_user.id)
    return {"status": "processing"}

@app.get("/api/agent/interview-prep/{job_id}")
def get_interview_prep(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_owned_job(db, job_id, current_user)
    task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="interview_prep").first()
    report = db.query(InterviewPrepReport).filter_by(job_id=job_id).first()
    
    if task and task.status == "processing":
        return {"status": "processing"}
    if report:
        return {"status": "completed", "prep": report.prep_data, "llm_used": report.llm_used}
    if task and task.status == "failed":
        return {"status": "failed", "error": task.error_message}
        
    return {"status": "none"}


# ── Story Bank ─────────────────────────────────────────────────────────────────

@app.get("/api/story-bank", response_model=List[StoryBankEntryOut])
def get_story_bank(
    tag: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the user's STAR+Reflection stories from the persistent story bank."""
    q = db.query(StoryBankEntry).filter(
        StoryBankEntry.user_id == current_user.id
    ).order_by(StoryBankEntry.times_used.desc())
    if tag:
        # Filter stories by tag (JSON array contains)
        q = q.filter(StoryBankEntry.tags.contains(tag))
    return q.all()


@app.post("/api/story-bank", response_model=StoryBankEntryOut)
def add_story(
    story: StoryBankEntryCreate,
    job_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually add a story to the bank."""
    if job_id is not None:
        get_owned_job(db, job_id, current_user)
    entry = StoryBankEntry(
        job_id=job_id,
        user_id=current_user.id,
        title=story.title,
        jd_requirement=story.jd_requirement,
        situation=story.situation,
        task=story.task,
        action=story.action,
        result=story.result,
        reflection=story.reflection,
        tags=story.tags or [],
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


# ── Follow-Up Cadence ──────────────────────────────────────────────────────────

@app.get("/api/analytics/followups")
def get_followup_cadence(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Get follow-up cadence dashboard with urgency classifications."""
    from agents import FollowUpCadenceEngine

    # Get all jobs in actionable statuses (for this user)
    actionable_statuses = ["applied", "screening", "interview_1", "interview_2", "responded"]
    jobs = db.query(Job).filter(Job.user_id == current_user.id, Job.status.in_(actionable_statuses)).all()

    now = datetime.utcnow()
    entries = []

    for job in jobs:
        applied_date = job.applied_at or job.created_at
        days_since_applied = (now - applied_date).days if applied_date else 0

        # Get follow-up history
        followups = db.query(FollowUpLog).filter(
            FollowUpLog.job_id == job.id
        ).order_by(FollowUpLog.sent_at.desc()).all()

        followup_count = len(followups)
        last_followup_date = followups[0].sent_at if followups else None
        days_since_last = (now - last_followup_date).days if last_followup_date else None

        urgency = FollowUpCadenceEngine.compute_urgency(
            status=job.status,
            days_since_applied=days_since_applied,
            days_since_last_followup=days_since_last,
            followup_count=followup_count,
        )

        next_date = FollowUpCadenceEngine.next_followup_date(
            status=job.status,
            applied_date=applied_date,
            last_followup_date=last_followup_date,
            followup_count=followup_count,
        )

        entries.append({
            "job_id": job.id,
            "company": job.company,
            "title": job.title,
            "status": job.status,
            "days_since_applied": days_since_applied,
            "followup_count": followup_count,
            "urgency": urgency,
            "next_followup_date": next_date,
        })

    # Sort by urgency
    urgency_order = {"urgent": 0, "overdue": 1, "waiting": 2, "cold": 3}
    entries.sort(key=lambda e: urgency_order.get(e["urgency"], 9))

    return {
        "total_tracked": len(jobs),
        "actionable": len(entries),
        "overdue": sum(1 for e in entries if e["urgency"] == "overdue"),
        "urgent": sum(1 for e in entries if e["urgency"] == "urgent"),
        "cold": sum(1 for e in entries if e["urgency"] == "cold"),
        "entries": entries,
    }


@app.post("/api/followups/{job_id}", response_model=FollowUpLogOut)
def log_followup(
    job_id: int,
    followup: FollowUpLogCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Log a follow-up action for a job."""
    job = get_owned_job(db, job_id, current_user)

    # Calculate follow-up number
    existing_count = db.query(FollowUpLog).filter(
        FollowUpLog.job_id == job_id
    ).count()

    entry = FollowUpLog(
        job_id=job_id,
        user_id=current_user.id,
        follow_up_number=existing_count + 1,
        channel=followup.channel,
        contact_name=followup.contact_name,
        contact_email=followup.contact_email,
        notes=followup.notes,
    )
    db.add(entry)

    # Update job's last_contact_at
    job.last_contact_at = datetime.utcnow()

    # Log event
    db.add(ApplicationEvent(
        job_id=job_id,
        user_id=current_user.id,
        event_type="follow_up_sent",
        title=f"Follow-up #{existing_count + 1} Sent",
        description=f"Channel: {followup.channel}" + (f" | Contact: {followup.contact_name}" if followup.contact_name else ""),
        source="user",
    ))

    db.commit()
    db.refresh(entry)
    return entry


@app.get("/api/followups/{job_id}", response_model=List[FollowUpLogOut])
def get_followups(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Get follow-up history for a job."""
    get_owned_job(db, job_id, current_user)
    return db.query(FollowUpLog).filter(
        FollowUpLog.job_id == job_id
    ).order_by(FollowUpLog.sent_at.desc()).all()


# ── Pattern Analytics ──────────────────────────────────────────────────────────

@app.get("/api/analytics/patterns")
def get_pattern_analytics(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Run rejection pattern analysis across the user's tracked jobs."""
    from agents import PatternAnalyticsEngine

    jobs = db.query(Job).filter(Job.user_id == current_user.id).all()
    return PatternAnalyticsEngine.analyze(jobs)


# ── Insights Dashboard ─────────────────────────────────────────────────────────

_INSIGHTS_CACHE_KEY = "insights_narrative"


@app.get("/api/insights")
async def get_insights(
    llm: str = "",
    days: int = 90,
    refresh: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Stats + LLM-generated narrative on job hunt activity (per user).

    Stats are recomputed fresh every call (cheap DB aggregation). The narrative
    is LLM-generated and expensive, so it's persisted in Settings and only
    regenerated when the client asks (?refresh=true) or no cache exists yet.
    """
    uid = current_user.id
    stats = chat_store.get_stats(days=days, user_id=uid)

    cache_row = db.query(Settings).filter(
        Settings.key == _INSIGHTS_CACHE_KEY, Settings.user_id == uid
    ).first()

    # Serve the cached narrative unless the caller forces a refresh.
    if not refresh and cache_row and cache_row.value:
        try:
            cached = json.loads(cache_row.value)
            return {
                "stats": stats,
                "narrative": cached.get("narrative"),
                "generated_at": cached.get("generated_at"),
            }
        except (ValueError, TypeError):
            pass  # corrupt cache → fall through and regenerate

    llm = resolve_llm(db, uid, llm)
    samples = chat_store.get_recent_samples(days=days, limit=20, user_id=uid)

    narrative = None
    try:
        agent = get_agent("insights", db, uid)
        narrative = await agent.generate_narrative(stats, samples, llm=llm)
    except Exception as e:
        logger.warning(f"[Insights] Narrative generation failed: {e}")
        # Don't cache failures — leave any prior good narrative in place.
        return {
            "stats": stats,
            "narrative": {
                "summary": "Could not generate narrative.",
                "what_youre_doing": "",
                "momentum_score": 0,
                "momentum_rationale": str(e),
                "gaps": "",
                "top_3_recommendations": [],
                "warning": None,
            },
            "generated_at": None,
        }

    generated_at = datetime.utcnow().isoformat()
    payload = json.dumps({"narrative": narrative, "generated_at": generated_at})
    if cache_row:
        cache_row.value = payload
        cache_row.updated_at = datetime.utcnow()
    else:
        db.add(Settings(key=_INSIGHTS_CACHE_KEY, value=payload, user_id=uid))
    db.commit()

    return {
        "stats": stats,
        "narrative": narrative,
        "generated_at": generated_at,
    }


@app.get("/api/insights/history", response_model=List[ChatInteractionOut])
def get_interaction_history(
    page: int = 1,
    limit: int = 50,
    action_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Paginated interaction history from SQLite (per user)."""
    q = db.query(ChatInteraction).filter(
        ChatInteraction.user_id == current_user.id
    ).order_by(ChatInteraction.created_at.desc())
    if action_type:
        q = q.filter(ChatInteraction.action_type == action_type)
    return q.offset((page - 1) * limit).limit(limit).all()


@app.get("/api/insights/search")
def search_interactions(q: str, n: int = 10, days: int = 90, current_user: User = Depends(get_current_user)):
    """Semantic search over the user's interaction history using ChromaDB."""
    if not q.strip():
        raise HTTPException(400, "Query cannot be empty")
    results = chat_store.search_similar(q, n=n, days=days, user_id=current_user.id)
    return {"query": q, "results": results}


# ── Serve React frontend (production) ─────────────────────────────────────────
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="frontend")
