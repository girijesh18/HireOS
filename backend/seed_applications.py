"""
One-time seed script — imports real applications from Girijesh_Resume_Gemini/ into HireOS.
Run from backend/: python seed_applications.py
"""
import sys, os, json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(Path(__file__).parent)

from database import SessionLocal, init_db, Job, ResumeVersion, ApplicationEvent

RESUME_ROOT = Path("/home/gs/Documents/Girijesh's Work/Antigravity_Works/Girijesh_Resume_Gemini")

APPLICATIONS = [
    {
        "company": "Citi",
        "title": "GenAI Engineer",
        "status": "applied",
        "platform": "direct",
        "location": "Remote / Hybrid",
        "remote": True,
        "resume_md_path": RESUME_ROOT / "Citi_Applied_GenAI" / "Citi_GenAI_Engineer_2026.md",
        "notes": "Applied via Citi careers portal.",
    },
    {
        "company": "FutureFit AI",
        "title": "Data Scientist",
        "status": "interview_1",
        "platform": "direct",
        "location": "Remote",
        "remote": True,
        "resume_md_path": RESUME_ROOT / "FutureFitAI_Data_Scientist" / "Girijesh_Singh_Data_Scientist.md",
        "notes": "In interview stage — interview prep materials in FutureFitAI_INterview/.",
    },
    {
        "company": "Infosys",
        "title": "GenAI Lead",
        "status": "applied",
        "platform": "direct",
        "location": "Remote / Hybrid",
        "remote": True,
        "resume_md_path": RESUME_ROOT / "Infosys_GenAI_Lead" / "Infosys_GenAI_Lead_2026.md",
        "notes": "",
    },
    {
        "company": "Jetson",
        "title": "Applied AI Engineer",
        "status": "applied",
        "platform": "direct",
        "location": "Remote",
        "remote": True,
        "resume_md_path": RESUME_ROOT / "Jetson_Applied_AI_Engineer" / "Jetson_Applied_AI_Engineer_2026.md",
        "notes": "",
    },
    {
        "company": "McKesson",
        "title": "Sr. Data Scientist",
        "status": "applied",
        "platform": "direct",
        "location": "Remote / Hybrid",
        "remote": True,
        "resume_md_path": RESUME_ROOT / "McKesson_Sr_Data_Scientist" / "McKesson_Sr_Data_Scientist_2026.md",
        "notes": "",
    },
    {
        "company": "Sanofi",
        "title": "AI/ML Lead",
        "status": "applied",
        "platform": "direct",
        "location": "Remote / Hybrid",
        "remote": False,
        "resume_md_path": RESUME_ROOT / "Sanofi_AIML_Lead" / "Sanofi_AIML_Lead_Resume.md",
        "notes": "",
    },
    {
        "company": "Thomson Reuters",
        "title": "Principal AI Engineer",
        "status": "applied",
        "platform": "direct",
        "location": "Remote / Hybrid",
        "remote": True,
        "resume_md_path": RESUME_ROOT / "Thomson Reuters" / "Principal_AI_Engineer_2026_TR.md",
        "notes": "",
    },
    {
        "company": "Workday",
        "title": "Senior ML Engineer",
        "status": "applied",
        "platform": "direct",
        "location": "Remote",
        "remote": True,
        "resume_md_path": RESUME_ROOT / "Workday_Senior_ML_Engineer_2026" / "Workday_Senior_ML_Engineer_2026.md",
        "notes": "",
    },
    {
        "company": "Xsolla",
        "title": "Data Scientist",
        "status": "applied",
        "platform": "direct",
        "location": "Remote",
        "remote": True,
        "resume_md_path": RESUME_ROOT / "Xsolla_Data_Scientist" / "Xsolla_Data_Scientist_Resume.md",
        "notes": "",
    },
]

def seed():
    init_db()
    db = SessionLocal()
    seeded = 0
    skipped = 0

    for app in APPLICATIONS:
        existing = db.query(Job).filter_by(company=app["company"], title=app["title"]).first()
        if existing:
            print(f"  SKIP  {app['company']} — {app['title']} (already exists, id={existing.id})")
            skipped += 1
            continue

        resume_md = ""
        rpath = app.get("resume_md_path")
        if rpath and Path(rpath).exists():
            resume_md = Path(rpath).read_text(encoding="utf-8")

        job = Job(
            company=app["company"],
            title=app["title"],
            status=app["status"],
            platform=app.get("platform", "direct"),
            location=app.get("location", ""),
            remote=app.get("remote", False),
            notes=app.get("notes", ""),
            applied_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(job)
        db.flush()  # get job.id

        if resume_md:
            rv = ResumeVersion(
                job_id=job.id,
                version=1,
                content_md=resume_md,
                llm_used="gemini",
            )
            db.add(rv)

        db.add(ApplicationEvent(
            job_id=job.id,
            event_type="application_submitted",
            title=f"Application submitted — {app['title']} at {app['company']}",
            description="Imported from Girijesh_Resume_Gemini/ seed script.",
            source="user",
        ))

        print(f"  ADD   {app['company']} — {app['title']} [{app['status']}] (resume: {'yes' if resume_md else 'no'})")
        seeded += 1

    db.commit()
    db.close()
    print(f"\nDone. {seeded} added, {skipped} skipped.")

if __name__ == "__main__":
    seed()
