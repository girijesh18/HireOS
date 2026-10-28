"""scripts/make_fly_db.py on a small hand-made database: one account kept, paths rewritten, source untouched.

Run: cd backend && python test_make_fly_db.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
import make_fly_db  # noqa: E402


def test_keeps_one_account():
    tmp = Path(tempfile.mkdtemp())
    src, dst = tmp / "src.db", tmp / "dst.db"
    db = sqlite3.connect(src)
    db.executescript("""
      create table users (id integer primary key, email text);
      create table jobs (id integer primary key, user_id int, url text);
      create table resume_versions (id integer primary key, job_id int, user_id int, pdf_path text, docx_path text);
      create table application_events (id integer primary key, job_id int, user_id int);
      create table settings (id integer primary key, user_id int, key text);
      insert into users values (1, 'me@x.com'), (2, 'other@x.com');
      insert into jobs values (10, 1, 'a'), (11, 2, 'b');
      insert into resume_versions values (1, 10, 1, '/data/output/10/r.pdf', 'output/10/r.docx'),
                                         (2, 11, 2, '/data/output/11/r.pdf', '');
      insert into application_events values (1, 10, 1), (2, 11, 2), (3, 11, null);
      insert into settings values (1, 1, 'mine'), (2, 2, 'theirs'), (3, null, 'global');
    """)
    db.commit()
    db.close()
    r = make_fly_db.make(str(src), str(dst), "me@x.com", "/app/data/output")
    assert r["user_id"] == 1 and r["job_dirs"] == [10]
    d = sqlite3.connect(dst)
    assert d.execute("select email from users").fetchall() == [("me@x.com",)]
    assert d.execute("select pdf_path, docx_path from resume_versions").fetchall() == \
        [("/app/data/output/10/r.pdf", "/app/data/output/10/r.docx")]
    assert d.execute("select id from application_events").fetchall() == [(1,)], "job-linked orphan removed too"
    assert sorted(k for (k,) in d.execute("select key from settings")) == ["global", "mine"]
    s = sqlite3.connect(src)
    assert s.execute("select count(*) from users").fetchone()[0] == 2, "source untouched"
    try:
        make_fly_db.make(str(src), str(tmp / "x.db"), "nobody@x.com", "/o")
        raise AssertionError("unknown email must stop")
    except SystemExit:
        pass


if __name__ == "__main__":
    test_keeps_one_account()
    print("ok test_keeps_one_account")
