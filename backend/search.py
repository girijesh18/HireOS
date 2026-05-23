"""
Search endpoint — full-text job search + keyword suggestions.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from typing import List, Optional

from database import get_db, Job

router = APIRouter()

# ── Pre-seeded keyword taxonomy ────────────────────────────────────────────────

KEYWORD_TAXONOMY = {
    "roles": [
        "Principal AI Engineer", "Staff AI Engineer", "Senior AI Engineer",
        "ML Engineer", "ML Architect", "Senior ML Engineer", "Staff ML Engineer",
        "MLOps Engineer", "Senior MLOps Engineer", "Staff MLOps Engineer",
        "Data Scientist", "Senior Data Scientist", "Principal Data Scientist", "Staff Data Scientist",
        "Research Engineer", "Applied Research Scientist", "Research Scientist",
        "AI Platform Engineer", "LLM Engineer", "Generative AI Engineer",
        "NLP Engineer", "Computer Vision Engineer", "AI Infrastructure Engineer",
        "Head of AI", "Director of Machine Learning", "VP of AI",
        "Lead AI Engineer", "Engineering Manager AI/ML", "Technical Lead AI",
        "Software Engineer", "Senior Software Engineer", "Staff Engineer",
        "Backend Engineer", "Full-Stack Engineer", "Platform Engineer",
    ],
    "tech": [
        "Python", "PyTorch", "TensorFlow", "JAX", "scikit-learn",
        "LLMs", "GPT", "Claude", "Gemini", "LangChain", "LlamaIndex",
        "RAG", "Vector Database", "Embeddings", "Fine-tuning", "RLHF",
        "MLOps", "Kubeflow", "MLflow", "Ray", "Airflow",
        "AWS SageMaker", "Google Vertex AI", "Azure ML",
        "Kubernetes", "Docker", "Terraform", "CI/CD",
        "SQL", "Spark", "dbt", "Snowflake", "BigQuery",
        "FastAPI", "Django", "Redis", "Kafka", "gRPC",
        "Transformer", "Diffusion Models", "RLHF", "LoRA", "Quantization",
    ],
    "seniority": [
        "Principal", "Staff", "Senior", "Lead", "Manager", "Director", "VP", "Head of",
    ],
    "companies": [
        "Google", "DeepMind", "OpenAI", "Anthropic", "Meta AI", "Apple", "Amazon",
        "Microsoft", "NVIDIA", "Stripe", "Airbnb", "Uber", "Lyft",
        "Databricks", "Hugging Face", "Cohere", "Mistral", "Stability AI",
        "Scale AI", "Palantir", "Waymo", "Tesla", "SpaceX",
    ],
    "work_style": [
        "Remote", "Hybrid", "Onsite", "Full-time", "Contract",
    ],
    "salary": [
        "$150K+", "$180K+", "$200K+", "$220K+", "$250K+", "$300K+",
    ],
}

ALL_KEYWORDS = {
    kw: category
    for category, keywords in KEYWORD_TAXONOMY.items()
    for kw in keywords
}


def _salary_filter(q, salary_str: str):
    """Parse '$200K+' → salary_min >= 200000 filter."""
    try:
        num = int(salary_str.replace("$", "").replace("K+", "").replace(",", "")) * 1000
        return q.filter(Job.salary_min >= num)
    except Exception:
        return q


@router.get("/api/search/suggest")
def suggest_keywords(q: str = Query(..., min_length=1), limit: int = 10, db: Session = Depends(get_db)):
    """
    Return keyword suggestions for a partial query string.
    Merges static taxonomy + dynamic terms from actual job DB.
    """
    q_lower = q.lower()
    suggestions = []

    # 1. Match static taxonomy
    for kw, category in ALL_KEYWORDS.items():
        if q_lower in kw.lower():
            suggestions.append({"keyword": kw, "category": category, "source": "taxonomy"})
        if len(suggestions) >= limit * 2:  # collect extras then trim
            break

    # 2. Append dynamic matches from actual job titles + companies in DB
    db_titles = db.query(Job.title).filter(
        func.lower(Job.title).contains(q_lower)
    ).distinct().limit(5).all()
    for (title,) in db_titles:
        if not any(s["keyword"].lower() == title.lower() for s in suggestions):
            suggestions.append({"keyword": title, "category": "roles", "source": "your_jobs"})

    db_companies = db.query(Job.company).filter(
        func.lower(Job.company).contains(q_lower)
    ).distinct().limit(5).all()
    for (company,) in db_companies:
        if not any(s["keyword"].lower() == company.lower() for s in suggestions):
            suggestions.append({"keyword": company, "category": "companies", "source": "your_jobs"})

    # Sort: exact prefix first, then contains, trim to limit
    def sort_key(s):
        kw_l = s["keyword"].lower()
        if kw_l.startswith(q_lower):
            return 0
        return 1

    suggestions.sort(key=sort_key)
    return suggestions[:limit]


@router.get("/api/search")
def search_jobs(
    q: Optional[str] = None,
    status: Optional[str] = None,
    platform: Optional[str] = None,
    remote: Optional[bool] = None,
    starred: Optional[bool] = None,
    priority: Optional[str] = None,
    salary_min: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """
    Full-text search across title, company, JD, location.
    Handles special keyword types (salary, remote, seniority).
    """
    query = db.query(Job)

    if q:
        q_clean = q.strip()

        # Handle salary shortcuts like "$200K+"
        if "$" in q_clean and "K" in q_clean:
            query = _salary_filter(query, q_clean)
        # Handle remote keyword
        elif q_clean.lower() in ("remote", "🌐 remote"):
            query = query.filter(Job.remote == True)
        else:
            # Full-text: search title, company, location, job_description
            search_term = f"%{q_clean.lower()}%"
            query = query.filter(
                or_(
                    func.lower(Job.title).like(search_term),
                    func.lower(Job.company).like(search_term),
                    func.lower(Job.location).like(search_term),
                    func.lower(Job.job_description).like(search_term),
                )
            )

    # Apply additional filters
    if status:
        query = query.filter(Job.status == status)
    if platform:
        query = query.filter(Job.platform == platform)
    if remote is not None:
        query = query.filter(Job.remote == remote)
    if starred is not None:
        query = query.filter(Job.starred == starred)
    if priority:
        query = query.filter(Job.priority == priority)
    if salary_min:
        query = query.filter(Job.salary_min >= salary_min)

    return query.order_by(Job.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/api/search/trending")
def trending_keywords(db: Session = Depends(get_db)):
    """Return most-searched keywords from your job pipeline (titles + tech)."""
    top_titles = db.query(Job.title, func.count(Job.id).label("n")).group_by(Job.title).order_by(func.count(Job.id).desc()).limit(5).all()

    trending = [{"keyword": t, "category": "roles", "count": n} for t, n in top_titles]

    # Add fixed hot keywords if DB is sparse
    hot = ["Principal AI Engineer", "LLMs", "MLOps", "RAG", "Remote", "$200K+"]
    for kw in hot:
        if not any(t["keyword"] == kw for t in trending):
            trending.append({"keyword": kw, "category": ALL_KEYWORDS.get(kw, "tech"), "count": 0})

    return trending[:10]
