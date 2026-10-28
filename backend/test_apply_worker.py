"""apply_worker.run_job outcomes, with a real headless browser and a fake HireOS. No network.
The rule under test: once Submit may have been clicked, a crash is needs_human, never failed
("failed" re-queues the job and a second run would apply twice).

Run: cd backend && python test_apply_worker.py
"""
import os
import tempfile
from pathlib import Path

import apply_worker as aw
import ats_form
from apply_worker import HandOff

JOB = {"id": 7, "company": "Acme", "title": "DS", "url": "https://boards.greenhouse.io/acme/jobs/1"}


class Resp:
    def __init__(self, data=None, content=b"%PDF-1.4", headers=None):
        self.data, self.content, self.headers = data, content, headers or {}

    def json(self):
        return self.data


class FakeHireOS:
    """Records what the worker reports."""
    posts = []

    def __init__(self):
        self.url, self.h = "http://fake", {}

    def get(self, path, **kw):
        return Resp(JOB) if path == f"/jobs/{JOB['id']}" else Resp()

    def post(self, path, body=None):
        FakeHireOS.posts.append((path, body))
        return {}


def run(fill=None, submit=None, decision="submit"):
    FakeHireOS.posts = []
    real = (aw.HireOS, aw.wait_for_submit, ats_form.fill, ats_form.submit, aw.RUNS, aw.PROFILE, aw.screen_up)
    tmp = Path(tempfile.mkdtemp())
    aw.HireOS, aw.RUNS, aw.PROFILE = FakeHireOS, tmp / "runs", tmp / "profile"
    aw.screen_up = lambda: False                 # never the real screen
    aw.wait_for_submit = lambda api, run: decision
    ats_form.fill = fill or (lambda run, resume: None)
    ats_form.submit = submit or (lambda run: True)
    try:
        aw.run_job(JOB["id"], headless=True, live=True, claimed=True)
    finally:
        aw.HireOS, aw.wait_for_submit, ats_form.fill, ats_form.submit, aw.RUNS, aw.PROFILE, aw.screen_up = real
    return [b for p, b in FakeHireOS.posts if p.endswith("/result")]


def test_crash_after_submit_is_never_retried():
    def clicked_then_crash(run):
        raise RuntimeError("Target closed")          # e.g. reading the confirmation page
    [res] = run(submit=clicked_then_crash)
    assert res["outcome"] == "needs_human" and "after Submit was clicked" in res["notes"], res


def test_crash_before_submit_is_retried():
    def bad_fill(run, resume):
        raise RuntimeError("selector timeout")
    [res] = run(fill=bad_fill)
    assert res["outcome"] == "failed", res


def test_handoff_survives_a_dead_page():
    def wall(run, resume):
        run.page.close()                              # the bot wall took the page with it
        raise HandOff("captcha", "solve it yourself")
    [res] = run(fill=wall)
    assert res["outcome"] == "needs_human" and "captcha" in res["notes"], res


def test_watch_level_crash_after_submit():
    aw.CLICKED.add(99)
    try:
        assert aw.crash_outcome(99, "boom")["outcome"] == "needs_human"
        assert aw.crash_outcome(98, "boom")["outcome"] == "failed"
    finally:
        aw.CLICKED.discard(99)


def test_ask_and_wait_and_screen_link():
    """The Submit card carries the screen link; a skip ends the wait as 'cancel'."""
    class Api:
        posts, states = [], ["waiting_human", "applying"]

        def post(self, path, body):
            Api.posts.append(body)

        def get(self, path):
            return Resp({"status": Api.states.pop(0), "answer": "submit"})
    run = type("R", (), {"job": JOB, "record": {"review_checked": 3}, "screen": "https://screen"})()
    real, aw.time.sleep = aw.time.sleep, lambda s: None
    try:
        assert aw.wait_for_submit(Api(), run) == "submit" and Api.posts[0]["url"] == "https://screen"
        Api.states = ["skipped"]
        assert aw.ask_and_wait(Api(), run, {"reason": "x"}, 15) == "cancel"
    finally:
        aw.time.sleep = real
    import telegram_bot as tg
    job = type("J", (), {"id": 7, "company": "Acme", "title": "DS"})()
    _, markup = tg.ask_message(job, "submit", "ok", "https://screen")
    assert markup["inline_keyboard"][1] == [{"text": "Open screen", "url": "https://screen"}]
    assert len(tg.ask_message(job, "submit", "ok")[1]["inline_keyboard"]) == 1


def test_config_env_over_file_and_screen_probe():
    import socket
    tmp = Path(tempfile.mkdtemp())
    f = tmp / "hireos.json"
    f.write_text('{"url": "http://file", "token": "ft", "screen_url": "https://file-screen"}')
    real = (aw.CREDS, dict(os.environ), aw.SCREEN_DISPLAY)
    try:
        aw.CREDS = f
        assert aw.creds() == {"url": "http://file", "token": "ft", "screen_url": "https://file-screen"}   # PC
        os.environ.update(HIREOS_URL="https://srv", HIREOS_TOKEN="et", WORKER_SCREEN_URL="https://ts")
        assert aw.creds() == {"url": "https://srv", "token": "et", "screen_url": "https://ts"}           # server
        aw.CREDS = tmp / "missing.json"
        assert aw.HireOS().url == "https://srv"                                    # no file needed
        for k in ("HIREOS_URL", "HIREOS_TOKEN"):
            os.environ.pop(k)
        try:
            aw.HireOS()
            raise AssertionError("no URL/token must stop the worker")
        except SystemExit as e:
            assert "HIREOS_TOKEN" in str(e)
        aw.SCREEN_DISPLAY = ":4242"                                                 # no such display
        assert not aw.screen_up()
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        if port > 6000:
            aw.SCREEN_DISPLAY = f"127.0.0.1:{port - 6000}"                         # TCP display answers
            assert aw.screen_up()
        srv.close()
    finally:
        aw.CREDS, aw.SCREEN_DISPLAY = real[0], real[2]
        os.environ.clear()
        os.environ.update(real[1])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
