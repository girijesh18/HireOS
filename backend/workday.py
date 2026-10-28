"""Workday (*.myworkdayjobs.com) flow for the apply worker.

Element names are Workday's data-automation-id values, the same on every employer's site.
Knowledge carried over from the OpenClaw applier (apply-workday skill, apply-site-notes):
Apply → "Apply Manually" (never autofill/LinkedIn), one candidate account per employer with
the same email + password, buttons sit under a `click_filter` overlay, dropdowns are custom
listboxes, phone is local digits (country code is pre-filled), never type into honeypots.
"""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from apply_worker import CREDS, HandOff

VAULT = Path(os.getenv("HIREOS_VAULT") or CREDS.parent / "ats-account.json")   # server: a secret file
A = lambda aid: f"[data-automation-id='{aid}']"
# The "Next / Save and Continue / Submit" button: its name differs between Workday versions.
NEXT = "[data-automation-id='pageFooterNextButton'], [data-automation-id='bottom-navigation-next-button']"
# Voluntary self-identification: the answer is the user's own (profile) or the decline option.
EEO = re.compile(r"gender|sex|orientation|indigenous|aboriginal|disab|ethnic|race|veteran|pronoun|visible minority", re.I)
DECLINE = re.compile(r"prefer not|decline|not to (answer|say|disclose|self.?identify)|do not wish|don.t wish", re.I)
# Required "I have read and agree to the terms" boxes. Girijesh chose (2026-10-26): tick them,
# and list every one in the Review report so he sees what was agreed before Submit.
CONSENT = re.compile(r"\b(agree|accept|consent|acknowledge)\b.*\b(terms|conditions|policy|privacy|agreements?|statement)\b|\b(terms|conditions)\b.*\b(agree|accept)", re.I)
OPT_IN = re.compile(r"preferred name|job alert|newsletter|marketing|text messages|sms|talent (community|network)", re.I)
HONEYPOT = re.compile(r"robot|do not (enter|fill)|leave (this )?(field )?blank|if you('| a)re human", re.I)
MAX_PAGES = 10

# My Information fields filled straight from HireOS contact facts, no model involved.
CONTACT_FIELDS = [
    ("legalNameSection_firstName", "first_name"), ("legalNameSection_lastName", "last_name"),
    ("addressSection_addressLine1", "street_address"), ("addressSection_city", "city"),
    ("addressSection_postalCode", "postal_code"), ("addressSection_countryRegion", "province"),
    ("phone-number", "phone"), ("linkedin", "linkedin"),
]

FIELDS_JS = r"""() => {
  const txt = el => (el && el.innerText || '').replace(/\s+/g, ' ').trim();
  const lab = el => txt(el.labels && el.labels[0]) || (el.getAttribute('aria-label') || '').trim();
  const out = [];
  document.querySelectorAll('[data-hw]').forEach(e => e.removeAttribute('data-hw'));   // re-scan: no stale ids
  document.querySelectorAll("[data-automation-id^='formField-']").forEach((f, i) => {
    if (!f.offsetParent || f.parentElement.closest("[data-automation-id^='formField-']")) return;
    const id = 'f' + i; f.setAttribute('data-hw', id);
    const label = txt(f.querySelector('label, legend')).replace(/\*\s*$/, '').trim();
    const required = /\*/.test(txt(f.querySelector('label, legend'))) || !!f.querySelector('[aria-required=true],[required]');
    const dd = f.querySelector('button[aria-haspopup=listbox]');
    const radios = [...f.querySelectorAll('input[type=radio]')];
    const cbs = [...f.querySelectorAll('input[type=checkbox]')];
    const ms = f.querySelector("[data-automation-id=multiselectInputContainer], input[data-uxi-widget-type=selectinput]");
    const ta = f.querySelector('textarea');
    const inp = f.querySelector('input[type=text], input:not([type]), input[type=email], input[type=tel], input[type=number]');
    let type, value = '', options = [];
    if (f.querySelector('input[type=file]')) type = 'file';
    else if (f.querySelector("[data-automation-id^=dateSection]")) { type = 'date'; value = [...f.querySelectorAll('input')].map(e => e.value).filter(Boolean).join('/'); }
    else if (dd) { type = 'select'; value = /^select one$/i.test(txt(dd)) ? '' : txt(dd); }
    else if (radios.length) { type = 'radio'; options = radios.map(lab); const c = radios.find(r => r.checked); value = c ? lab(c) : ''; }
    else if (ms) { type = 'multiselect'; value = [...f.querySelectorAll("[data-automation-id=selectedItem]")].map(txt).join(', '); }
    else if (cbs.length === 1 && !inp && !ta) { type = 'checkbox'; value = cbs[0].checked ? 'checked' : ''; options = ['checked', 'unchecked']; }
    else if (cbs.length > 1) { type = 'checkboxes'; options = cbs.map(lab); value = cbs.filter(c => c.checked).map(lab).join(', '); }
    else if (ta) { type = 'textarea'; value = ta.value; }
    else if (inp) { type = 'text'; value = inp.value; }
    else return;
    out.push({id, aid: f.getAttribute('data-automation-id'), label, required, type, value, options});
  });
  return out;
}"""


# ── Page helpers ──────────────────────────────────────────────────────────────

def settle(page, quiet_ms: int = 800):
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and page.locator(A("loadingSpinner")).filter(visible=True).count():
        page.wait_for_timeout(300)
    page.wait_for_timeout(quiet_ms)


def visible(page, sel: str) -> bool:
    return page.locator(sel).filter(visible=True).count() > 0


def click(page, sel: str):
    """A real click; Workday puts a transparent `click_filter` div over its buttons."""
    el = page.locator(sel).filter(visible=True).first
    try:
        el.click(timeout=5000)
    except Exception:
        el.locator("xpath=..").locator(A("click_filter")).first.click(timeout=5000)


def heading(page) -> str:
    """'step 2 of 4 My Experience' — the step counter plus the page's own title."""
    parts = []
    for sel in (A("progressBarActiveStep"), f"{A('applyFlowPage')} h2"):
        el = page.locator(sel).filter(visible=True)
        if el.count():
            parts.append(el.first.inner_text(timeout=3000).strip().split("\n")[0][:60])
    return " ".join(parts).replace("current ", "") or "page"


def job_source(job: dict) -> str:
    """Where Girijesh found the job, for "How did you hear about us". A LinkedIn/Glassdoor job
    moved to the company's own site keeps its old URL in notes ("[find-ats: was <url>]")."""
    was = re.search(r"\[find-ats: was (\S+)\]", job.get("notes") or "")
    url = (was.group(1) if was else job.get("url") or "").lower()
    for host, name in (("linkedin.", "LinkedIn"), ("glassdoor.", "Glassdoor"), ("indeed.", "Indeed")):
        if host in url:
            return name
    return "the company's own careers website"


def recover(page, run) -> bool:
    """Workday's 'Something went wrong — refresh the page' screen: reload, at most twice a run."""
    if not re.search(r"something went wrong", page.locator("body").inner_text(timeout=5000)[:3000], re.I):
        return False
    run.reloads = getattr(run, "reloads", 0) + 1
    if run.reloads > 2:
        raise HandOff("site error", "Workday kept showing 'Something went wrong'")
    run.shot("site-error-reload")
    page.reload(wait_until="domcontentloaded")
    settle(page, 2000)
    return True


def on_form(page) -> bool:
    return visible(page, NEXT)


def on_review(page) -> bool:
    btn = page.locator(NEXT).filter(visible=True)
    return visible(page, A("reviewJobApplicationPage")) or (
        btn.count() > 0 and btn.first.inner_text(timeout=3000).strip().lower() == "submit")


def form_errors(page) -> list:
    sels = f"{A('errorMessage')}, {A('errorBanner')}, [role=alert]"
    errs = [t.strip() for t in page.locator(sels).filter(visible=True).all_inner_texts()
            if t.strip() and not re.search(r"success", t, re.I)]   # "uploaded successfully" uses the same alert
    if not errs:   # Workday's "Errors Found" summary: "Error - <field>" lines
        body = page.locator("body").inner_text(timeout=5000)
        errs = [l.strip() for l in body.splitlines() if re.match(r"\s*(Error\b|The field .+ is required)", l)]
    return list(dict.fromkeys(errs))[:10]


def local_phone(phone: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    return d[1:] if len(d) == 11 and d.startswith("1") else d


def pick(options: list, answer: str):
    """The option matching an answer: exact, then case-insensitive, then containment."""
    a = (answer or "").strip().lower()
    for test in (lambda o: o == answer, lambda o: o.lower() == a, lambda o: a and (a in o.lower() or o.lower() in a)):
        hit = [o for o in options if o and test(o)]
        if hit:
            return hit[0]
    return None


STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "my", "our", "your", "company", "other"}


def closest(options: list, answer: str):
    """An answer worded differently from every option ("Company Website" vs "TD Careers
    Website"): the one option sharing the most meaningful words, if exactly one does."""
    words = lambda x: set(re.findall(r"[a-z0-9]+", (x or "").lower())) - STOP
    a = words(answer)
    scored = sorted(((len(a & words(o)), o) for o in options if o), reverse=True)
    if scored and scored[0][0] and (len(scored) == 1 or scored[1][0] < scored[0][0]):
        return scored[0][1]
    return None


# A dropdown's options live in a popup appended to <body>, not inside the field. Selected
# chips in other fields also have role=option, so only options outside every field count.
POPUP_JS = r"""(pick) => {
  const opts = [...document.querySelectorAll('[role=option]')]
    .filter(o => o.offsetParent && !o.closest("[data-automation-id^='formField-']"));
  if (pick === null) return opts.map(o => (o.innerText || '').trim()).filter(Boolean);
  const hit = opts.find(o => (o.innerText || '').trim() === pick);
  if (hit) hit.setAttribute('data-hw-opt', '1');
  return !!hit;
}"""


def popup_options(page) -> list:
    try:
        page.wait_for_function("() => [...document.querySelectorAll('[role=option]')].some(o => o.offsetParent && !o.closest(\"[data-automation-id^='formField-']\"))", timeout=5000)
    except Exception:
        return []
    return page.evaluate(POPUP_JS, None)


def close_popups(page):
    """Escape doesn't close Workday's multi-select list; a click on the page title does."""
    page.keyboard.press("Escape")
    for _ in range(3):
        if not visible(page, A("activeListContainer")):
            return
        title = page.locator(f"{A('applyFlowPage')} h2").filter(visible=True)
        (title.first if title.count() else page.locator("body")).click(position={"x": 5, "y": 5}, timeout=3000)
        page.wait_for_timeout(400)


def click_option(page, text: str) -> bool:
    page.evaluate("() => document.querySelectorAll('[data-hw-opt]').forEach(e => e.removeAttribute('data-hw-opt'))")
    if not page.evaluate(POPUP_JS, text):
        return False
    page.locator("[data-hw-opt='1']").first.click(timeout=5000)
    page.wait_for_timeout(600)
    return True


# ── Tracing: never record the password ────────────────────────────────────────

def trace_pause(run):
    run.trace_n = getattr(run, "trace_n", 0) + 1
    run.page.context.tracing.stop_chunk(path=str(run.out / f"trace-{run.trace_n}.zip"))


def trace_resume(run):
    run.page.context.tracing.start_chunk()


def trace_stop(ctx, out: Path):
    n = len(list(out.glob("trace-*.zip"))) + 1
    try:
        ctx.tracing.stop_chunk(path=str(out / f"trace-{n}.zip"))
    except Exception:
        pass
    try:
        ctx.tracing.stop()
    except Exception:
        pass


# ── Sign in / create account ──────────────────────────────────────────────────

def start_application(run):
    page = run.page
    page.goto(run.job["url"], wait_until="domcontentloaded")
    settle(page)
    run.check()
    if not visible(page, A("adventureButton")):
        body = page.locator("body").inner_text(timeout=5000)[:3000]
        if re.search(r"doesn.t exist|no longer (accepting|available|posted)|(job|posting) (has been |is )?(closed|filled|removed)", body, re.I):
            raise HandOff("closed", "the posting no longer exists")
        raise HandOff("posting", "no Apply button")
    click(page, A("adventureButton"))
    settle(page)
    if visible(page, A("applyManually")):
        click(page, A("applyManually"))
        settle(page)


def type_secret(run, sel: str, value: str):
    """Typed with tracing paused: the trace and logs never contain the password."""
    trace_pause(run)
    try:
        run.page.locator(sel).filter(visible=True).first.fill(value)
    finally:
        trace_resume(run)


def wait_outcome(page, secs: int = 25) -> str:
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        if on_form(page):
            return "in"
        if re.search(r"email has been sent|verify your (email|account)", page.locator("body").inner_text(timeout=5000)[:3000], re.I):
            return "verify"
        if form_errors(page):
            return "error"
        page.wait_for_timeout(1000)
    return "timeout"


def verify_email(run):
    """A new Workday account must be verified by email before it can sign in.
    1. HireOS looks for the link in the forwarded/read job-site emails (automatic, ≤2 min).
    2. Otherwise Girijesh is asked on Telegram to tap the link in his Gmail; the link only
       activates the account on the employer's side, so the worker just retries signing in
       every 30 s until it works (≤15 min). No "Done" tap needed.
    Returns True when the account is usable (either way), raises HandOff after the wait."""
    page = run.page
    host = urlparse(page.url).hostname
    for _ in range(4):
        link = run.api.post("/agent/workday-verify-link", {"host": host, "minutes": 60}).get("link")
        if link and urlparse(link).hostname.endswith(".myworkdayjobs.com"):
            page.goto(link, wait_until="domcontentloaded")
            settle(page)
            run.shot("email-verified")
            return True
        page.wait_for_timeout(30_000)
    from apply_worker import notify
    company = run.job["company"]
    notify(run.api, f"#{run.job['id']} {company}: {company} sent you a \"verify your account\" email "
                    f"(from …@myworkday.com). Open it in Gmail and tap the link — on your phone is fine. "
                    "I'll carry on by myself as soon as it's verified (I check every 30 s for 15 min).")
    run.record.setdefault("asked", []).append("verify account email")
    acct = json.loads(VAULT.read_text())
    deadline = time.monotonic() + 15 * 60
    while time.monotonic() < deadline:
        page.wait_for_timeout(30_000)
        start_application(run)
        settle(page)
        if on_form(page):
            return True
        by_email = page.locator("button, a, [role=button]").filter(has_text=re.compile(r"^\s*sign in with email\s*$", re.I)).filter(visible=True)
        if by_email.count() and not visible(page, A("signInSubmitButton")):
            by_email.first.click(timeout=5000)
            settle(page)
        if not visible(page, A("signInSubmitButton")) and visible(page, A("signInLink")):
            click(page, A("signInLink"))
            settle(page)
        if visible(page, A("signInSubmitButton")):
            page.locator(A("email")).filter(visible=True).first.fill(acct["email"])
            type_secret(run, A("password"), acct["password"])
            click(page, A("signInSubmitButton"))
            if wait_outcome(page, 20) == "in":
                run.shot("signed-in-after-verify")
                return True
    raise HandOff("verify email", f"{company} account not verified within 15 min — tap the link in the email, then retry")


def sign_in(run):
    page = run.page
    host = urlparse(page.url).hostname or ""
    if not host.endswith(".myworkdayjobs.com"):   # the vault is only ever typed into Workday
        raise HandOff("sign-in", f"login asked on {host}")
    acct = None
    created = False
    for attempt in range(4):
        settle(page)
        run.check()
        if recover(page, run) or on_form(page):
            if on_form(page):
                return
            continue
        acct = acct or json.loads(VAULT.read_text())
        # Some sites lead with "Sign in with Apple / Google"; only ever the email route.
        by_email = page.locator("button, a, [role=button]").filter(has_text=re.compile(r"^\s*sign in with email\s*$", re.I)).filter(visible=True)
        if by_email.count() and not visible(page, A("signInSubmitButton")):
            by_email.first.click(timeout=5000)
            settle(page)
        if visible(page, A("signInSubmitButton")) or (visible(page, A("signInLink")) and not created and attempt == 0):
            if not visible(page, A("signInSubmitButton")):
                click(page, A("signInLink"))
                settle(page)
            page.locator(A("email")).filter(visible=True).first.fill(acct["email"])
            type_secret(run, A("password"), acct["password"])
            click(page, A("signInSubmitButton"))
            run.shot("sign-in")
            outcome = wait_outcome(page)
            if outcome == "error" and not created and visible(page, A("createAccountLink")):
                click(page, A("createAccountLink"))      # no account on this employer's site yet
                settle(page)
                continue
        elif visible(page, A("createAccountSubmitButton")):
            page.locator(A("email")).filter(visible=True).first.fill(acct["email"])
            type_secret(run, A("password"), acct["password"])
            type_secret(run, A("verifyPassword"), acct["password"])
            if visible(page, A("createAccountCheckbox")):
                page.locator(A("createAccountCheckbox")).filter(visible=True).first.check()
            click(page, A("createAccountSubmitButton"))
            created = True
            run.shot("create-account")
            outcome = wait_outcome(page)
            if outcome != "in":   # a new account must be verified by email before it can sign in
                outcome = "verify"
        else:
            raise HandOff("sign-in", "no sign-in or create-account form found")
        if outcome == "verify":
            if getattr(run, "verified", False):
                raise HandOff("sign-in", "still asks for email verification after opening the link")
            run.verified = True
            if verify_email(run) and on_form(page):
                return
            start_application(run)
        elif outcome == "error" and (created or not visible(page, A("createAccountLink"))):
            raise HandOff("sign-in", ("; ".join(form_errors(page)) or "sign-in error")
                          + (" — account exists with another password? Never reset it: needs you" if not created else ""))
    raise HandOff("sign-in", "not signed in after 4 tries")


# ── Filling one page ──────────────────────────────────────────────────────────

def open_multiselect(page, box):
    """Click a multiselect's input; its container often sits over it and takes the click."""
    try:
        box.locator("input").first.click(timeout=5000)
    except Exception:
        box.locator(A("multiselectInputContainer")).first.click(timeout=5000, force=True)
        box.locator("input").first.focus()


def read_options(page, f: dict) -> list:
    """Workday lists a dropdown's options only while it is open."""
    box = page.locator(f"[data-hw='{f['id']}']")
    try:
        if f["type"] == "select":
            box.locator("button[aria-haspopup=listbox]").first.click(timeout=5000)
        else:
            open_multiselect(page, box)
            page.wait_for_timeout(1000)
        opts = popup_options(page)
    except Exception:
        opts = []
    close_popups(page)
    return opts


def set_value(run, f: dict, value: str) -> bool:
    """Fill one field and read it back. False when the value didn't stick."""
    page = run.page
    box = page.locator(f"[data-hw='{f['id']}']")
    t = f["type"]
    if t in ("text", "textarea"):
        el = box.locator("textarea" if t == "textarea" else "input").first
        el.fill(value)
        el.press("Tab")
        got = el.input_value().strip()
        norm = lambda x: re.sub(r"\s+", " ", x).strip().lower()
        if norm(got) != norm(value):   # the field reshaped it (digits only, max length): never leave that
            el.fill("")
            el.press("Tab")
            return False
        return True
    if t == "select":
        btn = box.locator("button[aria-haspopup=listbox]").first
        btn.click(timeout=5000)
        choice = pick(popup_options(page), value)
        if not choice or not click_option(page, choice):
            page.keyboard.press("Escape")
            return False
        return btn.inner_text().strip().lower() == choice.strip().lower()
    if t == "radio":
        choice = pick(f["options"], value)
        if not choice:
            return False
        radio = box.get_by_label(choice, exact=True).first
        try:
            radio.check(timeout=5000)
        except Exception:   # Workday's overlay intercepts the input; the label takes the click
            box.locator("label").filter(has_text=re.compile(rf"^\s*{re.escape(choice)}\s*$")).first.click(timeout=5000)
        return radio.is_checked()
    if t == "checkbox":
        if value.strip().lower() in ("checked", "yes", "true", "i agree", "agree"):
            cb = box.locator("input[type=checkbox]").first
            try:
                cb.check(timeout=5000)
            except Exception:
                cb.check(force=True)
            return cb.is_checked()
        return True
    if t == "checkboxes":   # "select all that apply": tick each answered option
        done = 0
        for part in [x.strip() for x in re.split(r",|;", value) if x.strip()]:
            choice = pick(f["options"], part)
            if not choice:
                continue
            cb = box.get_by_label(choice, exact=True).first
            try:
                cb.check(timeout=5000)
            except Exception:
                box.locator("label").filter(has_text=re.compile(rf"^\s*{re.escape(choice)}\s*$")).first.click(timeout=5000)
            done += cb.is_checked()
        return done > 0
    if t == "multiselect":
        # Search first: Workday's multiselects search every level (Job Board → LinkedIn) by typing.
        if set_search(run, f, value):
            return bool(box.locator(A("selectedItem")).count())
        for level in range(2):   # "How did you hear" lists open a second level (Job Board → LinkedIn)
            if level == 0:
                open_multiselect(page, box)
                page.wait_for_timeout(1000)
            opts = popup_options(page)
            choice = pick(opts, value) or closest(opts, value)
            if not choice and level == 1 and opts:   # second level: ask HireOS again with these options
                a = run.answers([{"id": "sub", "label": f"{f['label']} ({value})", "type": "select", "options": opts}]).get("sub")
                choice = pick(opts, a or "")
            if not choice or not click_option(page, choice):
                page.keyboard.press("Escape")
                return bool(box.locator(A("selectedItem")).count())
            page.wait_for_timeout(700)
            if box.locator(A("selectedItem")).count():
                page.keyboard.press("Escape")
                return True
        page.keyboard.press("Escape")
        return False
    return False


def upload_resume(run, resume: Path):
    """Workday creates its file input only when "Select files" is clicked: catch the file
    chooser that click opens and hand it the resume. Done once per page that asks for it."""
    page = run.page
    body = page.locator("body").inner_text(timeout=5000)
    if resume.name in body or not re.search(r"drop files here|select files|upload a file", body, re.I):
        run.record.setdefault("debug", []).append(f"upload skipped on {heading(page)}: name_in_body={resume.name in body} body_len={len(body)}")
        return
    trigger = page.locator(f"{A('select-files')}, button:has-text('Select files'), a:has-text('Select files')").filter(visible=True)
    try:
        with page.expect_file_chooser(timeout=10000) as fc:
            trigger.first.click(timeout=5000)
        fc.value.set_files(str(resume))
        page.get_by_text(resume.name).first.wait_for(timeout=30000)
        run.record["filled"].append(f"Resume = {resume.name}")
    except Exception as e:
        run.record["errors"].append(f"resume upload failed: {type(e).__name__}")
    settle(page, 2000)   # Workday's parser may fill experience/education from the PDF


# ── Work experience / education from the profile (parsed from the master resume) ──

ADDS_JS = r"""() => [...document.querySelectorAll("[data-automation-id='add-button']")].filter(b => b.offsetParent).map((b, i) => {
  let el = b, t = '';
  for (let k = 0; k < 8 && el && !t; k++) { el = el.parentElement; const h = el && el.querySelector('h3, h4, [role=heading]'); if (h) t = h.innerText.trim(); }
  b.setAttribute('data-hw-add', String(i)); return t; })"""
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# Filled only from the profile's history by fill_history, never guessed by the answer model.
HISTORY_AIDS = {"formField-jobTitle", "formField-companyName", "formField-location", "formField-currentlyWorkHere",
                "formField-startDate", "formField-endDate", "formField-roleDescription", "formField-school",
                "formField-schoolName", "formField-degree", "formField-fieldOfStudy", "formField-gradeAverage",
                "formField-firstYearAttended", "formField-lastYearAttended"}

DELETE_JS = r"""(id) => {
  let el = document.querySelector(`[data-hw='${id}']`);
  for (let k = 0; k < 10 && el; k++) {
    el = el.parentElement;
    const b = el && [...el.querySelectorAll('button')].find(b => /^delete$/i.test((b.innerText || b.getAttribute('aria-label') || '').trim()));
    if (b) { b.setAttribute('data-hw-del', '1'); return true; }
  }
  return false;
}"""


def delete_entry(run, f: dict, why: str):
    """Remove an entry we added but can't complete (a required field has no true answer)."""
    page = run.page
    page.evaluate("() => document.querySelectorAll('[data-hw-del]').forEach(e => e.removeAttribute('data-hw-del'))")
    if page.evaluate(DELETE_JS, f["id"]):
        page.locator("[data-hw-del='1']").first.click(timeout=5000)
        settle(page, 800)
        run.record.setdefault("skipped", []).append(why)
    else:
        run.record["unanswered"].append(why)


def add_entry(page, section: str) -> bool:
    heads = page.evaluate(ADDS_JS)
    i = next((i for i, h in enumerate(heads) if section.lower() in h.lower()), None)
    if i is None:
        return False
    page.locator(f"[data-hw-add='{i}']").first.click(timeout=5000)
    settle(page, 1000)
    return True


def newest(page, aid: str, after: tuple = ()):
    """The last (just added) entry's field with this automation id, as FIELDS_JS describes it.
    after: the entry's first field(s); only fields below the last of them count, so an
    education entry without its own start date never gets the work entry's above it."""
    fields = page.evaluate(FIELDS_JS)
    start = max((i for i, f in enumerate(fields) if f["aid"] in after), default=-1) if after else -1
    if after and start < 0:
        return None
    hits = [f for f in fields[start + 1:] if f["aid"] == aid]
    return hits[-1] if hits else None


def month_year(when: str) -> tuple:
    """'Jun 2018' / '06/2018' / '6/2018' -> (6, 2018); (None, None) if unreadable."""
    y = re.search(r"\d{4}", when or "")
    m = re.match(r"([A-Za-z]{3})", (when or "").strip())
    n = re.match(r"(\d{1,2})\D", (when or "").strip())
    month = MONTHS.get(m.group(1).lower()) if m else (int(n.group(1)) if n else None)
    return month, int(y.group(0)) if y else None


def fix_work_dates(run, profile: dict):
    """Work entries already on the form (a draft, Workday's resume parser): each entry's dates
    must match the profile's role with that title and company. Wrong ones are retyped."""
    fields = run.page.evaluate(FIELDS_JS)
    starts = [i for i, f in enumerate(fields) if f["aid"] == "formField-jobTitle"] + [len(fields)]
    for a, b in zip(starts, starts[1:]):
        entry = fields[a:b]
        get = lambda aid: next((f for f in entry if f["aid"] == aid), None)
        title, company = norm(entry[0]["value"]), norm((get("formField-companyName") or {}).get("value", ""))
        role = next((w for w in profile.get("work_history") or [] if norm(w["title"]) == title
                     and (not company or norm(w["company"]) in company or company in norm(w["company"]))), None)
        if not role:
            continue
        for aid, when in (("formField-startDate", role["start"]), ("formField-endDate", role["end"])):
            f = get(aid)
            if not f or (when or "").lower() == "present" or month_year(f["value"]) == month_year(when):
                continue
            ok = set_date(run, f, when)
            run.record["filled" if ok else "unanswered"].append(
                f"{role['title']} {aid.split('-')[1]} = {when} (fixed draft: was {f['value']})" if ok
                else f"{role['title']} {aid.split('-')[1]} (draft says {f['value']}, resume says {when}; couldn't fix)")


def set_date(run, f: dict, when: str) -> bool:
    """'Jan 2024' into Workday's month + year boxes (typed: they are spin buttons)."""
    m = re.match(r"([A-Za-z]{3})\w*\s+(\d{4})", when or "")
    y = re.search(r"\d{4}", when or "")
    if not y:
        return False
    box = run.page.locator(f"[data-hw='{f['id']}']")

    def type_into(part: str, digits: str) -> bool:
        # The visible "MM"/"YYYY" layer (…-display) sits over the input and takes the click.
        target = box.locator(f"{A(f'dateSection{part}-display')}, {A(f'dateSection{part}-input')}").first
        if not box.locator(A(f"dateSection{part}-input")).count():
            return False
        try:
            target.click(timeout=5000)
        except Exception:
            box.locator(A(f"dateSection{part}-input")).first.click(force=True)
        run.page.keyboard.type(digits, delay=60)
        return True

    try:
        if m:
            type_into("Month", f"{MONTHS.get(m.group(1).lower(), 1):02d}")
        if not type_into("Year", y.group(0)):
            return False
        run.page.keyboard.press("Tab")
        run.page.wait_for_timeout(300)
        return y.group(0) in (box.locator(A("dateSectionYear-input")).first.get_attribute("aria-valuetext") or "") \
            or y.group(0) in box.inner_text()
    except Exception:
        return False


def set_search(run, f: dict, text: str, fallback: str = "", strict: bool = False) -> str:
    """A Workday search box (school, field of study, skills): type, Enter, pick the closest
    result. strict: only the same name, optionally with a "(...)" qualifier (skills)."""
    page = run.page
    inp = page.locator(f"[data-hw='{f['id']}'] input").first
    for query in [q for q in ((text,) if strict else (text, " ".join(text.split()[:2]), fallback)) if q]:
        try:
            inp.click(timeout=5000)
        except Exception:   # the multiselect container sits over its input
            page.locator(f"[data-hw='{f['id']}'] {A('multiselectInputContainer')}").first.click(timeout=5000, force=True)
            inp.focus()
        inp.fill(query)
        inp.press("Enter")
        opts = []
        for _ in range(8):   # results arrive over a few seconds
            page.wait_for_timeout(1000)
            opts = [o for o in page.evaluate(POPUP_JS, None) if o != "No Items."]
            if opts:
                break
        choice = next((o for o in opts if norm(o) == norm(query) or norm(o).startswith(norm(query) + " (")), None) \
            if strict else pick(opts, query)
        if choice and click_option(page, choice):
            close_popups(page)
            return choice
        close_popups(page)
    return ""


def fill_history(run, profile: dict):
    """Work Experience and Education sections, when the page has them and they are empty.
    Entries already present (a draft, or Workday's own resume parsing) are left alone."""
    page = run.page
    heads = [h.lower() for h in page.evaluate(ADDS_JS)]
    filled = lambda aid: any(f["aid"] == aid and f["value"] for f in page.evaluate(FIELDS_JS))
    note = lambda ok, text: run.record["filled" if ok else "unanswered"].append(text)
    if filled("formField-jobTitle"):
        fix_work_dates(run, profile)
    elif any("work experience" in h for h in heads):
        for w in profile.get("work_history") or []:
            if not add_entry(page, "Work Experience"):
                break
            work = ("formField-jobTitle",)
            for aid, value in (("formField-jobTitle", w["title"]), ("formField-companyName", w["company"]),
                               ("formField-location", w["location"])):
                f = newest(page, aid, () if aid in work else work)
                if f and value:
                    try_set(run, f, value, " (resume)")
            current = (w["end"] or "").lower() == "present"
            if current and (f := newest(page, "formField-currentlyWorkHere", work)):
                page.locator(f"[data-hw='{f['id']}'] input[type=checkbox]").first.check(force=True)
            if f := newest(page, "formField-startDate", work):
                note(set_date(run, f, w["start"]), f"{w['title']} from = {w['start']}")
            if not current and (f := newest(page, "formField-endDate", work)):
                note(set_date(run, f, w["end"]), f"{w['title']} to = {w['end']}")
            if (f := newest(page, "formField-roleDescription", work)) and w.get("description"):
                el = page.locator(f"[data-hw='{f['id']}'] textarea").first
                limit = int(el.get_attribute("maxlength") or 0) or 10000
                text = w["description"] if len(w["description"]) <= limit else \
                    w["description"][:limit].rsplit("\n", 1)[0].rsplit(". ", 1)[0] + "."
                try_set(run, f, text, " (resume)")
            close_popups(page)
    if any(h.startswith("education") for h in heads) and not filled("formField-school") \
            and not filled("formField-schoolName"):
        for e in profile.get("education_history") or []:
            if not add_entry(page, "Education"):
                break
            f = newest(page, "formField-school") or newest(page, "formField-schoolName")
            if f and f["type"] == "multiselect":
                got = set_search(run, f, e["school"], "Other")
                if not got:   # the employer's school list lacks it: drop the entry, never pick a lookalike
                    delete_entry(run, f, f"Education '{e['degree']}' left out: {e['school']} isn't in {run.job['company']}'s school list")
                    continue
                note(True, f"School = {got}")
            elif f:
                try_set(run, f, e["school"], " (resume)")
            if f := newest(page, "formField-degree", ("formField-school", "formField-schoolName")):
                level = next((k for k in ("master", "bachelor", "doctor", "diploma") if k in e["degree"].lower()), "")
                opts = read_options(page, f)
                choice = pick(opts, {"master": "Masters", "bachelor": "Bachelors", "doctor": "Doctorate"}.get(level, "")) \
                    or next((o for o in opts if level and o.lower().startswith(level)), "")
                if choice:
                    try_set(run, f, choice, " (resume)")
                else:
                    note(False, f"Degree ({e['degree']})")
            study = (e["degree"].split(",", 1)[1] if "," in e["degree"] else e["degree"].split(" of ", 1)[-1]).strip()
            if (f := newest(page, "formField-fieldOfStudy", ("formField-school", "formField-schoolName"))) and f["type"] == "multiselect":
                got = set_search(run, f, study)
                note(bool(got), f"Field of Study = {got or study + ' (not in list)'}")
            for aid, when in (("formField-firstYearAttended", e["start"]), ("formField-lastYearAttended", e["end"]),
                              ("formField-startDate", e["start"]), ("formField-endDate", e["end"])):
                if f := newest(page, aid, ("formField-school", "formField-schoolName")):
                    note(set_date(run, f, when), f"{e['degree']} {aid.split('-')[1]} = {when}")
            close_popups(page)


SKILLS_HEAD = re.compile(r"^#+ .*(skill|expertise|technolog|tool)", re.I)
SKILLS_MAX, SKILLS_TRIES = 15, 30   # each miss costs a few seconds of searching


def resume_skills(md: str, jd: str = "") -> list:
    """Skills listed in the resume's skills section(s), those the job description names first.
    "classification (XGBoost, CatBoost)" gives XGBoost and CatBoost; long phrases are dropped."""
    out, on = [], False
    for line in (md or "").splitlines():
        if line.startswith("#"):
            on = bool(SKILLS_HEAD.search(line))
            continue
        if not on or not line.lstrip().startswith("-"):
            continue
        label = re.match(r"\s*-\s*\*\*([^*]+):\*\*", line)   # "**Python:** ..." — the label is a skill too
        body = (re.sub(r"\s*&\s*", ", ", label.group(1)) + ", " if label else "") + \
            re.sub(r"\*\*[^*]+:\*\*", "", line.lstrip("- "))
        depth, part, parts = 0, "", []
        for ch in body + ",":
            depth += (ch == "(") - (ch == ")")
            if ch in ",;" and depth == 0:
                parts.append(part)
                part = ""
            else:
                part += ch
        for p in parts:
            if "familiar" in p.lower():   # "Kubernetes (familiar)": not claimed as a skill
                continue
            base, _, inner = p.partition("(")
            for item in [base] + (re.split(r"[,/]", inner.rstrip(" )")) if "(" in p else []):
                item = re.sub(r"\s+", " ", item.strip(" .*"))
                if 2 <= len(item) <= 30 and len(item.split()) <= 3 \
                        and item.lower() not in {x.lower() for x in out}:
                    out.append(item)
    jdl = (jd or "").lower()
    named = lambda x: re.search(rf"(?<![a-z0-9]){re.escape(x.lower())}(?![a-z0-9])", jdl) is not None
    return sorted(out, key=lambda x: not named(x))


def fill_skills(run, md: str):
    """Workday's optional Skills box: add the resume's skills that exist in the employer's list."""
    page = run.page
    f = next((f for f in page.evaluate(FIELDS_JS) if f["type"] == "multiselect"
              and re.search(r"skill", f["aid"] + " " + f["label"], re.I)), None)
    if not f or f["value"]:   # absent, or filled by a draft / Workday's resume parser
        return
    added = []
    for skill in resume_skills(md, run.job.get("job_description") or "")[:SKILLS_TRIES]:
        if len(added) >= SKILLS_MAX:
            break
        try:
            got = set_search(run, f, skill, strict=True)
        except Exception:
            got = ""
        if got:
            added.append(got)
    close_popups(page)
    chips = [norm(c) for c in page.locator(f"[data-hw='{f['id']}'] {A('selectedItem')}").all_inner_texts()]
    lost = [x for x in added if norm(x) not in chips]   # Workday merges or drops some picks
    added = [x for x in added if x not in lost]
    for x in added:
        expect(run, "Skills", x)
    if lost:
        run.record["filled"].append(f"Skills not kept by Workday: {', '.join(lost)}")
    run.record["filled" if added else "unanswered"].append(
        f"Skills = {', '.join(added)} (resume)" if added else "Skills (none of the resume's skills are in the list)")


def wait_form_ready(page, secs: int = 30):
    """Workday draws the page frame first and the fields a few seconds later (Next is disabled
    meanwhile). Reading too early finds no fields and presses Next on an empty form."""
    try:
        page.wait_for_function("""(next) => {
            const b = document.querySelector(next);
            const fields = document.querySelectorAll("[data-automation-id^='formField-']").length;
            return b && !b.disabled && b.getAttribute('aria-disabled') !== 'true' && fields > 0;
        }""", arg=NEXT, timeout=secs * 1000)
    except Exception:
        pass
    settle(page, 500)


def fill_page(run, contact: dict, resume: Path):
    page = run.page
    wait_form_ready(page)
    name = heading(page)
    upload_resume(run, resume)
    fill_history(run, run.profile)
    fill_skills(run, run.resume_md)

    run.check([f["label"] for f in page.evaluate(FIELDS_JS)])
    run.record["pages"].append(name)
    tried = set()
    for _ in range(2):   # a second pass catches fields that appear or re-render after earlier answers
        fields = [f for f in page.evaluate(FIELDS_JS) if (f["aid"], f["label"]) not in tried]
        if not fill_fields(run, fields, contact, tried):
            break
    run.shot(name)


def norm(x) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_`]", "", str(x or ""))).strip().lower()


def same(f: dict, current: str, wanted: str) -> bool:
    """Is what the form holds what HireOS would answer? Options compare by the option the
    answer maps to; numbers-only values (phones) by their digits."""
    if f["type"] in ("select", "radio") and f["options"]:
        want = pick(f["options"], wanted)
        return bool(want) and norm(want) == norm(current)
    if f["type"] in ("multiselect", "checkboxes"):
        return all(norm(w) in norm(current) or norm(current) in norm(w) for w in re.split(r",|;", wanted) if w.strip())
    d1, d2 = re.sub(r"\D", "", current or ""), re.sub(r"\D", "", wanted or "")
    if d2 and len(d2) >= 7 and d2 == re.sub(r"\D", "", wanted):   # a phone-like value
        return d1.endswith(d2) or d2.endswith(d1)
    return norm(current) == norm(wanted)


def expect(run, label: str, value: str):
    """A value the Review page must show (checked by review_check before Submit)."""
    run.record.setdefault("expect", []).append([label, value])


def try_set(run, f: dict, value: str, source: str = ""):
    """set_value that never crashes the run: a field that won't take a value is recorded."""
    try:
        run.page.locator(f"[data-hw='{f['id']}']").first.scroll_into_view_if_needed(timeout=5000)
        ok = set_value(run, f, value)
    except Exception as e:
        ok = False
        value = f"{value}; {type(e).__name__}"
    close_popups(run.page)
    run.record["filled" if ok else "unanswered"].append(
        f"{f['label']} = {value}{source}" if ok else f"{f['label'] or f['aid']} (value didn't stick: {value})")
    if ok and f["type"] not in ("checkbox", "date"):
        expect(run, f["label"] or f["aid"], value)
    return ok


def clear_field(run, f: dict, why: str):
    """Empty an optional field (text) or untick an optional box."""
    box = run.page.locator(f"[data-hw='{f['id']}']")
    try:
        if f["type"] == "checkbox":
            box.locator("input[type=checkbox]").first.uncheck(force=True)
        else:
            el = box.locator("textarea" if f["type"] == "textarea" else "input").first
            el.fill("")
            el.press("Tab")
        run.record["filled"].append(f"{f['label']} = (cleared: {why})")
    except Exception as e:
        run.record["unanswered"].append(f"{f['label']} (couldn't clear {why}: {type(e).__name__})")


def fill_fields(run, fields: list, contact: dict, tried: set) -> int:
    """Fill the empty fields among `fields` and re-check the pre-filled ones (a saved draft
    is never trusted: it may hold a value an earlier run got wrong). Returns how many were seen."""
    page = run.page
    todo, prefilled, n = [], [], 0
    for f in fields:
        if f["type"] == "file" or HONEYPOT.search(f["label"]) or f["aid"] in HISTORY_AIDS \
                or (f["type"] == "multiselect" and re.search(r"skill", f["aid"] + " " + f["label"], re.I)) \
                or (f["aid"], f["label"]) in tried:
            continue
        tried.add((f["aid"], f["label"]))
        n += 1
        key = next((k for aid, k in CONTACT_FIELDS if aid.lower() in (f["aid"] or "").lower()), None)
        if key and contact.get(key):
            want = local_phone(contact[key]) if key == "phone" else contact[key]
            if f["value"] and same(f, f["value"], want):
                expect(run, f["label"], want)
            else:
                try_set(run, f, want, " (contact)" + (f", was '{f['value']}'" if f["value"] else ""))
            continue
        if f["type"] == "checkbox":
            if f["required"] and CONSENT.search(f["label"]):
                if not f["value"]:
                    try_set(run, f, "checked", " (consent)")
                run.record.setdefault("consented", []).append(f["label"])
            elif not f["required"] and f["value"] and OPT_IN.search(f["label"]):
                clear_field(run, f, "opt-in ticked in a draft")
            elif f["required"] and not f["value"]:
                todo.append(f)
            continue
        if f["type"] == "date":
            if f["required"] and not f["value"]:
                run.record["unanswered"].append(f"{f['label']} (date)")
            continue
        if f["type"] in ("select", "multiselect") and not f["options"]:
            f["options"] = read_options(page, f)
        (prefilled if f["value"] else todo).append(f)

    questions = [{"id": f["id"], "label": f["label"] or f["aid"], "type": f["type"],
                  **({"options": f["options"]} if f["options"] else {})} for f in todo + prefilled]
    answers = run.answers(questions)

    def answer_for(f):
        a = answers.get(f["id"])
        if a in (None, "") and EEO.search(f["label"]):
            a = next((o for o in f["options"] if DECLINE.search(o)), None)
        return None if a in (None, "") else (", ".join(a) if isinstance(a, list) else str(a))

    for f in todo:
        a = answer_for(f)
        if a is None:
            if f["required"]:
                run.record["unanswered"].append(f["label"] or f["aid"])
            continue
        if f["type"] in ("select", "radio", "multiselect") and f["options"] and not pick(f["options"], a):
            a = closest(f["options"], a) or a
        try_set(run, f, a)
    for f in prefilled:   # values already on the form: from a draft, Workday, or the resume parser
        a = answer_for(f)
        if a is None:
            if not f["required"] and f["type"] in ("text", "textarea"):
                clear_field(run, f, f"draft value '{f['value']}' has no grounded answer")
            continue   # required with no answer: keep what's there (often Workday's own default)
        if f["type"] in ("select", "radio", "multiselect", "checkboxes") and f["options"]:
            target = pick(f["options"], a)
            if not target or norm(target) in norm(f["value"]):   # same choice, or wording that maps to no option
                expect(run, f["label"] or f["aid"], f["value"])
                continue
            a = target
        elif same(f, f["value"], a):
            expect(run, f["label"] or f["aid"], f["value"])
            continue
        if f["type"] == "multiselect":   # remove the draft's chips first, or the old choice stays too
            chips = run.page.locator(f"[data-hw='{f['id']}'] {A('selectedItem')}")
            for _ in range(chips.count()):
                x = chips.first.locator("[data-automation-id*='DELETE'], [aria-label*='delete' i], [aria-label*='remove' i], button")
                try:
                    (x.first if x.count() else chips.first).click(timeout=3000)
                except Exception:
                    break
                run.page.wait_for_timeout(400)
        try_set(run, f, a, f" (fixed draft: was '{f['value']}')")
    return n


INVALID_JS = r"""() => [...document.querySelectorAll("[data-automation-id^='formField-']")]
  .filter(f => f.offsetParent && f.querySelector("[aria-invalid=true]"))
  .map(f => { const lab = (f.querySelector('label, legend') || {}).innerText || '';
              return {label: lab.replace(/\s+/g, ' ').replace(/\*\s*$/, '').trim(), required: /\*/.test(lab),
                      aid: f.getAttribute('data-automation-id')}; })"""


def next_disabled(page) -> bool:
    b = page.locator(NEXT).filter(visible=True)
    return b.count() > 0 and (b.first.is_disabled() or b.first.get_attribute("aria-disabled") == "true")


def clear_invalid(run):
    """Next stays disabled while a field holds a value Workday rejects (often left in a saved
    draft). Optional ones are emptied; required ones are reported."""
    page = run.page
    for f in page.evaluate(INVALID_JS):
        if f["required"]:
            run.record["unanswered"].append(f"{f['label']} (invalid value)")
            continue
        el = page.locator(f"[data-automation-id='{f['aid']}'] input, [data-automation-id='{f['aid']}'] textarea").first
        el.fill("")
        el.press("Tab")
        run.record["filled"].append(f"{f['label']} = (cleared: invalid value)")
    page.wait_for_timeout(800)


def next_page(run):
    page = run.page
    before = heading(page)
    if next_disabled(page):
        clear_invalid(run)
    click(page, NEXT)
    settle(page, 1500)
    deadline = time.monotonic() + 20   # Workday often takes a while to save and move on
    while heading(page) == before and not form_errors(page) and time.monotonic() < deadline:
        page.wait_for_timeout(1000)
    if heading(page) == before:   # still on the same step: Workday refused the page
        errs = form_errors(page) or ["stayed on the same page after Next, no error shown"]
        run.record["errors"] += errs
        raise HandOff("form errors", "; ".join(errs[:3]))


def save_review(run):
    """For Telegram: the Review page as a PDF (sharp, zoomable; a full-page photo gets
    downscaled until unreadable) and the top of the page as the photo above Submit/Cancel."""
    run.review_top = run.out / "review-top.png"
    run.page.screenshot(path=str(run.review_top))
    try:   # headless Chromium only
        run.page.emulate_media(media="screen")
        run.page.pdf(path=str(run.out / "review.pdf"), print_background=True, width="1280px",
                     margin={"top": "20px", "bottom": "20px"})
        run.review_pdf = run.out / "review.pdf"
    except Exception as e:
        run.record["errors"].append(f"review PDF not made: {type(e).__name__}")


def review_check(run, contact: dict):
    """Before Submit: every value this run entered or confirmed must appear on the Review page,
    plus name, email and phone. Anything missing blocks Submit (HandOff) and is listed."""
    more = run.page.get_by_text(re.compile(r"^\s*more \(\d+\)\s*$", re.I))   # long lists (Skills) fold
    for i in range(more.count()):
        try:
            more.nth(0).click(timeout=3000)
            run.page.wait_for_timeout(400)
        except Exception:
            break
    body = run.page.locator(f"{A('applyFlowPage')}, body").first.inner_text(timeout=10000)
    text, digits = norm(body), re.sub(r"\D", "", body)
    PROV = {"ontario": "on", "quebec": "qc", "british columbia": "bc", "alberta": "ab", "manitoba": "mb",
            "saskatchewan": "sk", "nova scotia": "ns", "new brunswick": "nb", "newfoundland and labrador": "nl",
            "prince edward island": "pe"}
    wanted = [["Name", f"{contact.get('first_name', '')} {contact.get('last_name', '')}"],
              ["Email", contact.get("email", "")], ["Phone", local_phone(contact.get("phone", ""))]]
    seen, missing = set(), []
    for label, value in wanted + run.record.get("expect", []):
        v = str(value or "").strip()
        if not v or (label, v) in seen or re.search(r"phone code|country code", label, re.I):
            continue   # Review shows the phone code as "+1" inside the number
        seen.add((label, v))
        d = re.sub(r"\D", "", v)
        if len(d) >= 7 and len(d) >= len(re.sub(r"\s", "", v)) - 4:   # phone-like: compare digits
            ok = d in digits
        else:
            ok = norm(v[:60] if len(v) > 80 else v) in text or \
                (norm(v) in PROV and re.search(rf"\b{PROV[norm(v)]}\b", text) is not None)   # Ontario shown as ON
        if not ok:
            missing.append(f"{label}: {v[:60]}")
    run.record["review_checked"] = len(seen)
    if missing:
        run.record["errors"] += [f"not on Review: {m}" for m in missing]
        raise HandOff("review check", f"{len(missing)} value(s) differ on the Review page: " + "; ".join(missing[:4]))


CONFIRMED = re.compile(r"application (has been )?(submitted|received)|thank you for (applying|your application)|"
                       r"successfully (submitted|applied)|you('ve| have) applied|congratulations", re.I)


def submit(run) -> bool:
    """Click Submit once and look for the confirmation. Never clicks twice: if no confirmation
    shows, the caller reports it for a human to check (the employer may already have it)."""
    page = run.page
    if not on_review(page):
        raise HandOff("submit", "not on the Review page any more")
    click(page, NEXT)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        page.wait_for_timeout(2000)
        if CONFIRMED.search(page.locator("body").inner_text(timeout=5000)[:5000]) or not on_review(page) and not form_errors(page):
            settle(page, 1500)
            run.shot("submitted")
            return bool(CONFIRMED.search(page.locator("body").inner_text(timeout=5000)[:5000]))
        if form_errors(page):
            break
    run.shot("submit-unconfirmed")
    return False


def fill(run, resume: Path):
    """Dry run: posting → sign in → every page → stop on Review. Nothing here clicks Submit."""
    contact = run.api.get("/profile/contact").json()
    run.profile = run.api.get("/apply/profile").json()
    versions = run.api.get(f"/jobs/{run.job['id']}/resumes").json()
    run.resume_md = max(versions, key=lambda v: v["version"])["content_md"] if versions else ""
    source = job_source(run.job)
    run.guidance = (f"Fact: Girijesh found this job on {source}. For 'How did you hear about us' (or 'source') "
                    f"pick the option that names {source}; if no option does, pick the company website / "
                    "careers site option for the careers website, otherwise 'Job Board' or 'Other'. Never pick a "
                    "site he didn't use.")
    if contact.get("problems"):
        raise HandOff("contact", "; ".join(contact["problems"]))
    start_application(run)
    run.shot("apply-start")
    sign_in(run)
    for _ in range(MAX_PAGES):
        settle(page := run.page)
        run.check()
        recover(page, run)
        if on_review(page):
            run.record["pages"].append("Review")
            deadline = time.monotonic() + 30   # the summary loads after the frame; Submit enables then
            while next_disabled(page) and time.monotonic() < deadline:
                page.wait_for_timeout(1000)
            settle(page, 1500)
            run.shot("review")
            save_review(run)
            review_check(run, contact)
            return
        fill_page(run, contact, resume)
        next_page(run)
    raise HandOff("stuck", f"no Review page after {MAX_PAGES} pages")
