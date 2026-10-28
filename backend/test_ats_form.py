"""Greenhouse / Lever / Ashby form filling (ats_form.py) on static pages: labels, required marks,
CAPTCHA detection, the opt-in and voluntary-question rules, and one full fill + check. No network.

Run: cd backend && python test_ats_form.py
"""
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

import apply_worker
import ats_form as a
from apply_worker import HandOff

# The three patterns seen on live forms (2026-10-27): Greenhouse label[for] + React Select + legend'd
# checkbox group + file inputs labelled "Attach"; Ashby fieldsets with no legend whose title is a
# label with a _required_ class; Lever's .application-question with the ✱ and helper text in the label.
PAGE = """<html><body><form>
<label for="first_name">First Name*</label><input id="first_name" type="text" required>
<label for="preferred_name">Preferred First Name</label><input id="preferred_name" type="text">
<label for="email">Email*</label><input id="email" type="text" required>
<label for="phone">Phone</label><input id="phone" type="tel">
<label for="country">Country*</label><input id="country" class="select__input" type="text">
<label for="resume">Attach</label><input id="resume" type="file">
<label for="cover_letter">Attach</label><input id="cover_letter" type="file">
<fieldset><legend>How did you hear about us? *</legend>
  <label><input type="checkbox" name="src[]" value="a">Job posting on LinkedIn, Indeed, or other job board</label>
  <label><input type="checkbox" name="src[]" value="b">LinkedIn post or content (not a job posting)</label>
  <label><input type="checkbox" name="src[]" value="c">Acme's website</label>
</fieldset>
<div class="entry"><label class="_heading_x _required_f7cvd_91 question-title" for="q1">Can you work on site 3 days a week?</label>
 <fieldset class="radio-group">
  <div><input type="radio" id="r1" name="onsite"><label for="r1">Yes</label></div>
  <div><input type="radio" id="r2" name="onsite"><label for="r2">No</label></div>
 </fieldset></div>
<div class="entry"><label class="question-title" for="q2">Gender</label>
 <fieldset><div><input type="radio" id="g1" name="gender"><label for="g1">Man</label></div>
 <div><input type="radio" id="g2" name="gender"><label for="g2">Prefer not to say</label></div></fieldset></div>
<ul><li class="application-question"><div class="application-label">Current location <span>✱</span><span>No location found. Try again</span></div>
 <div class="application-field"><input name="location" type="text" required></div></li></ul>
<label for="why">Why us?*</label><textarea id="why" required></textarea>
<label for="years">Years of Python</label><select id="years"><option>Select...</option><option>0-2</option><option>3-5</option><option>6+</option></select>
<label><input type="checkbox" id="terms" required> I agree to the privacy policy and terms</label>
<label><input type="checkbox" id="future"> You may contact me about future job opportunities</label>
<label for="hp">Leave this field blank</label><input id="hp" type="text">
</form>
<iframe id="badge" src="https://www.recaptcha.net/recaptcha/enterprise/anchor?ar=1&size=invisible" style="width:256px;height:60px;border:0"></iframe>
<iframe id="parked" src="https://www.recaptcha.net/recaptcha/enterprise/bframe?hl=en" style="position:absolute;top:-10000px;width:400px;height:580px"></iframe>
</body></html>"""


class FakeApi:
    """HireOS for cover_letter(): no letter yet, one appears after the generate call."""
    def __init__(self, out: Path):
        self.out, self.calls, self.made = out, [], False

    def get(self, path):
        self.calls.append(("GET", path))
        body = [{"version": 1, "pdf_path": "/data/output/1/cover_letter_v1.pdf"}] if self.made else []
        return type("R", (), {"json": lambda _: body, "content": b"%PDF-1.4 letter"})()

    def post(self, path, body):
        self.calls.append(("POST", path))
        self.made = True


class FakeRun:
    """What ats_form needs from apply_worker.Run."""
    def __init__(self, page, out: Path, answers: dict, job_notes: str = ""):
        self.page, self.out, self.platform = page, out, "greenhouse"
        self.job = {"id": 1, "url": "https://boards.greenhouse.io/acme/jobs/1", "notes": job_notes}
        self.record = {"pages": [], "filled": [], "unanswered": [], "errors": []}
        self._answers, self.asked = answers, []
        self.api, self.profile, self.screen = FakeApi(out), {}, ""

    def answers(self, questions):
        self.asked += questions
        by_label = {q["label"]: q["id"] for q in questions}
        return {by_label[k]: v for k, v in self._answers.items() if k in by_label}

    def shot(self, name):
        return self.out / f"{name}.png"


def fields_by_label(page):
    return {f["label"]: f for f in page.evaluate(a.QUESTIONS_JS)}


def test_pure_helpers():
    assert a.apply_url("lever", "https://jobs.lever.co/x/1/") == "https://jobs.lever.co/x/1/apply"
    assert a.apply_url("lever", "https://jobs.lever.co/x/1/apply") == "https://jobs.lever.co/x/1/apply"
    assert a.apply_url("ashby", "https://jobs.ashbyhq.com/x/1") == "https://jobs.ashbyhq.com/x/1/application"
    assert a.apply_url("greenhouse", "https://boards.greenhouse.io/x/jobs/1?gh_jid=1") == "https://boards.greenhouse.io/x/jobs/1?gh_jid=1"
    assert a.same("(519) 555-0142", "5195550142") and a.same("+1 519 555 0142", "519-555-0142")
    assert a.same("+1", "Canada +1", picked=True) and not a.same("+1", "Canada +1")
    assert not a.same("", "x", picked=True) and a.same("Kitchener, ON, CAN", "Kitchener")
    pw = apply_worker.platform_of
    assert pw({"url": "https://td.wd3.myworkdayjobs.com/x"}) == "workday"
    assert pw({"url": "https://job-boards.greenhouse.io/x/jobs/1"}) == "greenhouse"
    assert pw({"url": "https://www.acme.com/careers?gh_jid=1", "platform": "greenhouse"}) == "greenhouse"   # embedded
    assert pw({"url": "https://acme.icims.com/jobs/1", "platform": "icims"}) is None
    assert pw({"url": "https://acme.com/jobs", "platform": "workday"}) is None   # Workday needs its own host


def test_page_reading_and_captcha():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page()
        page.set_content(PAGE)
        f = fields_by_label(page)
        assert f["First Name"]["required"] and not f["Preferred First Name"]["required"]
        assert not f["Phone"]["required"], "a single input's own label decides, not the field before it"
        assert f["Country"]["type"] == "combo"
        assert [x["key"] for x in page.evaluate(a.QUESTIONS_JS) if x["type"] == "file"] == ["resume", "cover_letter"]
        assert f["How did you hear about us?"]["type"] == "checkboxes" and f["How did you hear about us?"]["required"]
        onsite = f["Can you work on site 3 days a week?"]
        assert onsite["type"] == "radio" and onsite["required"] and onsite["options"] == ["Yes", "No"]
        assert not f["Gender"]["required"]
        assert f["Current location"]["required"], "Lever: ✱ and helper text are cut from the label"
        assert f["Years of Python"]["options"] == ["0-2", "3-5", "6+"]
        assert f["I agree to the privacy policy and terms"]["type"] == "checkbox"
        assert not apply_worker.captcha_challenge(page), "invisible badge and parked challenge don't count"
        page.evaluate("""() => { const p = document.getElementById('parked'); p.style.top = '10px'; }""")
        assert apply_worker.captcha_challenge(page), "a challenge on screen does"
        page.evaluate("""() => { const p = document.getElementById('parked'); p.style.visibility = 'hidden'; }""")
        assert not apply_worker.captcha_challenge(page)
        b.close()


def test_preferred_name_and_profile_eeo():
    """Preferred name comes from the profile; a voluntary question is answered only when the
    profile holds that fact (disability), never inferred (pronouns)."""
    html = """<label for="pn">Preferred First Name</label><input id="pn" type="text">
    <div><label class="question-title">Are you a person living with a disability?</label><fieldset>
      <div><input type="radio" id="d1" name="dis"><label for="d1">Yes</label></div>
      <div><input type="radio" id="d2" name="dis"><label for="d2">No</label></div></fieldset></div>
    <div><label class="question-title">Pronouns</label><fieldset>
      <div><input type="checkbox" id="p1" name="pro"><label for="p1">He/him</label></div>
      <div><input type="checkbox" id="p2" name="pro"><label for="p2">She/her</label></div></fieldset></div>"""
    out = Path(tempfile.mkdtemp())
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page()
        page.set_content(html)
        run = FakeRun(page, out, {"Are you a person living with a disability?": "No", "Pronouns": "He/him"})
        run.profile = {"eeo_disability": "No", "eeo_gender": "Male"}
        a.fill_form(run, {"preferred_name": "Ada"}, out / "r.pdf")
        f = fields_by_label(page)
        assert f["Preferred First Name"]["value"] == "Ada"
        assert f["Are you a person living with a disability?"]["value"] == "No"
        assert f["Pronouns"]["value"] == "" and "Pronouns" not in {q["label"] for q in run.asked}
        page.set_content(html)
        run = FakeRun(page, out, {"Are you a person living with a disability?": "No"})
        a.fill_form(run, {}, out / "r.pdf")                      # nothing in the profile: both stay blank
        f = fields_by_label(page)
        assert f["Preferred First Name"]["value"] == "" and f["Are you a person living with a disability?"]["value"] == ""
        b.close()


def test_fill_and_check():
    out = Path(tempfile.mkdtemp())
    resume = out / "me_resume.pdf"
    resume.write_bytes(b"%PDF-1.4 x")
    contact = {"first_name": "Ada", "last_name": "L", "email": "ada@x.com", "phone": "+1 807 555 0100", "city": "Kitchener"}
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page()
        page.set_content(PAGE)
        run = FakeRun(page, out, {"Can you work on site 3 days a week?": "Yes", "Why us?": "  ",
                                  "Years of Python": "6+", "Country": "Canada", "Gender": "Man",
                                  "You may contact me about future job opportunities": "checked"},
                      job_notes="[find-ats: was https://www.linkedin.com/jobs/view/1/]")
        a.cover_letter.__defaults__ = (30,)                     # the fake "generates" at once
        real_sleep, a.time.sleep = a.time.sleep, lambda s: None
        try:
            a.fill_form(run, contact, resume)
        finally:
            a.time.sleep = real_sleep
        f = fields_by_label(page)
        asked = {q["label"] for q in run.asked}
        assert {"Gender", "You may contact me about future job opportunities", "Preferred First Name",
                "Leave this field blank", "How did you hear about us?", "Email"}.isdisjoint(asked), asked
        assert f["First Name"]["value"] == "Ada" and f["Email"]["value"] == "ada@x.com" and f["Current location"]["value"] == "Kitchener"
        assert f["Preferred First Name"]["value"] == "" and f["Leave this field blank"]["value"] == ""
        assert f["Can you work on site 3 days a week?"]["value"] == "Yes" and f["Years of Python"]["value"] == "6+"
        assert f["How did you hear about us?"]["value"] == "Job posting on LinkedIn, Indeed, or other job board"
        assert f["I agree to the privacy policy and terms"]["value"] == "checked"
        assert f["You may contact me about future job opportunities"]["value"] == "", "optional opt-in never ticked"
        assert f["Gender"]["value"] == "", "optional voluntary question left blank"
        assert page.evaluate("document.getElementById('resume').files.length") == 1
        assert page.evaluate("document.getElementById('cover_letter').files.length") == 1, "generated letter attached"
        assert ("POST", "/agent/cover-letter/1") in run.api.calls and run.record["cover_letter"] == "cover_letter_v1.pdf"
        assert run.record["consented"] == ["I agree to the privacy policy and terms"]
        assert "Why us?" in run.record["unanswered"], "a blank answer is no answer"
        try:
            a.check_form(run)
            raise AssertionError("a required field is empty: Submit must be blocked")
        except HandOff as h:
            assert h.kind == "unanswered" and "Why us?" in h.reason
        page.fill("#why", "Because.")
        page.evaluate("document.getElementById('first_name').value = 'Bob'")       # a value that didn't stick
        try:
            a.check_form(run)
            raise AssertionError("a changed value must block Submit")
        except HandOff as h:
            assert h.kind == "review check" and "First Name" in h.reason
        page.fill("#first_name", "Ada")
        a.check_form(run)
        assert run.record["review_checked"] >= 5
        b.close()


GREENHOUSE_CODE = """<form id="f"><div id="email-verification" hidden>
<p>A verification code was sent to ada@x.com. To submit your application, enter the 8-character code to
confirm you're a human.</p>""" + "".join(f'<input id="security-input-{i}" maxlength="1">' for i in range(8)) + """
</div><button type="submit">Submit application</button></form>
<script>f.onsubmit = e => { e.preventDefault(); const ev = document.getElementById('email-verification');
  if (ev.hidden) { ev.hidden = false; return; }                      // first Submit: the code is emailed
  const code = [...ev.querySelectorAll('input')].map(i => i.value).join('');
  if (code === 'ABCD1234') document.body.innerHTML = '<h1>Thank you for applying!</h1>';
  window.submits = (window.submits || 0) + 1; };</script>"""


def test_security_code_after_submit():
    """Greenhouse's reCAPTCHA fallback (seen on #69): the user replies with the emailed code (or enters
    it on the screen); the worker never reads mail. Skip, no answer and three misses hand off."""
    a.SETTLE_S = 0
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page()
        run = FakeRun(page, Path(tempfile.mkdtemp()), {})

        def replies(*answers, on_ask=None):
            asks, left = [], list(answers)

            def ask(kind, reason, minutes=15):
                asks.append((kind, reason))
                if on_ask:
                    on_ask()
                return left.pop(0)
            run.ask = ask
            page.set_content(GREENHOUSE_CODE)
            page.evaluate("window.submits = 0")
            return asks

        asks = replies(" abcd 1234 ".upper())                     # spaces from a phone keyboard are dropped
        assert a.submit(run) is True and [k for k, _ in asks] == ["code"]
        assert "emailed you a security code" in asks[0][1] and "screen" not in asks[0][1]

        asks = replies("WRONG123", "ABCD1234")                     # a wrong code: asked again, then sent
        assert a.submit(run) is True and asks[1][1].startswith("That code didn't go through.")

        asks = replies("123", "ABCD1234")                           # wrong length: nothing typed, asked again
        assert a.submit(run) is True and len(asks) == 2

        run.screen = "https://screen"                               # done by the user on the screen
        asks = replies("done", on_ask=lambda: page.evaluate(
            "() => { [...'ABCD1234'].forEach((c, i) => document.getElementById('security-input-' + i).value = c);"
            " document.querySelector('button').click(); }"))
        assert a.submit(run) is True and "https://screen" in asks[0][1]
        run.screen = ""

        for answers, kind, words in ((["cancel"], "cancelled", "skipped"), (["timeout"], "email code", "no answer"),
                                     (["X1234567"] * 3, "email code", "three tries")):
            replies(*answers)
            try:
                a.submit(run)
                raise AssertionError(f"{answers}: must hand off")
            except HandOff as h:
                assert h.kind == kind and words in h.reason, h.reason
                assert kind == "cancelled" or run.job["url"] in h.reason
        assert page.evaluate("window.submits") == 3, "three codes tried, never a fourth click"
        b.close()
    a.SETTLE_S = 20


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
