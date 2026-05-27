"""
HireOS — FastAPI Backend
Includes all CRUD endpoints + live agent endpoints.
"""
from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from typing import List, Optional, Any, Dict
import os
import csv
import io
import json
import asyncio
from pathlib import Path
import shutil

from database import (
    init_db, get_db, Job, ApplicationEvent, ResumeVersion,
    CoverLetterVersion, Settings, LLMComparison,
    EvaluationReport, StoryBankEntry, FollowUpLog,
    MasterResumeComponent, ChatInteraction
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

app = FastAPI(title="HireOS API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search_router)

# Lazy-loaded singletons
_router = None
_agents = {}


def get_llm_router():
    global _router
    if _router is None:
        from llm_router import LLMRouter
        _router = LLMRouter()
    return _router


def get_agent(name: str):
    global _agents
    if name not in _agents:
        from agents import (
            JobDiscoveryAgent, FitAssessmentAgent,
            ResumeTailoringAgent, CoverLetterAgent, ChatDispatcherAgent,
            LinkedInOutreachAgent, DeepResearchAgent, InterviewPrepAgent,
            ResumeCriticAgent, InsightsAgent,
        )
        router = get_llm_router()
        _agents = {
            "discovery": JobDiscoveryAgent(router),
            "fit": FitAssessmentAgent(router),
            "resume": ResumeTailoringAgent(router),
            "critic": ResumeCriticAgent(router),
            "cover": CoverLetterAgent(router),
            "chat": ChatDispatcherAgent(router),
            "linkedin": LinkedInOutreachAgent(router),
            "deep_research": DeepResearchAgent(router),
            "interview_prep": InterviewPrepAgent(router),
            "insights": InsightsAgent(router),
        }
    return _agents[name]


def get_master_resume(db: Session) -> str:
    """Load the master resume by joining all active components."""
    # Check new system first
    components = db.query(MasterResumeComponent).filter(MasterResumeComponent.is_active == True).order_by(MasterResumeComponent.order.asc()).all()
    if components:
        combined = []
        for c in components:
            combined.append(f"--- START COMPONENT: {c.name} ({c.type}) ---")
            combined.append(c.content_text or "")
        return "\n\n".join(combined)

    # Fallback to legacy setting
    row = db.query(Settings).filter(Settings.key == "master_resume").first()
    if row and row.value:
        return row.value
    # Fallback to output dir file
    resume_file = Path(os.getenv("OUTPUT_DIR", "./output")) / "master_resume.md"
    if resume_file.exists():
        return resume_file.read_text()
    return ""


@app.on_event("startup")
def startup():
    init_db()
    os.makedirs(os.getenv("OUTPUT_DIR", "./output"), exist_ok=True)
    _startup_sync_env()
    # Init ChromaDB and purge interactions older than 90 days
    try:
        chat_store._get_collection()
        purged = chat_store.purge_old(days=90)
        if purged:
            logger.info(f"[ChatStore] Purged {purged} old interactions on startup")
    except Exception as e:
        logger.warning(f"[ChatStore] Startup init failed (non-fatal): {e}")
    logger.info("HireOS API started ✓")


def _startup_sync_env():
    """Load API keys from DB into os.environ at server startup."""
    from database import engine
    from sqlalchemy.orm import Session as _Session
    env_map = {
        "gemini_api_key": "GEMINI_API_KEY",
        "groq_api_key": "GROQ_API_KEY",
        "openrouter_api_key": "OPENROUTER_API_KEY",
        "together_api_key": "TOGETHER_API_KEY",
        "ollama_base_url": "OLLAMA_BASE_URL",
        "github_token": "GITHUB_TOKEN",
        "github_username": "GITHUB_USERNAME",
    }
    try:
        with _Session(engine) as db:
            rows = db.query(Settings).all()
            loaded = []
            for row in rows:
                if row.key in env_map and row.value and len(row.value) > 4 and "\u2022" not in row.value:
                    os.environ[env_map[row.key]] = row.value
                    loaded.append(env_map[row.key])
            if loaded:
                logger.info(f"Loaded {len(loaded)} API key(s) from DB: {loaded}")
    except Exception as e:
        logger.warning(f"Could not sync settings at startup: {e}")


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/")
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
def get_stats(db: Session = Depends(get_db)):
    total = db.query(func.count(Job.id)).scalar()
    week_ago = datetime.utcnow() - timedelta(days=7)

    by_status = {
        row.status: row.count
        for row in db.query(Job.status, func.count(Job.id).label("count")).group_by(Job.status).all()
    }
    applied_this_week = db.query(func.count(Job.id)).filter(Job.applied_at >= week_ago).scalar()
    now = datetime.utcnow()
    follow_ups_due = db.query(func.count(Job.id)).filter(
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
):
    q = db.query(Job)
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
def create_job(job: JobCreate, db: Session = Depends(get_db)):
    db_job = Job(**job.model_dump())
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    _log_event(db, db_job.id, "status_change", "Job Added",
               f"Tracking started for {db_job.title} at {db_job.company}",
               new_value="found", source="user")
    return db_job


@app.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.patch("/api/jobs/{job_id}", response_model=JobOut)
def update_job(job_id: int, updates: JobUpdate, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

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
                   old_value=old_status, new_value=job.status, source="user")
    return job


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    db.delete(job)
    db.commit()
    return {"status": "deleted"}


@app.get("/api/jobs/{job_id}/tasks")
def get_job_tasks(job_id: int, db: Session = Depends(get_db)):
    """Get status of all async LLM tasks for a job."""
    return db.query(LLMTaskStatus).filter(
        LLMTaskStatus.job_id == job_id
    ).all()


# ── Application Timeline Events ────────────────────────────────────────────────

@app.get("/api/jobs/{job_id}/events", response_model=List[EventOut])
def get_events(job_id: int, db: Session = Depends(get_db)):
    """Get all events and follow-up logs for a job, unified and sorted."""
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
def add_event(job_id: int, event: EventCreate, db: Session = Depends(get_db)):
    db_event = ApplicationEvent(job_id=job_id, **event.model_dump())
    db.add(db_event)
    db.commit()
    db.refresh(db_event)
    return db_event


def _log_event(db, job_id, event_type, title, description=None,
               old_value=None, new_value=None, source="agent"):
    ev = ApplicationEvent(
        job_id=job_id, event_type=event_type, title=title,
        description=description, old_value=old_value,
        new_value=new_value, source=source,
    )
    db.add(ev)
    db.commit()


# ── Resumes & Cover Letters ────────────────────────────────────────────────────

@app.get("/api/jobs/{job_id}/resumes", response_model=List[ResumeVersionOut])
def get_resumes(job_id: int, db: Session = Depends(get_db)):
    return db.query(ResumeVersion).filter(ResumeVersion.job_id == job_id).order_by(ResumeVersion.created_at.desc()).all()


@app.get("/api/jobs/{job_id}/cover-letters", response_model=List[CoverLetterVersionOut])
def get_cover_letters(job_id: int, db: Session = Depends(get_db)):
    return db.query(CoverLetterVersion).filter(CoverLetterVersion.job_id == job_id).order_by(CoverLetterVersion.created_at.desc()).all()


@app.get("/api/download/{job_id}/{filename}")
def download_file(job_id: int, filename: str):
    """Download a generated document file."""
    output_dir = Path(os.getenv("OUTPUT_DIR", "./output"))
    file_path = output_dir / str(job_id) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path), filename=filename)


# ── Settings ───────────────────────────────────────────────────────────────────

@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db)):
    rows = db.query(Settings).all()
    result = {r.key: r.value for r in rows}
    # Mask secrets in response
    for k in result:
        if any(s in k for s in ["key", "token", "secret", "password"]):
            if result[k]:
                result[k] = "••••••••" + result[k][-4:] if len(result[k]) > 4 else "••••••••"
    return result


@app.post("/api/settings")
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_db)):
    for item in payload.settings:
        # Don't save masked values back
        if item.value and "••••" in item.value:
            continue
        row = db.query(Settings).filter(Settings.key == item.key).first()
        if row:
            row.value = item.value
            row.updated_at = datetime.utcnow()
        else:
            db.add(Settings(key=item.key, value=item.value))
    db.commit()
    # Reload .env-style settings into env for agents
    _sync_settings_to_env(db)
    return {"status": "saved"}


def _sync_settings_to_env(db: Session):
    """Sync DB settings → os.environ so agents pick up new keys without restart."""
    env_map = {
        "gemini_api_key": "GEMINI_API_KEY",
        "groq_api_key": "GROQ_API_KEY",
        "openrouter_api_key": "OPENROUTER_API_KEY",
        "together_api_key": "TOGETHER_API_KEY",
        "ollama_base_url": "OLLAMA_BASE_URL",
        "github_token": "GITHUB_TOKEN",
        "github_username": "GITHUB_USERNAME",
    }
    rows = db.query(Settings).all()
    for row in rows:
        if row.key in env_map and row.value and "••••" not in row.value:
            os.environ[env_map[row.key]] = row.value
    # Reset router so it picks up new keys
    global _router, _agents
    _router = None
    _agents = {}


# ── Master Resume Components ──────────────────────────────────────────────────

@app.get("/api/settings/resume-components", response_model=List[ResumeComponentOut])
def list_resume_components(db: Session = Depends(get_db)):
    """List all master resume components."""
    return db.query(MasterResumeComponent).order_by(MasterResumeComponent.order.asc()).all()


@app.post("/api/settings/resume-components/text", response_model=ResumeComponentOut)
def create_text_component(payload: ResumeComponentCreate, db: Session = Depends(get_db)):
    """Add a manual text block to the master resume."""
    component = MasterResumeComponent(
        name=payload.name,
        type="text",
        content_text=payload.content_text,
        is_active=payload.is_active,
        order=payload.order
    )
    db.add(component)
    db.commit()
    db.refresh(component)
    return component


@app.post("/api/settings/resume-components/file", response_model=ResumeComponentOut)
async def upload_resume_file(
    name: str = Query(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """Upload a PDF or Markdown file as a resume component."""
    from pdf_utils import extract_text_from_pdf
    
    file_content = await file.read()
    filename = file.filename
    content_text = ""
    
    # Extract text based on extension
    if filename.lower().endswith(".pdf"):
        content_text = extract_text_from_pdf(file_content)
    elif filename.lower().endswith(".md") or filename.lower().endswith(".txt"):
        content_text = file_content.decode("utf-8", errors="ignore")
    else:
        raise HTTPException(status_code=400, detail="Unsupported file type. Please upload PDF or Markdown.")

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
        order=0
    )
    db.add(component)
    db.commit()
    db.refresh(component)
    return component


@app.patch("/api/settings/resume-components/{id}", response_model=ResumeComponentOut)
def update_resume_component(id: int, payload: ResumeComponentUpdate, db: Session = Depends(get_db)):
    """Update a resume component (toggle active, change name, etc)."""
    component = db.query(MasterResumeComponent).get(id)
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
def delete_resume_component(id: int, db: Session = Depends(get_db)):
    """Delete a resume component."""
    component = db.query(MasterResumeComponent).get(id)
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
async def track_url(payload: Dict[str, Any], db: Session = Depends(get_db)):
    """
    Fetch a job URL, extract structured data using AI, and save it to DB.
    Body: { "url": "...", "llm": "gemini" }
    """
    url = payload.get("url", "").strip()
    llm = payload.get("llm", "gemini")
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    try:
        # Retrieve additional settings for scraping
        captcha_key = db.query(Settings).filter(Settings.key == "twocaptcha_api_key").first()
        proxy_url = db.query(Settings).filter(Settings.key == "proxy_url").first()
        
        agent = get_agent("discovery")
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
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    _log_event(db, job.id, "agent_action", "Job discovered via URL",
               f"Auto-extracted: {job.title} at {job.company}", source="agent")

    return {"job_id": job.id, "job": {
        "id": job.id, "company": job.company, "title": job.title,
        "status": job.status, "platform": job.platform, "remote": job.remote,
    }}




@app.post("/api/agent/track-jd")
async def track_jd_text(payload: Dict[str, Any], db: Session = Depends(get_db)):
    """
    Extract job data from pasted JD text (for LinkedIn and login-walled sites).
    Body: { "text": "...", "llm": "gemini" }
    """
    text = payload.get("text", "").strip()
    llm = payload.get("llm", "gemini")
    if not text or len(text) < 50:
        raise HTTPException(status_code=400, detail="Please paste the full job description text (at least 50 characters).")

    try:
        agent = get_agent("discovery")
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
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    _log_event(db, job.id, "agent_action", "Job added via pasted JD",
               f"Extracted: {job.title} at {job.company}", source="agent")

    return {"job_id": job.id, "job": {
        "id": job.id, "company": job.company, "title": job.title,
        "status": job.status, "platform": job.platform, "remote": job.remote,
    }}

# ── Agent: Fit Assessment ──────────────────────────────────────────────────────

async def _bg_analyze(job_id: int, llm: str):
    from database import SessionLocal
    db = SessionLocal()
    try:
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="analyze").first()
        if not task:
            task = LLMTaskStatus(job_id=job_id, task_type="analyze", status="processing")
            db.add(task)
        else:
            task.status = "processing"
            task.error_message = None
        db.commit()

        job = db.query(Job).filter(Job.id == job_id).first()
        if not job: raise ValueError("Job not found")
        master_resume = db.query(Settings).filter(Settings.key == "master_resume").first()
        if not master_resume or not master_resume.value: raise ValueError("Master resume not found")

        job.status = "analyzing"
        db.commit()

        agent = get_agent("fit")
        result = await agent.analyze(
            job_description=job.job_description or "",
            master_resume=master_resume.value,
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
            job_id=job_id, event_type="agent_action",
            title=f"Gap Analysis complete — {job.match_score}% match",
            description=f"{len(job.gaps or [])} gaps found", source="agent"
        ))

        try:
            cid = chat_store.log_interaction(
                user_input=f"Run gap analysis for {job.title} at {job.company}",
                agent_output=f"Score: {job.match_score}%, {len(job.gaps or [])} gaps found",
                action_type="gap_analysis", job_id=job_id, llm_used=llm,
                company=job.company, title=job.title,
            )
            db.add(ChatInteraction(chroma_id=cid, action_type="gap_analysis", job_id=job_id,
                                   company=job.company, title=job.title, llm_used=llm))
        except Exception as ce:
            logger.warning(f"[ChatStore] gap_analysis log failed: {ce}")

        task.status = "completed"
        db.commit()
    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)
        job = db.query(Job).filter(Job.id == job_id).first()
        if job and job.status == "analyzing":
            job.status = "found"
        db.commit()
    finally:
        db.close()


@app.post("/api/agent/analyze/{job_id}")
async def analyze_job(job_id: int, background_tasks: BackgroundTasks, payload: Dict[str, Any] = {}, db: Session = Depends(get_db)):
    llm = payload.get("llm", "gemini")
    background_tasks.add_task(_bg_analyze, job_id, llm)
    return {"status": "processing"}


# ── Agent: Generate Resume ─────────────────────────────────────────────────────

async def _bg_resume(job_id: int, llm: str, feedback: str, critic_llm: str = "claude"):
    from database import SessionLocal
    from doc_generator import save_resume
    db = SessionLocal()
    try:
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="resume").first()
        if not task:
            task = LLMTaskStatus(job_id=job_id, task_type="resume", status="processing")
            db.add(task)
        else:
            task.status = "processing"
            task.error_message = None
        db.commit()

        job = db.query(Job).get(job_id)
        if not job:
            raise ValueError("Job not found")
        master_resume = db.query(Settings).filter(Settings.key == "master_resume").first()
        if not master_resume or not master_resume.value:
            raise ValueError("Master resume not configured. Go to Settings and upload your master resume.")

        gh_token = os.getenv("GITHUB_TOKEN", "")
        gh_user = os.getenv("GITHUB_USERNAME", "")
        agent_resume = get_agent("resume")
        github_ctx = ""
        if gh_token and gh_user:
            github_ctx = await agent_resume.fetch_github_context(gh_user, gh_token)

        design_rules_path = Path(__file__).parent.parent / "meta" / "resume_design.md"
        design_rules = design_rules_path.read_text() if design_rules_path.exists() else ""

        # Pass 1: tailor (design rules injected into prompt)
        resume_md = await agent_resume.tailor(
            job_description=job.job_description or "",
            master_resume=master_resume.value,
            company=job.company,
            title=job.title,
            gaps=job.gaps or [],
            action_items=job.action_items or [],
            github_context=github_ctx,
            feedback=feedback,
            design_rules=design_rules,
            llm=llm,
        )

        # Pass 2: validate against design rules
        if design_rules:
            resume_md = await agent_resume.validate_design(resume_md, design_rules, llm=llm)

        # Pass 3: brutal critique using a different LLM for independent perspective
        critic_notes = None
        try:
            agent_critic = get_agent("critic")
            critic_notes = await agent_critic.critique(
                resume_md=resume_md,
                job_description=job.job_description or "",
                company=job.company,
                title=job.title,
                llm=critic_llm,
            )
        except Exception as ce:
            logger.warning(f"[ResumeCritic] Critique failed (non-fatal): {ce}")

        # Save version
        version = db.query(func.count(ResumeVersion.id)).filter(ResumeVersion.job_id == job_id).scalar() + 1
        paths = save_resume(job_id, version, resume_md)

        rv = ResumeVersion(
            job_id=job_id, version=version,
            content_md=resume_md,
            pdf_path=paths.get("pdf"),
            docx_path=paths.get("docx"),
            llm_used=llm,
            critic_notes=critic_notes,
        )
        db.add(rv)

        _log_event(db, job_id, "document_generated",
                   f"Resume v{version} generated",
                   f"Tailored via {llm} | Critic: {critic_llm} (score: {critic_notes.get('score', '?') if critic_notes else 'n/a'}/10)",
                   source="agent")

        try:
            cid = chat_store.log_interaction(
                user_input=f"Generate resume for {job.title} at {job.company}",
                agent_output=f"Resume v{version} generated. Critic score: {critic_notes.get('score', '?') if critic_notes else 'n/a'}/10",
                action_type="resume", job_id=job_id, llm_used=llm,
                company=job.company, title=job.title,
            )
            db.add(ChatInteraction(chroma_id=cid, action_type="resume", job_id=job_id,
                                   company=job.company, title=job.title, llm_used=llm))
        except Exception as ce:
            logger.warning(f"[ChatStore] resume log failed: {ce}")

        task.status = "completed"
        db.commit()

    except Exception as e:
        logger.error(f"[ResumeGen] Failed for job {job_id}: {e}")
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="resume").first()
        if task:
            task.status = "failed"
            task.error_message = str(e)
            db.commit()
    finally:
        db.close()


@app.post("/api/agent/resume/{job_id}")
async def start_generate_resume(
    job_id: int,
    background_tasks: BackgroundTasks,
    payload: Dict[str, Any] = {},
    db: Session = Depends(get_db),
):
    llm = payload.get("llm", "gemini")
    feedback = payload.get("feedback", "")
    critic_llm = payload.get("critic_llm", "claude")
    background_tasks.add_task(_bg_resume, job_id, llm, feedback, critic_llm)
    return {"status": "processing"}


@app.get("/api/agent/resume/{job_id}")
def get_resume_status(job_id: int, db: Session = Depends(get_db)):
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
            "llm_used": latest.llm_used,
        }
    if task and task.status == "failed":
        return {"status": "failed", "error": task.error_message}
    return {"status": "none"}


# ── Agent: Generate Cover Letter ───────────────────────────────────────────────

async def _bg_cover_letter(job_id: int, llm: str, feedback: str, resume_version: int):
    from database import SessionLocal
    db = SessionLocal()
    try:
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="cover_letter").first()
        if not task:
            task = LLMTaskStatus(job_id=job_id, task_type="cover_letter", status="processing")
            db.add(task)
        else:
            task.status = "processing"
            task.error_message = None
        db.commit()

        job = db.query(Job).get(job_id)
        if not job: raise ValueError("Job not found")

        q = db.query(ResumeVersion).filter(ResumeVersion.job_id == job_id)
        if resume_version: q = q.filter(ResumeVersion.version == resume_version)
        rv = q.order_by(ResumeVersion.created_at.desc()).first()

        master_resume = db.query(Settings).filter(Settings.key == "master_resume").first()
        if not rv and (not master_resume or not master_resume.value):
            raise ValueError("Generate a resume first or set master resume")

        agent_cover = get_agent("cover")
        cl_md = await agent_cover.generate(
            job_description=job.job_description or "",
            tailored_resume=rv.content_md if rv else master_resume.value,
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
            job_id=job_id, version=version,
            content_md=cl_md, pdf_path=paths.get("pdf"),
            llm_used=llm
        )
        db.add(clv)

        db.add(ApplicationEvent(
            job_id=job_id, event_type="document_generated",
            title=f"Cover Letter v{version} generated", source="agent"
        ))

        try:
            job = db.query(Job).get(job_id)
            cid = chat_store.log_interaction(
                user_input=f"Generate cover letter for {job.title if job else ''} at {job.company if job else ''}",
                agent_output=f"Cover letter v{version} generated",
                action_type="cover_letter", job_id=job_id, llm_used=llm,
                company=job.company if job else "", title=job.title if job else "",
            )
            db.add(ChatInteraction(chroma_id=cid, action_type="cover_letter", job_id=job_id,
                                   company=job.company if job else "", title=job.title if job else "",
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
async def generate_cover_letter(job_id: int, background_tasks: BackgroundTasks, payload: Dict[str, Any] = {}, db: Session = Depends(get_db)):
    llm = payload.get("llm", "gemini")
    feedback = payload.get("feedback", "")
    resume_version = payload.get("resume_version", None)
    background_tasks.add_task(_bg_cover_letter, job_id, llm, feedback, resume_version)
    return {"status": "processing"}


# ── Agent: LLM Comparison ──────────────────────────────────────────────────────

@app.post("/api/agent/compare/{job_id}")
async def compare_llms(job_id: int, payload: Dict[str, Any] = {}, db: Session = Depends(get_db)):
    """
    Fan out resume or cover letter generation to multiple LLMs.
    Body: { "task": "resume|cover_letter", "providers": ["gemini", "groq"] }
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    task = (payload or {}).get("task", "resume")
    providers = (payload or {}).get("providers", ["gemini", "groq"])
    master_resume = get_master_resume(db)

    router = get_llm_router()

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
    comp = LLMComparison(job_id=job_id, prompt_type=task, results=results)
    db.add(comp)
    db.commit()

    _log_event(db, job_id, "agent_action",
               f"LLM Comparison: {task}",
               f"Compared {len(providers)} providers: {', '.join(providers)}", source="agent")

    return {"task": task, "results": results}


# ── Agent: Floating Chat ───────────────────────────────────────────────────────

@app.post("/api/chat")
async def chat(msg: ChatMessage, db: Session = Depends(get_db)):
    """
    Global floating chat — routes natural language to agents.
    """
    llm = getattr(msg, "llm", "gemini") or "gemini"
    context = getattr(msg, "context", None)

    try:
        dispatcher = get_agent("chat")
        action = await dispatcher.dispatch(msg.message, context=context, llm=llm)
    except Exception as e:
        logger.warning(f"[Chat] Dispatch failed: {e}")
        return ChatResponse(reply=f"Sorry, I hit an error: {e}")

    action_type = action.get("action", "general_reply")
    result_data = None

    # Execute the dispatched action
    try:
        if action_type == "track_job":
            url = action.get("url", "")
            if url:
                result_data = await track_url({"url": url, "llm": llm}, db=db)
                reply = f"✅ Added **{result_data['job']['title']}** at **{result_data['job']['company']}** to your pipeline! (Job #{result_data['job_id']})"
            else:
                reply = "I need the job URL. Paste it and I'll add it for you!"

        elif action_type == "analyze_job":
            job_id = action.get("job_id")
            if job_id:
                asyncio.create_task(_bg_analyze(job_id, llm))
                reply = f"🤖 Gap analysis running for job #{job_id} — check back in a moment or watch the timeline."
            else:
                reply = "Which job should I analyze? Share the job ID or company name."

        elif action_type == "generate_resume":
            job_id = action.get("job_id")
            if job_id:
                asyncio.create_task(_bg_resume(job_id, llm, "", "claude"))
                reply = f"📄 Resume generation started for job #{job_id} — check the Resumes tab shortly."
            else:
                reply = "Which job? Give me the job ID or company name."

        elif action_type == "generate_cover_letter":
            job_id = action.get("job_id")
            if job_id:
                asyncio.create_task(_bg_cover_letter(job_id, llm, "", 0))
                reply = f"✉️ Cover letter generation started for job #{job_id} — check the Cover Letters tab shortly."
            else:
                reply = "Which job? Give me the job ID."

        elif action_type == "evaluate_job_structured":
            job_id = action.get("job_id")
            if job_id:
                asyncio.create_task(_bg_evaluate(job_id, llm))
                reply = f"⚡ A-G Evaluation running for job #{job_id} — the full 7-block report will appear in the Evaluation tab."
            else:
                reply = "Which job should I evaluate?"

        elif action_type == "linkedin_outreach":
            job_id = action.get("job_id")
            contact_type = action.get("contact_type", "recruiter")
            if job_id:
                asyncio.create_task(_bg_linkedin(job_id, contact_type, llm))
                reply = f"💼 LinkedIn outreach generation started for job #{job_id} (targeting: {contact_type}) — check the LinkedIn tab."
            else:
                reply = "Which job do you need LinkedIn outreach for?"

        elif action_type == "deep_research":
            job_id = action.get("job_id")
            if job_id:
                asyncio.create_task(_bg_deep_research(job_id, llm))
                reply = f"🔍 Deep research running for job #{job_id} — results will appear in the Research tab."
            else:
                reply = "Which company/job should I research?"

        elif action_type == "interview_prep":
            job_id = action.get("job_id")
            if job_id:
                asyncio.create_task(_bg_interview_prep(job_id, llm))
                reply = f"🎤 Interview prep running for job #{job_id} — STAR stories will appear in the Interview Prep tab."
            else:
                reply = "Which job are you prepping for?"

        elif action_type == "get_stats":
            stats = get_stats(db=db)
            reply = f"📊 You have **{stats.total}** jobs tracked — {stats.by_status.get('applied', 0)} applied, {stats.interviews_scheduled} interviews, {stats.offers} offers. {stats.follow_ups_due} follow-ups due!"

        elif action_type == "list_jobs":
            filt = action.get("filter")
            jobs = list_jobs(status=filt, limit=5, db=db)
            if jobs:
                lines = "\n".join(f"• **{j.company}** — {j.title} (#{j.id}, {j.status})" for j in jobs)
                reply = f"Here are your {'recent' if not filt else filt} jobs:\n\n{lines}"
            else:
                reply = f"No jobs found{' with status ' + filt if filt else ''}."

        elif action_type == "apply_job":
            job_id = action.get("job_id")
            if job_id:
                reply = f"🚀 To protect you, the Application Agent requires you to click **Approve & Apply** in the Job Detail view (Job #{job_id}). Open it now?"
            else:
                reply = "Which job should I apply to? Share the job ID."

        else:
            reply = action.get("text", "How can I help you with your job search? I can track jobs, run gap analysis, generate resumes and cover letters, or apply to jobs.")

    except HTTPException as e:
        reply = f"⚠️ {e.detail}"
    except Exception as e:
        logger.error(f"[Chat] Action execution failed: {e}")
        reply = f"Something went wrong: {e}"

    # Log to ChromaDB + SQLite
    try:
        job_id_for_log = action.get("job_id")
        job_for_log = db.query(Job).get(job_id_for_log) if job_id_for_log else None
        chroma_id = chat_store.log_interaction(
            user_input=msg.message,
            agent_output=reply,
            action_type=action_type if action_type != "general_reply" else "chat",
            job_id=job_id_for_log,
            llm_used=llm,
            company=job_for_log.company if job_for_log else "",
            title=job_for_log.title if job_for_log else "",
        )
        db.add(ChatInteraction(
            chroma_id=chroma_id,
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
def export_csv(db: Session = Depends(get_db)):
    jobs = db.query(Job).all()
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
def get_providers():
    router = get_llm_router()
    return {"available": router.available_providers()}


# ══════════════════════════════════════════════════════════════════════════════
# CAREER-OPS ENHANCED ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

# ── Full A-G Structured Evaluation ─────────────────────────────────────────────

async def _bg_evaluate(job_id: int, llm: str):
    from database import SessionLocal
    db = SessionLocal()
    try:
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="evaluate").first()
        if not task:
            task = LLMTaskStatus(job_id=job_id, task_type="evaluate", status="processing")
            db.add(task)
        else:
            task.status = "processing"
            task.error_message = None
        db.commit()

        job = db.query(Job).get(job_id)
        if not job:
            raise ValueError("Job not found")

        master_resume = db.query(Settings).filter(Settings.key == "master_resume").first()
        if not master_resume or not master_resume.value:
            raise ValueError("Set your master resume in Settings first")

        agent = get_agent("fit")
        result = await agent.analyze(
            job_description=job.job_description or "",
            master_resume=master_resume.value,
            company=job.company,
            title=job.title,
            llm=llm,
        )

        # Save evaluation report to DB
        report = EvaluationReport(
            job_id=job_id,
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
            event_type="agent_action",
            title="Full A-G Evaluation Completed",
            description=f"Global score: {result.get('global_score', 'N/A')}/100 | Archetype: {block_a.get('archetype', 'N/A')}",
            source="agent",
        ))

        try:
            job = db.query(Job).get(job_id)
            cid = chat_store.log_interaction(
                user_input=f"A-G evaluation for {job.title if job else ''} at {job.company if job else ''}",
                agent_output=f"Score: {result.get('global_score', '?')}/100 | Archetype: {block_a.get('archetype', '?')}",
                action_type="evaluation", job_id=job_id, llm_used=llm,
                company=job.company if job else "", title=job.title if job else "",
            )
            db.add(ChatInteraction(chroma_id=cid, action_type="evaluation", job_id=job_id,
                                   company=job.company if job else "", title=job.title if job else "",
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
    llm: str = "gemini",
    db: Session = Depends(get_db),
):
    background_tasks.add_task(_bg_evaluate, job_id, llm)
    return {"status": "processing"}


@app.get("/api/evaluations/{job_id}", response_model=List[EvaluationReportOut])
def get_evaluations(job_id: int, db: Session = Depends(get_db)):
    """Get all evaluation reports for a job."""
    return db.query(EvaluationReport).filter(
        EvaluationReport.job_id == job_id
    ).order_by(EvaluationReport.created_at.desc()).all()


# ── LinkedIn Outreach ──────────────────────────────────────────────────────────

async def _bg_linkedin(job_id: int, contact_type: str, llm: str):
    from database import SessionLocal
    db = SessionLocal()
    try:
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="linkedin").first()
        if not task:
            task = LLMTaskStatus(job_id=job_id, task_type="linkedin", status="processing")
            db.add(task)
        else:
            task.status = "processing"
            task.error_message = None
        db.commit()

        job = db.query(Job).get(job_id)
        if not job:
            raise ValueError("Job not found")

        master_resume = db.query(Settings).filter(Settings.key == "master_resume").first()
        if not master_resume or not master_resume.value:
            raise ValueError("Set your master resume in Settings first")

        agent = get_agent("linkedin")
        result = await agent.generate(
            job_description=job.job_description or "",
            master_resume=master_resume.value,
            company=job.company,
            title=job.title,
            contact_type=contact_type,
            llm=llm,
        )

        db.add(ApplicationEvent(
            job_id=job_id,
            event_type="agent_action",
            title="LinkedIn Outreach Generated",
            description=f"Contact type: {contact_type}",
            source="agent",
        ))

        report = db.query(LinkedInOutreachReport).filter_by(job_id=job_id).first()
        if not report:
            report = LinkedInOutreachReport(job_id=job_id)
            db.add(report)
        report.outreach_data = result
        report.llm_used = llm

        try:
            job = db.query(Job).get(job_id)
            cid = chat_store.log_interaction(
                user_input=f"LinkedIn outreach ({contact_type}) for {job.title if job else ''} at {job.company if job else ''}",
                agent_output=f"Messages generated for {contact_type}",
                action_type="linkedin", job_id=job_id, llm_used=llm,
                company=job.company if job else "", title=job.title if job else "",
            )
            db.add(ChatInteraction(chroma_id=cid, action_type="linkedin", job_id=job_id,
                                   company=job.company if job else "", title=job.title if job else "",
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
):
    llm = payload.get("llm", "gemini")
    if "contact_type" in payload:
        contact_type = payload["contact_type"]
    background_tasks.add_task(_bg_linkedin, job_id, contact_type, llm)
    return {"status": "processing"}


@app.get("/api/agent/linkedin/{job_id}")
def get_linkedin_outreach(job_id: int, db: Session = Depends(get_db)):
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

async def _bg_deep_research(job_id: int, llm: str):
    from database import SessionLocal
    db = SessionLocal()
    try:
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="research").first()
        if not task:
            task = LLMTaskStatus(job_id=job_id, task_type="research", status="processing")
            db.add(task)
        else:
            task.status = "processing"
            task.error_message = None
        db.commit()

        job = db.query(Job).get(job_id)
        if not job:
            raise ValueError("Job not found")

        master_resume = db.query(Settings).filter(Settings.key == "master_resume").first()

        agent = get_agent("deep_research")
        result = await agent.research(
            company=job.company,
            title=job.title,
            job_description=job.job_description or "",
            master_resume=master_resume.value if master_resume else "",
            llm=llm,
        )

        db.add(ApplicationEvent(
            job_id=job_id,
            event_type="agent_action",
            title="Deep Company Research Completed",
            description=f"6-axis research for {job.company}",
            source="agent",
        ))

        report = db.query(ResearchReport).filter_by(job_id=job_id).first()
        if not report:
            report = ResearchReport(job_id=job_id)
            db.add(report)
        report.research_data = result
        report.llm_used = llm

        try:
            cid = chat_store.log_interaction(
                user_input=f"Deep research for {job.title} at {job.company}",
                agent_output=f"6-axis research completed for {job.company}",
                action_type="research", job_id=job_id, llm_used=llm,
                company=job.company, title=job.title,
            )
            db.add(ChatInteraction(chroma_id=cid, action_type="research", job_id=job_id,
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
):
    llm = payload.get("llm", "gemini")
    background_tasks.add_task(_bg_deep_research, job_id, llm)
    return {"status": "processing"}


@app.get("/api/agent/deep-research/{job_id}")
def get_deep_research(job_id: int, db: Session = Depends(get_db)):
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

async def _bg_interview_prep(job_id: int, llm: str):
    from database import SessionLocal
    db = SessionLocal()
    try:
        task = db.query(LLMTaskStatus).filter_by(job_id=job_id, task_type="interview_prep").first()
        if not task:
            task = LLMTaskStatus(job_id=job_id, task_type="interview_prep", status="processing")
            db.add(task)
        else:
            task.status = "processing"
            task.error_message = None
        db.commit()

        job = db.query(Job).get(job_id)
        if not job:
            raise ValueError("Job not found")

        master_resume = db.query(Settings).filter(Settings.key == "master_resume").first()
        if not master_resume or not master_resume.value:
            raise ValueError("Set your master resume in Settings first")

        existing = db.query(StoryBankEntry).filter(StoryBankEntry.job_id == job_id).all()
        existing_dicts = [{"title": s.title, "jd_requirement": s.jd_requirement} for s in existing]

        agent = get_agent("interview_prep")
        result = await agent.generate_stories(
            job_description=job.job_description or "",
            master_resume=master_resume.value,
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
            event_type="agent_action",
            title="Interview Prep Generated",
            description=f"{saved_count} STAR+Reflection stories created",
            source="agent",
        ))

        report = db.query(InterviewPrepReport).filter_by(job_id=job_id).first()
        if not report:
            report = InterviewPrepReport(job_id=job_id)
            db.add(report)
        report.prep_data = result
        report.llm_used = llm

        try:
            job = db.query(Job).get(job_id)
            cid = chat_store.log_interaction(
                user_input=f"Interview prep for {job.title if job else ''} at {job.company if job else ''}",
                agent_output=f"{saved_count} STAR stories generated",
                action_type="interview_prep", job_id=job_id, llm_used=llm,
                company=job.company if job else "", title=job.title if job else "",
            )
            db.add(ChatInteraction(chroma_id=cid, action_type="interview_prep", job_id=job_id,
                                   company=job.company if job else "", title=job.title if job else "",
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
):
    llm = payload.get("llm", "gemini")
    background_tasks.add_task(_bg_interview_prep, job_id, llm)
    return {"status": "processing"}

@app.get("/api/agent/interview-prep/{job_id}")
def get_interview_prep(job_id: int, db: Session = Depends(get_db)):
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
):
    """Get all STAR+Reflection stories from the persistent story bank."""
    q = db.query(StoryBankEntry).order_by(StoryBankEntry.times_used.desc())
    if tag:
        # Filter stories by tag (JSON array contains)
        q = q.filter(StoryBankEntry.tags.contains(tag))
    return q.all()


@app.post("/api/story-bank", response_model=StoryBankEntryOut)
def add_story(
    story: StoryBankEntryCreate,
    job_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Manually add a story to the bank."""
    entry = StoryBankEntry(
        job_id=job_id,
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
def get_followup_cadence(db: Session = Depends(get_db)):
    """Get follow-up cadence dashboard with urgency classifications."""
    from agents import FollowUpCadenceEngine

    # Get all jobs in actionable statuses
    actionable_statuses = ["applied", "screening", "interview_1", "interview_2", "responded"]
    jobs = db.query(Job).filter(Job.status.in_(actionable_statuses)).all()

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
):
    """Log a follow-up action for a job."""
    job = db.query(Job).get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    # Calculate follow-up number
    existing_count = db.query(FollowUpLog).filter(
        FollowUpLog.job_id == job_id
    ).count()

    entry = FollowUpLog(
        job_id=job_id,
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
        event_type="follow_up_sent",
        title=f"Follow-up #{existing_count + 1} Sent",
        description=f"Channel: {followup.channel}" + (f" | Contact: {followup.contact_name}" if followup.contact_name else ""),
        source="user",
    ))

    db.commit()
    db.refresh(entry)
    return entry


@app.get("/api/followups/{job_id}", response_model=List[FollowUpLogOut])
def get_followups(job_id: int, db: Session = Depends(get_db)):
    """Get follow-up history for a job."""
    return db.query(FollowUpLog).filter(
        FollowUpLog.job_id == job_id
    ).order_by(FollowUpLog.sent_at.desc()).all()


# ── Pattern Analytics ──────────────────────────────────────────────────────────

@app.get("/api/analytics/patterns")
def get_pattern_analytics(db: Session = Depends(get_db)):
    """Run rejection pattern analysis across all tracked jobs."""
    from agents import PatternAnalyticsEngine

    jobs = db.query(Job).all()
    return PatternAnalyticsEngine.analyze(jobs)


# ── Insights Dashboard ─────────────────────────────────────────────────────────

@app.get("/api/insights")
async def get_insights(
    llm: str = "gemini",
    days: int = 90,
    db: Session = Depends(get_db),
):
    """Stats + LLM-generated narrative on job hunt activity."""
    stats = chat_store.get_stats(days=days)
    samples = chat_store.get_recent_samples(days=days, limit=20)

    narrative = None
    try:
        agent = get_agent("insights")
        narrative = await agent.generate_narrative(stats, samples, llm=llm)
    except Exception as e:
        logger.warning(f"[Insights] Narrative generation failed: {e}")
        narrative = {
            "summary": "Could not generate narrative.",
            "what_youre_doing": "",
            "momentum_score": 0,
            "momentum_rationale": str(e),
            "gaps": "",
            "top_3_recommendations": [],
            "warning": None,
        }

    return {
        "stats": stats,
        "narrative": narrative,
        "generated_at": datetime.utcnow().isoformat(),
    }


@app.get("/api/insights/history", response_model=List[ChatInteractionOut])
def get_interaction_history(
    page: int = 1,
    limit: int = 50,
    action_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Paginated interaction history from SQLite."""
    q = db.query(ChatInteraction).order_by(ChatInteraction.created_at.desc())
    if action_type:
        q = q.filter(ChatInteraction.action_type == action_type)
    return q.offset((page - 1) * limit).limit(limit).all()


@app.get("/api/insights/search")
def search_interactions(q: str, n: int = 10, days: int = 90):
    """Semantic search over interaction history using ChromaDB."""
    if not q.strip():
        raise HTTPException(400, "Query cannot be empty")
    results = chat_store.search_similar(q, n=n, days=days)
    return {"query": q, "results": results}


# ── Serve React frontend (production) ─────────────────────────────────────────
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="frontend")
