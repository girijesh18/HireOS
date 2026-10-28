"""Greenhouse, Lever and Ashby application forms for the apply worker.

One page, no account. Fill every field from contact facts (no model) or HireOS answers (profile and
resume only), upload the resume, re-read the whole form and check each value stuck, then hand the
filled form to Telegram for Submit. Nothing here clicks Submit until `submit` is called.
"""
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from apply_worker import HandOff, captcha_challenge
from workday import CONFIRMED, CONSENT, DECLINE, EEO, HONEYPOT, OPT_IN, closest, job_source, pick, save_review

PLATFORMS = ("greenhouse", "lever", "ashby")
SUBMIT = re.compile(r"^\s*submit( (your )?application)?\s*$", re.I)
DONE_URL = re.compile(r"/(confirmation|thanks|thank-you|success)\b", re.I)
SETTLE_S = 20   # after the user's answer, how long the bot check may stay up before asking again
# Greenhouse's fallback when its invisible reCAPTCHA fails: an emailed code "to confirm you're a human"
EMAIL_CODE = re.compile(r"verification code was sent to|enter the \d+-character code", re.I)
ERRORS = "[role=alert]:visible, .error:visible, .field-error:visible, .helper-text--error:visible, [class*='_error_']:visible"

# Contact facts by label (no model): (label pattern, contact key)
CONTACT = [
    (r"^first name|^given name|^legal first name", "first_name"),
    (r"^last name|^family name|^surname|^legal last name", "last_name"),
    (r"^(full )?name$|^your name$", "name"),
    (r"^e-?mail", "email"),
    (r"^phone|^mobile|^telephone", "phone"),
    (r"linkedin", "linkedin"),
    (r"github", "github"),
    (r"^(portfolio|website|personal (site|website))", "website"),
]
PREFERRED = re.compile(r"preferred (first |given )?name|nickname", re.I)   # from the profile, else left blank
COVER = re.compile(r"cover", re.I)
# Voluntary self-identification the profile answers (eeo_gender, eeo_race_ethnicity, eeo_disability, ...):
# optional ones are answered only when they ask one of these; anything else (pronouns) stays blank.
PROFILE_EEO = {"gender": re.compile(r"gender|\bsex\b", re.I), "race_ethnicity": re.compile(r"ethnic|race|racial|categories describe", re.I),
               "hispanic_or_latino": re.compile(r"hispanic|latin", re.I), "disability": re.compile(r"disab", re.I),
               "indigenous": re.compile(r"indigenous|aboriginal|first nations", re.I), "veteran_status": re.compile(r"veteran|military", re.I)}
HEARD = re.compile(r"how did you (hear|find|learn|come across)|where did you (hear|find|see)|^(job |application )?source\b|referral source", re.I)
LOCATION = re.compile(r"^(current )?location( \(city\))?$|^city$|^where are you (located|based)", re.I)

QUESTIONS_JS = r"""() => {
  const txt = el => (el && el.innerText || '').replace(/\s+/g, ' ').trim();
  const bare = el => {   // an element's text without the form controls inside it (a <label> wrapping a <select>)
    if (!el) return '';
    const c = el.cloneNode(true);
    c.querySelectorAll('select, option, input, textarea, [role=listbox]').forEach(x => x.remove());
    return (c.textContent || '').replace(/\s+/g, ' ').trim();
  };
  const shown = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const optLabel = el => bare(el.labels && el.labels[0]) || bare(el.closest('label')) || el.value || '';
  const ownLabel = el => (el.id && bare(document.querySelector(`label[for="${CSS.escape(el.id)}"]`)))
    || (el.getAttribute('aria-labelledby') || '').split(' ').map(i => bare(document.getElementById(i))).join(' ').trim()
    || bare(el.closest('label')) || (el.getAttribute('aria-label') || '').trim();
  // A question's title: the nearest label-like element before the input that isn't an option's
  // own label (Ashby: label.question-title with for= the question; Lever: .application-label).
  const isOpt = (l, els) => els.some(e => (e.labels && [...e.labels].includes(l)) || l.contains(e));
  const titleOf = (els) => {
    let a = els[0].parentElement;
    for (let i = 0; a && i < 7; i++, a = a.parentElement) {
      const cands = [...a.querySelectorAll('legend, label, .application-label, [class*=question-title], [class*=Label], [class*=label]')]
        .filter(l => !isOpt(l, els) && !l.querySelector('input, select, textarea') && bare(l)
                     && (l.compareDocumentPosition(els[0]) & Node.DOCUMENT_POSITION_FOLLOWING));
      if (cands.length) return cands[cands.length - 1];
    }
    return null;
  };
  const questionOf = (els) => {
    const fs = els[0].closest('fieldset');
    if (fs && fs.querySelector('legend')) return bare(fs.querySelector('legend'));
    const lq = els[0].closest('.application-question');
    if (lq && lq.querySelector('.application-label, .text')) return bare(lq.querySelector('.application-label, .text'));
    const t = titleOf(els);
    return t ? bare(t) : '';
  };
  const titleRequired = (els) => {   // a single input's own label is its title; a group's is the label before it
    const own = els.length === 1 && !['radio', 'checkbox'].includes(els[0].type) && els[0].labels && els[0].labels[0];
    const t = own || titleOf(els);
    return !!t && (/required/i.test(t.className || '') || !!t.querySelector('[class*=required]'));
  };
  const sel = el => el.id ? `[id="${CSS.escape(el.id)}"]` : el.name ? `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]` : null;
  const star = t => /[*✱]/.test(t || '');
  const clean = t => (t || '').split(/\s*[*✱]/)[0].trim().slice(0, 300);   // "Location ✱No location found…" -> "Location"
  document.querySelectorAll('[data-hw]').forEach(e => e.removeAttribute('data-hw'));
  const out = [], groups = new Map();
  let n = 0, lone = 0;
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const t = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'search', 'image', 'reset'].includes(t)) continue;
    if (/g-recaptcha|h-captcha/.test(el.name || '') || /requiredInput/.test(el.className || '')) continue;
    if (t === 'radio' || t === 'checkbox') {
      // grouped by name, else by fieldset; a lone box with neither is its own question
      const key = el.name ? 'n:' + el.name : el.closest('fieldset') ? 'fs' + [...document.querySelectorAll('fieldset')].indexOf(el.closest('fieldset')) : 'el' + (lone++);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(el);
      continue;
    }
    const lab = el.labels && el.labels[0] ? el.labels[0] : null;
    if (t === 'file') {
      const id = 'q' + (n++); el.setAttribute('data-hw', id);
      const label = ownLabel(el) || questionOf([el]);
      out.push({id, sel: sel(el), key: (el.id || el.name || '').toLowerCase(), type: 'file', label: clean(label), required: el.required || star(label), value: el.files && el.files.length ? 'attached' : '', options: []});
      continue;
    }
    if (!shown(el)) continue;
    const id = 'q' + (n++); el.setAttribute('data-hw', id);
    const raw = ownLabel(el) || questionOf([el]);
    const required = el.required || el.getAttribute('aria-required') === 'true' || star(raw) || star(txt(lab))
      || /required/i.test((lab && lab.className) || '') || titleRequired([el]);
    let type = el.tagName === 'TEXTAREA' ? 'textarea' : el.tagName === 'SELECT' ? 'select' : 'text', value = el.value || '', options = [];
    if (type === 'select') {
      options = [...el.options].map(o => o.text.trim()).filter(o => o && !/^(select|choose|please select)\b/i.test(o) && o !== '--');
      value = el.selectedIndex > 0 || (el.value && options.includes(el.options[el.selectedIndex].text.trim())) ? el.options[el.selectedIndex].text.trim() : '';
    } else if (/select__input/.test(el.className) || el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete')) {
      type = 'combo';
      const box = el.closest('.select__container, .select, [class*=select-shell], [class*=_container_]') || el.parentElement;
      const single = box && box.querySelector('.select__single-value, [class*=singleValue], .select__multi-value__label');
      value = single ? [...box.querySelectorAll('.select__single-value, [class*=singleValue], .select__multi-value__label')].map(txt).join(', ') : (el.value || '');
    }
    out.push({id, sel: sel(el), type, label: clean(raw), required, value, options});
  }
  for (const [key, els] of groups) {
    const vis = els.filter(e => shown(e) || (e.labels && e.labels[0] && shown(e.labels[0])) || (e.closest('label') && shown(e.closest('label'))));
    if (!vis.length) continue;
    const id = 'q' + (n++);
    els.forEach((e, i) => { e.setAttribute('data-hw', id); e.setAttribute('data-hw-i', String(i)); });
    const t = els[0].type;
    const single = t === 'checkbox' && els.length === 1;
    const q = single ? (optLabel(els[0]) || questionOf(els)) : questionOf(els);
    const required = els.some(e => e.required || e.getAttribute('aria-required') === 'true') || star(q) || titleRequired(els);
    const options = single ? ['checked', 'unchecked'] : els.map(optLabel);
    const value = single ? (els[0].checked ? 'checked' : '') : els.filter(e => e.checked).map(optLabel).join(', ');
    out.push({id, sels: els.map(sel), type: single ? 'checkbox' : t === 'radio' ? 'radio' : 'checkboxes', label: clean(q), required, value, options});
  }
  return out;
}"""


def apply_url(platform: str, url: str) -> str:
    u = url.split("#")[0].rstrip("/")
    if platform == "lever" and not u.endswith("/apply"):
        return u + "/apply"
    if platform == "ashby" and not u.endswith("/application"):
        return u + "/application"
    return url


def fields(run) -> list:
    return run.page.evaluate(QUESTIONS_JS)


def loc(run, f: dict, i: int = None):
    """The field's element (or option i of a group): by id/name when it has one, so a React
    re-render (which drops our data-hw tags) doesn't lose it; else by the tag."""
    if i is None and f.get("sel"):
        return run.page.locator(f["sel"]).first
    if i is not None and f.get("sels") and f["sels"][i] and "[id=" in f["sels"][i]:
        return run.page.locator(f["sels"][i]).first
    if i is not None and f.get("sels") and f["sels"][i]:          # radios share a name: the i-th of them
        return run.page.locator(f["sels"][i]).nth(i)
    sel = f"[data-hw='{f['id']}']" + (f"[data-hw-i='{i}']" if i is not None else "")
    return run.page.locator(sel).first


def click_option(run, f: dict, i: int):
    """Tick a radio/checkbox like a person: its label (custom-styled inputs are hidden)."""
    el = loc(run, f, i)
    el_id = el.get_attribute("id")
    label = run.page.locator(f"label[for='{el_id}']") if el_id else el.locator("xpath=ancestor::label[1]")
    if label.count() and label.first.is_visible():
        label.first.click(timeout=5000)
    else:
        el.check(force=True, timeout=5000)


def combo_options(run, f: dict, typed: str = "") -> list:
    """Open a searchable dropdown (React Select and the like), optionally type, read its options."""
    el = loc(run, f)
    el.click(timeout=5000)
    if typed:
        el.fill("")
        el.type(typed, delay=40)
    run.page.wait_for_timeout(1500 if typed else 700)
    opts = run.page.locator(".select__option:visible, [role=option]:visible")
    return [t.strip() for t in opts.all_inner_texts() if t.strip()]


def set_value(run, f: dict, value: str) -> bool:
    page, t = run.page, f["type"]
    if t in ("text", "textarea"):
        el = loc(run, f)
        el.fill(value, timeout=5000)
        el.press("Tab")
        return same(el.input_value(), value)          # a phone widget reformats the digits
    if t == "select":
        target = pick(f["options"], value) or closest(f["options"], value)
        if not target:
            return False
        loc(run, f).select_option(label=target, timeout=5000)
        return True
    if t == "combo":
        typed = value if LOCATION.search(f["label"]) else ""
        options = combo_options(run, f, typed)
        if not options and not typed:      # options load only after typing (async search)
            options = combo_options(run, f, value)
        target = pick(options, value) or closest(options, value) or (options[0] if typed and options else None)
        if not target:
            page.keyboard.press("Escape")
            return False
        page.locator(".select__option:visible, [role=option]:visible").filter(has_text=target).first.click(timeout=5000)
        page.wait_for_timeout(400)
        return True
    if t == "checkbox":
        if value == "checked":
            click_option(run, f, 0)
        return True
    if t in ("radio", "checkboxes"):
        wanted = [w for w in ([value] if t == "radio" else [v.strip() for v in value.split(",")]) if w.strip()]
        done = False
        for w in wanted:
            target = pick(f["options"], w) or closest(f["options"], w)
            if target:
                click_option(run, f, f["options"].index(target))
                done = True
        return done
    return False


def heard_option(options: list, source: str):
    """"How did you hear about us" is a fact, not a judgement: the option naming where the job was
    really found (job_source). None when no option fits; the model then gets the question."""
    site = None if source.startswith("the company") else source
    def score(o):
        lo = o.lower()
        if site:
            return (3 if site.lower() in lo else 0) + (1 if re.search(r"job|posting|board", lo) else 0) \
                - (5 if re.search(r"not a job|post or content", lo) else 0)
        return 3 if re.search(r"company('s)? (web)?site|careers? (page|site|website)|\bwebsite\b", lo) else 0
    best = max(options, key=score, default=None)
    return best if best and score(best) >= 3 else None


def profile_eeo(run, label: str):
    """The profile's own answer to a voluntary self-identification question, if it has one."""
    prof = getattr(run, "profile", None) or {}
    return next((prof.get(f"eeo_{k}") for k, pat in PROFILE_EEO.items() if pat.search(label) and prof.get(f"eeo_{k}")), None)


def cover_letter(run, wait_s: int = 600):
    """The job's cover letter PDF from HireOS: the latest one, else a newly generated one."""
    jid = run.job["id"]

    def latest():
        versions = [v for v in run.api.get(f"/jobs/{jid}/cover-letters").json() if v.get("pdf_path")]
        return max(versions, key=lambda v: v["version"]) if versions else None
    try:
        v = latest()
        if not v:
            run.api.post(f"/agent/cover-letter/{jid}", {})
            deadline = time.monotonic() + wait_s
            while not v and time.monotonic() < deadline:
                time.sleep(10)
                v = latest()
        if not v:
            run.record["errors"].append("cover letter not generated in time")
            return None
        name = Path(v["pdf_path"]).name
        path = run.out / name
        path.write_bytes(run.api.get(f"/download/{jid}/{name}").content)
        return path
    except Exception as e:
        run.record["errors"].append(f"cover letter: {type(e).__name__}: {str(e)[:120]}")
        return None


def contact_value(f: dict, contact: dict):
    label = f["label"].lower()
    for pat, key in CONTACT:
        if re.search(pat, label) and contact.get(key):
            return contact[key]
    if LOCATION.search(f["label"]) and contact.get("city"):
        return contact["city"]
    return None


def fill_form(run, contact: dict, resume: Path):
    page = run.page
    fs = fields(run)
    todo = []
    for f in fs:
        label = f["label"]
        if f["type"] == "file":
            what = f"{label} {f.get('key', '')}"           # Greenhouse labels both file inputs just "Attach"
            if re.search(r"autofill|auto-fill|parse", what, re.I):
                continue      # "autofill from resume": the site's parser would pre-fill fields we never check
            if COVER.search(what):
                letter = cover_letter(run)
                if letter:
                    loc(run, f).set_input_files(str(letter))
                    page.wait_for_timeout(1500)
                    run.record["filled"].append(f"{label or 'Cover letter'} = {letter.name}")
                    run.record["cover_letter"] = letter.name
                elif f["required"]:
                    run.record["unanswered"].append(f"{label or 'Cover letter'} (file)")
                continue
            if re.search(r"resume|\bcv\b", what, re.I) or f["required"]:
                loc(run, f).set_input_files(str(resume))
                page.wait_for_timeout(2500)          # the upload, and any resume parsing that pre-fills fields
                run.record["filled"].append(f"{label or 'Resume'} = {resume.name}")
            elif f["required"]:
                run.record["unanswered"].append(f"{label} (file)")
            continue
        if PREFERRED.search(label) and f["type"] == "text":
            if contact.get("preferred_name"):
                f["want"], f["source"] = contact["preferred_name"], " (profile)"
            continue
        if HONEYPOT.search(label) or (not f["required"] and OPT_IN.search(label)):
            continue
        cv = contact_value(f, contact)
        if cv:
            f["want"], f["source"] = cv, " (contact)"
            continue
        if f["type"] == "checkbox" and f["required"] and CONSENT.search(label):
            f["want"], f["source"] = "checked", " (consent)"
            run.record.setdefault("consented", []).append(label)
            continue
        if not f["required"] and f["type"] == "checkbox":
            continue      # optional ticks are opt-ins ("contact me about future jobs")
        if not f["required"] and EEO.search(label) and not profile_eeo(run, label):
            continue      # voluntary question the profile doesn't answer (pronouns): left blank
        if f["type"] == "combo" and not f["options"]:
            try:
                f["options"] = combo_options(run, f)[:60]
            except Exception:
                f["options"] = []
            page.keyboard.press("Escape")
        if HEARD.search(label) and f["options"]:
            heard = heard_option(f["options"], job_source(run.job))
            if heard:
                f["want"], f["source"] = heard, " (where the job was found)"
                continue
        todo.append(f)

    answers = run.answers([{"id": f["id"], "label": f["label"], "type": "select" if f["type"] == "combo" else f["type"],
                            **({"options": f["options"]} if f["options"] else {})} for f in todo])
    for f in todo:
        a = answers.get(f["id"])
        a = (", ".join(str(x) for x in a if str(x).strip()) if isinstance(a, list) else str(a or "")).strip()
        if not a and EEO.search(f["label"]) and profile_eeo(run, f["label"]) and f["options"]:
            a = closest(f["options"], profile_eeo(run, f["label"])) or ""   # profile "No" -> "No, I do not have a disability"
        if not a and EEO.search(f["label"]) and f["required"]:
            a = next((o for o in f["options"] if DECLINE.search(o)), "")
        if a:
            f["want"], f["source"] = a, ""
        elif f["required"] and not f["value"]:
            run.record["unanswered"].append(f["label"] or f["id"])

    # Re-scan before typing: the resume upload may have re-rendered the form (new ids).
    wanted = {(f["label"], f["type"]): f for f in fs if f.get("want")}
    for f in fields(run):
        w = wanted.get((f["label"], f["type"]))
        if not w:
            continue
        f.update(want=w["want"], source=w["source"])
        if f["value"] and same(f["value"], f["want"], picked=f["type"] in ("combo", "select", "radio", "checkboxes")):
            run.record["filled"].append(f"{f['label']} = {f['value']} (already on the form)")
            continue
        try:
            loc(run, f).scroll_into_view_if_needed(timeout=5000)
            ok = set_value(run, f, f["want"])
        except Exception as e:
            ok, f["want"] = False, f"{f['want']}; {type(e).__name__}"
        run.record["filled" if ok else "unanswered"].append(
            f"{f['label']} = {f['want']}{f['source']}" if ok else f"{f['label']} (value didn't stick: {f['want']})")
        if ok and f["type"] != "checkbox":
            run.record.setdefault("expect", []).append([f["label"], f["want"]])


def norm(x) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(x or "").lower()).strip()


def same(current: str, wanted: str, picked: bool = False) -> bool:
    """`picked`: chosen from the field's own list, which may show only part of the option
    (Greenhouse shows "Canada +1" as "+1")."""
    c, w = norm(current), norm(wanted)
    if picked and c and (c in w or w in c):
        return True
    d1, d2 = re.sub(r"\D", "", str(current)), re.sub(r"\D", "", str(wanted))
    if len(d2) >= 7 and len(d2) >= len(re.sub(r"\s", "", str(wanted))) - 4:      # phone-like: compare digits
        return d2[-10:] in d1
    return bool(c) and (c == w or w in c or (len(c) > 3 and c in w))


def check_form(run):
    """Before Submit: re-read the form. Every value entered must still be there, and no required
    field may be empty. Anything wrong blocks Submit (HandOff) and is listed."""
    fs = {f["label"]: f for f in fields(run)}
    missing, checked = [], 0
    for label, value in run.record.get("expect", []):
        f = fs.get(label)
        checked += 1
        if not f or not same(f["value"], value, picked=f["type"] in ("combo", "select", "radio", "checkboxes")):
            missing.append(f"{label}: wanted '{str(value)[:50]}', form shows '{(f or {}).get('value', '')[:50]}'")
    empty = [f["label"] or f["id"] for f in fs.values() if f["required"] and not f["value"]
             and f["type"] != "file" and not HONEYPOT.search(f["label"])]
    files = [f for f in fs.values() if f["type"] == "file" and f["required"] and re.search(r"resume|\bcv\b", f"{f['label']} {f.get('key', '')}", re.I)]
    run.record["review_checked"] = checked
    if missing:
        run.record["errors"] += [f"not on the form: {m}" for m in missing]
        raise HandOff("review check", f"{len(missing)} value(s) didn't stick: " + "; ".join(missing[:4]))
    if empty:
        raise HandOff("unanswered", "required with no grounded answer: " + "; ".join(empty[:6]))
    if files and not any("resume" in x.lower() or x.endswith(".pdf") for x in run.record["filled"]):
        raise HandOff("unanswered", "the resume wasn't attached")


def open_form(run):
    page = run.page
    page.goto(apply_url(run.platform, run.job["url"]), wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    frame = page.locator("iframe#grnhse_iframe")           # Greenhouse embedded on the company's own site
    if frame.count():
        page.goto(frame.first.get_attribute("src"), wait_until="networkidle", timeout=60000)
    body = page.locator("body").inner_text(timeout=10000)[:3000]
    if re.search(r"no longer (accepting|available|open)|job (is )?(closed|not found)|position has been filled|page (you('re| are) looking for )?(can.t be|was not) found", body, re.I):
        raise HandOff("closed", "the posting is closed")
    if run.platform == "greenhouse":                        # the form sits below the description
        btn = page.get_by_role("button", name=re.compile(r"^apply( for this job| now)?$", re.I))
        if btn.count() and btn.first.is_visible():
            btn.first.click()
    page.wait_for_timeout(1500)
    if not page.locator("input[type=file], input[type=email], textarea").count():
        raise HandOff("stuck", "no application form on the page")


def fill(run, resume: Path):
    """Open the form, fill it, check it, save it for Telegram. Nothing here clicks Submit."""
    contact = run.api.get("/profile/contact").json()
    run.profile = run.api.get("/apply/profile").json()
    if contact.get("problems"):
        raise HandOff("contact", "; ".join(contact["problems"]))
    source = job_source(run.job)
    run.guidance = (f"Fact: the candidate found this job on {source}. For 'How did you hear about us' (or 'source') "
                    f"pick the option that names {source}; if no option does, the company website / careers site "
                    "option, otherwise 'Job Board' or 'Other'. Never pick a site the candidate didn't use.")
    open_form(run)
    run.check()
    run.shot("form")
    run.record["pages"].append("Application")
    fill_form(run, contact, resume)
    page = run.page
    page.wait_for_timeout(1000)
    run.check()
    check_form(run)
    run.shot("review")
    save_review(run)


def submit(run) -> bool:
    """Click Submit and look for the confirmation. Clicks again only to send a security code."""
    page = run.page
    btn = page.get_by_role("button", name=SUBMIT)
    if not btn.count():
        btn = page.locator("input[type=submit], button[type=submit]")
    if not btn.count():
        raise HandOff("submit", "no Submit button on the form")
    start_url = page.url
    btn.first.click(timeout=10000)
    deadline, settle, tries = time.monotonic() + 60, 0, 0
    while time.monotonic() < deadline:
        page.wait_for_timeout(2000)
        text = page.locator("body").inner_text(timeout=5000)
        if CONFIRMED.search(text[:5000]) or (page.url != start_url and DONE_URL.search(urlparse(page.url).path)):
            page.wait_for_timeout(1500)
            run.shot("submitted")
            return True
        # A bot check after Submit: a CAPTCHA, or Greenhouse's emailed code (its CAPTCHA fallback)
        check = "captcha" if captcha_challenge(page) else "email code" if EMAIL_CODE.search(text) else None
        if check and time.monotonic() > settle:   # settle: give the last answer time to go through
            if tries == 3:
                raise HandOff(check, f"after Submit, three tries didn't get past the {check} — nothing was sent. "
                                     f"Apply by hand: {run.job['url']}")
            tries += 1
            run.shot(f"submit-{check}")
            bot_check(run, check, btn, retry=tries > 1)
            deadline, settle = time.monotonic() + 60, time.monotonic() + SETTLE_S
        elif not check and page.locator(ERRORS).count() and page.url == start_url:
            break
    run.shot("submit-unconfirmed")
    return False


def bot_check(run, check: str, btn, retry: bool):
    """The user gets past it, never the worker: a CAPTCHA is done by them on the screen; the emailed
    code they read in their own mail and reply with (or enter on the screen). HandOff on skip / no answer."""
    url, screen = run.job["url"], getattr(run, "screen", "")
    if check == "captcha":
        if not screen:
            raise HandOff("captcha", f"after Submit a CAPTCHA appeared — nothing was sent. Apply by hand: {url}")
        got = run.ask("wait", "after Submit a CAPTCHA appeared — nothing is sent until it's done. Open the "
                              "screen, finish it there (press Submit again if asked), then tap Done.")
    else:
        got = run.ask("code", ("That code didn't go through. " if retry else "")
                      + "After Submit, Greenhouse emailed you a security code — nothing is sent until it's entered."
                      + (f" Or enter it yourself on the screen ({screen}), then reply done." if screen else ""))
    if got == "cancel":
        raise HandOff("cancelled", "skipped by you")
    if got == "timeout":
        raise HandOff(check, f"after Submit, no answer within 15 min — nothing was sent. Apply by hand: {url}")
    if check == "email code" and got.strip().lower() != "done":
        enter_code(run.page, got)
        btn.first.click(timeout=10000)


def enter_code(page, code: str):
    """Greenhouse's code boxes: one character each (#security-input-0..7). A code of the wrong length
    is left out, so the page still asks and the user is asked again."""
    code = re.sub(r"\s+", "", code)
    boxes = page.locator("#email-verification input[id^='security-input-']")
    if boxes.count() and boxes.count() == len(code):
        for i, ch in enumerate(code):
            boxes.nth(i).fill(ch)
