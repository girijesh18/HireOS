"""Targeted resume edits from a plain-language request ("make the summary mention LLM agents").

The model never rewrites the resume. It returns exact find/replace patches, and the
patches are applied here, so text outside them cannot change. Each patch is then
checked against the facts:
  - the name/contact header, section headings and company/date lines stay as they are
  - no number, tool or name that is not already in the master resume (no new metrics,
    no skills lifted from the job description)
  - the title rule ("Lead Data Scientist") is not weakened, banned words stay out
Then a second, fresh-context model read lists any claim the master resume doesn't
support (phrases built from real words that say something new). A rejected edit
changes nothing; the reason goes back to the user.
"""
import json
import re
from datetime import date
from typing import Dict, List, Tuple

from resume_profile import numbers, banned_hits, parroted


class EditError(ValueError):
    """The edit was refused (asks for unsupported facts) or failed the checks."""


def _header_end(md: str) -> int:
    m = re.search(r'^## ', md, re.M)
    return m.start() if m else 0


def apply_patches(md: str, edits: List[Dict]) -> str:
    """Apply exact find/replace patches. Every find must occur exactly once and
    lie below the header; overlapping or ambiguous patches are refused."""
    if not edits:
        raise EditError("no change was proposed")
    spans = []
    for e in edits:
        find = e.get("find") or ""
        if not find.strip():
            raise EditError("a patch has an empty 'find'")
        n = md.count(find)
        if n != 1:
            raise EditError(f"patch text {'not found' if n == 0 else f'found {n} times'}: {find[:80]!r}")
        i = md.index(find)
        if i < _header_end(md):
            raise EditError("the name/contact header is managed separately and can't be edited")
        spans.append((i, i + len(find), e.get("replace") or ""))
    spans.sort()
    for (a0, a1, _), (b0, _, _) in zip(spans, spans[1:]):
        if b0 < a1:
            raise EditError("two patches overlap")
    out, pos = [], 0
    for s, t, rep in spans:
        out += [md[pos:s], rep]
        pos = t
    return "".join(out + [md[pos:]])


_TERM = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*[A-Za-z0-9+#]")


def _plain(s: str) -> str:
    return re.sub(r"[*_`]", "", s)


def new_terms(replace: str, find: str, master: str) -> List[str]:
    """Names, tools and acronyms in the replacement that neither the replaced text
    nor the master resume contains (PyTorch, AWS, GPT-4, Kubernetes). Ordinary
    lowercase rewording and sentence-initial capitals are allowed."""
    known = (_plain(find) + " " + _plain(master)).lower()
    out = []
    for sentence in re.split(r"(?<=[.;:!?])\s+|\n", _plain(replace)):
        words = _TERM.findall(re.sub(r"^\s*[-•]\s*", "", sentence))
        for k, w in enumerate(words):
            special = bool(re.search(r"[A-Z].*[A-Z]|\d|[+#]", w)) or (k > 0 and w[0].isupper())
            if special and not re.search(rf"(?<![a-z0-9]){re.escape(w.lower())}(?![a-z0-9])", known):
                out.append(w)
    return sorted(set(out))


def check(old_md: str, new_md: str, edits: List[Dict], master: str, jd: str, banned: List[str]) -> List[str]:
    """Problems with an applied edit; empty means it keeps every fact intact."""
    problems = []
    if new_md[:_header_end(new_md)] != old_md[:_header_end(old_md)]:
        problems.append("the header changed")
    structural = lambda md: [l for l in md.splitlines() if l.startswith(("## ", "### "))]
    lost = [l for l in structural(old_md) if l not in structural(new_md)]
    if lost:
        problems.append(f"section/company/date lines must stay unchanged: {lost[0][:80]!r}")
    for e in edits:
        find, rep = e.get("find", ""), e.get("replace", "")
        extra = numbers(rep) - numbers(find) - numbers(master)
        if extra:
            problems.append(f"new numbers not in your resume: {', '.join(sorted(extra))}")
        terms = new_terms(rep, find, master)
        if terms:
            problems.append(f"new names/tools not in your resume: {', '.join(terms)}")
        # Stricter than generation's allowance of 2: a reviewer asking to "emphasise X"
        # must not turn X into a claim. If it's true, it belongs in the master resume first.
        lifted = parroted(rep, jd, find + " " + master)
        if lifted:
            problems.append(f"claims from the job description that your master resume doesn't support: "
                            f"{', '.join(lifted)} (add them to the master resume first if they're true)")
    hits = banned_hits(new_md, banned)
    if hits and not banned_hits(old_md, banned):
        problems.append(f"banned words: {', '.join(hits)}")
    if old_md.lower().count("lead data scientist") > new_md.lower().count("lead data scientist"):
        problems.append('the "Lead Data Scientist" title/opener was removed')
    return problems


PROMPT = """You edit one resume. Apply the user's request and nothing else.

REQUEST: {instruction}

CURRENT RESUME (markdown):
{resume}

MASTER RESUME — the only source of facts. Any number, tool, company, title or date you
write must already appear here or in the current resume:
{master}

JOB DESCRIPTION (context only — never claim a skill from it that the master resume lacks):
{jd}
{style}{feedback}
Reply with JSON only:
{{"edits": [{{"find": "<exact text copied from CURRENT RESUME>", "replace": "<new text>"}}],
  "cannot": "<why, if the request needs facts the master resume does not have; else empty>"}}
Rules: `find` is copied character-for-character and is unique; keep it as short as the
change allows (one bullet or sentence). Keep the markdown style of the text you replace
(`- **Label:** text`, `**bold**` metrics). Never touch the header, `## ` or `### ` lines."""


VERIFY = """You check one resume edit for invented or overstated claims. You did not write it.

SOURCE FACTS (the candidate's master resume and the text being replaced):
{source}

NEW TEXT:
{new}

List every claim in NEW TEXT that SOURCE FACTS do not support: a skill, domain, kind of
experience, specialisation, seniority, scope, tool or number. "Insurance claims routing" in
the source does not support "insurance risk modelling"; "built a model" does not support
"led the modelling team". Pure rewording of a supported fact is fine. Removing facts is fine.
Anything SOURCE FACTS already state (e.g. "8 years") is supported as written: never recompute
it from dates. Today is {today}; "Present" means today.
Reply with JSON only: {{"unsupported": ["<claim> — <why>", ...]}}  (empty list if none)"""


async def unsupported_claims(router, edits: List[Dict], master: str, llm: str, loads=json.loads) -> List[str]:
    """Fresh-context second read: catches claims assembled from words that each exist
    in the resume but say something it doesn't (the word checks can't see those)."""
    new = "\n".join(e.get("replace", "") for e in edits)
    source = master[:14000] + "\n\nTEXT BEING REPLACED:\n" + "\n".join(e.get("find", "") for e in edits)
    raw = await router.complete(VERIFY.format(source=source, new=new, today=date.today().isoformat()), llm=llm, temperature=0, max_tokens=800)
    try:
        out = loads(re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip())
    except ValueError:
        return ["the fact check could not read its own answer — refusing to be safe"]
    return [str(x) for x in (out.get("unsupported") or []) if str(x).strip()]


async def edit(router, md: str, instruction: str, master: str, jd: str, banned: List[str],
               llm: str, loads=json.loads, style: str = "") -> Tuple[str, List[Dict]]:
    """→ (new markdown, patches). Two attempts; the second sees why the first failed."""
    feedback = ""
    for attempt in range(2):
        raw = await router.complete(
            PROMPT.format(instruction=instruction, resume=md, master=master[:14000],
                          jd=(jd or "")[:4000], feedback=feedback,
                          style=f"\nTHE USER'S STANDING RULES (keep the edited text compliant):\n{style}\n" if style.strip() else ""),
            llm=llm, temperature=0.1, max_tokens=3000)
        try:
            out = loads(re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip())
        except ValueError:
            feedback = "\nYour last reply was not valid JSON. Reply with the JSON object only.\n"
            continue
        if (out.get("cannot") or "").strip() and not out.get("edits"):
            raise EditError(f"can't do that without inventing facts: {out['cannot'].strip()}")
        try:
            new_md = apply_patches(md, out.get("edits") or [])
            problems = check(md, new_md, out["edits"], master, jd, banned)
        except EditError as e:
            problems = [str(e)]
        if not problems:
            problems = [f"not supported by your resume: {c}" for c in
                        await unsupported_claims(router, out["edits"], master, llm, loads)]
        if not problems:
            return new_md, out["edits"]
        feedback = "\nYour last patches were rejected: " + "; ".join(problems) + ". Fix that.\n"
    raise EditError("; ".join(problems) if 'problems' in locals() else "the model gave no usable edit")
