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
from sqlalchemy import func, text, or_, and_
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Any, Dict
import os
import re
import sys
import csv
import io
import json
import asyncio
from pathlib import Path
from urllib.parse import urlencode
import shutil
import imaplib
import httpx
from jose import JWTError, jwt
import bcrypt as _bcrypt
from pydantic import BaseModel as _BaseModel

from database import (
    init_db, get_db, SessionLocal, Job, ApplicationEvent, ApplicationFormEntry, ResumeVersion,
    CoverLetterVersion, Settings, LLMComparison,
    EvaluationReport, StoryBankEntry, FollowUpLog,
    MasterResumeComponent, ChatInteraction, User,
    LLMTaskStatus, ResearchReport, LinkedInOutreachReport, InterviewPrepReport,
)
import billing
from async_bus import AsyncBusMiddleware
import resume_profile
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
import connectors
import apply_queue as aq
import linkedin_guest
from search import router as search_router
from dotenv import load_dotenv
from loguru import logger

load_dotenv()
import telegram_bot   # after load_dotenv: reads TELEGRAM_BOT_TOKEN at import
import discord_bot    # same, DISCORD_BOT_TOKEN

# ── Logging ───────────────────────────────────────────────────────────────────
# loguru was imported in eight modules but never configured, so everything went
# to stderr and nothing survived a restart. diagnose=False is not optional here:
# diagnose=True writes local variables into the traceback, and the locals around
# these call sites hold API keys.
LOG_DIR = Path(os.getenv("LOG_DIR", "./logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
logger.add(
    LOG_DIR / "hireos_{time:YYYY-MM-DD}.log",
    rotation="10 MB", retention="30 days", level=os.getenv("LOG_LEVEL", "INFO"),
    backtrace=True, diagnose=False, enqueue=True,
)
# Recent errors, served by /api/ops/errors so the OpenClaw watchdog can post them to
# Discord #alerts -- an error that only reaches a log file is an error nobody sees.
import collections as _collections
OPS_ERRORS: "_collections.deque" = _collections.deque(maxlen=200)
logger.add(lambda m: OPS_ERRORS.append({"at": m.record["time"].astimezone(timezone.utc).isoformat(),
                                        "where": f'{m.record["name"]}:{m.record["function"]}',
                                        "message": str(m.record["message"])[:500]}),
           level="ERROR", format="{message}")

# ── Admin ─────────────────────────────────────────────────────────────────────
# Same shape as BILLING_UNLIMITED_EMAILS: an env allowlist, so admin rights need
# no column, no migration, and no way for a request to grant them to itself.
ADMIN_EMAILS = {
    e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()
}

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


# Exact URLs (comma-separated) allowed to receive an auth token after SSO, for
# clients that aren't the web app -- the MCP server's OAuth bridge. Exact match
# only: a prefix rule would let an attacker append a path and walk off with a
# session JWT, since the token rides in the redirect query string.
OAUTH_RETURN_ALLOWLIST = {
    u.strip().rstrip("/") for u in os.getenv("OAUTH_RETURN_ALLOWLIST", "").split(",") if u.strip()
}


def _allowed_return_to(return_to: str) -> bool:
    return bool(return_to) and return_to.rstrip("/") in OAUTH_RETURN_ALLOWLIST


def _make_oauth_state(provider: str, return_to: str = None, return_state: str = None) -> str:
    """Signed, short-lived CSRF state — stateless, no server-side session store.
    Also carries the post-login destination; signing is what stops a caller from
    swapping in their own URL after we've allowlisted ours."""
    payload = {
        "p": provider,
        "typ": "oauth_state",
        "exp": datetime.utcnow() + timedelta(minutes=10),
    }
    if return_to:
        payload["rt"] = return_to
    if return_state:
        # Opaque to us; echoed back so the caller can bind the round trip to the
        # browser that started it (CSRF protection for their own flow).
        payload["rs"] = return_state
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _oauth_state_payload(state: str, provider: str) -> Optional[dict]:
    """Decoded state if valid for this provider, else None."""
    if not state:
        return None
    try:
        payload = jwt.decode(state, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None
    if payload.get("typ") != "oauth_state" or payload.get("p") != provider:
        return None
    return payload

def _hash_pw(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()

def _verify_pw(password: str, hashed: str) -> bool:
    return _bcrypt.checkpw(password.encode(), hashed.encode())

def _create_token(email: str, days: int = None, extra: dict = None) -> str:
    exp = datetime.utcnow() + timedelta(days=days if days is not None else JWT_EXPIRE_DAYS)
    return jwt.encode({"sub": email, "exp": exp, **(extra or {})}, JWT_SECRET, algorithm=JWT_ALGORITHM)


# Long-lived token for MCP clients that can't run the browser OAuth flow. The
# jti is mirrored into Settings so regenerating one invalidates its predecessor
# -- otherwise a leaked year-long token would be unrevokable.
MCP_TOKEN_DAYS = 365
MCP_TOKEN_JTI_KEY = "mcp_token_jti"


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
    if payload.get("typ") == "mcp":
        row = db.query(Settings).filter(
            Settings.key == MCP_TOKEN_JTI_KEY, Settings.user_id == user.id
        ).first()
        if not row or not row.value or row.value != payload.get("jti"):
            raise HTTPException(status_code=401, detail="MCP token revoked — generate a new one in Settings")
    return user

def is_admin(user: User) -> bool:
    return (user.email or "").lower() in ADMIN_EMAILS


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Gate for every /api/admin route.

    The bearer middleware only checks that a JWT is valid -- it does no
    authorization at all. Without this dependency on the route itself, an admin
    endpoint is open to every logged-in user.
    """
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def require_resume_quota(user: User):
    """Block a free user who has spent their platform-key token allowance.

    402 rather than 403: the client distinguishes "you must pay" from "you may
    not" and shows the upgrade prompt only for the former. Both ways out are
    named, because adding your own key is a real answer here -- own-key usage
    is not metered.
    """
    if billing.has_token_quota(user):
        return
    raise HTTPException(
        status_code=402,
        detail=(
            "You've used all your free AI credits. Upgrade to Pro for unlimited "
            "generations, or add your own API key in Settings to keep going free."
        ),
    )


app = FastAPI(title="HireOS API", version="2.0.0")

# Innermost of the two so CORS headers also land on its 202s and poll replies.
app.add_middleware(AsyncBusMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_AUTH_EXEMPT = {"/api/health", "/api/build"}

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


class _PasswordBody(_BaseModel):
    password: str


@app.post("/auth/change-password")
def change_password(data: _PasswordBody, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    """Set the *caller's* password. Replaces /auth/reset-password, which took an
    email and a new password from anyone -- no token, no proof of ownership -- so
    knowing an address was enough to take over the account. SSO accounts were the
    worst case: it wrote a password_hash onto an account created without one,
    which then satisfied /auth/login.

    Self-serve reset for a locked-out user needs an emailed link, which needs mail
    infrastructure we don't have. Until then, locked-out password users sign in
    with Google/GitHub on the same email -- _upsert_oauth_user links by address.
    """
    if len(data.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    current_user.password_hash = _hash_pw(data.password)
    db.commit()
    return {"status": "ok"}


@app.get("/auth/me")
def me(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # is_admin drives whether the admin nav item renders. Hiding the link is
    # cosmetic -- require_admin on each route is the actual control.
    # on_platform_key drives whether the model picker renders: a user on our key
    # gets the admin's model whatever they select, so offering the choice lies.
    return {
        "email": current_user.email,
        "is_admin": is_admin(current_user),
        "on_platform_key": on_platform_key(db, current_user.id),
    }


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
def oauth_login(provider: str, return_to: str = None, return_state: str = None):
    cfg = OAUTH_PROVIDERS.get(provider)
    if not cfg:
        raise HTTPException(404, "Unknown provider")
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise HTTPException(503, f"{provider} SSO is not configured")
    if return_to and not _allowed_return_to(return_to):
        raise HTTPException(400, "return_to is not an allowed destination")
    if return_state and len(return_state) > 256:
        raise HTTPException(400, "return_state too long")
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": _oauth_redirect_uri(provider),
        "scope": cfg["scope"],
        "state": _make_oauth_state(provider, return_to, return_state),
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
    state_payload = _oauth_state_payload(state, provider)
    if not code or not state_payload:
        return _fail_redirect("invalid_state")
    return_to = state_payload.get("rt")
    # Re-check on the way out: the allowlist may have changed since the state was
    # signed, and a signed-but-no-longer-allowed URL should not be honoured.
    if return_to and not _allowed_return_to(return_to):
        return _fail_redirect("return_to_not_allowed")

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
    if return_to:
        q = {"auth_token": token}
        if state_payload.get("rs"):
            q["rs"] = state_payload["rs"]
        sep = "&" if "?" in return_to else "?"
        return RedirectResponse(f"{return_to}{sep}{urlencode(q)}")
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
    "openai_api_key": "openai",
    "nvidia_api_key": "nvidia",
    "ollama_base_url": "ollama_url",
    "local_llm_url": "local_url",
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


# ── Platform config (global Settings rows, user_id IS NULL) ───────────────────
# A NULL user_id row is invisible to every per-user query in the app, so the
# settings table doubles as platform config with no new table and no migration.

def get_platform_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(Settings).filter(Settings.key == key, Settings.user_id.is_(None)).first()
    return (row.value if row and row.value else "") or default


def set_platform_setting(db: Session, key: str, value: str) -> None:
    row = db.query(Settings).filter(Settings.key == key, Settings.user_id.is_(None)).first()
    if row:
        row.value = value
        row.updated_at = datetime.utcnow()
    else:
        db.add(Settings(key=key, value=value, user_id=None))
    db.commit()


def platform_key_for(db: Session, provider: str) -> str:
    """The platform's key for one provider. The DB row wins; the env var is only
    the bootstrap for a fresh deploy that has no admin panel visit yet."""
    saved = get_platform_setting(db, connectors.PLATFORM_KEY_SETTING.get(provider, ""))
    if saved:
        return saved
    return os.getenv(connectors.PLATFORM_KEY_ENV.get(provider, ""), "")


def platform_selection(db: Session) -> tuple:
    """(provider, model) the admin chose for the free tier."""
    provider = get_platform_setting(db, "platform_provider", connectors.DEFAULT_PROVIDER)
    model = get_platform_setting(db, "platform_model", "")
    if provider == connectors.DEFAULT_PROVIDER and not model:
        model = connectors.DEFAULT_MODEL
    return provider, model


def platform_llm(db: Session) -> str:
    """The llm string the router dispatches on, e.g. "gemini:gemini-2.5-flash"."""
    provider, model = platform_selection(db)
    routed = connectors.ROUTER_PROVIDER.get(provider, provider)
    return f"{routed}:{model}" if model else routed


def _usage_meter(user_id: int):
    """Charge platform-key usage to one user.

    Opens its own session on purpose: generation runs on FastAPI background
    threads, and the request's session is closed by then.
    """
    def meter(in_tokens: int, out_tokens: int):
        db = SessionLocal()
        try:
            billing.consume_tokens(db, user_id, in_tokens, out_tokens)
        finally:
            db.close()
    return meter


def get_llm_router(db: Session = None, user_id: int = None):
    """Router scoped strictly to a user's BYOK keys when a user is given (no env fallback,
    so one user's generations never use another user's or a shared server key).

    A user with no key of their own falls back to the platform's key, metered
    against their free token allowance. With no user, returns a server-fallback
    router (health/diagnostics only)."""
    from llm_router import LLMRouter
    if db is not None and user_id is not None:
        keys = {}
        rows = db.query(Settings).filter(Settings.user_id == user_id).all()
        for r in rows:
            if r.key in _USER_KEY_MAP and r.value and "•" not in r.value:
                keys[_USER_KEY_MAP[r.key]] = r.value
        if _provider_keys(keys):
            # Own key: unmetered, unrestricted, exactly as before.
            return LLMRouter(keys=keys, allow_env=False)

        provider, _ = platform_selection(db)
        key = platform_key_for(db, provider)
        if key:
            # Only the selected provider's key is loaded, so FALLBACK_ORDER has
            # nowhere else to go -- a free user cannot spend the platform's money
            # on a model the admin didn't pick.
            slot = connectors.ROUTER_KEY_SLOT.get(provider, provider)
            return LLMRouter(keys={slot: key, **_non_provider_keys(keys)},
                             allow_env=False, on_usage=_usage_meter(user_id))
        return LLMRouter(keys=keys, allow_env=False)
    return LLMRouter(allow_env=True)


# github_token / github_username are not LLM providers -- they must survive the
# swap to the platform key, or GitHub project context silently disappears for
# free users.
_NON_PROVIDER_SLOTS = {"github_token", "github_username"}


def _provider_keys(keys: dict) -> dict:
    return {k: v for k, v in keys.items() if k not in _NON_PROVIDER_SLOTS}


def _non_provider_keys(keys: dict) -> dict:
    return {k: v for k, v in keys.items() if k in _NON_PROVIDER_SLOTS}


def get_agent(name: str, db: Session = None, user_id: int = None):
    """Build a fresh agent bound to the requesting user's BYOK router."""
    classes = _load_agent_classes()
    router = get_llm_router(db, user_id)
    return classes[name](router)


def on_platform_key(db: Session, user_id: int) -> bool:
    """True when this user's generations run on the platform's key rather than
    their own -- the condition for metering, for the model lock, and for
    skipping the expensive extras."""
    rows = db.query(Settings).filter(Settings.user_id == user_id).all()
    own = {_USER_KEY_MAP[r.key] for r in rows
           if r.key in _USER_KEY_MAP and r.value and "•" not in r.value}
    if own - _NON_PROVIDER_SLOTS:
        return False
    provider, _ = platform_selection(db)
    return bool(platform_key_for(db, provider))


def resolve_llm(db: Session, user_id: int, requested=None) -> str:
    """The model to use: the caller's explicit choice, else the user's first
    configured provider (not a hardcoded gemini).

    A user on the platform's key gets the admin's choice regardless of what they
    asked for -- otherwise the model picker in the top bar would let anyone run
    the most expensive model available on our account."""
    if on_platform_key(db, user_id):
        return platform_llm(db)
    if requested:
        return requested
    router = get_llm_router(db, user_id)
    mine = _setting(db, user_id, DEFAULT_LLM_KEY)   # the one model the user picked (Settings / top bar)
    if mine and llm_provider(mine) in router.available_providers():
        return mine
    return router.default_llm()


DEFAULT_LLM_KEY = "default_llm"


def llm_provider(llm: str) -> str:
    """'openrouter:qwen/x' -> openrouter, 'gemini-2.5-flash' -> gemini (as the router routes it)."""
    return (llm.split(":", 1)[0] if ":" in llm else llm.split("-")[0]).lower()


def get_owned_job(db: Session, job_id: int, user) -> Job:
    """Fetch a job that belongs to the user, else 404 (prevents cross-user access)."""
    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _owns_job(db: Session, job_id: int, user_id: int) -> bool:
    return db.query(Job.id).filter(Job.id == job_id, Job.user_id == user_id).first() is not None


# Background generation runs in-process, so a task can be abandoned two ways: a
# restart kills the worker, or the worker wedges on a provider call that never
# returns. Either way the row sits at "processing" and the UI polls it forever.
# ponytail: a wall-clock cutoff, no heartbeat. It cannot tell a wedged task from
# a legitimately slow one, so keep the cutoff well above the slowest real run
# (resumes take 40-90s, ATS a few minutes). Add a heartbeat if that stops holding.
STALE_TASK_TIMEOUT = timedelta(minutes=15)


def _fail_abandoned_tasks(db: Session, message: str, job_id: int = None,
                          older_than: timedelta = None) -> int:
    """Flip abandoned 'processing' rows to failed. Returns how many were swept."""
    q = db.query(LLMTaskStatus).filter(LLMTaskStatus.status == "processing")
    if job_id is not None:
        q = q.filter(LLMTaskStatus.job_id == job_id)
    if older_than is not None:
        q = q.filter(LLMTaskStatus.updated_at < datetime.utcnow() - older_than)
    try:
        swept = q.update({"status": "failed", "error_message": message}, synchronize_session=False)
        db.commit()
        return swept
    except Exception as e:
        db.rollback()
        logger.warning(f"[Tasks] Abandoned-task sweep failed (non-fatal): {e}")
        return 0


def _as_int(value):
    """Salary columns are Integer; LLM extraction sometimes returns floats
    (e.g. '$117,923.72 per annum'). SQLite stores those as REAL, and JobOut
    then fails validation, 500-ing the whole job list."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


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
    if contact_div_m:
        loc_search_text = re.sub(r'<[^>]+>', ' ', contact_div_m.group(1))
    else:
        # No structured contact div (PDF/plain-text source): scan only the header
        # block (first few non-empty lines), never the whole doc — otherwise a
        # "Lastname, XX"-shaped string in the body is misread as the location.
        # ponytail: first 3 non-empty lines = name + contact rows; bump if contact
        # block is longer, add India/other state codes to the regex if needed.
        loc_search_text = ' '.join([l.strip() for l in decoded.split('\n') if l.strip()][:3])
    loc_search_text = html_lib.unescape(loc_search_text)
    # Strip the candidate's name so its tokens can't be read as a city (e.g. "Singh, IN").
    for tok in name.split():
        if len(tok) > 1:
            loc_search_text = re.sub(re.escape(tok), ' ', loc_search_text, flags=re.IGNORECASE)
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

    # Google Scholar — prefer href attribute
    sch_href = re.search(r'href=["\']([^"\']*scholar\.google\.[a-z.]+/citations[^\s"\']*)["\']', raw, re.IGNORECASE)
    if sch_href:
        scholar = sch_href.group(1)
    else:
        sch_m = re.search(r'(?:https?://)?scholar\.google\.[a-z.]+/citations\S*', plain)
        scholar = sch_m.group(0) if sch_m else ''
        if scholar and not scholar.startswith('http'):
            scholar = 'https://' + scholar

    return {
        'name': name,
        'email': email,
        'phone': phone,
        'location': location,
        'linkedin': linkedin,
        'github': github,
        'scholar': scholar,
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

    # Positive evidence of resume content beats every heuristic below. Misclassifying
    # a real component drops those sections from the tailoring prompt entirely, and
    # nothing downstream can recover content the LLM never saw.
    if re.search(r'^#{1,3}\s*(work\s+)?experience\b', content or "", re.IGNORECASE | re.MULTILINE) \
            or "professional summary" in cl or "work experience" in cl:
        return "resume"

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


def excluded_component_names(db: Session, user_id: int) -> List[str]:
    """Active components the classifier keeps out of the tailoring prompt.

    Exclusion happens before the LLM sees anything, so a wrongly-excluded
    component looks exactly like the model dropping a section. Surface it.
    """
    active = db.query(MasterResumeComponent).filter(
        MasterResumeComponent.is_active == True,
        MasterResumeComponent.user_id == user_id,
    ).all()
    used = {c.id for c in _resume_components(db, user_id)}
    return [c.name for c in active if c.id not in used]


def _component_markdown(c) -> str:
    raw = c.content_text or ""
    is_html = bool(re.search(r'<(?:h[1-6]|div|ul|li|p)\b', raw, re.IGNORECASE))
    return _html_resume_to_markdown(raw) if is_html else raw


def get_master_resume(db: Session, user_id: int) -> str:
    """Load a user's master resume — joins resume-content components as structured markdown."""
    components = _resume_components(db, user_id)

    if components:
        result = "\n\n".join(_component_markdown(c) for c in components)
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
        # extract_contact_info reads the name off the first lines, so the actual
        # resume must lead -- a whitepaper first put its title in the header.
        main, _ = resume_profile.pick_sources([_component_markdown(c) for c in components])
        if main:
            components = [components[main]] + components[:main] + components[main + 1:]
        return "\n".join(c.content_text or "" for c in components)
    row = db.query(Settings).filter(Settings.key == "master_resume", Settings.user_id == user_id).first()
    return row.value if row and row.value else ""


RESUME_PROFILE_KEY = "resume_profile"


def load_resume_profile(db: Session, user_id: int) -> Optional[resume_profile.Profile]:
    """The user's parsed resume. Reuses the saved one (which may carry the user's
    corrections) while the source components are unchanged; re-parses otherwise.
    None when no component has a readable experience section."""
    texts = [_component_markdown(c) for c in _resume_components(db, user_id)]
    if not texts:
        return None
    current = resume_profile.source_hash(texts)
    row = db.query(Settings).filter(Settings.key == RESUME_PROFILE_KEY, Settings.user_id == user_id).first()
    if row and row.value:
        try:
            saved = resume_profile.Profile.model_validate_json(row.value)
            if saved.source_hash == current:
                return saved
        except Exception as e:
            logger.warning(f"[ResumeProfile] saved profile unreadable, re-parsing: {e}")
    profile = resume_profile.build_profile(texts)
    if profile is None:
        return None
    if row:
        row.value = profile.model_dump_json()
    else:
        db.add(Settings(key=RESUME_PROFILE_KEY, value=profile.model_dump_json(), user_id=user_id))
    db.commit()
    return profile


@app.get("/api/settings/resume-profile")
def get_resume_profile(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The parsed master resume every generation builds on -- facts, order, layout."""
    profile = load_resume_profile(db, current_user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="No resume with an experience section found in your components.")
    return profile.model_dump()


@app.put("/api/settings/resume-profile")
def put_resume_profile(payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Save user corrections. Kept until the resume components change."""
    texts = [_component_markdown(c) for c in _resume_components(db, current_user.id)]
    try:
        profile = resume_profile.Profile.model_validate({**payload, "source_hash": resume_profile.source_hash(texts)})
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid profile: {e}")
    row = db.query(Settings).filter(Settings.key == RESUME_PROFILE_KEY, Settings.user_id == current_user.id).first()
    if row:
        row.value = profile.model_dump_json()
    else:
        db.add(Settings(key=RESUME_PROFILE_KEY, value=profile.model_dump_json(), user_id=current_user.id))
    db.commit()
    return profile.model_dump()


@app.delete("/api/settings/resume-profile")
def reset_resume_profile(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Drop corrections and re-parse from the components."""
    db.query(Settings).filter(Settings.key == RESUME_PROFILE_KEY, Settings.user_id == current_user.id).delete()
    db.commit()
    return get_resume_profile(db, current_user)


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

    # Nothing can still be running at startup, so every "processing" row is
    # orphaned by definition -- no age cutoff needed.
    db = SessionLocal()
    try:
        orphaned = _fail_abandoned_tasks(
            db, message="Interrupted by a server restart before it finished. Press Generate again.")
        if orphaned:
            logger.warning(f"[Startup] Failed {orphaned} task(s) orphaned by the last restart")
    finally:
        db.close()

    logger.info("HireOS API started ✓")


@app.on_event("startup")
async def _start_telegram():
    # DATABASE_URL guard: tests import main and must never poll the real bot
    if telegram_bot.TOKEN and "test" not in os.getenv("DATABASE_URL", ""):
        asyncio.create_task(telegram_bot.run())
        logger.info("[telegram] bot polling started")


@app.on_event("startup")
async def _start_discord():
    if discord_bot.TOKEN and "test" not in os.getenv("DATABASE_URL", ""):
        asyncio.create_task(discord_bot.run())
        logger.info("[discord] bot started")


@app.post("/api/discord/link")
def discord_link(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """One-time code (15 min): typing `link <code>` in a Discord channel binds that channel and that Discord user."""
    if not discord_bot.TOKEN:
        raise HTTPException(503, "Discord bot is not configured on this server")
    code, stored = telegram_bot.new_link_code(datetime.utcnow())
    _put_setting(db, current_user.id, discord_bot.CODE_KEY, stored)
    db.commit()
    return {"code": code, "say": f"link {code}"}


def _chat_send(db: Session, user_id: int, text: str, markup: dict = None, photo: bytes = None) -> dict:
    """One message to every chat the user linked (Telegram, Discord). {"msg": telegram id, "dmsg": discord id}."""
    if photo:
        return {"msg": telegram_bot.send_photo(db, user_id, photo, text, markup),
                "dmsg": discord_bot.send_photo(db, user_id, photo, text, markup)}
    return {"msg": telegram_bot.send(db, user_id, text, markup), "dmsg": discord_bot.send(db, user_id, text, markup)}


@app.on_event("startup")
async def _start_scheduler():
    import scheduler, sys
    if scheduler.enabled():
        asyncio.create_task(scheduler.run(sys.modules[__name__]))
        logger.info("[scheduler] background jobs started")


@app.post("/api/telegram/link")
def telegram_link(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """One-time t.me link (15 min) that binds the Telegram chat that opens it to this user."""
    if not telegram_bot.TOKEN:
        raise HTTPException(503, "Telegram bot is not configured on this server")
    code, stored = telegram_bot.new_link_code(datetime.utcnow())
    _put_setting(db, current_user.id, telegram_bot.CODE_KEY, stored)
    db.commit()
    return {"url": f"https://t.me/{telegram_bot.username()}?start={code}"}




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
    # This is the poll that drives every spinner in JobDetail, so expiring stale
    # rows here is what actually ends a hung one -- and it persists the failure,
    # so the per-task status endpoints agree without each repeating the check.
    _fail_abandoned_tasks(
        db, job_id=job_id, older_than=STALE_TASK_TIMEOUT,
        message=f"Timed out after {int(STALE_TASK_TIMEOUT.total_seconds() // 60)} minutes "
                "with no result. Press Generate again.")
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

# ── Billing ───────────────────────────────────────────────────────────────────
# Free tier is 30 lifetime resume generations; Pro is an unlimited monthly
# subscription. Checkout and cancellation are hosted by Stripe, so no card data
# ever reaches this server.

@app.get("/api/billing/status")
def billing_status(current_user: User = Depends(get_current_user)):
    return billing.plan_snapshot(current_user)


@app.post("/api/billing/checkout")
def billing_checkout(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if billing.is_pro(current_user):
        raise HTTPException(400, "You're already on Pro")
    if not billing.is_configured():
        raise HTTPException(503, "Billing isn't configured on this server")
    # Land back on Settings either way; the query flag drives the toast.
    base = f"{APP_BASE_URL}/#/settings"
    try:
        url = billing.create_checkout_session(
            db, current_user,
            success_url=f"{base}?billing=success",
            cancel_url=f"{base}?billing=cancelled",
        )
    except billing.StripeError as e:
        logger.error(f"[Billing] checkout failed for user {current_user.id}: {e}")
        raise HTTPException(502, str(e))
    return {"url": url}


@app.post("/api/billing/portal")
def billing_portal(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        url = billing.create_portal_session(
            db, current_user, return_url=f"{APP_BASE_URL}/#/settings"
        )
    except billing.StripeError as e:
        logger.error(f"[Billing] portal failed for user {current_user.id}: {e}")
        raise HTTPException(502, str(e))
    return {"url": url}


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Stripe's callback. Deliberately outside /api/ so the bearer-token auth
    middleware doesn't 401 it — the signature check below is what authenticates
    this endpoint, and it is the only thing allowed to change a user's plan.
    """
    payload = await request.body()   # raw bytes: re-serialising breaks the HMAC
    try:
        billing.verify_webhook(payload, request.headers.get("Stripe-Signature", ""))
    except billing.StripeError as e:
        logger.warning(f"[Stripe] rejected webhook: {e}")
        raise HTTPException(400, "Invalid signature")

    try:
        event = json.loads(payload)
    except ValueError:
        raise HTTPException(400, "Invalid payload")

    event_id = event.get("id")
    if not event_id:
        raise HTTPException(400, "Event missing id")

    if not billing.record_event_once(db, event_id, event.get("type", "")):
        # Already handled. 200 so Stripe stops retrying.
        return {"received": True, "duplicate": True}

    changed, summary = billing.apply_event(db, event)
    logger.info(f"[Stripe] {event.get('type')} {event_id}: {summary}")
    return {"received": True, "changed": changed}


@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = db.query(Settings).filter(Settings.user_id == current_user.id).all()
    # Skip internal caches (e.g. the insights narrative blob) — not user settings.
    result = {r.key: r.value for r in rows if r.key not in (_INSIGHTS_CACHE_KEY, RESUME_PROFILE_KEY)}
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


# ── MCP personal access token ─────────────────────────────────────────────────

def _mcp_token_row(db: Session, user_id: int) -> Settings:
    return db.query(Settings).filter(
        Settings.key == MCP_TOKEN_JTI_KEY, Settings.user_id == user_id
    ).first()


@app.get("/api/settings/mcp-token")
def mcp_token_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Whether a token exists. The token itself is shown once, at creation, and
    is not recoverable -- we store only its jti, never the token."""
    row = _mcp_token_row(db, current_user.id)
    return {"exists": bool(row and row.value),
            "created_at": row.updated_at.isoformat() if row and row.value and row.updated_at else None}


@app.post("/api/settings/mcp-token")
def create_mcp_token(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Issue a token for an MCP client. Any previously issued one stops working."""
    import secrets
    jti = secrets.token_urlsafe(12)
    row = _mcp_token_row(db, current_user.id)
    if row:
        row.value = jti
        row.updated_at = datetime.utcnow()
    else:
        db.add(Settings(key=MCP_TOKEN_JTI_KEY, value=jti, user_id=current_user.id))
    db.commit()
    token = _create_token(current_user.email, days=MCP_TOKEN_DAYS, extra={"typ": "mcp", "jti": jti})
    return {"token": token, "expires_days": MCP_TOKEN_DAYS}


@app.delete("/api/settings/mcp-token")
def revoke_mcp_token(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = _mcp_token_row(db, current_user.id)
    if row:
        row.value = ""
        row.updated_at = datetime.utcnow()
        db.commit()
    return {"status": "revoked"}


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
    """Upload a PDF, DOCX, HTML, Markdown or text file as a resume component."""
    from pdf_utils import extract_text_from_pdf

    file_content = await file.read()
    filename = file.filename
    content_text = ""

    # Extract text based on extension
    if filename.lower().endswith(".pdf"):
        content_text = extract_text_from_pdf(file_content)
    elif filename.lower().endswith(".docx"):
        # python-docx is already a dependency (it writes the generated .docx).
        # Most people's resume is a Word file, and this used to 400.
        import io
        from docx import Document
        doc = Document(io.BytesIO(file_content))
        blocks = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    blocks.append(" | ".join(cells))
        content_text = "\n".join(b for b in blocks if b.strip())
    elif filename.lower().endswith(".html") or filename.lower().endswith(".htm"):
        raw = file_content.decode("utf-8", errors="ignore")
        content_text = _strip_html(raw)
    elif filename.lower().endswith(".md") or filename.lower().endswith(".txt"):
        raw = file_content.decode("utf-8", errors="ignore")
        content_text = _strip_html(raw) if ('<html' in raw.lower() or '<div' in raw.lower()) else raw
    else:
        raise HTTPException(status_code=400, detail="Unsupported file type. Upload PDF, DOCX, HTML, Markdown, or TXT.")

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


# ── Onboarding ────────────────────────────────────────────────────────────────
# Two independent ways in, not a forced sequence: upload documents, OR answer a
# few questions. Uploading also unlocks a better version of the questions --
# the documents are analysed first, so the follow-ups ask only about what the
# documents did NOT already say.
#
# The profile is a list of key/value facts with LLM-chosen keys, not a fixed
# schema. A career-changer, a new grad and a staff engineer need different
# questions asked, so nothing here hardcodes what a person consists of.
#
# Facts are rendered back into an ordinary master-resume component, which is
# how they reach every prompt in the app: _resume_components collects it and
# get_master_resume concatenates it. See _classify_component for why the
# markdown must read as statements under a "## Professional Summary" heading --
# question-shaped text is classified "qa" and silently dropped from prompts.

ONBOARDING_COMPONENT_NAME = "Career Context"
ONBOARDING_PROFILE_KEY = "onboarding_profile"
ONBOARDING_DONE_KEY = "onboarding_done"

# Cap what we send for analysis. Whole resumes are ~10k chars; several uploads
# plus a schema is still comfortably inside a Flash-class context.
_ANALYSIS_CHAR_BUDGET = 24000

# Hard wall-clock budget for the two onboarding LLM calls. Onboarding is the
# first thing a new user ever does, so it must degrade to the canned questions
# rather than leave them watching a spinner: providers do go slow (a model that
# answered in 14s yesterday can hang for minutes today, or EOL out from under
# you mid-session). The router's own per-provider timeouts are longer because
# they serve generation, where waiting is worth it.
_ONBOARDING_LLM_TIMEOUT = float(os.getenv("ONBOARDING_LLM_TIMEOUT", "60"))
# Analysis gets a bigger budget than the question call: it ships every uploaded
# document, so the prompt is an order of magnitude larger. The UI also tells the
# user their documents are being read, which buys the extra wait honestly --
# the scratch-question path has no such cover and must stay snappy.
_ONBOARDING_ANALYSIS_TIMEOUT = float(os.getenv("ONBOARDING_ANALYSIS_TIMEOUT", "150"))


class ProfileFact(_BaseModel):
    """One key/value fact. `key` is invented by the model per user -- there is
    deliberately no enum here.

    `source` says where it came from. It decides what gets written back into the
    prompt: facts read out of an uploaded document must NOT be, because that
    document is already part of the master resume and restating it would make
    every future generation pay for the same content twice.
    """
    key: str
    label: str = ""
    value: str = ""
    source: str = "answer"      # "answer" | "document"


class OnboardingQuestion(_BaseModel):
    key: str                 # the profile key this answer fills
    label: str = ""          # human name for that key
    question: str
    prefix: str = ""         # opening words of the user's own answer
    placeholder: str = ""


class DocAnalysis(_BaseModel):
    """What the uploaded documents already say, and what is still missing."""
    extracted: List[ProfileFact] = []
    questions: List[OnboardingQuestion] = []


class QuestionSet(_BaseModel):
    questions: List[OnboardingQuestion] = []


class OnboardingAnswersIn(_BaseModel):
    facts: List[ProfileFact] = []


# Used only when the model is unreachable. Deliberately short: it exists so the
# flow never dead-ends, not to be a good interview.
_FALLBACK_QUESTIONS = [
    {"key": "target_role", "label": "Target role", "question": "What roles are you targeting right now?",
     "prefix": "I'm targeting", "placeholder": "Senior ML Engineer roles at product companies"},
    {"key": "experience", "label": "Experience", "question": "How many years of relevant experience, and at what level?",
     "prefix": "I have", "placeholder": "8 years, currently senior"},
    {"key": "strengths", "label": "Strengths", "question": "What are you genuinely best at?",
     "prefix": "I'm strongest at", "placeholder": "production ML systems, Python, mentoring"},
    {"key": "signature_win", "label": "Signature achievement", "question": "What achievement would you most want a hiring manager to see?",
     "prefix": "My biggest win was", "placeholder": "cut inference cost 60% by rebuilding the serving layer"},
    {"key": "tools", "label": "Tools", "question": "Which tools and technologies belong on the resume?",
     "prefix": "I work with", "placeholder": "PyTorch, Kubernetes, GCP"},
    {"key": "location", "label": "Location", "question": "Where are you looking to work?",
     "prefix": "I'm looking for work", "placeholder": "remote in Canada, or hybrid in Toronto"},
]

_SCRATCH_PROMPT = """A new user has uploaded nothing. Write the 6-8 questions that would
tell you the most about them for tailoring resumes.

Rules:
- Each question fills one profile fact. Invent a short snake_case `key` for it and a
  human `label`. Choose keys that suit a general professional -- do not assume an industry.
- Answers must be short: a phrase or one sentence. Never ask for a paragraph.
- `prefix` is the opening words of the user's own answer ("I'm targeting"), so they
  only type the ending. `placeholder` is a realistic example answer.
- Cover positioning and target role first, then seniority, strengths, a signature
  achievement, tools, and any constraint (location, authorization, things to omit).

Return only the questions."""

_ANALYSIS_PROMPT = """You are reading a new user's uploaded career documents so you can
tailor resumes for them later.

Do two things:

1. `extracted` -- the facts the documents already establish. One entry per fact, with a
   short snake_case `key`, a human `label`, and the `value` in the user's own terms.
   Choose the keys that matter for THIS person; a career-changer, a new grad and a staff
   engineer do not need the same fields. Do not invent anything the documents do not say.

2. `questions` -- 4-7 questions covering only what the documents did NOT answer, ranked
   by how much a resume would improve if you knew. Never ask something the documents
   already state. Prefer intent and positioning (target role, seniority aim, constraints,
   what to leave off) over restating history. Each question carries the `key` and `label`
   of the fact it will fill, a `prefix` (the opening words of the user's own answer) and
   a realistic `placeholder`. Answers must be short.

THE USER'S DOCUMENTS:
{material}"""


def _profile_facts(db: Session, user_id: int) -> List[dict]:
    row = db.query(Settings).filter(
        Settings.key == ONBOARDING_PROFILE_KEY, Settings.user_id == user_id
    ).first()
    if not row or not row.value:
        return []
    try:
        data = json.loads(row.value)
    except (ValueError, TypeError):
        return []
    return data.get("facts", []) if isinstance(data, dict) else []


def _merge_facts(existing: List[dict], incoming: List[dict]) -> List[dict]:
    """Later answers win, order of first appearance is kept, blanks never
    overwrite something we already knew."""
    merged = {f["key"]: dict(f) for f in existing if f.get("key")}
    order = [f["key"] for f in existing if f.get("key")]
    for f in incoming:
        key = (f.get("key") or "").strip()
        value = (f.get("value") or "").strip()
        if not key or not value:
            continue
        if key not in merged:
            order.append(key)
        merged[key] = {
            "key": key,
            "label": (f.get("label") or key.replace("_", " ")).strip(),
            "value": value,
            "source": f.get("source") or "answer",
        }
    return [merged[k] for k in order if k in merged]


def _render_career_context(facts: List[dict]) -> str:
    """Facts -> markdown that _classify_component reads as resume content.

    Only what the user TOLD us. Facts extracted from an uploaded document are
    skipped: that document is already a master-resume component, so repeating
    its contents here would duplicate it in every prompt for no new information.
    What the extraction is actually for is knowing which questions to skip.

    Two silent traps: three or more `**Q1`-style labels, or more than 40 question
    marks, and the classifier files this as "qa" and drops it from every prompt.
    So only the values appear, as statements, under a heading it recognises.
    """
    lines = ["## Professional Summary", "",
             "Context the candidate gave about themselves:", ""]
    for f in facts:
        if (f.get("source") or "answer") == "document":
            continue
        value = (f.get("value") or "").strip()
        if not value:
            continue
        label = (f.get("label") or f.get("key") or "").strip().rstrip(":")
        lines.append(f"- **{label}:** {value.rstrip('.?')}." if label else f"- {value.rstrip('.?')}.")
    return "\n".join(lines)


def _upsert_career_context(db: Session, user_id: int, markdown: str) -> None:
    component = db.query(MasterResumeComponent).filter(
        MasterResumeComponent.user_id == user_id,
        MasterResumeComponent.name == ONBOARDING_COMPONENT_NAME,
    ).first()
    if component:
        component.content_text = markdown
        component.is_active = True
        component.updated_at = datetime.utcnow()
    else:
        db.add(MasterResumeComponent(
            name=ONBOARDING_COMPONENT_NAME, type="text", content_text=markdown,
            is_active=True, order=99, user_id=user_id,
        ))
    db.commit()


def _uploaded_components(db: Session, user_id: int):
    """Everything the user supplied, minus the component we generate ourselves."""
    return [c for c in db.query(MasterResumeComponent).filter(
        MasterResumeComponent.user_id == user_id,
        MasterResumeComponent.is_active == True,
    ).order_by(MasterResumeComponent.order.asc()).all()
        if c.name != ONBOARDING_COMPONENT_NAME]


def _set_user_setting(db: Session, user_id: int, key: str, value: str) -> None:
    row = db.query(Settings).filter(Settings.key == key, Settings.user_id == user_id).first()
    if row:
        row.value = value
        row.updated_at = datetime.utcnow()
    else:
        db.add(Settings(key=key, value=value, user_id=user_id))
    db.commit()


def _fallback_questions(exclude_keys=()) -> List[OnboardingQuestion]:
    return [OnboardingQuestion(**q) for q in _FALLBACK_QUESTIONS if q["key"] not in set(exclude_keys)]


@app.get("/api/onboarding/status")
def onboarding_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    uploaded = _uploaded_components(db, current_user.id)
    done_row = db.query(Settings).filter(
        Settings.key == ONBOARDING_DONE_KEY, Settings.user_id == current_user.id
    ).first()
    return {
        "done": bool(done_row and done_row.value == "1"),
        "has_components": bool(uploaded),
        "component_count": len(uploaded),
        "documents": [{"id": c.id, "name": c.name} for c in uploaded],
        "facts": _profile_facts(db, current_user.id),
    }


@app.post("/api/onboarding/analyze", response_model=DocAnalysis)
async def analyze_documents(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Read what was uploaded, then ask only about what it does not say.

    This is the pay-off for uploading: the questions get sharper instead of the
    user answering things their own resume already answered.
    """
    components = _uploaded_components(db, current_user.id)
    if not components:
        raise HTTPException(status_code=400, detail="Upload a document first, or answer the questions instead.")

    material = "\n\n".join(
        f"--- {c.name} ---\n{(c.content_text or '')}" for c in components
    )[:_ANALYSIS_CHAR_BUDGET]

    try:
        router = get_llm_router(db, current_user.id)
        result = await asyncio.wait_for(router.structured_complete(
            _ANALYSIS_PROMPT.format(material=material),
            DocAnalysis,
            llm=resolve_llm(db, current_user.id),
            system="You extract structured facts from career documents and ask only what is missing.",
        ), timeout=_ONBOARDING_ANALYSIS_TIMEOUT)
        if result.questions or result.extracted:
            # Remember what the documents told us, so answers merge on top of it
            # rather than the two living in different places.
            if result.extracted:
                extracted = [{**f.model_dump(), "source": "document"} for f in result.extracted]
                facts = _merge_facts(_profile_facts(db, current_user.id), extracted)
                _set_user_setting(db, current_user.id, ONBOARDING_PROFILE_KEY,
                                  json.dumps({"facts": facts}))
            if not result.questions:
                result.questions = _fallback_questions([f.key for f in result.extracted])
            return result
        logger.warning("[Onboarding] analysis returned nothing — using the fallback questions")
    except asyncio.TimeoutError:
        logger.warning(f"[Onboarding] analysis exceeded {_ONBOARDING_ANALYSIS_TIMEOUT}s — using fallback questions")
    except Exception as e:
        # A dead provider key must not dead-end the one flow that earns the user
        # a working profile. Degrade to the generic questions instead.
        logger.warning(f"[Onboarding] document analysis failed, using fallback: {e}")
    return DocAnalysis(extracted=[], questions=_fallback_questions())


@app.post("/api/onboarding/questions", response_model=QuestionSet)
async def onboarding_questions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The no-documents path: ask from scratch."""
    try:
        router = get_llm_router(db, current_user.id)
        result = await asyncio.wait_for(router.structured_complete(
            _SCRATCH_PROMPT, QuestionSet,
            llm=resolve_llm(db, current_user.id),
            system="You write short, high-signal onboarding questions. Never ask for an essay.",
        ), timeout=_ONBOARDING_LLM_TIMEOUT)
        if result.questions:
            return result
        logger.warning("[Onboarding] empty question set — using the fallback")
    except asyncio.TimeoutError:
        logger.warning(f"[Onboarding] question generation exceeded {_ONBOARDING_LLM_TIMEOUT}s — using fallback")
    except Exception as e:
        logger.warning(f"[Onboarding] question generation failed, using fallback: {e}")
    return QuestionSet(questions=_fallback_questions())


@app.post("/api/onboarding/answers")
def save_onboarding_answers(
    payload: OnboardingAnswersIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    answered = [{**f.model_dump(), "source": "answer"} for f in payload.facts]
    facts = _merge_facts(_profile_facts(db, current_user.id), answered)
    _set_user_setting(db, current_user.id, ONBOARDING_PROFILE_KEY, json.dumps({"facts": facts}))
    _set_user_setting(db, current_user.id, ONBOARDING_DONE_KEY, "1")

    if facts:
        _upsert_career_context(db, current_user.id, _render_career_context(facts))
    return {"status": "saved", "fact_count": len(facts), "component_written": bool(facts)}


@app.post("/api/onboarding/skip")
def skip_onboarding(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Mark onboarding done without answering. Skippable on purpose: a hard gate
    at signup costs more in drop-off than it wins in resume quality."""
    _set_user_setting(db, current_user.id, ONBOARDING_DONE_KEY, "1")
    return {"status": "skipped"}


# ── Admin ─────────────────────────────────────────────────────────────────────
# Which provider and model the platform's own key runs on, chosen from each
# provider's live catalogue rather than a hardcoded list. Every route here takes
# Depends(require_admin) -- the bearer middleware authenticates but authorizes
# nothing, so a route without it is open to every logged-in user.

def _mask(value: str) -> str:
    if not value:
        return ""
    return "••••••••" + value[-4:] if len(value) > 4 else "••••••••"


class PlatformModelIn(_BaseModel):
    provider: str
    model: str


class PlatformKeyIn(_BaseModel):
    provider: str
    key: str = ""


@app.get("/api/admin/providers")
def admin_providers(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    provider, model = platform_selection(db)
    out = []
    for name in connectors.PROVIDERS:
        key = platform_key_for(db, name)
        models, error = connectors.list_models(name, key)
        out.append({
            "provider": name,
            "key_masked": _mask(key),
            "configured": bool(key),
            "model_count": len(models),
            "error": error,
            "active": name == provider,
        })
    return {"providers": out, "active_provider": provider, "active_model": model}


@app.get("/api/admin/models")
def admin_models(
    provider: str = Query(...),
    refresh: bool = Query(False),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if provider not in connectors.PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    models, error = connectors.list_models(provider, platform_key_for(db, provider), refresh=refresh)
    return {"provider": provider, "models": models, "error": error}


@app.put("/api/admin/platform-model")
def admin_set_platform_model(
    payload: PlatformModelIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if payload.provider not in connectors.PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {payload.provider}")
    key = platform_key_for(db, payload.provider)
    if not key:
        # Listing a catalogue and running a generation are different privileges:
        # OpenRouter publishes its model list to anyone but still charges a key
        # to answer a prompt. Without this, selecting it leaves every free user
        # with a router that has no usable provider.
        raise HTTPException(
            status_code=400,
            detail=f"Save a {payload.provider} API key before making it the free-tier provider.",
        )
    models, error = connectors.list_models(payload.provider, key)
    if error and not models:
        raise HTTPException(status_code=400, detail=f"Cannot verify model: {error}")
    # Validated against the live catalogue on purpose: a typo saved here breaks
    # generation for every free user at once, and it would fail at their request
    # time rather than here.
    if payload.model not in {m["id"] for m in models}:
        raise HTTPException(
            status_code=400,
            detail=f"{payload.model} is not in {payload.provider}'s model list.",
        )
    set_platform_setting(db, "platform_provider", payload.provider)
    set_platform_setting(db, "platform_model", payload.model)
    logger.info(f"[Admin] platform model set to {payload.provider}:{payload.model}")
    return {"status": "saved", "provider": payload.provider, "model": payload.model}


@app.put("/api/admin/keys")
def admin_set_key(
    payload: PlatformKeyIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if payload.provider not in connectors.PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {payload.provider}")
    # Same rule as /api/settings: a masked value coming back from the UI is the
    # display string, not a key, and saving it would destroy the real one.
    if payload.key and "••••" in payload.key:
        return {"status": "unchanged"}
    set_platform_setting(db, connectors.PLATFORM_KEY_SETTING[payload.provider], payload.key.strip())
    models, error = connectors.list_models(payload.provider, payload.key.strip(), refresh=True)
    return {"status": "saved", "model_count": len(models), "error": error}


@app.get("/api/admin/usage")
def admin_usage(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    users = db.query(User).all()
    rows = [{
        "id": u.id,
        "email": u.email,
        "plan": "pro" if billing.is_pro(u) else "free",
        "input_tokens": u.free_input_tokens_used or 0,
        "output_tokens": u.free_output_tokens_used or 0,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    } for u in users]
    rows.sort(key=lambda r: r["input_tokens"] + r["output_tokens"], reverse=True)
    return {
        "users": rows,
        "limits": {"input": billing.FREE_INPUT_TOKENS, "output": billing.FREE_OUTPUT_TOKENS},
        "totals": {
            "input": sum(r["input_tokens"] for r in rows),
            "output": sum(r["output_tokens"] for r in rows),
        },
    }


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

    # Alert emails repeat postings; answer from the DB before spending a scrape + LLM call.
    existing = _find_duplicate_job(db, current_user.id, url=url)
    if existing:
        return {"job_id": existing.id, "duplicate": True, "job": {
            "id": existing.id, "company": existing.company, "title": existing.title,
            "status": existing.status, "platform": existing.platform, "remote": existing.remote,
        }}

    try:
        agent = get_agent("discovery", db, current_user.id)
        data = await agent.analyze_url(url, llm=llm)
    except ValueError as e:
        # Known scraping/validation error (e.g. login wall, bot check)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Unexpected crash
        raise HTTPException(status_code=500, detail=f"Job discovery failed: {e}")

    # LinkedIn reposts one opening under several ids (one per city), and a posting can
    # arrive from both an ATS feed and LinkedIn, so the URL check above isn't enough.
    existing = _find_duplicate_job(db, current_user.id, url=data.get("url"),
                                   company=data.get("company"), title=data.get("title"))
    if existing:
        return {"job_id": existing.id, "duplicate": True, "job": {
            "id": existing.id, "company": existing.company, "title": existing.title,
            "status": existing.status, "platform": existing.platform, "remote": existing.remote,
        }}

    # Map extracted data to DB fields
    job = Job(
        company=data.get("company") or "Unknown",
        title=data.get("title") or "Unknown",
        url=data.get("url") or url,
        job_description=data.get("job_description", ""),
        location=data.get("location", ""),
        remote=data.get("remote", False),
        salary_min=_as_int(data.get("salary_min")),
        salary_max=_as_int(data.get("salary_max")),
        platform=_platform_for(data.get("url") or url, data.get("platform")),
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




@app.post("/api/agent/linkedin-search")
async def linkedin_search(payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Import LinkedIn jobs via the public guest API (no LinkedIn login).
    Body: { "keywords": "...", "location": "Toronto, Ontario, Canada", "posted_within": "day|week|month|any", "limit": 10, "llm": "gemini" }
    New postings go through track-url, so each one costs one LLM extraction; duplicates are free.
    """
    keywords = (payload.get("keywords") or "").strip()
    if not keywords:
        raise HTTPException(status_code=400, detail="keywords is required")
    # ponytail: synchronous and capped at 10 so the request finishes inside proxy timeouts; move to a background task if bigger imports are needed
    limit = max(1, min(int(payload.get("limit") or 10), 10))
    try:
        ids = await linkedin_guest.search_ids(keywords, payload.get("location") or "",
                                              payload.get("posted_within") or "week", limit=limit * 3)
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))

    added, duplicates, failed = [], 0, []
    for job_id in ids:
        if len(added) >= limit:
            break
        url = linkedin_guest.canonical_url(job_id)
        if _find_duplicate_job(db, current_user.id, url=url):
            duplicates += 1
            continue
        try:
            res = await track_url({"url": url, "llm": payload.get("llm")}, db, current_user)
        except HTTPException as e:
            failed.append({"url": url, "error": e.detail})
            continue
        if res.get("duplicate"):
            duplicates += 1
        else:
            added.append(res["job"])
        await asyncio.sleep(1.5)  # stay well under LinkedIn's burst limit

    return {"found": len(ids), "added": added, "duplicates": duplicates, "failed": failed}


async def _linkedin_twin(j: Dict[str, str]) -> str:
    """LinkedIn URL of the same posting (same company, same title) as a Glassdoor alert job, or ''."""
    import mail_updates
    try:
        ids = await linkedin_guest.search_ids(f'{j["title"]} {j["company"]}', j.get("location") or "", "month", limit=5)
        for job_id in ids[:5]:
            li = await linkedin_guest.fetch_job(job_id)
            if mail_updates._says(li["company"], j["company"]) and mail_updates._says(li["title"], j["title"]):
                return linkedin_guest.canonical_url(job_id)
            await asyncio.sleep(1)
    except ValueError:
        pass
    return ""


@app.post("/api/agent/scan-mail")
async def scan_mail(payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Add jobs from LinkedIn / Indeed / Glassdoor alert emails in the Gmail inbox set by GMAIL_ADDRESS (read-only IMAP).
    Body: { "days": 2, "limit": 10, "llm": "local" }
    Re-scanning is cheap: postings already tracked are skipped before any fetch or LLM call.
    Indeed jobs only have the alert-email snippet as their JD and are flagged meta.partial_jd.
    """
    import mail_alerts
    # ponytail: one mailbox from env, fine for the local single-user instance; per-user creds in Settings if Fly users need it
    limit = max(1, min(int(payload.get("limit") or 10), 10))
    try:
        found = await asyncio.to_thread(mail_alerts.fetch_alert_jobs, int(payload.get("days") or 2))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except imaplib.IMAP4.error as e:
        raise HTTPException(status_code=401, detail=f"Gmail login failed: {e}")

    added, duplicates, failed = [], 0, []
    for j in found:
        if len(added) >= limit:
            break
        if _find_duplicate_job(db, current_user.id, url=j["url"], company=j.get("company"), title=j.get("title")):
            duplicates += 1
            continue
        try:
            if j["source"] == "glassdoor":
                # Glassdoor pages are behind Cloudflare: take the full posting from LinkedIn when it is there.
                li = await _linkedin_twin(j)
                if li:
                    j = {**j, "source": "linkedin", "url": li}
            if j["source"] == "linkedin":
                res = await track_url({"url": j["url"], "llm": payload.get("llm")}, db, current_user)
                await asyncio.sleep(1.5)  # LinkedIn burst limit
            else:
                res = await track_jd_text({"url": j["url"], "text": j["text"], "llm": payload.get("llm")}, db, current_user)
                if not res.get("duplicate"):
                    job = db.query(Job).get(res["job_id"])
                    job.meta = {**(job.meta or {}), "partial_jd": True}
                    db.commit()
                    res["job"]["partial_jd"] = True
        except HTTPException as e:
            failed.append({"url": j["url"], "error": e.detail})
            continue
        if res.get("duplicate"):
            duplicates += 1
        else:
            added.append({**res["job"], "url": j["url"]})

    return {"found": len(found), "added": added, "duplicates": duplicates, "failed": failed}


@app.post("/api/agent/workday-verify-link")
async def workday_verify_link(payload: Dict[str, Any], current_user: User = Depends(get_current_user)):
    """
    Newest Workday account-verification link from the Gmail inbox (read-only), for the apply agent.
    Body: { "host": "manulife.wd3.myworkdayjobs.com", "minutes": 30 }  →  { "link": "..." | "" }
    Only links on *.myworkdayjobs.com (and on `host`, when given) are ever returned.
    """
    import mail_alerts
    try:
        link = await asyncio.to_thread(mail_alerts.fetch_workday_verify_link,
                                       (payload.get("host") or "").strip().lower(), int(payload.get("minutes") or 30))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except imaplib.IMAP4.error as e:
        raise HTTPException(status_code=401, detail=f"Gmail login failed: {e}")
    return {"link": link}


@app.post("/api/agent/email-code")
async def agent_email_code(payload: Dict[str, Any], current_user: User = Depends(get_current_user)):
    """
    Newest one-time code an application system (Oracle, Greenhouse, Workday, ...) emailed the user.
    Body: { "minutes": 15 }  →  { "code": "123456", "sender": "oraclecloud.com" } | {}
    Mail from any other sender is never downloaded, so this can't read Google or bank codes.
    """
    import mail_alerts
    try:
        return await asyncio.to_thread(mail_alerts.fetch_ats_code, int(payload.get("minutes") or 15))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except imaplib.IMAP4.error as e:
        raise HTTPException(status_code=401, detail=f"Gmail login failed: {e}")


@app.post("/api/agent/confirmation-email")
async def agent_confirmation_email(payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Emails confirming an application to this job's company, for applications made outside an
    apply run. Body: { "job_id": 47, "days": 14 } → { "company", "emails": [{from, subject, date, matched_on}] }
    Headers only; no message body is read.
    """
    import mail_alerts
    job = db.query(Job).filter(Job.id == int(payload.get("job_id") or 0), Job.user_id == current_user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        emails = await asyncio.to_thread(mail_alerts.fetch_confirmations, job.company, job.title, int(payload.get("days") or 14))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except imaplib.IMAP4.error as e:
        raise HTTPException(status_code=401, detail=f"Gmail login failed: {e}")
    return {"job_id": job.id, "company": job.company, "emails": emails}


@app.post("/api/agent/scan-updates")
async def agent_scan_updates(payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Application status updates from Gmail (received / rejected / interview / assessment / offer),
    applied to the matching jobs. Each email is handled once. Body: { "days": 3 }
    → { "checked": n, "updates": [{job_id?, company, title, kind, old, new, subject, from, date}] }
    Status only moves forward, and only when the email names the job's title (company-only
    matches are reported, not applied).
    """
    try:
        return await scan_mail_updates(db, current_user.id, int(payload.get("days") or 3))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except imaplib.IMAP4.error as e:
        raise HTTPException(status_code=401, detail=f"Gmail login failed: {e}")


async def scan_mail_updates(db: Session, user_id: int, days: int) -> Dict[str, Any]:
    """The Gmail status scan behind /api/agent/scan-updates (also run by scheduler.py).
    Raises ValueError (Gmail not configured) and imaplib.IMAP4.error (login failed)."""
    import mail_updates as mu
    seen = json.loads(_setting(db, user_id, "mail_updates_seen") or "[]")
    jobs = [{"id": j.id, "company": j.company or "", "title": j.title or "", "status": j.status, "applied_at": j.applied_at}
            for j in db.query(Job).filter(Job.user_id == user_id, Job.status != "withdrawn").all()]
    mails = await asyncio.to_thread(mu.fetch, days, set(seen), jobs)
    router = get_llm_router(db, user_id)
    llm = os.getenv("MAIL_LLM") or resolve_llm(db, user_id)   # MAIL_LLM=local keeps mail on the local model
    updates = []
    for m in reversed(mails):                             # oldest first, so statuses move in order
        try:
            kind = await mu.classify(router, m, loads=_parse_llm_json, llm=llm)
        except Exception as e:                            # local model down: keep it unseen, retry next scan
            logger.warning(f"[mail-updates] classify failed, will retry: {e}")
            break
        seen.append(m["id"])
        mj = m["job"]
        if kind == "other" or (not mj and kind == "received"):
            continue
        u = {"kind": kind, "subject": m["subject"], "from": m["from"], "date": m["date"],
             "company": mj.get("company", ""), "title": mj.get("title", ""), "job_id": mj.get("id")}
        if mj:
            job = db.query(Job).get(mj["id"])
            named = mu._says(m["subject"] + " " + m["body"], job.title or "")
            new = mu.new_status(job.status, kind) if named else ""
            u.update(old=job.status, new=new or job.status, title_matched=named)
            if new:
                job.status = new
                if new == "applied" and not job.applied_at:
                    job.applied_at = datetime.utcnow()
                if new == "rejected":
                    job.rejection_reason = m["subject"][:200]
                mj["status"] = new
            elif kind == "received":
                continue                                  # already past "applied": nothing to say
            _log_event(db, job.id, "status_change" if new else "agent_action", f"Email: {kind}",
                       f'"{m["subject"]}" from {m["from"]} ({m["date"]})', old_value=u["old"], new_value=u["new"],
                       source="email", user_id=user_id)
        updates.append(u)
    _put_setting(db, user_id, "mail_updates_seen", json.dumps(seen[-1000:]))
    db.commit()
    return {"checked": len(mails), "updates": updates}


@app.post("/api/agent/track-jd")
async def track_jd_text(payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Extract job data from pasted JD text (for LinkedIn and login-walled sites).
    Body: { "text": "...", "llm": "gemini", "url"?: "where the JD came from" }
    """
    text = payload.get("text", "").strip()
    source_url = (payload.get("url") or "").strip()
    existing = _find_duplicate_job(db, current_user.id, url=source_url) if source_url else None
    if existing:
        return {"job_id": existing.id, "duplicate": True, "job": {
            "id": existing.id, "company": existing.company, "title": existing.title,
            "status": existing.status, "platform": existing.platform, "remote": existing.remote,
        }}
    llm = resolve_llm(db, current_user.id, payload.get("llm"))
    if not text or len(text) < 50:
        raise HTTPException(status_code=400, detail="Please paste the full job description text (at least 50 characters).")

    try:
        agent = get_agent("discovery", db, current_user.id)
        data = await agent.analyze_text(text, url="", llm=llm)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Job extraction failed: {e}")

    existing = _find_duplicate_job(db, current_user.id, url=data.get("url"),
                                   company=data.get("company"), title=data.get("title"))
    if existing:
        return {"job_id": existing.id, "duplicate": True, "job": {
            "id": existing.id, "company": existing.company, "title": existing.title,
            "status": existing.status, "platform": existing.platform, "remote": existing.remote,
        }}

    job_url = source_url or data.get("url") or ""
    job = Job(
        company=data.get("company") or "Unknown",
        title=data.get("title") or "Unknown",
        url=job_url,
        job_description=data.get("job_description") or text,
        location=data.get("location", ""),
        remote=data.get("remote", False),
        salary_min=_as_int(data.get("salary_min")),
        salary_max=_as_int(data.get("salary_max")),
        platform=_platform_for(job_url, data.get("platform") or "linkedin"),
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

# ── Agent: Application-question answering (browser-extension autofill) ────────

class _AppQuestion(_BaseModel):
    id: str
    label: str
    type: str = "text"
    options: Optional[List[str]] = None
    maxlen: Optional[int] = None


class _AppAnswer(_BaseModel):
    id: str
    answer: Optional[str] = None
    drafted: bool = False   # written by the model rather than taken from a stated fact


class _AppAnswerList(_BaseModel):
    answers: List[_AppAnswer]


def _validate_answers(answers: List[dict], questions: List[dict]) -> List[dict]:
    """Post-validate LLM answers: unknown ids dropped, option answers must match
    one of the field's options (case-insensitive), maxlen enforced, every
    question gets a row (missing → null). Pure function — unit tested."""
    q_by_id = {q["id"]: q for q in questions}
    out = {}
    for a in answers:
        q = q_by_id.get(a.get("id"))
        if not q:
            continue
        ans = a.get("answer")
        if ans is not None:
            ans = str(ans).strip() or None
        if ans is not None and q.get("options"):
            match = next((o for o in q["options"] if o.strip().lower() == ans.strip().lower()), None)
            ans = match  # None if the model invented a choice
        if ans is not None and q.get("maxlen"):
            ans = ans[: int(q["maxlen"])]
        out[q["id"]] = (ans, bool(a.get("drafted")) and ans is not None)
    return [{"id": qid, "answer": out.get(qid, (None, False))[0], "drafted": out.get(qid, (None, False))[1]}
            for qid in q_by_id]


def _qa_components_text(db: Session, user_id: int) -> str:
    """Q&A/questionnaire components — extra ground truth beyond the resume."""
    components = db.query(MasterResumeComponent).filter(
        MasterResumeComponent.is_active == True,
        MasterResumeComponent.user_id == user_id,
    ).all()
    parts = [c.content_text for c in components
             if _classify_component(c.name, c.content_text or "") == "qa" and c.content_text]
    return "\n\n".join(parts)


SYSTEM_APP_ANSWERS = """You answer job-application form questions on behalf of a candidate.
You are given the candidate's verified background (resume + questionnaire answers) and the job description.
STRICT RULES:
- Answer ONLY from the provided background. Never invent, guess, or embellish facts.
- Values DERIVABLE from the background count as grounded: total years of experience computed from work-history dates, seniority from titles, tech familiarity from listed skills.
- If the background does not contain the information needed, return null for that question's answer.
- When a question lists options, the answer MUST be exactly one of the options (verbatim).
- Respect maxlen when given. Write in first person, concise and professional.
- Legal/eligibility questions (visa, work authorization, criminal record, etc.): null unless explicitly stated in the background.
- The APPLICATION PROFILE is filled in by the candidate and overrides anything else. Its custom Q&A answers apply to any question asking the same thing.
- Voluntary demographic (EEO) questions: use the profile's answer; if it has none, pick the "decline to answer"/"prefer not to say" option, or null if there is none.
- Set drafted=false on every answer unless a rule below allows drafting."""

SYSTEM_APP_DRAFTS = """
- DRAFTING ALLOWED for open-ended prose questions only (why this company, why this role, tell us about a project, cover-letter style): write a short answer from the background and job description and set drafted=true. Never draft facts: eligibility, salary, dates, years, degrees, yes/no or option questions stay grounded or null."""


APPLICATION_PROFILE_KEY = "application_profile"


def _application_profile(db: Session, user_id: int) -> Dict[str, Any]:
    try:
        return json.loads(_setting(db, user_id, APPLICATION_PROFILE_KEY) or "{}")
    except ValueError:
        return {}


def _application_profile_text(profile: Dict[str, Any]) -> str:
    """The form as "label: value" lines for the answer prompt; blanks dropped."""
    lines = [f"{k.replace('_', ' ')}: {v}" for k, v in profile.items()
             # work/education history duplicate the master resume, which the prompt already has
             if not k.startswith("_") and k not in ("custom_qa", "work_history", "education_history")
             and str(v or "").strip()]
    lines += [f"Q: {qa.get('q')}\nA: {qa.get('a')}" for qa in profile.get("custom_qa") or []
              if str(qa.get("q") or "").strip() and str(qa.get("a") or "").strip()]
    return "\n".join(lines)


@app.get("/api/apply/profile")
def get_application_profile(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _application_profile(db, current_user.id)


@app.put("/api/apply/profile")
def put_application_profile(payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The user's own answers to common application questions -- ground truth for the apply agent."""
    raw = json.dumps(payload)
    if len(raw) > 50_000:
        raise HTTPException(status_code=400, detail="Profile too large")
    _put_setting(db, current_user.id, APPLICATION_PROFILE_KEY, raw)
    db.commit()
    return payload


@app.post("/api/agent/answer-questions")
async def answer_questions(payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Answer application-form questions from ground truth only.
    Body: { "job_id": 1, "questions": [{id,label,type,options?,maxlen?}], "llm"?, "guidance"? }
    Returns: { "answers": [{id, answer|null}] }  — null = no ground truth, leave blank.
    """
    job_id = payload.get("job_id")
    raw_questions = payload.get("questions") or []
    if not job_id or not raw_questions:
        raise HTTPException(status_code=400, detail="job_id and questions are required")
    job = get_owned_job(db, job_id, current_user)

    try:
        questions = [_AppQuestion(**q).model_dump() for q in raw_questions]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid question shape: {e}")

    background = get_master_resume(db, current_user.id)
    qa_text = _qa_components_text(db, current_user.id)
    contact = contact_facts(db, current_user.id)
    profile = _application_profile(db, current_user.id)
    profile_text = _application_profile_text(profile)
    if not background and not qa_text and not profile_text:
        raise HTTPException(status_code=400, detail="No master resume configured — nothing to answer from.")

    llm = resolve_llm(db, current_user.id, payload.get("llm"))
    router = get_llm_router(db, current_user.id)
    guidance = payload.get("guidance", "")

    prompt = f"""JOB: {job.title} at {job.company}

JOB DESCRIPTION:
{(job.job_description or "")[:6000]}

CANDIDATE CONTACT FACTS (verified):
{json.dumps(contact)}

{"APPLICATION PROFILE (filled in by the candidate -- highest priority):" + chr(10) + profile_text if profile_text else ""}

CANDIDATE BACKGROUND (verified — the ONLY source of truth):
{background[:12000]}

{"ADDITIONAL Q&A BACKGROUND:" + chr(10) + qa_text[:6000] if qa_text else ""}

{"USER GUIDANCE FOR THIS RUN: " + guidance if guidance else ""}

APPLICATION QUESTIONS:
{json.dumps(questions, indent=2)}

Answer each question. Return null for any answer not grounded in the background."""

    try:
        system = SYSTEM_APP_ANSWERS + (SYSTEM_APP_DRAFTS if profile.get("_allow_drafts") else "")
        result = await router.structured_complete(prompt, _AppAnswerList, llm=llm, system=system)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Answer generation failed: {e}")

    answers = _validate_answers([a.model_dump() for a in result.answers], questions)
    drafted = [q["label"] for q, a in zip(questions, answers) if a["drafted"]]
    _log_event(db, job.id, "agent_action", "Application questions answered",
               f"{sum(1 for a in answers if a['answer'] and not a['drafted'])} of {len(answers)} fields grounded"
               + (f"; drafted (review): {', '.join(drafted)}" if drafted else ""),
               source="agent", user_id=current_user.id)
    _add_form_entries(db, job, current_user.id, [
        {"label": q["label"], "value": a["answer"], "kind": "proposed", "drafted": a["drafted"]}
        for q, a in zip(questions, answers)], source="answers")
    return {"answers": answers}


# ── Application form data (what each application was filled with) ─────────────

def _add_form_entries(db: Session, job: Job, user_id: int, entries: List[Dict[str, Any]], source: str):
    attempt = (job.meta or {}).get("apply_attempts") or 1
    for e in entries:
        label = str(e.get("label") or "").strip()
        if not label:
            continue
        value = e.get("value")
        db.add(ApplicationFormEntry(
            job_id=job.id, user_id=user_id, attempt=attempt, label=label[:500],
            value=None if value is None else str(value)[:4000], kind=e.get("kind"), source=source,
            drafted=bool(e.get("drafted")), page_url=(e.get("page_url") or None)))
    db.commit()


@app.post("/api/apply/{job_id}/form")
def add_form_entries(job_id: int, payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Body: {entries: [{label, value, kind, page_url?}]} -- fields the apply agent filled."""
    job = get_owned_job(db, job_id, current_user)
    _add_form_entries(db, job, current_user.id, payload.get("entries") or [], source="browser")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/form")
def get_form_entries(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Every field of every apply attempt, oldest first; the last browser value per label is
    what was submitted."""
    get_owned_job(db, job_id, current_user)
    rows = db.query(ApplicationFormEntry).filter_by(job_id=job_id, user_id=current_user.id) \
        .order_by(ApplicationFormEntry.id).all()
    return [{"attempt": r.attempt, "label": r.label, "value": r.value, "kind": r.kind, "source": r.source,
             "drafted": r.drafted, "page_url": r.page_url, "at": r.created_at.isoformat()} for r in rows]


def merge_contact(read: dict, resume_name: str, resume_contact: str, form: Dict[str, Any]) -> dict:
    """Contact facts for application forms. The application form the user filled in
    wins, then the parsed resume's header, then the regex read of the raw resume
    (which can mistake a job title for the name)."""
    c = dict(read)
    if (resume_name or "").strip():
        c["name"] = resume_name.strip()
    # Header line: "Kitchener, Ontario, Canada | +1 ... | email | linkedin"
    first = (resume_contact or "").split("|")[0].strip()
    if first and "@" not in first and not re.search(r"\d", first):
        c["location"] = first
    for key, field in (("preferred_name", "preferred_name"), ("phone", "phone"),
                       ("linkedin_url", "linkedin"), ("github_url", "github"),
                       ("portfolio_url", "website"), ("street_address", "street_address"),
                       ("postal_code", "postal_code")):
        v = str(form.get(key) or "").strip()
        if v:
            c[field] = v
    names = c.get("name", "").split()
    # one word ("Girijesh") is a first name with no last name, not nothing
    c["first_name"], c["last_name"] = (" ".join(names[:-1]), names[-1]) if len(names) > 1 else ((names or [""])[0], "")
    parts = [x.strip() for x in c.get("location", "").split(",")]
    c["city"], c["province"], c["country"] = (parts + ["", "", ""])[:3]
    c["problems"] = contact_problems(c)
    return c


def contact_problems(c: dict) -> List[str]:
    """Sanity checks on what an application form will receive. A non-empty list
    means a reader upstream is broken -- the agent stops instead of typing it."""
    p = []
    name = c.get("name", "")
    if not c.get("last_name") or re.search(r"[*#()\d|@]", name) or len(name.split()) > 4 \
            or re.search(r"\b(scientist|engineer|lead|manager|developer|analyst|present)\b", name, re.I):
        p.append(f"name looks wrong: {name!r}")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.\w+", c.get("email", "")):
        p.append("email missing")
    if len(re.sub(r"\D", "", c.get("phone", ""))) < 10:
        p.append("phone missing")
    loc = c.get("location", "").lower()
    if not c.get("city") or any(t.lower() in loc for t in name.split() if len(t) > 2):
        p.append(f"location looks wrong: {c.get('location', '')!r}")
    return p


def contact_facts(db: Session, user_id: int) -> dict:
    rp = load_resume_profile(db, user_id)
    return merge_contact(extract_contact_info(get_raw_resume_content(db, user_id)),
                         rp.name if rp else "", rp.contact if rp else "",
                         _application_profile(db, user_id))


@app.get("/api/profile/contact")
def profile_contact(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Contact facts (no LLM) for the extension and the apply agent."""
    return contact_facts(db, current_user.id)


# ── Apply queue (consumed by the OpenClaw apply agent) ─────────────────────────
# The agent asks for one job at a time; caps and approval are decided here, so
# the agent never controls the volume.

def _platform_for(url: Optional[str], extracted: Optional[str]) -> str:
    """The URL's host is authoritative; the LLM's guess only fills "other"."""
    detected = aq.detect_platform(url)
    if detected != "other":
        return detected
    guess = (extracted or "").lower()
    return guess if guess and guess != "direct" else "other"


def _find_duplicate_job(db: Session, user_id: int, url: str = None,
                        company: str = None, title: str = None) -> Optional[Job]:
    norm = aq.normalize_url(url)
    if norm:
        # ids and URLs only (not whole rows with their descriptions); normalize_url is cached.
        # ponytail: still a scan per insert -- store the normalized URL in an indexed column past ~50k jobs
        for jid, u in db.query(Job.id, Job.url).filter(Job.user_id == user_id, Job.url.isnot(None), Job.url != ""):
            if aq.normalize_url(u) == norm:
                return db.get(Job, jid)
    if company and title and company != "Unknown":
        since = datetime.utcnow() - timedelta(days=60)
        return db.query(Job).filter(
            Job.user_id == user_id, Job.created_at >= since,
            func.lower(Job.company) == company.lower(), func.lower(Job.title) == title.lower(),
        ).first()
    return None


def _setting(db: Session, user_id: int, key: str) -> Optional[str]:
    row = db.query(Settings).filter(Settings.key == key, Settings.user_id == user_id).first()
    return row.value if row else None


def _put_setting(db: Session, user_id: int, key: str, value: str):
    row = db.query(Settings).filter(Settings.key == key, Settings.user_id == user_id).first()
    if row:
        row.value = value
        row.updated_at = datetime.utcnow()
    else:
        db.add(Settings(key=key, value=value, user_id=user_id))


def _apply_config(db: Session, user_id: int) -> Dict[str, Any]:
    def num(key, default, cast=float):
        try:
            return cast(float(_setting(db, user_id, key) or default))
        except ValueError:
            return default
    cfg = {
        "auto_apply": (_setting(db, user_id, aq.AUTO_APPLY_KEY) or "").lower() == "true",
        "paused": (_setting(db, user_id, aq.PAUSED_KEY) or "").lower() == "true",
        "min_score": num(aq.MIN_SCORE_KEY, aq.DEFAULT_MIN_SCORE),
        "max_attempts": num(aq.MAX_ATTEMPTS_KEY, aq.MAX_ATTEMPTS, int),
        "claim_ttl_minutes": num(aq.CLAIM_TTL_KEY, int(aq.CLAIM_TTL.total_seconds() // 60), int),
        "caps": aq.parse_caps(_setting(db, user_id, aq.CAPS_KEY)),
        "timezone": _setting(db, user_id, aq.TIMEZONE_KEY) or aq.DEFAULT_TIMEZONE,
        "max_posting_age_hours": num(aq.MAX_AGE_KEY, aq.DEFAULT_MAX_AGE_HOURS, int),
        "auto_min_score": num(aq.AUTO_MIN_SCORE_KEY, aq.DEFAULT_AUTO_MIN_SCORE),
    }
    # min_score decides which jobs get a tailored resume; auto mode only applies on its
    # own at the stricter auto_min_score -- review mode still shows everything >= min_score.
    cfg["auto_min_score"] = max(cfg["auto_min_score"], cfg["min_score"])
    # Two modes, one switch: auto = approve at/above min_score and apply on schedule;
    # review = nothing is applied until the user approves the resume in #resume-review.
    cfg["mode"] = "auto" if cfg["auto_apply"] else "review"
    return cfg


def _claim_ttl(cfg: Dict[str, Any]) -> timedelta:
    return timedelta(minutes=cfg["claim_ttl_minutes"])


def _latest_resume_pdf(db: Session, job_id: int) -> Optional[ResumeVersion]:
    rows = (db.query(ResumeVersion).filter(ResumeVersion.job_id == job_id)
            .order_by(ResumeVersion.version.desc()).all())
    return next((r for r in rows if r.pdf_path and os.path.exists(r.pdf_path)), None)


def _apply_usage(db: Session, user_id: int, cfg: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    """Per-platform {used, cap} for the user's current day. In-flight claims
    count as used, so two concurrent agent runs can't overshoot a cap."""
    now = datetime.utcnow()
    day, start, end = aq.day_bounds_utc(cfg["timezone"], now)
    used: Dict[str, int] = {}
    applied = db.query(Job.platform).filter(
        Job.user_id == user_id, Job.applied_at >= start, Job.applied_at < end).all()
    for (platform,) in applied:
        used[platform or "other"] = used.get(platform or "other", 0) + 1
    for job in db.query(Job).filter(Job.user_id == user_id, Job.status.in_(["applying", "waiting_human"])).all():
        if job.status == "waiting_human" or not aq.claim_is_stale(
                (job.meta or {}).get("apply_claimed_at"), now, _claim_ttl(cfg)):
            used[job.platform or "other"] = used.get(job.platform or "other", 0) + 1
    platforms = set(used) | {p for p in cfg["caps"] if p != "default"}
    return {p: {"used": used.get(p, 0), "cap": aq.daily_cap(cfg["caps"], p, user_id, day)}
            for p in sorted(platforms)}


@app.get("/api/apply/config")
def get_apply_config(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _apply_config(db, current_user.id)


@app.put("/api/apply/config")
def put_apply_config(payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Body (all optional): {mode: auto|review, auto_apply: bool, paused: bool, min_score: 0-100,
    auto_min_score: 0-100 (auto mode's bar; never below min_score),
    max_attempts: 1-10, max_posting_age_hours: 1-720,
    claim_ttl_minutes: 10-1440, caps: {platform: n | [lo, hi]}, timezone}"""
    if "mode" in payload:
        if payload["mode"] not in ("auto", "review"):
            raise HTTPException(status_code=400, detail="mode must be auto or review")
        payload = {**payload, "auto_apply": payload["mode"] == "auto"}
    for field, key in (("auto_apply", aq.AUTO_APPLY_KEY), ("paused", aq.PAUSED_KEY)):
        if field in payload:
            _put_setting(db, current_user.id, key, "true" if payload[field] else "false")
    for field, key, lo, hi in (("max_attempts", aq.MAX_ATTEMPTS_KEY, 1, 10),
                               ("claim_ttl_minutes", aq.CLAIM_TTL_KEY, 10, 1440),
                               ("max_posting_age_hours", aq.MAX_AGE_KEY, 1, 720)):
        if field in payload:
            try:
                n = int(payload[field])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"{field} must be a whole number")
            if not lo <= n <= hi:
                raise HTTPException(status_code=400, detail=f"{field} must be between {lo} and {hi}")
            _put_setting(db, current_user.id, key, str(n))
    for field, key in (("min_score", aq.MIN_SCORE_KEY), ("auto_min_score", aq.AUTO_MIN_SCORE_KEY)):
        if field not in payload:
            continue
        try:
            score = float(payload[field])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{field} must be a number")
        if not 0 <= score <= 100:
            raise HTTPException(status_code=400, detail=f"{field} must be between 0 and 100")
        _put_setting(db, current_user.id, key, str(score))
    if "caps" in payload:
        if not isinstance(payload["caps"], dict):
            raise HTTPException(status_code=400, detail="caps must be an object")
        bad = [k for k, v in payload["caps"].items() if not aq.valid_cap(v)]
        if bad:
            raise HTTPException(status_code=400, detail=f"caps must be a whole number or [low, high]: {', '.join(map(str, bad))}")
        _put_setting(db, current_user.id, aq.CAPS_KEY, json.dumps(payload["caps"]))
    if "timezone" in payload:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(payload["timezone"])
        except Exception:
            raise HTTPException(status_code=400, detail="Unknown timezone")
        _put_setting(db, current_user.id, aq.TIMEZONE_KEY, payload["timezone"])
    db.commit()
    return _apply_config(db, current_user.id)


@app.get("/api/apply/status")
def apply_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Today's usage per platform plus queue depth -- for the UI and the agent."""
    cfg = _apply_config(db, current_user.id)
    by_status = dict(db.query(Job.status, func.count(Job.id)).filter(
        Job.user_id == current_user.id,
        Job.status.in_(["pending", "approved", "applying", "waiting_human", "needs_human"]),
    ).group_by(Job.status).all())
    return {"auto_apply": cfg["auto_apply"], "paused": cfg["paused"], "min_score": cfg["min_score"],
            "today": _apply_usage(db, current_user.id, cfg), "queue": by_status}


# Why the agent handed a job back, from its own note. First match wins.
# ponytail: keyword heuristic on free-text notes; store a structured cause on /result if it misfiles.
HANDOFF_CAUSES = [
    ("workday", "Workday account — agent can do these now (Retry)", r"workday|myworkdayjobs"),
    ("easy_apply", "LinkedIn Easy Apply only", r"easy apply"),
    ("not_found", "No company posting found", r"no public apply|not found|no reachable|none found"),
    ("account", "Needs an account / sign-in", r"account|sign[- ]?in|log ?in|passport\.amazon"),
    ("bot", "Bot wall / CAPTCHA", r"captcha|bot|access denied|incapsula|cloudflare|robot"),
    ("answers", "Missing answers", r"no grounded|required field|has no grounded|missing"),
]


def handoff_cause(note: str) -> str:
    n = (note or "").lower()
    return next((k for k, _, rx in HANDOFF_CAUSES if re.search(rx, n)), "other")


@app.get("/api/apply/handoff")
def apply_handoff(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Jobs the agent handed back, grouped by cause, with what you need to apply yourself."""
    jobs = db.query(Job).filter(Job.user_id == current_user.id, Job.status == "needs_human").all()
    groups: Dict[str, list] = {}
    for job in jobs:
        ev = (db.query(ApplicationEvent).filter(ApplicationEvent.job_id == job.id,
                                                ApplicationEvent.title == "Apply agent: needs human")
              .order_by(ApplicationEvent.id.desc()).first())
        note = ev.description if ev else ""
        rv = (db.query(ResumeVersion).filter(ResumeVersion.job_id == job.id)
              .order_by(ResumeVersion.version.desc()).first())
        careers = re.search(r"\[careers: (\S+?)\]", job.notes or "")
        groups.setdefault(handoff_cause(note), []).append({
            "id": job.id, "company": job.company, "title": job.title, "url": job.url,
            "careers_url": careers.group(1) if careers else None, "note": note,
            "resume_version": rv.version if rv else None,
            "has_pdf": bool(rv and rv.pdf_path), "has_docx": bool(rv and rv.docx_path),
            "answers": (job.meta or {}).get("apply_answers") or [],
        })
    labels = {k: label for k, label, _ in HANDOFF_CAUSES} | {"other": "Other"}
    order = [k for k, _, _ in HANDOFF_CAUSES] + ["other"]
    return [{"cause": k, "label": labels[k], "jobs": groups[k]} for k in order if k in groups]


# A run that stops for the user (CAPTCHA, sign-in, missing answers, email code) parks
# its job as waiting_human: the queue never hands it out again on its own, so a slow
# reply can't lead to a second application of the same job. The 2-hour claim expiry
# only applies to "applying", i.e. runs that died.
@app.post("/api/apply/{job_id}/wait")
def apply_wait(job_id: int, payload: Dict[str, Any] = {}, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Body: {reason, kind?: wait|question|code|captcha|submit, url?, photo_b64?, doc_b64?, doc_name?, minutes?: 15}. Only a job this run
    claimed (applying) can wait. The user is asked on Telegram. If they answer within `minutes`
    the waiting run continues (it polls GET /api/apply/{id}/ask); later, the job is re-approved
    and the watchdog starts a fresh run."""
    job = get_owned_job(db, job_id, current_user)
    if job.status != "applying":
        raise HTTPException(409, f"Job is {job.status}, not applying -- only a claimed job can wait")
    reason = (payload.get("reason") or "").strip() or "waiting for the user"
    kind = payload.get("kind") if payload.get("kind") in ("question", "code", "captcha", "submit") else "wait"
    deadline = datetime.utcnow() + timedelta(minutes=int(payload.get("minutes") or 15))
    text, markup = telegram_bot.ask_message(job, kind, reason, payload.get("url"))
    _send_doc(db, current_user.id, payload)
    import base64   # photo: e.g. the Review page, so he sees exactly what would be sent
    sent = _chat_send(db, current_user.id, text, markup,
                      base64.b64decode(payload["photo_b64"]) if payload.get("photo_b64") else None)
    job.status = "waiting_human"
    job.meta = {**(job.meta or {}), "waiting_since": datetime.utcnow().isoformat(), "waiting_reason": reason,
                "ask": {"q": reason, "kind": kind, **sent, "deadline": deadline.isoformat()}}
    db.commit()
    _log_event(db, job.id, "status_change", "Apply agent: waiting for you", reason,
               old_value="applying", new_value="waiting_human", source="agent", user_id=current_user.id)
    return {"job_id": job.id, "status": job.status, "telegram": bool(sent["msg"]), "discord": bool(sent["dmsg"])}


def _send_doc(db: Session, user_id: int, payload: Dict[str, Any]):
    """An attached file (doc_b64 + doc_name), sent ahead of the message it belongs to."""
    if payload.get("doc_b64"):
        import base64
        name = re.sub(r"[^\w.-]+", "_", payload.get("doc_name") or "review.pdf")[:80]
        data = base64.b64decode(payload["doc_b64"])
        telegram_bot.send_document(db, user_id, data, name)
        discord_bot.send_document(db, user_id, data, name)


@app.get("/api/apply/{job_id}/ask")
def apply_ask_status(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """What the waiting run polls: status goes back to applying once the user answered in time."""
    job = get_owned_job(db, job_id, current_user)
    ask = (job.meta or {}).get("ask") or {}
    return {"job_id": job.id, "status": job.status, "kind": ask.get("kind"), "answer": ask.get("answer")}


@app.post("/api/notify")
def notify_user(payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Body: {text, retry_job_id?, photo_b64?, doc_b64?, doc_name?}. A message (or screenshot + caption) to the user's linked Telegram chat."""
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    rid = payload.get("retry_job_id")
    markup = telegram_bot.retry_button(int(rid)) if rid else None
    _send_doc(db, current_user.id, payload)
    import base64
    sent = _chat_send(db, current_user.id, text, markup,
                      base64.b64decode(payload["photo_b64"]) if payload.get("photo_b64") else None)
    return {"sent": bool(sent["msg"] or sent["dmsg"])}


@app.post("/api/apply/{job_id}/continue")
def apply_continue(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The user answered: the same run takes the job back, with a fresh claim."""
    job = get_owned_job(db, job_id, current_user)
    if job.status != "waiting_human":
        raise HTTPException(409, f"Job is {job.status}, not waiting")
    meta = {k: v for k, v in (job.meta or {}).items() if k not in ("waiting_since", "waiting_reason")}
    meta["apply_claimed_at"] = datetime.utcnow().isoformat()
    job.status, job.meta = "applying", meta
    db.commit()
    _log_event(db, job.id, "status_change", "Apply agent: continuing", None,
               old_value="waiting_human", new_value="applying", source="agent", user_id=current_user.id)
    return {"job_id": job.id, "status": job.status}


@app.post("/api/jobs/{job_id}/approve")
def approve_job(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = get_owned_job(db, job_id, current_user)
    if job.status in ("applied", "applying"):
        raise HTTPException(status_code=409, detail=f"Job is already {job.status}")
    old, job.status = job.status, "approved"
    # A fresh approval (e.g. after needs_human) gets a fresh set of attempts. Approving a job held
    # for the company cooldown is the user choosing to apply anyway.
    meta = {k: v for k, v in (job.meta or {}).items() if k not in ("apply_attempts", "apply_claimed_at", "company_hold")}
    if (job.meta or {}).get("company_hold"):
        meta["company_ok"] = True
    job.meta = meta
    db.commit()
    _log_event(db, job.id, "status_change", "Approved for applying", old_value=old, new_value="approved",
               source="user", user_id=current_user.id)
    return {"job_id": job.id, "status": job.status}


@app.post("/api/apply/next")
def apply_next(payload: Dict[str, Any] = {}, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Claim the next job to apply to. Body (optional): {platforms: ["greenhouse", ...], job_id: 91}.
    With job_id, only that job can be claimed (an apply worker spawned for one job).

    Picks an approved job (or, with auto-apply on, a pending one at/above the
    minimum score) that has a tailored resume PDF and whose platform still has
    room under today's cap. 204 when nothing is ready; 429 when every requested
    platform is at its cap; 423 when the user paused the agent.
    """
    cfg = _apply_config(db, current_user.id)
    if cfg["paused"]:
        raise HTTPException(status_code=423, detail="Auto-apply is paused in HireOS settings")
    usage = _apply_usage(db, current_user.id, cfg)
    wanted = [p.lower() for p in (payload.get("platforms") or [])]
    # Platforms the HireOS apply worker owns: hidden from other claimers (OpenClaw), and the
    # only ones the worker (payload.worker) takes.
    worker_platforms = {p.strip().lower() for p in (_setting(db, current_user.id, "worker_platforms") or "").split(",") if p.strip()}
    worker_owns = lambda platform: "*" in worker_platforms or platform in worker_platforms   # "*": the worker takes every job
    now = datetime.utcnow()
    auto_ok = aq.auto_claim_allowed(cfg["timezone"], now, _setting(db, current_user.id, aq.AUTO_LAST_KEY))

    def has_room(platform: str) -> bool:
        u = usage.get(platform) or {"used": 0, "cap": aq.daily_cap(cfg["caps"], platform, current_user.id,
                                                                   aq.day_bounds_utc(cfg["timezone"], now)[0])}
        return u["used"] < u["cap"]

    candidates = db.query(Job).filter(
        Job.user_id == current_user.id,
        Job.status.in_(["approved", "pending", "applying"]),
        *([Job.id == int(payload["job_id"])] if payload.get("job_id") else []),
    ).order_by(Job.match_score.desc().nullslast(), Job.created_at).all()

    since = now - timedelta(days=aq.COMPANY_COOLDOWN_DAYS)
    applied_to = {aq.company_key(c): (c, at) for c, at in db.query(Job.company, Job.applied_at).filter(
        Job.user_id == current_user.id, Job.applied_at >= since)}

    capped = False
    for job in candidates:
        platform = job.platform or "other"
        if wanted and platform not in wanted:
            continue
        if worker_owns(platform) != bool(payload.get("worker")):
            continue
        meta = dict(job.meta or {})
        if job.status == "applying" and not aq.claim_is_stale(meta.get("apply_claimed_at"), now, _claim_ttl(cfg)):
            continue
        if job.status == "pending" and not aq.is_auto_approvable(cfg["auto_apply"], cfg["auto_min_score"], job.status, job.match_score):
            continue
        if job.status == "pending" and not auto_ok and not payload.get("job_id"):   # auto mode's own pace
            continue
        if meta.get("apply_attempts", 0) >= cfg["max_attempts"]:
            continue
        # One application per company per cooldown. Checked only for a job that would be claimed now,
        # so it is held (and logged) once. Naming the job, or approving it again after the hold, overrides.
        key = aq.company_key(job.company)
        prior = applied_to.get(key) if key else None
        if prior and not payload.get("job_id") and not meta.get("company_ok"):
            old = job.status
            job.status, meta["apply_attempts"], meta["company_hold"] = "pending", cfg["max_attempts"], True
            job.meta = meta
            db.commit()
            _log_event(db, job.id, "status_change", "Held: already applied to this company",
                       f"Applied to {prior[0]} on {prior[1]:%Y-%m-%d}; one application per company per "
                       f"{aq.COMPANY_COOLDOWN_DAYS} days. Approve it again (or tap Retry) to apply anyway.",
                       old_value=old, new_value="pending", source="agent", user_id=current_user.id)
            continue
        if not has_room(platform):
            capped = True
            continue
        resume = _latest_resume_pdf(db, job.id)
        if not resume:
            continue
        if job.status == "pending":
            _put_setting(db, current_user.id, aq.AUTO_LAST_KEY, now.isoformat())
            _log_event(db, job.id, "status_change", "Auto-approved",
                       f"Match score {job.match_score} ≥ {cfg['auto_min_score']}", old_value="pending",
                       new_value="approved", source="agent", user_id=current_user.id)
        meta["apply_claimed_at"] = now.isoformat()
        meta["apply_attempts"] = meta.get("apply_attempts", 0) + 1
        job.meta = meta
        job.status = "applying"
        db.commit()
        _log_event(db, job.id, "agent_action", "Apply agent: claimed",
                   f"Attempt {meta['apply_attempts']} on {platform}", old_value=None, new_value="applying",
                   source="agent", user_id=current_user.id)
        return {
            "job_id": job.id, "company": job.company, "title": job.title, "url": job.url,
            "platform": platform, "match_score": job.match_score, "location": job.location,
            "resume_pdf_url": f"/api/jobs/{job.id}/resume.pdf",
            "resume_filename": resume_filename(job, resume),
            "attempt": meta["apply_attempts"],
            "remaining_today": usage.get(platform, {}).get("cap", 0) - usage.get(platform, {}).get("used", 0) - 1,
        }
    if capped:
        raise HTTPException(status_code=429, detail="Daily apply cap reached for every platform with queued jobs")
    return Response(status_code=204)


@app.post("/api/apply/{job_id}/result")
def apply_result(job_id: int, payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Report how an application went. Body: {outcome: submitted|needs_human|failed|skipped,
    notes?, answers?: [{label, answer}], screenshot_b64?: PNG}"""
    job = get_owned_job(db, job_id, current_user)
    max_attempts = _apply_config(db, current_user.id)["max_attempts"]
    outcome = payload.get("outcome")
    if outcome not in aq.OUTCOMES:
        raise HTTPException(status_code=400, detail=f"outcome must be one of {', '.join(aq.OUTCOMES)}")
    meta = dict(job.meta or {})
    meta.pop("apply_claimed_at", None)

    shot = payload.get("screenshot_b64")
    if shot:
        import base64
        out_dir = Path(os.getenv("OUTPUT_DIR", "./output")) / str(job.id)
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"apply_{outcome}_{datetime.utcnow():%Y%m%d_%H%M%S}.png"
        try:
            (out_dir / name).write_bytes(base64.b64decode(shot.split(",", 1)[-1]))
            meta.setdefault("apply_screenshots", []).append(name)
        except (ValueError, OSError) as e:
            logger.warning(f"[apply] screenshot not saved for job {job.id}: {e}")
    if payload.get("answers"):
        meta["apply_answers"] = payload["answers"]

    old = job.status
    if outcome == "submitted":
        job.status, job.applied_at = "applied", datetime.utcnow()
    elif outcome == "needs_human":
        job.status = "needs_human"
    elif outcome == "skipped":
        job.status = "pending"
        meta["apply_attempts"] = max_attempts   # don't hand it out again automatically
    else:  # failed: back in the queue until max_attempts, then a human looks
        job.status = "approved" if meta.get("apply_attempts", 0) < max_attempts else "needs_human"
    job.meta = meta
    db.commit()
    _log_event(db, job.id, "status_change" if job.status != old else "agent_action",
               f"Apply agent: {outcome.replace('_', ' ')}", payload.get("notes") or None,
               old_value=old, new_value=job.status, source="agent", user_id=current_user.id)
    telegram_bot.notify_outcome(db, current_user.id, job, outcome, payload.get("notes"))
    discord_bot.notify_outcome(db, current_user.id, job, outcome, payload.get("notes"))
    return {"job_id": job.id, "status": job.status}


# ── Activity & flags (dashboard) ──────────────────────────────────────────────
# Everything comes from tables that already exist: the event log, job status/meta
# and LLM task statuses. A flag is derived on each request, so it clears itself
# once the underlying problem is fixed; dismissing only hides it.

DISMISSED_FLAGS_KEY = "dismissed_flags"


@app.get("/api/activity")
def activity(days: int = 7, source: Optional[str] = None, limit: int = 500,
             db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Recent events across all jobs, newest first, with the job's company/title/platform."""
    since = datetime.utcnow() - timedelta(days=max(1, min(days, 90)))
    q = (db.query(ApplicationEvent, Job.company, Job.title, Job.platform)
         .join(Job, Job.id == ApplicationEvent.job_id)
         .filter(Job.user_id == current_user.id, ApplicationEvent.created_at >= since))
    if source:
        q = q.filter(ApplicationEvent.source == source)
    rows = q.order_by(ApplicationEvent.created_at.desc()).limit(max(1, min(limit, 2000))).all()
    return [{"id": e.id, "job_id": e.job_id, "company": c, "title": t, "platform": p or "other",
             "event_type": e.event_type, "event_title": e.title, "description": e.description,
             "old_value": e.old_value, "new_value": e.new_value, "source": e.source,
             "created_at": e.created_at.isoformat() if e.created_at else None}
            for e, c, t, p in rows]


def _flags(db: Session, user_id: int) -> List[Dict[str, Any]]:
    cfg = _apply_config(db, user_id)
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    flags = []

    def add(fid, severity, kind, message, job=None, at=None):
        flags.append({"id": fid, "severity": severity, "kind": kind, "message": message,
                      "job_id": job.id if job else None, "company": job.company if job else None,
                      "title": job.title if job else None, "at": (at or now).isoformat()})

    def last_agent_note(job):
        ev = (db.query(ApplicationEvent).filter(ApplicationEvent.job_id == job.id, ApplicationEvent.source == "agent")
              .order_by(ApplicationEvent.id.desc()).first())
        return (ev.description or ev.title) if ev else ""

    if cfg["paused"]:
        add("paused", "info", "paused", "Auto-apply is paused")
    jobs = db.query(Job).filter(Job.user_id == user_id,
                                Job.status.in_(["needs_human", "applying", "approved", "applied"])).all()
    for job in jobs:
        meta = job.meta or {}
        if job.status == "needs_human":
            add(f"needs_human:{job.id}", "high", "needs_human", last_agent_note(job) or "The agent needs you", job, job.updated_at)
        elif job.status == "applying" and aq.claim_is_stale(meta.get("apply_claimed_at"), now, _claim_ttl(cfg)):
            add(f"stuck:{job.id}:{meta.get('apply_claimed_at')}", "medium", "stuck",
                "Claimed but no result reported; it goes back in the queue", job)
        elif job.status == "approved" and meta.get("apply_attempts"):
            add(f"retrying:{job.id}:{meta['apply_attempts']}", "medium", "failed_attempt",
                f"Failed attempt {meta['apply_attempts']} of {cfg['max_attempts']}: {last_agent_note(job)}", job)
        elif (job.status == "applied" and meta.get("apply_attempts") and not meta.get("apply_screenshots")
              and job.applied_at and job.applied_at >= week_ago):
            add(f"no_screenshot:{job.id}", "medium", "no_screenshot",
                "Agent reported submitted without a confirmation screenshot -- check it went through", job, job.applied_at)

    drafted = (db.query(ApplicationEvent, Job).join(Job, Job.id == ApplicationEvent.job_id)
               .filter(Job.user_id == user_id, ApplicationEvent.created_at >= week_ago,
                       ApplicationEvent.description.like("%drafted (review)%")).all())
    for ev, job in drafted:
        add(f"drafted:{ev.id}", "low", "drafted_answers",
            ev.description.split("; ", 1)[-1], job, ev.created_at)

    failed_tasks = (db.query(LLMTaskStatus, Job).join(Job, Job.id == LLMTaskStatus.job_id)
                    .filter(Job.user_id == user_id, LLMTaskStatus.status == "failed",
                            LLMTaskStatus.updated_at >= week_ago).all())
    for t, job in failed_tasks:
        add(f"task:{t.id}", "medium", "llm_error", f"{t.task_type} failed: {(t.error_message or '')[:200]}", job, t.updated_at)

    for platform, u in _apply_usage(db, user_id, cfg).items():
        if u["cap"] and u["used"] >= u["cap"]:
            add(f"cap:{platform}:{aq.day_bounds_utc(cfg['timezone'], now)[0]}", "info", "cap_reached",
                f"{platform}: daily cap reached ({u['used']}/{u['cap']})")

    try:
        dismissed = set(json.loads(_setting(db, user_id, DISMISSED_FLAGS_KEY) or "[]"))
    except ValueError:
        dismissed = set()
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    return sorted((f for f in flags if f["id"] not in dismissed), key=lambda f: (order[f["severity"]], f["at"]))


@app.get("/api/flags")
def flags(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _flags(db, current_user.id)


@app.post("/api/flags/dismiss")
def dismiss_flag(payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    fid = str(payload.get("id") or "")
    if not fid:
        raise HTTPException(status_code=400, detail="id is required")
    try:
        dismissed = json.loads(_setting(db, current_user.id, DISMISSED_FLAGS_KEY) or "[]")
    except ValueError:
        dismissed = []
    # ponytail: list grows forever; prune ids older than a month if it ever matters
    _put_setting(db, current_user.id, DISMISSED_FLAGS_KEY, json.dumps((dismissed + [fid])[-2000:]))
    db.commit()
    return {"ok": True}


@app.get("/api/jobs/{job_id}/resume.pdf")
def latest_resume_pdf(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = get_owned_job(db, job_id, current_user)
    rv = _latest_resume_pdf(db, job_id)
    if not rv:
        raise HTTPException(status_code=404, detail="No tailored resume PDF for this job yet")
    return FileResponse(rv.pdf_path, media_type="application/pdf", filename=resume_filename(job, rv))


def resume_filename(job: Job, rv: ResumeVersion) -> str:
    """The name an employer sees on the upload: girijesh_singh_manulife_resume.pdf.
    The candidate's name comes from the resume's own '# Name' header line."""
    name = next((l[2:] for l in (rv.content_md or "").splitlines() if l.startswith("# ")), "")
    slug = lambda s: re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return "_".join(x for x in (slug(name), slug(job.company or ""), "resume") if x) + ".pdf"


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

        if job.status == "found":
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
        if job.status in ("found", "analyzing"):  # re-scoring must not undo an approval or hand-off
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

        excluded = excluded_component_names(db, user_id)
        if excluded:
            _log_event(db, job_id, "components_excluded",
                       f"{len(excluded)} master-resume component(s) excluded from generation",
                       "Classified as questionnaire/style-guide content, not resume content: "
                       + ", ".join(excluded),
                       source="agent", user_id=user_id)

        # ── Step 3: Resume agent (its router carries the user's keys) ──
        agent_resume = get_agent("resume", db, user_id)

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

        banned_row = db.query(Settings).filter(Settings.key == "resume_banned_words", Settings.user_id == user_id).first()
        banned = [w.strip() for w in re.split(r'[,\n]', banned_row.value or "")] if banned_row else []

        # ── Step 5: Tailor. Structured path: facts and layout from the parsed
        # profile, the LLM only picks/rewords -- consistent on any model. The
        # free-form path is the fallback for resumes the parser can't read.
        profile = load_resume_profile(db, user_id)
        if profile is not None:
            resume_md = await resume_profile.tailor(
                agent_resume.router, profile, job.job_description or "",
                company=job.company, title=job.title, feedback=feedback,
                style_notes=design_rules, banned=banned, llm=llm,
            )
        else:
            logger.warning(f"[Resume] no parseable resume profile for user {user_id}; free-form generation")
            gh_token = agent_resume.router.github_token
            gh_user  = agent_resume.router.github_username
            github_ctx = ""
            if gh_token and gh_user:
                github_ctx = await agent_resume.fetch_github_context(gh_user, gh_token)
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
            # Step 6: enforce header correctness post-LLM
            resume_md = agent_resume.enforce_header(resume_md, contact_facts)
            # Step 7: style-guide enforcement pass (only if user provided rules)
            if design_rules.strip():
                resume_md = await agent_resume.validate_design(resume_md, design_rules, llm=llm)
                resume_md = agent_resume.enforce_header(resume_md, contact_facts)

        # Standing "never include" rules: enforced here, after either generation path.
        resume_md, dropped = resume_profile.drop_excluded(resume_md, _rules(db, user_id)["exclude"])
        if dropped:
            logger.info(f"[Resume] job {job_id}: removed {len(dropped)} excluded point(s)")

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

        # Nothing to charge here any more: the free allowance is spent in tokens,
        # metered per call by the router's usage hook (see _usage_meter). That
        # bills real provider usage rather than a flat per-resume guess, and it
        # counts the calls that failed halfway too -- those cost money as well.

        # ── Step 9: Critic pass (best-effort, runs after the resume is ready) ──
        # Same placement as the ATS pass below: the resume is already marked
        # completed, so this never delays it. Gets the master resume as well as
        # the JD, so it can flag invented claims and content tailoring dropped —
        # neither of which it can see from the job description alone.
        try:
            critic = get_agent("critic", db, user_id)
            notes = await critic.critique(
                resume_md=resume_md,
                job_description=job.job_description or "",
                company=job.company,
                title=job.title,
                master_resume=master_resume,
                llm=critic_llm or llm,
            )
            rv.critic_notes = notes
            db.commit()
            fabs = len(notes.get("fabrications") or [])
            drops = len(notes.get("dropped_strengths") or [])
            detail = f"Score {notes.get('score', '?')}/10"
            if fabs or drops:
                detail += f" — {fabs} unsupported claim(s), {drops} dropped strength(s)"
            _log_event(db, job_id, "resume_critiqued",
                       f"Resume v{version} critiqued", detail,
                       source="agent", user_id=user_id)
        except Exception as critic_err:
            logger.warning(f"[Critic] critique failed for job {job_id}: {critic_err}")

        # ── Step 10: Auto ATS scoring (best-effort, runs after resume is ready) ──
        # Resume is already marked completed above, so the UI shows it immediately;
        # the ATS score fills in a bit later once the vendored scorer returns.
        try:
            from ats_score import score_resume_pdf
            pdf_path = paths.get("pdf")
            # The vendored scorer fans out to 7-8 more LLM calls in its own
            # subprocess -- more than the whole rest of the pipeline -- and its
            # usage never comes back to the meter. Free users would be paying
            # for it out of a budget it cannot be counted against, so they skip
            # it; own-key and Pro users still get scored.
            if pdf_path and not on_platform_key(db, user_id):
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

    job = get_owned_job(db, job_id, current_user)
    agent = get_agent("resume", db, current_user.id)

    # Same context the generation flow gets. Without the master resume the editor
    # can only ever lose information: each edit's output is the next edit's input.
    master_resume = get_master_resume(db, current_user.id)
    raw_content = get_raw_resume_content(db, current_user.id)
    contact_facts = extract_contact_info(raw_content) if raw_content else None
    style_row = db.query(Settings).filter(
        Settings.key == "resume_style_guide", Settings.user_id == current_user.id
    ).first()

    try:
        updated_md = await agent.chat_edit(
            current_md, instruction,
            master_resume=master_resume,
            job_description=job.job_description or "",
            company=job.company or "",
            title=job.title or "",
            design_rules=(style_row.value if style_row and style_row.value else ""),
            history=payload.get("history") or [],
            contact_facts=contact_facts,
            llm=llm,
        )
        return {"updated_md": updated_md}
    except ValueError as e:      # guard rejected the edit — resume left untouched
        raise HTTPException(status_code=422, detail=str(e))
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
    require_resume_quota(current_user)
    llm = resolve_llm(db, current_user.id, payload.get("llm"))
    feedback = payload.get("feedback", "")
    # Claude critiques by default, but only when the user has a Claude key --
    # otherwise the critic call fails over on every generation.
    critic_llm = payload.get("critic_llm") or (
        "claude" if "claude" in get_llm_router(db, current_user.id).available_providers() else llm)
    background_tasks.add_task(_bg_resume, job_id, llm, feedback, current_user.id, critic_llm)
    return {"status": "processing"}


@app.get("/api/agent/resume/{job_id}")
def get_resume_status(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_owned_job(db, job_id, current_user)
    task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="resume").first()
    latest = db.query(ResumeVersion).filter_by(job_id=job_id).order_by(ResumeVersion.version.desc()).first()

    if task and task.status == "processing":
        return {"status": "processing"}
    # Failure outranks an older version: otherwise a failed regeneration serves
    # the previous resume back as "completed" and the error never reaches the user.
    if task and task.status == "failed":
        return {"status": "failed", "error": task.error_message}
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
    return {"status": "none"}


async def _bg_ats(job_id: int, resume_id: int, llm: str, user_id: int):
    from ats_score import score_resume_pdf
    db = SessionLocal()
    try:
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="ats").first()
        if not task:
            task = LLMTaskStatus(job_id=job_id, task_type="ats", status="processing", user_id=user_id)
            db.add(task)
        else:
            task.status = "processing"
            task.error_message = None
        db.commit()

        rv = db.query(ResumeVersion).filter_by(
            id=resume_id, job_id=job_id, user_id=user_id
        ).first()
        if not rv:
            raise ValueError("Resume version not found")

        _r = get_agent("resume", db, user_id).router
        ats = await asyncio.to_thread(
            score_resume_pdf, rv.pdf_path, llm,
            _r.gemini_key, _r.nvidia_key, _r.github_token,
        )
        if not ats:
            raise ValueError("ATS scorer returned no result")

        rv.ats_score = ats
        task.status = "completed"
        db.commit()
        _log_event(db, job_id, "ats_scored",
                   f"Resume v{rv.version} ATS score: {ats.get('total')}/100",
                   "Manually re-evaluated via hiring-agent",
                   source="user", user_id=user_id)
    except Exception as e:
        logger.error(f"[ATS] manual scoring failed for resume {resume_id}: {e}")
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="ats").first()
        if task:
            task.status = "failed"
            task.error_message = str(e)
            db.commit()
    finally:
        db.close()


@app.post("/api/agent/resume/{job_id}/ats/{resume_id}")
async def run_ats_score(
    job_id: int,
    resume_id: int,
    background_tasks: BackgroundTasks,
    payload: Dict[str, Any] = {},
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually (re)run ATS scoring on an existing resume version's PDF.

    Scoring shells out to the vendored hiring-agent and can take minutes, which
    is well past the 100s edge-proxy timeout — so it runs in the background and
    the client polls the resume list (or /jobs/{id}/tasks) for the result.
    """
    get_owned_job(db, job_id, current_user)
    rv = db.query(ResumeVersion).filter_by(
        id=resume_id, job_id=job_id, user_id=current_user.id
    ).first()
    if not rv:
        raise HTTPException(404, "Resume version not found")
    if not rv.pdf_path or not os.path.exists(rv.pdf_path):
        raise HTTPException(400, "Resume PDF not found on disk — regenerate the resume first")

    llm = resolve_llm(db, current_user.id, payload.get("llm"))
    background_tasks.add_task(_bg_ats, job_id, resume_id, llm, current_user.id)
    return {"status": "processing"}


@app.post("/api/agent/resume/{job_id}/save")
async def save_chat_resume(
    job_id: int,
    background_tasks: BackgroundTasks,
    payload: Dict[str, Any] = {},
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Saves the final chat-edited markdown to the DB and generates the PDF.

    Runs synchronously (PDF render takes ~1-3s) so the client gets a confirmed
    version back — a background fire-and-forget here made successful saves look
    like nothing happened (the UI reloaded before the row existed) and hid
    failures entirely.
    """
    get_owned_job(db, job_id, current_user)
    final_md = payload.get("final_md")
    llm = payload.get("llm", "chat-editor")
    if not final_md:
        raise HTTPException(status_code=400, detail="Missing final_md")

    from doc_generator import save_resume
    latest = db.query(ResumeVersion).filter_by(job_id=job_id).order_by(ResumeVersion.version.desc()).first()
    version = 1 if not latest else latest.version + 1

    pdf_style_row = db.query(Settings).filter(Settings.key == "resume_pdf_style", Settings.user_id == current_user.id).first()
    pdf_style = {}
    if pdf_style_row and pdf_style_row.value:
        try:
            pdf_style = json.loads(pdf_style_row.value)
        except (ValueError, TypeError):
            pass

    try:
        paths = await asyncio.to_thread(save_resume, job_id, version, final_md, pdf_style)
    except Exception as e:
        logger.error(f"[ResumeChatSave] Failed to save chat resume: {e}")
        raise HTTPException(500, f"Failed to generate documents: {e}")

    rv = ResumeVersion(
        job_id=job_id, version=version, content_md=final_md,
        pdf_path=paths.get("pdf"), docx_path=paths.get("docx"),
        llm_used=llm, user_id=current_user.id,
    )
    db.add(rv)
    db.commit()

    _log_event(db, job_id, "document_generated",
               f"Resume v{version} saved via Chat Editor",
               "Saved by user", source="user", user_id=current_user.id)
    return {"status": "saved", "version": version, "pdf": bool(paths.get("pdf"))}


# ── Ops: what the watchdog needs to make failures loud ─────────────────────────

@app.exception_handler(Exception)
async def _log_unhandled(request: Request, exc: Exception):
    # Without this an unhandled error is a bare 500 with no ERROR record to alert on.
    logger.opt(exception=exc).error(f"Unhandled {type(exc).__name__} on {request.method} {request.url.path}: {exc}")
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


def stuck_jobs(db: Session, user_id: int, now: datetime) -> tuple:
    """(apply claims with no result for over an hour, jobs waiting on the user for over 24 h)."""
    stale = []
    for job in db.query(Job).filter(Job.user_id == user_id, Job.status == "applying").all():
        at = (job.meta or {}).get("apply_claimed_at")
        if at and now - datetime.fromisoformat(at) > timedelta(hours=1):
            stale.append({"job_id": job.id, "company": job.company, "claimed_at": at})
    waiting = []
    for job in db.query(Job).filter(Job.user_id == user_id, Job.status == "waiting_human").all():
        at = (job.meta or {}).get("waiting_since")
        if at and now - datetime.fromisoformat(at) > timedelta(hours=24):
            waiting.append({"job_id": job.id, "company": job.company, "since": at,
                            "reason": (job.meta or {}).get("waiting_reason")})
    return stale, waiting


# ── Discovery (discovery.py; scheduled by scheduler.py) ───────────────────────
@app.get("/api/discovery/targets")
def get_discovery_targets(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    import discovery, scheduler
    # runs_here: only the instance with the scheduler on runs discovery (not the hosted app)
    return {"targets": discovery.targets(sys.modules[__name__], db, current_user.id),
            "runs_here": scheduler.CURRENT is not None}


@app.put("/api/discovery/targets")
def put_discovery_targets(payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Body: {targets: {...}} (see discovery.py). An empty object switches scheduled discovery off."""
    import discovery
    t = payload.get("targets")
    if t == {}:
        db.query(Settings).filter(Settings.key == discovery.TARGETS_KEY, Settings.user_id == current_user.id).delete()
        db.commit()
        return {"targets": None}
    try:
        clean = discovery.clean_targets(t)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    _put_setting(db, current_user.id, discovery.TARGETS_KEY, json.dumps(clean))
    db.commit()
    return {"targets": clean}


@app.post("/api/discovery/run")
def run_discovery(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Start a discovery run now (it takes minutes; problems arrive on Telegram)."""
    import discovery, scheduler
    if not scheduler.CURRENT:
        raise HTTPException(409, "Discovery runs only on the instance with the scheduler on.")
    if discovery.targets(sys.modules[__name__], db, current_user.id) is None:
        raise HTTPException(400, "Set your discovery targets first.")
    return {"started": scheduler.CURRENT.start_discovery_threadsafe(current_user.id)}


@app.get("/api/ops/errors")
def ops_errors(since: str = "", db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Errors logged after `since` (ISO time), failed background tasks, and apply claims
    with no result for over an hour."""
    errors = [e for e in OPS_ERRORS if e["at"] > since]
    now = datetime.utcnow()
    stale, waiting = stuck_jobs(db, current_user.id, now)
    # Every apply outcome since the last poll, so the watchdog can tell the user about each one.
    since_dt = datetime.fromisoformat(since).astimezone(timezone.utc).replace(tzinfo=None) if since else now
    results = [{"job_id": e.job_id, "company": c, "title": t, "url": u, "outcome": e.title.split(": ", 1)[1],
                "notes": (e.description or "")[:1500]}
               for e, c, t, u in db.query(ApplicationEvent, Job.company, Job.title, Job.url).join(Job, Job.id == ApplicationEvent.job_id)
               .filter(ApplicationEvent.user_id == current_user.id, ApplicationEvent.created_at > since_dt,
                       ApplicationEvent.title.like("Apply agent: %"),
                       ApplicationEvent.title.notin_(["Apply agent: claimed", "Apply agent: continuing"]))
               .order_by(ApplicationEvent.id)]
    cfg = _apply_config(db, current_user.id)
    ready = db.query(func.count(Job.id)).filter(Job.user_id == current_user.id, or_(
        Job.status == "approved",
        and_(Job.status == "pending", cfg["auto_apply"], Job.match_score >= cfg["auto_min_score"]))).scalar()
    return {"now": datetime.now(timezone.utc).isoformat(), "errors": errors, "stale_claims": stale,
            "long_waits": waiting, "results": results, "mode": cfg["mode"], "paused": cfg["paused"],
            "worker_platforms": [p.strip() for p in (_setting(db, current_user.id, "worker_platforms") or "").split(",") if p.strip()],
            "ready": ready, "timezone": cfg["timezone"]}


# ── Standing resume rules ("remember this for future resumes") ────────────────
# style   -> resume_style_guide, one rule per line; every generation and edit prompt gets them
# exclude -> resume_excluded_points (JSON list); matching bullets are removed in code
# ban     -> resume_banned_words (comma list); generation and edits are checked in code
RULE_KEYS = {"style": "resume_style_guide", "exclude": "resume_excluded_points", "ban": "resume_banned_words"}


def _rules(db: Session, user_id: int) -> Dict[str, List[str]]:
    out = {}
    for kind, key in RULE_KEYS.items():
        raw = _setting(db, user_id, key) or ""
        if kind == "exclude":
            try:
                out[kind] = [str(x) for x in json.loads(raw or "[]")]
            except ValueError:
                out[kind] = []
        elif kind == "ban":
            out[kind] = [w.strip() for w in re.split(r'[,\n]', raw) if w.strip()]
        else:
            out[kind] = [l.strip() for l in raw.split("\n") if l.strip()]
    return out


def _save_rules(db: Session, user_id: int, kind: str, items: List[str]):
    value = json.dumps(items) if kind == "exclude" else (", " if kind == "ban" else "\n").join(items)
    _put_setting(db, user_id, RULE_KEYS[kind], value)
    db.commit()


@app.get("/api/resume/rules")
def get_resume_rules(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _rules(db, current_user.id)


# Resumes that may still change. Applied/closed ones are the record of what was sent; one being
# applied (or parked mid-form) must keep the file the agent is uploading.
RULES_OPEN_STATUSES = ("found", "analyzing", "pending", "approved", "needs_human")


def _exclusion_targets(db: Session, user_id: int, excluded: List[str]) -> List[tuple]:
    """[(job, latest version, new md, removed lines)] for open jobs whose latest resume has an excluded point."""
    out = []
    for job in db.query(Job).filter(Job.user_id == user_id, Job.status.in_(RULES_OPEN_STATUSES)).all():
        latest = db.query(ResumeVersion).filter_by(job_id=job.id).order_by(ResumeVersion.version.desc()).first()
        if latest and latest.content_md:
            md, removed = resume_profile.drop_excluded(latest.content_md, excluded)
            if removed:
                out.append((job, latest, md, removed))
    return out


async def _bg_apply_exclusions(user_id: int):
    """New resume version (text removal only, no model) for every open job still carrying an excluded point."""
    db = SessionLocal()
    try:
        done, failed = [], []
        for job, latest, md, removed in _exclusion_targets(db, user_id, _rules(db, user_id)["exclude"]):
            try:
                await _save_version(db, job.id, user_id, md, "rules",
                                    {"instruction": "removed excluded point(s): " + "; ".join(r[:80] for r in removed),
                                     "from_version": latest.version}, f"cleaned (from v{latest.version})")
                done.append(job.id)
            except Exception as e:
                failed.append(job.id)
                logger.error(f"[Rules] excluded point not removed from job {job.id}: {e}")
        logger.info(f"[Rules] excluded points removed from {len(done)} resume(s): {done}; failed: {failed}")
    finally:
        db.close()


@app.post("/api/resume/rules/apply")
def apply_resume_rules(background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Remove excluded points from the latest resume of every open job (found → needs_human), in the background."""
    ids = [t[0].id for t in _exclusion_targets(db, current_user.id, _rules(db, current_user.id)["exclude"])]
    if ids:
        background_tasks.add_task(_bg_apply_exclusions, current_user.id)
    return {"updating": ids, "skipped_statuses": "applied, rejected, withdrawn, applying, waiting_human (kept as sent / in use)"}


@app.post("/api/resume/rules")
def add_resume_rule(payload: Dict[str, Any], background_tasks: BackgroundTasks = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Body: {kind: style|exclude|ban, text}. Applies to resumes generated or edited from now on;
    an exclude rule is also applied right away to the latest resume of every open job."""
    kind, text = payload.get("kind"), (payload.get("text") or "").strip()
    if kind not in RULE_KEYS or not text:
        raise HTTPException(400, "kind must be style, exclude or ban, with text")
    rules = _rules(db, current_user.id)
    if text.lower() not in (r.lower() for r in rules[kind]):
        rules[kind].append(text)
        _save_rules(db, current_user.id, kind, rules[kind])
        logger.info(f"[Rules] {kind} rule added: {text[:120]}")
    if kind == "exclude" and background_tasks is not None:
        rules["updating"] = apply_resume_rules(background_tasks, db=db, current_user=current_user)["updating"]
    return rules


@app.delete("/api/resume/rules/{kind}/{index}")
def remove_resume_rule(kind: str, index: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """index is 1-based, as listed."""
    rules = _rules(db, current_user.id)
    if kind not in RULE_KEYS or not 1 <= index <= len(rules[kind]):
        raise HTTPException(404, f"no {kind} rule #{index}")
    removed = rules[kind].pop(index - 1)
    _save_rules(db, current_user.id, kind, rules[kind])
    logger.info(f"[Rules] {kind} rule removed: {removed[:120]}")
    return rules


# ── Resume review (Discord #resume-review) ────────────────────────────────────
# A reviewer asks for a change in plain language; resume_edit applies it as exact
# patches and refuses anything that would invent or alter a fact. Every accepted edit
# is a new version rendered with the same template, so the apply agent uploads it.

def _pdf_style(db: Session, user_id: int) -> dict:
    row = db.query(Settings).filter(Settings.key == "resume_pdf_style", Settings.user_id == user_id).first()
    try:
        return json.loads(row.value) if row and row.value else {}
    except (ValueError, TypeError):
        return {}


async def _save_version(db: Session, job_id: int, user_id: int, md: str, llm_used: str, notes: dict, title: str) -> ResumeVersion:
    from doc_generator import save_resume, _fit_pages, MAX_RESUME_PAGES
    style = _pdf_style(db, user_id)
    # PDF rendering is CPU work for seconds: off the event loop so the API stays responsive.
    pages = len((await asyncio.to_thread(_fit_pages, md, style)).pages)
    if pages > MAX_RESUME_PAGES:
        raise HTTPException(422, f"That change makes the resume {pages} pages (limit {MAX_RESUME_PAGES}); nothing saved.")
    latest = db.query(ResumeVersion).filter_by(job_id=job_id).order_by(ResumeVersion.version.desc()).first()
    version = (latest.version if latest else 0) + 1
    paths = await asyncio.to_thread(save_resume, job_id, version, md, style)
    rv = ResumeVersion(job_id=job_id, user_id=user_id, version=version, content_md=md, pdf_path=paths.get("pdf"),
                       docx_path=paths.get("docx"), llm_used=llm_used, critic_notes=notes)
    db.add(rv)
    db.commit()
    _log_event(db, job_id, "document_generated", f"Resume v{version} {title}", notes.get("instruction"),
               source="user", user_id=user_id)
    return rv


def _parse_llm_json(text: str) -> dict:
    from agents import _parse_json
    out = _parse_json(text)
    if not isinstance(out, dict):
        raise ValueError("not a JSON object")
    return out


@app.post("/api/jobs/{job_id}/resume/edit")
async def edit_resume(job_id: int, payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Body: {instruction}. Applies one requested change to the latest resume version.
    422 with the reason when the change would alter or invent a fact; nothing is saved then."""
    import resume_edit
    job = get_owned_job(db, job_id, current_user)
    instruction = (payload.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(400, "instruction is required")
    latest = db.query(ResumeVersion).filter_by(job_id=job_id).order_by(ResumeVersion.version.desc()).first()
    if not latest or not latest.content_md:
        raise HTTPException(404, "This job has no resume yet -- prepare it first")
    rules = _rules(db, current_user.id)
    llm = resolve_llm(db, current_user.id, payload.get("llm"))
    try:
        new_md, edits = await resume_edit.edit(
            get_llm_router(db, current_user.id), latest.content_md, instruction,
            get_master_resume(db, current_user.id), job.job_description or "", rules["ban"], llm,
            loads=_parse_llm_json, style="\n".join(f"- {r}" for r in rules["style"]))
    except resume_edit.EditError as e:
        raise HTTPException(422, str(e))
    new_md, _ = resume_profile.drop_excluded(new_md, rules["exclude"])
    rv = await _save_version(db, job_id, current_user.id, new_md, f"edit:{llm}",
                       {"instruction": instruction, "edits": edits, "from_version": latest.version},
                       f"edited (from v{latest.version})")
    return {"version": rv.version, "from_version": latest.version, "edits": edits, "pdf": bool(rv.pdf_path)}


@app.post("/api/jobs/{job_id}/resume/use/{version}")
async def use_resume_version(job_id: int, version: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Make an older version the current one (saved as a new version, history kept)."""
    get_owned_job(db, job_id, current_user)
    old = db.query(ResumeVersion).filter_by(job_id=job_id, version=version).first()
    if not old:
        raise HTTPException(404, f"No resume v{version} for job {job_id}")
    rv = await _save_version(db, job_id, current_user.id, old.content_md, f"copy-of-v{version}",
                       {"instruction": f"use v{version} again", "from_version": version}, f"restored from v{version}")
    return {"version": rv.version, "from_version": version}


@app.get("/api/review/pending")
def review_pending(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Jobs whose latest resume version hasn't been posted for review yet (not applied/closed)."""
    out = []
    for job in db.query(Job).filter(Job.user_id == current_user.id,
                                    Job.status.in_(["pending", "approved", "found", "needs_human"])).all():
        latest = db.query(ResumeVersion).filter_by(job_id=job.id).order_by(ResumeVersion.version.desc()).first()
        if latest and latest.pdf_path and (job.meta or {}).get("review_posted_version") != latest.version:
            out.append({"job_id": job.id, "version": latest.version})
    return out


@app.post("/api/review/{job_id}/posted")
def review_posted(job_id: int, payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = get_owned_job(db, job_id, current_user)
    job.meta = {**(job.meta or {}), "review_posted_version": int(payload["version"])}
    db.commit()
    return {"ok": True}


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

def _chat_jobs(db: Session, uid: int, p: dict) -> str:
    """list_jobs for the chat: status / last N hours / best score first, up to 25."""
    q = db.query(Job).filter(Job.user_id == uid)
    if p.get("status"):
        q = q.filter(Job.status == p["status"])
    elif p.get("sort") == "score":   # "top matches" means ones still open, not applied or rejected ones
        q = q.filter(Job.status.in_(("found", "pending", "approved", "needs_human")))
    hours = p.get("since_hours")
    if isinstance(hours, (int, float)) and hours > 0:
        q = q.filter(Job.created_at >= datetime.utcnow() - timedelta(hours=hours))
    q = q.order_by(Job.match_score.desc().nullslast(), Job.id.desc()) if p.get("sort") == "score" \
        else q.order_by(Job.id.desc())
    limit = max(1, min(int(p.get("limit") or 10), 25))
    jobs = q.limit(limit).all()
    if not jobs:
        return "No jobs match that."
    when = f" from the last {hours:g}h" if isinstance(hours, (int, float)) and hours > 0 else ""
    head = f"{'Top' if p.get('sort') == 'score' else 'Latest'} {len(jobs)} jobs{when}" + \
        (f" ({p['status']})" if p.get("status") else "") + ":"
    return head + "\n" + "\n".join(
        f"• #{j.id} {j.company} — {j.title} · {j.status}" + (f" · score {j.match_score:g}" if j.match_score is not None else "")
        for j in jobs)


def _chat_approve(db: Session, user: User, ids: list) -> str:
    """Approve the named jobs that have a resume; say why the others weren't."""
    out = []
    for jid in [int(i) for i in ids if str(i).isdigit()][:25]:
        job = db.query(Job).filter(Job.id == jid, Job.user_id == user.id).first()
        if not job:
            out.append(f"#{jid}: not found")
        elif job.status in ("applied", "applying"):
            out.append(f"#{jid} {job.company}: already {job.status}")
        elif not db.query(ResumeVersion).filter_by(job_id=jid).first():
            out.append(f"#{jid} {job.company}: no resume yet — not approved")
        else:
            try:
                approve_job(jid, db, user)
                out.append(f"#{jid} {job.company}: approved")
            except HTTPException as e:
                out.append(f"#{jid} {job.company}: {e.detail}")
    return "\n".join(out) or "Which jobs? Give me their numbers, e.g. approve 154 155."


def _chat_status(db: Session, uid: int, hours: float = 24) -> str:
    """What's going on: what moved in the period, what's in flight, what waits for the user."""
    since = datetime.utcnow() - timedelta(hours=hours)
    jobs = db.query(Job).filter(Job.user_id == uid)
    found = jobs.filter(Job.created_at >= since).count()
    applied = jobs.filter(Job.applied_at >= since).count()
    lines = [f"Last {hours:g}h: {found} new jobs found, {applied} applications submitted."]
    for status, label in (("applying", "Applying now"), ("waiting_human", "Waiting for you"),
                          ("approved", "Queued to apply"), ("needs_human", "Apply yourself")):
        rows = jobs.filter(Job.status == status).order_by(Job.id.desc()).all()
        if rows:
            names = ", ".join(f"#{j.id} {j.company}" for j in rows[:6]) + (f" +{len(rows) - 6}" if len(rows) > 6 else "")
            lines.append(f"{label} ({len(rows)}): {names}")
    pending = jobs.filter(Job.status == "pending").count()
    if pending:
        lines.append(f"Resumes to review: {pending} (say review)")
    last = _setting(db, uid, "discovery_last_run")
    if last:
        lines.append(f"Last discovery run: {last}")
    return "\n".join(lines)


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
            if _owned(job_id) and not billing.can_generate_resume(current_user):
                # Same quota as the REST route — otherwise asking the chat agent
                # for a resume is a free bypass of the paywall.
                reply = (
                    f"You've used all {billing.FREE_RESUME_LIMIT} free resume generations. "
                    "Upgrade to Pro in Settings → Billing for unlimited generations."
                )
            elif _owned(job_id):
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

        elif action_type == "list_jobs" and (action.get("params") or {}):
            reply = _chat_jobs(db, uid, action["params"])

        elif action_type == "approve_jobs":
            reply = _chat_approve(db, current_user, (action.get("params") or {}).get("job_ids") or
                                  ([action["job_id"]] if action.get("job_id") else []))

        elif action_type == "run_discovery":
            reply = "🔎 Discovery started — new resumes arrive here when it's done." \
                if run_discovery(db, current_user)["started"] else "A discovery run is already going."

        elif action_type == "status_summary":
            hours = (action.get("params") or {}).get("since_hours")
            reply = _chat_status(db, uid, hours if isinstance(hours, (int, float)) and hours > 0 else 24)

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
    """What the model pickers show: providers with a key, the model in use, the user's added models."""
    router = get_llm_router(db, current_user.id)
    try:
        custom = json.loads(_setting(db, current_user.id, "custom_nvidia_models") or "[]")
    except ValueError:
        custom = []
    return {"available": router.available_providers(), "model": resolve_llm(db, current_user.id),
            "custom": custom}


@app.get("/api/llm/models")
def get_provider_models(
    provider: str = Query(...),
    refresh: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Live model catalogue for one provider, so the model picker offers real
    ids (with OpenRouter's prices) instead of a hand-typed guess. Read with the
    user's own key, falling back to the platform's -- OpenRouter publishes its
    catalogue to anyone, so it lists even with no key at all."""
    if provider not in connectors.PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    row = db.query(Settings).filter(
        Settings.user_id == current_user.id,
        Settings.key == f"{provider}_api_key",
    ).first()
    key = row.value if row and row.value and "\u2022" not in row.value else platform_key_for(db, provider)
    models, error = connectors.list_models(provider, key or "", refresh=refresh)
    return {"provider": provider, "models": models, "error": error}


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
# ── Admin: user monitoring ────────────────────────────────────────────────────
# Everything about one account in one place. Counts come from one grouped query
# per table rather than a query per user, so the list does not degrade as the
# user table grows.

# Tables counted per user on the list view: (table, label).
_USER_COUNT_TABLES = [
    ("jobs", "jobs"),
    ("resume_versions", "resumes"),
    ("cover_letter_versions", "cover_letters"),
    ("evaluation_reports", "evaluations"),
    ("story_bank", "stories"),
    ("research_reports", "research"),
    ("interview_prep_reports", "interview_preps"),
    ("linkedin_outreach_reports", "outreach"),
    ("chat_interactions", "chats"),
    ("application_events", "events"),
    ("master_resume_components", "documents"),
]


def _counts_by_user(db: Session, table: str) -> dict:
    rows = db.execute(text(
        f"SELECT user_id, COUNT(*) FROM {table} WHERE user_id IS NOT NULL GROUP BY user_id"
    )).fetchall()
    return {r[0]: r[1] for r in rows}


def _latest_by_user(db: Session, table: str, column: str = "created_at") -> dict:
    rows = db.execute(text(
        f"SELECT user_id, MAX({column}) FROM {table} WHERE user_id IS NOT NULL GROUP BY user_id"
    )).fetchall()
    return {r[0]: r[1] for r in rows if r[1]}


def _own_providers(db: Session, user_id: int) -> List[str]:
    """Which LLM providers this user configured for themselves. Names only --
    an admin screen must never surface another person's key material."""
    rows = db.query(Settings).filter(Settings.user_id == user_id).all()
    return sorted({
        _USER_KEY_MAP[r.key] for r in rows
        if r.key in _USER_KEY_MAP and r.value and "•" not in r.value
        and _USER_KEY_MAP[r.key] not in _NON_PROVIDER_SLOTS
    })


def _user_row(u: User, counts: dict, latest: dict, providers: List[str],
              onboarding: dict, limits: dict) -> dict:
    used_in = u.free_input_tokens_used or 0
    used_out = u.free_output_tokens_used or 0
    pct = 0
    if not billing.is_pro(u) and limits["input"] and limits["output"]:
        pct = max(round(used_in / limits["input"] * 100), round(used_out / limits["output"] * 100))
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "avatar_url": u.avatar_url,
        "signup_method": u.oauth_provider or "password",
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "plan": "pro" if billing.is_pro(u) else "free",
        "plan_status": u.plan_status,
        "current_period_end": u.current_period_end.isoformat() if u.current_period_end else None,
        "has_subscription": bool(u.stripe_subscription_id),
        "input_tokens": used_in,
        "output_tokens": used_out,
        "quota_pct": min(pct, 100),
        "quota_exhausted": not billing.has_token_quota(u),
        "own_providers": providers,
        "on_platform_key": not providers,
        "is_admin": is_admin(u),
        "onboarding_done": onboarding.get("done", False),
        "profile_facts": onboarding.get("facts", 0),
        "last_active": latest.get(u.id),
        "counts": counts,
    }


@app.get("/api/admin/users")
def admin_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Every account with the activity that matters, in one payload."""
    users = db.query(User).order_by(User.id.asc()).all()

    counts = {label: _counts_by_user(db, table) for table, label in _USER_COUNT_TABLES}
    # "Last active" is the newest thing they caused anywhere, not their signup.
    latest = {}
    for table in ("jobs", "resume_versions", "chat_interactions", "application_events"):
        for uid, ts in _latest_by_user(db, table).items():
            if not latest.get(uid) or str(ts) > str(latest[uid]):
                latest[uid] = ts

    profile_rows = {
        r.user_id: r.value for r in
        db.query(Settings).filter(Settings.key == ONBOARDING_PROFILE_KEY).all()
    }
    done_rows = {
        r.user_id: r.value for r in
        db.query(Settings).filter(Settings.key == ONBOARDING_DONE_KEY).all()
    }
    limits = {"input": billing.FREE_INPUT_TOKENS, "output": billing.FREE_OUTPUT_TOKENS}

    rows = []
    for u in users:
        try:
            facts = len(json.loads(profile_rows.get(u.id) or "{}").get("facts", []))
        except (ValueError, TypeError):
            facts = 0
        rows.append(_user_row(
            u,
            {label: counts[label].get(u.id, 0) for _, label in _USER_COUNT_TABLES},
            latest,
            _own_providers(db, u.id),
            {"done": done_rows.get(u.id) == "1", "facts": facts},
            limits,
        ))

    active = [r for r in rows if r["last_active"]]
    return {
        "users": rows,
        "limits": limits,
        "totals": {
            "accounts": len(rows),
            "active": len(active),
            "pro": sum(1 for r in rows if r["plan"] == "pro"),
            "on_platform_key": sum(1 for r in rows if r["on_platform_key"]),
            "exhausted": sum(1 for r in rows if r["quota_exhausted"]),
            "input_tokens": sum(r["input_tokens"] for r in rows),
            "output_tokens": sum(r["output_tokens"] for r in rows),
            **{label: sum(r["counts"][label] for r in rows) for _, label in _USER_COUNT_TABLES},
        },
    }


@app.get("/api/admin/users/{user_id}")
def admin_user_detail(user_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """One account in full: what they uploaded, what they told us, what they
    generated, and what failed."""
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")

    counts = {label: _counts_by_user(db, table).get(user_id, 0) for table, label in _USER_COUNT_TABLES}
    latest = {}
    for table in ("jobs", "resume_versions", "chat_interactions", "application_events"):
        ts = _latest_by_user(db, table).get(user_id)
        if ts and (not latest.get(user_id) or str(ts) > str(latest[user_id])):
            latest[user_id] = ts

    try:
        facts = json.loads(
            (db.query(Settings).filter(Settings.key == ONBOARDING_PROFILE_KEY,
                                       Settings.user_id == user_id).first() or Settings()).value or "{}"
        ).get("facts", [])
    except (ValueError, TypeError):
        facts = []
    done_row = db.query(Settings).filter(
        Settings.key == ONBOARDING_DONE_KEY, Settings.user_id == user_id).first()

    row = _user_row(
        u, counts, latest, _own_providers(db, user_id),
        {"done": bool(done_row and done_row.value == "1"), "facts": len(facts)},
        {"input": billing.FREE_INPUT_TOKENS, "output": billing.FREE_OUTPUT_TOKENS},
    )

    jobs = db.query(Job).filter(Job.user_id == user_id).order_by(Job.created_at.desc()).limit(25).all()
    resumes = db.query(ResumeVersion).filter(ResumeVersion.user_id == user_id)\
        .order_by(ResumeVersion.created_at.desc()).limit(25).all()
    tasks = db.query(LLMTaskStatus).filter(LLMTaskStatus.user_id == user_id)\
        .order_by(LLMTaskStatus.updated_at.desc()).limit(25).all()
    components = db.query(MasterResumeComponent).filter(
        MasterResumeComponent.user_id == user_id).order_by(MasterResumeComponent.order.asc()).all()
    events = db.query(ApplicationEvent).filter(ApplicationEvent.user_id == user_id)\
        .order_by(ApplicationEvent.created_at.desc()).limit(30).all()

    status_mix = {}
    for j in db.query(Job).filter(Job.user_id == user_id).all():
        status_mix[j.status or "unknown"] = status_mix.get(j.status or "unknown", 0) + 1

    return {
        **row,
        "job_status_mix": status_mix,
        # Profile facts are the user's own words about themselves, so they are
        # shown as-is; nothing here exposes credentials.
        "facts": facts,
        "documents": [{
            "id": c.id, "name": c.name, "type": c.type,
            "chars": len(c.content_text or ""), "active": bool(c.is_active),
            "filename": c.original_filename,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        } for c in components],
        "jobs": [{
            "id": j.id, "company": j.company, "title": j.title, "status": j.status,
            "match_score": j.match_score,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        } for j in jobs],
        "resumes": [{
            "id": r.id, "job_id": r.job_id, "version": r.version, "llm_used": r.llm_used,
            "chars": len(r.content_md or ""),
            "ats_total": (r.ats_score or {}).get("total") if isinstance(r.ats_score, dict) else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in resumes],
        "tasks": [{
            "id": t.id, "job_id": t.job_id, "task_type": t.task_type, "status": t.status,
            "error": t.error_message,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        } for t in tasks],
        "events": [{
            "id": e.id, "job_id": e.job_id, "type": e.event_type, "title": e.title,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        } for e in events],
    }


_static_dir = Path(__file__).parent / "static"


@app.get("/api/build")
def get_build():
    """Which frontend bundle this server is actually serving.

    Asset filenames are content-hashed, so this is the one honest answer to
    "is my change deployed?" -- compare it against the script tag the browser
    loaded. A stale index.html pins a browser to a bundle that no longer
    exists here, and nothing in the UI says so.
    """
    assets = _static_dir / "assets"
    return {"bundle": sorted(f.name for f in assets.glob("index-*.js"))} if assets.exists() else {"bundle": []}


class _SPAStatic(StaticFiles):
    """Serve index.html uncached.

    Every other file carries a content hash in its name, so it can be cached
    forever. index.html cannot -- its name never changes -- and a cached copy
    keeps pointing browsers (and Cloudflare, for its whole TTL) at the bundle
    from the previous deploy.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if str(getattr(response, "media_type", "")).startswith("text/html"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


if _static_dir.exists():
    app.mount("/", _SPAStatic(directory=str(_static_dir), html=True), name="frontend")
