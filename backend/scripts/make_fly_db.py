"""Copy of a HireOS database for a hosted instance, keeping one account.

  python scripts/make_fly_db.py swarm.db out/fly.db you@example.com [--output /app/data/output]

Reads the source through SQLite's backup API (safe while HireOS is running), then in the copy:
deletes every other user's rows and anything left pointing at a deleted job, and rewrites file
paths (resume / cover-letter PDFs, DOCX) to the hosted OUTPUT_DIR. Prints the job folders to
upload next to it. The source is never written.
"""
import argparse
import sqlite3
import sys

PATH_COLS = {"resume_versions": ("pdf_path", "docx_path"), "cover_letter_versions": ("pdf_path",),
             "master_resume_components": ("file_path",)}
LOCAL_PREFIXES = ("/data/output/", "output/", "./output/")


def make(src: str, dst: str, email: str, output: str) -> dict:
    s, d = sqlite3.connect(f"file:{src}?mode=ro", uri=True), sqlite3.connect(dst)
    s.backup(d)
    s.close()
    try:
        row = d.execute("select id from users where email = ?", (email,)).fetchone()
        if not row:
            raise SystemExit(f"no user {email} in {src}")
        uid = row[0]
        tables = [t for (t,) in d.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
        cols = {t: {c[1] for c in d.execute(f'pragma table_info("{t}")')} for t in tables}
        d.execute("delete from users where id != ?", (uid,))
        for t in tables:   # rows with no user (global settings) stay
            if "user_id" in cols[t]:
                d.execute(f'delete from "{t}" where user_id is not null and user_id != ?', (uid,))
        for t in tables:
            if "job_id" in cols[t] and t != "jobs":
                d.execute(f'delete from "{t}" where job_id is not null and job_id not in (select id from jobs)')
        for t, names in PATH_COLS.items():
            for c in names:
                if t in cols and c in cols[t]:
                    for p in LOCAL_PREFIXES:
                        d.execute(f'update "{t}" set {c} = ? || substr({c}, ?) where {c} like ?',
                                  (output.rstrip("/") + "/", len(p) + 1, p + "%"))
        d.commit()
        d.execute("vacuum")
        if d.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise SystemExit("integrity check failed")
        counts = {t: d.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}
        jobs = [j for (j,) in d.execute("select id from jobs order by id")]
        return {"user_id": uid, "counts": counts, "job_dirs": jobs}
    finally:
        d.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("email")
    ap.add_argument("--output", default="/app/data/output", help="OUTPUT_DIR on the hosted instance")
    a = ap.parse_args()
    if a.src == a.dst:
        sys.exit("dst must differ from src")
    r = make(a.src, a.dst, a.email, a.output)
    print(f"kept user {r['user_id']}:", ", ".join(f"{t} {n}" for t, n in r["counts"].items() if n))
    print("job folders:", len(r["job_dirs"]))
