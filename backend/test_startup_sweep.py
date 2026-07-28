"""A task left at "processing" by a restart must be failed on startup.

Background generation runs in-process, so a deploy kills anything in flight
and the row never leaves "processing" -- the UI then polls it forever.
Run with a scratch DB: DATABASE_URL=sqlite:///./test_sweep.db python test_startup_sweep.py
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(tempfile.mkdtemp(), "sweep.db"))

from database import SessionLocal, LLMTaskStatus, init_db  # noqa: E402
import main  # noqa: E402


def test_startup_fails_orphaned_tasks():
    init_db()
    db = SessionLocal()
    db.query(LLMTaskStatus).delete()
    db.add_all([
        LLMTaskStatus(job_id=1, task_type="resume", status="processing", user_id=1),
        LLMTaskStatus(job_id=2, task_type="cover_letter", status="completed", user_id=1),
        LLMTaskStatus(job_id=3, task_type="ats", status="failed", error_message="nope", user_id=1),
    ])
    db.commit()
    db.close()

    main.startup()

    db = SessionLocal()
    rows = {t.job_id: t for t in db.query(LLMTaskStatus).all()}
    assert rows[1].status == "failed", "orphaned task should be failed"
    assert "restart" in rows[1].error_message
    assert rows[2].status == "completed", "finished tasks must not be touched"
    assert rows[3].error_message == "nope", "existing failures keep their message"
    db.close()


if __name__ == "__main__":
    test_startup_fails_orphaned_tasks()
    print("ok")
