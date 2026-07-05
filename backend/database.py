from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime, JSON, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./swarm.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Job(Base):
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    company = Column(String, nullable=False)
    title = Column(String, nullable=False)
    url = Column(String)
    job_description = Column(Text)
    location = Column(String)
    salary_min = Column(Integer)
    salary_max = Column(Integer)
    remote = Column(Boolean, default=False)
    listed_at = Column(DateTime)
    platform = Column(String)   # linkedin | greenhouse | lever | workday
    status = Column(String, default="found")
    # found → analyzing → pending → approved → applied → screening
    # → interview_1 → interview_2 → offer | rejected | withdrawn
    match_score = Column(Float)
    strengths = Column(JSON)
    gaps = Column(JSON)
    action_items = Column(JSON)
    meta = Column(JSON)
    archetype = Column(String)  # Role archetype: AI Platform/LLMOps, Agentic, PM, SA, FDE, Transformation
    posting_legitimacy = Column(String)  # High Confidence | Proceed with Caution | Suspicious
    priority = Column(String, default="medium")  # low | medium | high
    starred = Column(Boolean, default=False)
    recruiter_name = Column(String)
    recruiter_email = Column(String)
    recruiter_linkedin = Column(String)
    applied_at = Column(DateTime)
    last_contact_at = Column(DateTime)
    follow_up_due = Column(DateTime)
    offer_amount = Column(Integer)
    rejection_reason = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ApplicationEvent(Base):
    """Full audit trail / activity log per job — manually added or auto-logged by agents."""
    __tablename__ = "application_events"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    event_type = Column(String, nullable=False)
    # Types: status_change | note | recruiter_contact | interview_scheduled |
    #        interview_completed | offer_received | follow_up_sent |
    #        agent_action | document_generated | application_submitted
    title = Column(String, nullable=False)
    description = Column(Text)
    old_value = Column(String)   # e.g. old status
    new_value = Column(String)   # e.g. new status
    source = Column(String, default="user")  # user | agent
    scheduled_at = Column(DateTime)           # for upcoming events
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class ResumeVersion(Base):
    __tablename__ = "resume_versions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    version = Column(Integer, default=1)
    name = Column(String, nullable=True)
    content_md = Column(Text)
    pdf_path = Column(String)
    docx_path = Column(String)
    llm_used = Column(String)
    critic_notes = Column(JSON)   # structured critique from CriticAgent
    ats_score = Column(JSON)      # auto ATS evaluation (vendored hiring-agent)
    created_at = Column(DateTime, default=datetime.utcnow)


class CoverLetterVersion(Base):
    __tablename__ = "cover_letter_versions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    version = Column(Integer, default=1)
    name = Column(String, nullable=True)
    content_md = Column(Text)
    pdf_path = Column(String)
    llm_used = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class LLMComparison(Base):
    __tablename__ = "llm_comparisons"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    prompt_type = Column(String)  # resume | cover_letter
    results = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)


class EvaluationReport(Base):
    """Structured A-G evaluation report inspired by career-ops' 7-block system."""
    __tablename__ = "evaluation_reports"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    # Block A: Role Summary
    block_a_role_summary = Column(JSON)   # archetype, domain, seniority, remote, tldr
    # Block B: CV Match
    block_b_cv_match = Column(JSON)       # requirements, gaps, gap_mitigations
    # Block C: Level & Strategy
    block_c_level_strategy = Column(JSON) # detected_level, positioning_plan, downlevel_plan
    # Block D: Comp & Demand
    block_d_comp_demand = Column(JSON)    # salary_data, market_trend, sources
    # Block E: Personalization Plan
    block_e_personalization = Column(JSON) # cv_changes, linkedin_changes
    # Block F: Interview Prep
    block_f_interview_prep = Column(JSON) # star_stories, case_study, red_flag_qa
    # Block G: Posting Legitimacy
    block_g_legitimacy = Column(JSON)     # tier, signals, context_notes
    # Global score (1-5 scale)
    global_score = Column(Float)
    llm_used = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class StoryBankEntry(Base):
    """Persistent STAR+Reflection interview stories that grow across evaluations."""
    __tablename__ = "story_bank"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"))  # Source job, nullable if manually added
    title = Column(String, nullable=False)            # Short title for the story
    jd_requirement = Column(String)                   # JD requirement this story maps to
    situation = Column(Text)
    task = Column(Text)
    action = Column(Text)
    result = Column(Text)
    reflection = Column(Text)                         # What was learned / done differently
    tags = Column(JSON)                               # ["leadership", "scaling", "ml-ops"]
    times_used = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FollowUpLog(Base):
    """Track follow-up actions with cadence intelligence."""
    __tablename__ = "follow_up_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    follow_up_number = Column(Integer, default=1)     # 1st, 2nd, 3rd follow-up
    channel = Column(String)                          # email | linkedin | phone | other
    contact_name = Column(String)
    contact_email = Column(String)
    notes = Column(Text)
    sent_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class ChatInteraction(Base):
    """Relational mirror of ChromaDB for fast stats queries."""
    __tablename__ = "chat_interactions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    chroma_id = Column(String)
    action_type = Column(String, nullable=False)
    job_id = Column(Integer, nullable=True)
    company = Column(String)
    title = Column(String)
    llm_used = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class Settings(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    key = Column(String, nullable=False, index=True)  # unique per (user_id, key), enforced in code
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LLMTaskStatus(Base):
    __tablename__ = "llm_task_statuses"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    task_type = Column(String, nullable=False)
    status = Column(String, default="processing") # processing | completed | failed
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MasterResumeComponent(Base):
    """Multiple files or text blocks that make up the candidate's master resume."""
    __tablename__ = "master_resume_components"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)   # 'file' | 'text'
    content_text = Column(Text)              # Extracted or manual text
    original_filename = Column(String)
    file_path = Column(String)
    is_active = Column(Boolean, default=True)
    order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ResearchReport(Base):
    __tablename__ = "research_reports"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    research_data = Column(JSON)
    llm_used = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class LinkedInOutreachReport(Base):
    __tablename__ = "linkedin_outreach_reports"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    outreach_data = Column(JSON)
    llm_used = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class InterviewPrepReport(Base):
    __tablename__ = "interview_prep_reports"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    prep_data = Column(JSON)
    llm_used = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    # Empty string for SSO-only accounts (no local password). Never NULL so the
    # column constraint stays valid on the pre-existing prod table.
    password_hash = Column(String, nullable=False, default="")
    oauth_provider = Column(String, nullable=True)  # 'google' | 'github' | None for password signup
    name = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# Tables that hold per-user data and gained a user_id column (settings handled separately)
_TENANT_TABLES = [
    "jobs", "application_events", "resume_versions", "cover_letter_versions",
    "llm_comparisons", "evaluation_reports", "story_bank", "follow_up_logs",
    "chat_interactions", "llm_task_statuses", "master_resume_components",
    "research_reports", "linkedin_outreach_reports", "interview_prep_reports",
]


def _column_exists(conn, table: str, col: str) -> bool:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def _table_exists(conn, table: str) -> bool:
    row = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _settings_needs_rebuild(conn) -> bool:
    if not _table_exists(conn, "settings"):
        return False
    if not _column_exists(conn, "settings", "user_id"):
        return True
    # Old schema has a UNIQUE index on `key` alone — must be dropped for per-user keys
    for idx in conn.exec_driver_sql("PRAGMA index_list(settings)").fetchall():
        if idx[2] == 1:  # unique
            cols = [c[2] for c in conn.exec_driver_sql(f"PRAGMA index_info({idx[1]})").fetchall()]
            if cols == ["key"]:
                return True
    return False


def migrate_multitenant():
    """Backfill multi-tenancy on an existing SQLite DB: add user_id columns and
    rebuild the settings table to drop the global UNIQUE(key) constraint.
    Idempotent and safe on fresh databases."""
    with engine.begin() as conn:
        owner = conn.exec_driver_sql("SELECT id FROM users ORDER BY id ASC LIMIT 1").fetchone()
        owner_id = owner[0] if owner else None

        for t in _TENANT_TABLES:
            if not _table_exists(conn, t):
                continue
            if not _column_exists(conn, t, "user_id"):
                conn.exec_driver_sql(f"ALTER TABLE {t} ADD COLUMN user_id INTEGER")
                if owner_id is not None:
                    conn.exec_driver_sql(f"UPDATE {t} SET user_id = {owner_id} WHERE user_id IS NULL")

        if _settings_needs_rebuild(conn):
            had_uid = _column_exists(conn, "settings", "user_id")
            conn.exec_driver_sql("ALTER TABLE settings RENAME TO settings_old")
            conn.exec_driver_sql(
                "CREATE TABLE settings ("
                "id INTEGER PRIMARY KEY, user_id INTEGER, key VARCHAR NOT NULL, "
                "value TEXT, updated_at DATETIME)"
            )
            if had_uid:
                conn.exec_driver_sql(
                    "INSERT INTO settings (id, user_id, key, value, updated_at) "
                    "SELECT id, user_id, key, value, updated_at FROM settings_old"
                )
            else:
                uid_val = str(owner_id) if owner_id is not None else "NULL"
                conn.exec_driver_sql(
                    "INSERT INTO settings (id, user_id, key, value, updated_at) "
                    f"SELECT id, {uid_val}, key, value, updated_at FROM settings_old"
                )
            conn.exec_driver_sql("DROP TABLE settings_old")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_settings_user_id ON settings(user_id)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_settings_key ON settings(key)")


def migrate_user_oauth():
    """Add SSO columns to the users table on an existing DB. Idempotent."""
    with engine.begin() as conn:
        if not _table_exists(conn, "users"):
            return
        for col in ("oauth_provider", "name", "avatar_url"):
            if not _column_exists(conn, "users", col):
                conn.exec_driver_sql(f"ALTER TABLE users ADD COLUMN {col} VARCHAR")


def migrate_ats_score():
    """Add ats_score column to resume_versions on an existing DB. Idempotent."""
    with engine.begin() as conn:
        if not _table_exists(conn, "resume_versions"):
            return
        if not _column_exists(conn, "resume_versions", "ats_score"):
            conn.exec_driver_sql("ALTER TABLE resume_versions ADD COLUMN ats_score JSON")


def init_db():
    Base.metadata.create_all(bind=engine)
    migrate_multitenant()
    migrate_user_oauth()
    migrate_ats_score()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
