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
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    version = Column(Integer, default=1)
    content_md = Column(Text)
    pdf_path = Column(String)
    docx_path = Column(String)
    llm_used = Column(String)
    critic_notes = Column(JSON)   # structured critique from CriticAgent
    created_at = Column(DateTime, default=datetime.utcnow)


class CoverLetterVersion(Base):
    __tablename__ = "cover_letter_versions"
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    version = Column(Integer, default=1)
    content_md = Column(Text)
    pdf_path = Column(String)
    llm_used = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class LLMComparison(Base):
    __tablename__ = "llm_comparisons"
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    prompt_type = Column(String)  # resume | cover_letter
    results = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)


class EvaluationReport(Base):
    """Structured A-G evaluation report inspired by career-ops' 7-block system."""
    __tablename__ = "evaluation_reports"
    id = Column(Integer, primary_key=True, index=True)
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
    key = Column(String, unique=True, nullable=False)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LLMTaskStatus(Base):
    __tablename__ = "llm_task_statuses"
    id = Column(Integer, primary_key=True, index=True)
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
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    research_data = Column(JSON)
    llm_used = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class LinkedInOutreachReport(Base):
    __tablename__ = "linkedin_outreach_reports"
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    outreach_data = Column(JSON)
    llm_used = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class InterviewPrepReport(Base):
    __tablename__ = "interview_prep_reports"
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    prep_data = Column(JSON)
    llm_used = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
