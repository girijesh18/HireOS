from pydantic import BaseModel, Field
from typing import Optional, List, Any
from datetime import datetime


# ── Jobs ──────────────────────────────────────────────────────────────────────

class JobCreate(BaseModel):
    company: str
    title: str
    url: Optional[str] = None
    job_description: Optional[str] = None
    location: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    remote: bool = False
    listed_at: Optional[datetime] = None
    platform: Optional[str] = None
    priority: Optional[str] = "medium"
    notes: Optional[str] = None


class JobUpdate(BaseModel):
    status: Optional[str] = None
    match_score: Optional[float] = None
    strengths: Optional[List[str]] = None
    gaps: Optional[List[str]] = None
    action_items: Optional[List[str]] = None
    meta: Optional[Any] = None
    priority: Optional[str] = None
    starred: Optional[bool] = None
    recruiter_name: Optional[str] = None
    recruiter_email: Optional[str] = None
    recruiter_linkedin: Optional[str] = None
    applied_at: Optional[datetime] = None
    last_contact_at: Optional[datetime] = None
    follow_up_due: Optional[datetime] = None
    offer_amount: Optional[int] = None
    rejection_reason: Optional[str] = None
    notes: Optional[str] = None


class JobOut(BaseModel):
    id: int
    company: str
    title: str
    url: Optional[str]
    job_description: Optional[str]
    location: Optional[str]
    salary_min: Optional[int]
    salary_max: Optional[int]
    remote: bool
    listed_at: Optional[datetime]
    platform: Optional[str]
    status: str
    match_score: Optional[float]
    strengths: Optional[List[str]]
    gaps: Optional[List[str]]
    action_items: Optional[List[str]]
    archetype: Optional[str]
    posting_legitimacy: Optional[str]
    priority: str
    starred: bool
    recruiter_name: Optional[str]
    recruiter_email: Optional[str]
    recruiter_linkedin: Optional[str]
    applied_at: Optional[datetime]
    last_contact_at: Optional[datetime]
    follow_up_due: Optional[datetime]
    offer_amount: Optional[int]
    rejection_reason: Optional[str]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ── Application Events (Timeline) ─────────────────────────────────────────────

class EventCreate(BaseModel):
    event_type: str
    title: str
    description: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    source: str = "user"
    scheduled_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class EventOut(BaseModel):
    id: int
    job_id: int
    event_type: str
    title: str
    description: Optional[str]
    old_value: Optional[str]
    new_value: Optional[str]
    source: str
    scheduled_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


# ── Resume / Cover Letter Versions ────────────────────────────────────────────

class ResumeVersionOut(BaseModel):
    id: int
    job_id: int
    version: int
    name: Optional[str] = None
    content_md: Optional[str]
    pdf_path: Optional[str]
    docx_path: Optional[str]
    llm_used: Optional[str]
    critic_notes: Optional[dict]
    ats_score: Optional[dict] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CoverLetterVersionOut(BaseModel):
    id: int
    job_id: int
    version: int
    name: Optional[str] = None
    content_md: Optional[str]
    pdf_path: Optional[str]
    llm_used: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    message: str
    llm: Optional[str] = "gemini"
    context: Optional[dict] = None


class ChatResponse(BaseModel):
    reply: str
    action_taken: Optional[str] = None
    data: Optional[Any] = None


# ── Settings ──────────────────────────────────────────────────────────────────

class SettingItem(BaseModel):
    key: str
    value: str


class SettingsUpdate(BaseModel):
    settings: List[SettingItem]


# ── LLM Comparison ────────────────────────────────────────────────────────────

class CompareRequest(BaseModel):
    job_id: int
    prompt_type: str = "resume"
    llms: List[str] = ["gemini", "groq"]


class CompareResult(BaseModel):
    llm: str
    output: str
    latency_ms: int


class CompareResponse(BaseModel):
    job_id: int
    prompt_type: str
    results: List[CompareResult]


# ── Stats (Dashboard) ─────────────────────────────────────────────────────────

class DashboardStats(BaseModel):
    total: int
    by_status: dict
    applied_this_week: int
    pending_review: int
    interviews_scheduled: int
    offers: int
    follow_ups_due: int


# ── Evaluation Report (A-G Blocks) ────────────────────────────────────────────

class EvaluationReportOut(BaseModel):
    id: int
    job_id: int
    block_a_role_summary: Optional[dict]
    block_b_cv_match: Optional[dict]
    block_c_level_strategy: Optional[dict]
    block_d_comp_demand: Optional[dict]
    block_e_personalization: Optional[dict]
    block_f_interview_prep: Optional[dict]
    block_g_legitimacy: Optional[dict]
    global_score: Optional[float]
    llm_used: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ── Story Bank ────────────────────────────────────────────────────────────────

class StoryBankEntryCreate(BaseModel):
    title: str
    jd_requirement: Optional[str] = None
    situation: Optional[str] = None
    task: Optional[str] = None
    action: Optional[str] = None
    result: Optional[str] = None
    reflection: Optional[str] = None
    tags: Optional[List[str]] = None


class StoryBankEntryOut(BaseModel):
    id: int
    job_id: Optional[int]
    title: str
    jd_requirement: Optional[str]
    situation: Optional[str]
    task: Optional[str]
    action: Optional[str]
    result: Optional[str]
    reflection: Optional[str]
    tags: Optional[List[str]]
    times_used: int
    created_at: datetime

    class Config:
        from_attributes = True


# ── Follow-Up Log ─────────────────────────────────────────────────────────────

class FollowUpLogCreate(BaseModel):
    channel: str = "email"
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    notes: Optional[str] = None


class FollowUpLogOut(BaseModel):
    id: int
    job_id: int
    follow_up_number: int
    channel: Optional[str]
    contact_name: Optional[str]
    contact_email: Optional[str]
    notes: Optional[str]
    sent_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


# ── Follow-Up Cadence ─────────────────────────────────────────────────────────

class FollowUpCadenceEntry(BaseModel):
    job_id: int
    company: str
    title: str
    status: str
    days_since_applied: Optional[int]
    followup_count: int
    urgency: str
    next_followup_date: Optional[datetime]


class FollowUpCadenceOut(BaseModel):
    total_tracked: int
    actionable: int
    overdue: int
    urgent: int
    cold: int
    entries: List[FollowUpCadenceEntry]


# ── Pattern Analytics ─────────────────────────────────────────────────────────

class PatternAnalyticsOut(BaseModel):
    metadata: dict
    funnel: dict
    score_comparison: dict
    archetype_breakdown: List[dict]
    platform_breakdown: List[dict]
    common_gaps: List[dict]
    recommendations: List[dict]


# ── Master Resume Components ──────────────────────────────────────────────────

class ResumeComponentCreate(BaseModel):
    name: str
    type: str  # 'text' (files are handled via multipart/form-data)
    content_text: Optional[str] = None
    is_active: bool = True
    order: int = 0


class ResumeComponentOut(BaseModel):
    id: int
    name: str
    type: str
    content_text: Optional[str]
    original_filename: Optional[str]
    is_active: bool
    order: int
    created_at: datetime

    class Config:
        from_attributes = True


class ResumeComponentUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    order: Optional[int] = None
    content_text: Optional[str] = None


# ── Chat History & Insights ────────────────────────────────────────────────────

class ChatInteractionOut(BaseModel):
    id: int
    chroma_id: Optional[str]
    action_type: str
    job_id: Optional[int]
    company: Optional[str]
    title: Optional[str]
    llm_used: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class InsightsStatsOut(BaseModel):
    total_interactions: int
    by_action: dict
    by_day: List[dict]
    top_companies: List[str]
    most_used_llm: Optional[str]
    active_days: int


class InsightsNarrativeOut(BaseModel):
    summary: str
    what_youre_doing: str
    momentum_score: int
    momentum_rationale: str
    gaps: str
    top_3_recommendations: List[str]
    warning: Optional[str]


class InsightsOut(BaseModel):
    stats: InsightsStatsOut
    narrative: InsightsNarrativeOut
    generated_at: datetime

