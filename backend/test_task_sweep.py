"""Abandoned "processing" tasks must not poll forever.

Background generation runs in-process, so a task dies two ways: a restart kills
the worker, or the worker wedges on a provider call that never returns. Either
way the row sticks at "processing" and the UI spins with no way out.

Run with a scratch DB:
    DATABASE_URL=sqlite:///./test_sweep.db python test_task_sweep.py
"""
import os
import tempfile
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(tempfile.mkdtemp(), "sweep.db"))

from database import SessionLocal, LLMTaskStatus, init_db  # noqa: E402
import main  # noqa: E402


def _seed(rows):
    init_db()
    db = SessionLocal()
    db.query(LLMTaskStatus).delete()
    db.add_all(rows)
    db.commit()
    db.close()


def test_startup_fails_orphaned_tasks():
    """A restart abandons everything in flight, regardless of age."""
    _seed([
        LLMTaskStatus(job_id=1, task_type="resume", status="processing", user_id=1),
        LLMTaskStatus(job_id=2, task_type="cover_letter", status="completed", user_id=1),
        LLMTaskStatus(job_id=3, task_type="ats", status="failed", error_message="nope", user_id=1),
    ])

    main.startup()

    db = SessionLocal()
    rows = {t.job_id: t for t in db.query(LLMTaskStatus).all()}
    assert rows[1].status == "failed", "orphaned task should be failed"
    assert "restart" in rows[1].error_message
    assert rows[2].status == "completed", "finished tasks must not be touched"
    assert rows[3].error_message == "nope", "existing failures keep their message"
    db.close()


def test_stale_tasks_expire_but_running_ones_survive():
    """No restart: only rows past the cutoff get swept, and only for this job."""
    old = datetime.utcnow() - main.STALE_TASK_TIMEOUT - timedelta(minutes=1)
    fresh = datetime.utcnow() - timedelta(minutes=1)
    _seed([
        LLMTaskStatus(job_id=1, task_type="resume", status="processing", user_id=1, updated_at=old),
        LLMTaskStatus(job_id=1, task_type="ats", status="processing", user_id=1, updated_at=fresh),
        LLMTaskStatus(job_id=2, task_type="resume", status="processing", user_id=1, updated_at=old),
    ])

    db = SessionLocal()
    swept = main._fail_abandoned_tasks(db, message="timed out", job_id=1,
                                       older_than=main.STALE_TASK_TIMEOUT)
    assert swept == 1, f"expected only the stale row of job 1, swept {swept}"

    rows = {(t.job_id, t.task_type): t for t in db.query(LLMTaskStatus).all()}
    assert rows[(1, "resume")].status == "failed"
    assert rows[(1, "ats")].status == "processing", "a task inside the cutoff is still running"
    assert rows[(2, "resume")].status == "processing", "other jobs are none of our business"
    db.close()


if __name__ == "__main__":
    test_startup_fails_orphaned_tasks()
    test_stale_tasks_expire_but_running_ones_survive()
    print("ok")
