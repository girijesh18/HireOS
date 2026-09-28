"""Model-proof resume generation: facts and layout in code, wording by the LLM.

The old flow asked the model to rewrite the whole resume as markdown. Every run
could move a date, split a company, invent a heading or break the markup -- and
open models did all of those. Here:

  1. The master resume is parsed ONCE into a `Profile` (companies, roles,
     dates, bullets, verbatim sections). The user can correct it in Settings;
     every generation reuses it.
  2. The LLM only answers small JSON questions: which bullets matter for this
     job, reworded how, and a tailored summary. Schema-constrained where the
     provider supports it, validated in code always.
  3. Anything the LLM gets wrong (new numbers, banned words, bad ids) is
     re-asked once, then falls back to the master wording. One click never
     fails on content.
  4. Markdown is rendered here, in the exact grammar doc_generator expects.
"""
import asyncio
import hashlib
import re
from typing import Callable, Dict, List, Optional, Tuple

from loguru import logger
from pydantic import BaseModel, Field


# ── Model ──────────────────────────────────────────────────────────────────────

class Bullet(BaseModel):
    id: str
    text: str                      # original markdown, e.g. "**Label:** body with **metric**"


class Role(BaseModel):
    id: str
    title: str = ""
    dates: str = ""
    blurb: str = ""
    bullets: List[Bullet] = []


class Company(BaseModel):
    name: str
    dates: str = ""
    roles: List[Role] = []


class SkillGroup(BaseModel):
    label: str
    items: List[str]


class Section(BaseModel):
    heading: str
    kind: str                      # summary | experience | skills | verbatim
    lines: List[str] = []          # verbatim sections only


class Profile(BaseModel):
    name: str
    contact: str = ""
    summary: str = ""
    experience: List[Company] = []
    skills: List[SkillGroup] = []
    sections: List[Section] = []   # document order
    source_hash: str = ""

    @property
    def headline(self) -> str:
        """Current title -- the most recent role's."""
        for c in self.experience:
            for r in c.roles:
                if r.title:
                    return r.title
        return ""

    def roles(self) -> List[Role]:
        return [r for c in self.experience for r in c.roles]


# ── Parsing ────────────────────────────────────────────────────────────────────

_MONTH = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?'
_DATE = rf'(?:{_MONTH}\s+)?\d{{4}}'
_DATE_CELL = re.compile(
    rf'^\(?{_DATE}(?:\s*(?:–|—|-|to)\s*(?:{_DATE}|Present|Current|Now))?\)?$', re.I)
_TITLE_WORDS = re.compile(
    r'\b(scientist|engineer|intern|developer|manager|lead|analyst|architect|consultant|'
    r'director|researcher|associate|specialist|head|officer|founder|co-op|fellow|assistant)\b', re.I)
_SECTION_WORDS = re.compile(
    r'SUMMARY|PROFILE|OBJECTIVE|EXPERIENCE|EMPLOYMENT|SKILL|EXPERTISE|TECHNOLOG|EDUCATION|'
    r'PUBLICATION|PROJECT|OPEN SOURCE|CERTIFICATION|AWARD|HONOR|HONOUR|LEADERSHIP|VOLUNTEER|'
    r'LANGUAGE|INTEREST|ACHIEVEMENT|ACTIVIT', re.I)
_EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')


def _kind(heading: str) -> str:
    h = heading.upper()
    if re.search(r'SUMMARY|PROFILE|OBJECTIVE|ABOUT', h):
        return "summary"
    if re.search(r'EXPERIENCE|EMPLOYMENT|WORK HISTORY', h):
        return "experience"
    if re.search(r'SKILL|EXPERTISE|TECHNOLOG', h) and not re.search(r'EDUCATION|SOFT|LEADERSHIP', h):
        return "skills"
    return "verbatim"


def _headings(text: str) -> List[str]:
    return [l.strip()[3:].strip() for l in text.split('\n') if l.strip().startswith('## ')]


def pick_sources(texts: List[str]) -> Tuple[Optional[int], List[int]]:
    """Which components make up the resume: the main one (has an experience
    section and an email) plus supplements made only of resume sections (a
    Projects block). Whitepapers and Q&A docs are neither -- they must not
    leak structure or a name into the resume."""
    main = None
    for i, t in enumerate(texts):
        if any(_kind(h) == "experience" for h in _headings(t)) and _EMAIL.search(t):
            main = i
            break
    if main is None:
        main = next((i for i, t in enumerate(texts)
                     if any(_kind(h) == "experience" for h in _headings(t))), None)
    extra = [i for i, t in enumerate(texts)
             if i != main and _headings(t) and all(_SECTION_WORDS.search(h) for h in _headings(t))]
    return main, extra


def _normalize(text: str) -> str:
    """Turn table-cell layouts (a DOCX export puts company / dates / title /
    dates on their own indented lines) into the canonical markdown grammar."""
    out: List[str] = []
    section = ""
    cells: List[str] = []

    def flush():
        pairs: List[List[str]] = []
        for c in cells:
            if _DATE_CELL.match(c) and pairs and not pairs[-1][1]:
                pairs[-1][1] = c.strip('()')
            else:
                pairs.append([c, ""])
        for n, (txt, date) in enumerate(pairs):
            is_role = n > 0 if len(pairs) > 1 else bool(_TITLE_WORDS.search(txt))
            if _kind(section) == "experience" and is_role:
                out.append(f"**{txt}**" + (f" || {date}" if date else ""))
            else:
                out.append(f"### {txt}" + (f" || {date}" if date else ""))
        cells.clear()

    for raw in text.split('\n'):
        s = raw.strip()
        is_cell = (raw[:1] in (' ', '\t') and s and not re.match(r'^([-*•]\s|#)', s))
        if is_cell:
            cells.append(s)
            continue
        if not s and cells:
            continue                  # blank lines between cells of one group
        if cells:
            flush()
        if s.startswith('## '):
            section = s[3:]
        out.append(s)
    if cells:
        flush()
    return '\n'.join(out)


def _split_items(s: str) -> List[str]:
    """Comma split that keeps "Orchestration (PydanticAI, FastMCP)" whole."""
    items, depth, cur = [], 0, ""
    for ch in s:
        depth += ch == '(' or ch == '['
        depth -= ch == ')' or ch == ']'
        if ch == ',' and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    items.append(cur)
    return [i.strip().rstrip('.').strip() for i in items if i.strip().rstrip('.').strip()]


_ROLE_LINE = [re.compile(r'^\*\*(.+?)\*\*\s*\|\|?\s*(.+?)\s*$'),
              re.compile(r'^\*\*(.+?)\*\*\s*\((.+)\)\s*$'),
              re.compile(r'^\*\*([^*]+)\*\*\s*$')]


def parse_profile(text: str) -> Profile:
    """Parse resume markdown (canonical grammar, or the DOCX cell layout) into
    a Profile. Deterministic -- the same input always gives the same facts."""
    lines = _normalize(text).split('\n')
    name = contact = ""
    i = 0
    while i < len(lines) and not lines[i].startswith('## '):
        s = lines[i].strip()
        if s and not name:
            name = s.lstrip('#').strip()
        elif s and not contact:
            contact = s
        i += 1

    p = Profile(name=name, contact=contact)
    sec: Optional[Section] = None
    company: Optional[Company] = None
    role: Optional[Role] = None
    summary: List[str] = []
    n_roles = 0

    def new_role(title="", dates=""):
        nonlocal role, n_roles
        n_roles += 1
        role = Role(id=f"r{n_roles}", title=title, dates=dates)
        company.roles.append(role)

    for raw in lines[i:]:
        s = raw.strip()
        if s.startswith('## '):
            kind = _kind(s[3:])
            if kind != "verbatim" and any(x.kind == kind for x in p.sections):
                kind = "verbatim"      # a second skills/summary block renders as written
            sec = Section(heading=s[3:].strip(), kind=kind)
            p.sections.append(sec)
            company = role = None
            continue
        if sec is None:
            continue
        if sec.kind == "summary":
            if s:
                summary.append(s)
        elif sec.kind == "skills":
            m = re.match(r'^[-*•]\s*\*\*(.+?):?\*\*:?\s*(.+)$', s)
            if m:
                p.skills.append(SkillGroup(label=m.group(1).strip().rstrip(':'), items=_split_items(m.group(2))))
            elif s:
                sec.lines.append(s)   # unparseable skills line -- kept verbatim
        elif sec.kind == "experience":
            if not s:
                continue
            if s.startswith('### '):
                left, _, right = s[4:].partition('||')
                company = Company(name=left.strip().strip('*').strip(), dates=right.strip())
                p.experience.append(company)
                role = None
                continue
            if company is None:
                continue
            m = next((r.match(s) for r in _ROLE_LINE if r.match(s)), None)
            if m:
                new_role(m.group(1).strip(), (m.group(2) if m.lastindex > 1 else "").strip())
                continue
            if re.match(r'^[-*•]\s', s):
                if role is None:
                    new_role()
                role.bullets.append(Bullet(id=f"{role.id}.b{len(role.bullets) + 1}", text=s[2:].strip()))
                continue
            if role is None:
                new_role()
            if not role.bullets:
                role.blurb = (role.blurb + " " + s).strip()   # markdown as written
        else:
            if s or (sec.lines and sec.lines[-1]):
                sec.lines.append(s)

    p.summary = " ".join(summary)
    for c in p.experience:
        if not c.dates and c.roles:
            c.dates = c.roles[0].dates
    return p


def build_profile(texts: List[str]) -> Optional[Profile]:
    """Profile from the user's resume components (markdown text each), or None
    when no component has a readable experience section."""
    main, extra = pick_sources(texts)
    if main is None:
        return None
    joined = "\n\n".join([texts[main]] + [texts[i] for i in extra])
    p = parse_profile(joined)
    if not p.experience or not p.roles():
        return None
    p.source_hash = source_hash(texts)
    return p


def source_hash(texts: List[str]) -> str:
    return hashlib.sha256("\x00".join(texts).encode()).hexdigest()[:16]


# ── Rendering ──────────────────────────────────────────────────────────────────

def _end_key(dates: str) -> Tuple[int, int]:
    """Sort key for reverse-chronological order: (year, month) of the end date."""
    if re.search(r'present|current|now', dates or "", re.I):
        return (9999, 12)
    found = re.findall(rf'({_MONTH})?\s*(\d{{4}})', dates or "")
    if not found:
        return (0, 0)
    mon, year = found[-1]
    months = "jan feb mar apr may jun jul aug sep oct nov dec".split()
    m = next((k + 1 for k, n in enumerate(months) if mon.lower().startswith(n)), 0)
    return (int(year), m)


def _start_key(dates: str) -> Tuple[int, int]:
    first = re.split(r'\s*(?:–|—|-|to)\s*', dates or "", maxsplit=1)[0]
    return _end_key(first) if first else (0, 0)


def render(p: Profile, summary: Optional[str] = None,
           bullets: Optional[Dict[str, List[str]]] = None,
           skill_order: Optional[List[str]] = None) -> str:
    """Profile (+ tailored summary, per-role bullet lists, skill order) ->
    resume markdown. Unset parts render from the profile as-is."""
    out = [f"# {p.name}"] + ([p.contact] if p.contact else []) + [""]
    for sec in p.sections:
        out.append(f"## {sec.heading.upper()}")
        if sec.kind == "summary":
            out.append(summary or p.summary)
        elif sec.kind == "experience":
            for c in sorted(p.experience, key=lambda c: (_end_key(c.dates), _start_key(c.dates)), reverse=True):
                roles = sorted(c.roles, key=lambda r: (_end_key(r.dates), _start_key(r.dates)), reverse=True)
                out.append(f"### {c.name}" + (f" || {c.dates}" if c.dates else ""))
                for r in roles:
                    if r.title:
                        # A role's own date only when it differs from the company's.
                        own = r.dates if r.dates and (len(roles) > 1 or r.dates != c.dates) else ""
                        out.append(f"**{r.title}**" + (f" || {own}" if own else ""))
                    if r.blurb:
                        out.append(r.blurb)
                    texts = bullets.get(r.id) if bullets and r.id in bullets else [b.text for b in r.bullets]
                    out += [f"- {t}" for t in texts]
                out.append("")
        elif sec.kind == "skills":
            order = {l.lower(): n for n, l in enumerate(skill_order or [])}
            groups = sorted(p.skills, key=lambda g: order.get(g.label.lower(), len(order)))
            out += [f"- **{g.label}:** {', '.join(g.items)}" for g in groups]
            out += sec.lines
        else:
            out += sec.lines
        while out and not out[-1]:
            out.pop()
        out.append("")
    return "\n".join(out).strip() + "\n"


# ── Validation ─────────────────────────────────────────────────────────────────

_NUM = re.compile(r'\d+(?:[.,]\d+)*')


def numbers(text: str) -> set:
    return {n.replace(',', '').rstrip('.') for n in _NUM.findall(text or "")}


def plain(text: str) -> str:
    """LLM text -> plain sentence: no markdown, one line."""
    t = re.sub(r'[*`#]|^\s*[-•]\s*', '', text or "")
    return re.sub(r'\s+', ' ', t).strip()


def banned_hits(text: str, banned: List[str]) -> List[str]:
    return [w for w in banned if w and re.search(rf'\b{re.escape(w)}', text or "", re.I)]


_WORD = re.compile(r"[a-z][a-z-]{4,}")


def parroted(text: str, jd: str, source: str) -> List[str]:
    """Words the model lifted from the job description that the candidate's own
    material never uses -- the tell of a claimed-but-unearned skill ("experimental
    design", "A/B testing") that the numbers check cannot see."""
    src, job = set(_WORD.findall(source.lower())), set(_WORD.findall(jd.lower()))
    return sorted(w for w in set(_WORD.findall(text.lower())) if w in job and w not in src)


PARROT_LIMIT = 2   # a couple of generic words ("improving", "scalable") are fine


def split_label(md: str) -> Tuple[str, str]:
    """"**Label:** body" -> ("Label", "body"); no label -> ("", md)."""
    m = re.match(r'^\*\*(.+?):\*\*\s*(.*)$', md) or re.match(r'^\*\*(.+?)\*\*:\s*(.*)$', md)
    return (m.group(1).strip(), m.group(2).strip()) if m else ("", md.strip())


def rebold(text: str, source_body: str) -> str:
    """Re-apply the source's bold spans (metrics) wherever they survive verbatim."""
    for span in sorted(set(re.findall(r'\*\*(.+?)\*\*', source_body)), key=len, reverse=True):
        idx = text.find(span)
        # skip if it already sits inside a bold run (odd number of ** before it)
        if idx != -1 and text[:idx].count('**') % 2 == 0:
            text = text[:idx] + f"**{span}**" + text[idx + len(span):]
    return text


# ── LLM tailoring ──────────────────────────────────────────────────────────────

class _SummaryOut(BaseModel):
    summary: str = Field(description="Tailored professional summary, plain text")
    skill_order: List[str] = Field(default=[], description="Skill category labels, most relevant first")


class _PickedBullet(BaseModel):
    source_id: str = Field(description="id of the source bullet, e.g. r1.b3")
    text: str = Field(description="Reworded bullet body, plain text, no label")


class _RoleOut(BaseModel):
    bullets: List[_PickedBullet]


def _max_len(source_md: str) -> int:
    """A reworded bullet may run a little longer than its source, never much."""
    return int(len(plain(split_label(source_md)[1])) * 1.15) + 20


MAX_BULLETS = 6
MIN_BULLETS = 3


async def _ask(router, prompt: str, model_cls, validate: Callable, llm: str, system: str):
    """One structured call plus one targeted re-ask. Returns (output, errors)
    for the last attempt; (None, [...]) if the model never produced valid JSON."""
    out, errors = None, ["no answer"]
    for attempt in range(2):
        try:
            out = await router.structured_complete(prompt, model_cls, llm=llm, system=system, temperature=0.2)
        except Exception as e:
            logger.warning(f"[ResumeProfile] structured call failed: {e}")
            return None, [str(e)]
        errors = validate(out)
        if not errors:
            return out, []
        logger.info(f"[ResumeProfile] re-asking, attempt {attempt + 1}: {errors[:5]}")
        prompt += ("\n\nYOUR PREVIOUS ANSWER BROKE THESE RULES -- fix only these and answer again:\n"
                   + "\n".join(f"- {e}" for e in errors))
    return out, errors


SYSTEM = ("You tailor resumes. You only choose and reword the candidate's own facts. "
          "You never add numbers, tools, employers or claims that are not in the source. "
          "Answer with JSON only.")


async def tailor(router, p: Profile, job_description: str, company: str = "", title: str = "",
                 feedback: str = "", style_notes: str = "", banned: Optional[List[str]] = None,
                 llm: str = "gemini") -> str:
    """Tailored resume markdown for one job. Facts, order and layout come from
    the profile; the LLM only picks and rewords bullets and writes the summary."""
    banned = [b.strip() for b in (banned or []) if b.strip()]
    jd = (job_description or "")[:4000]
    notes = ""
    if style_notes:
        notes += f"\nUSER STYLE NOTES (follow them):\n{style_notes}\n"
    if feedback:
        notes += f"\nUSER FEEDBACK FOR THIS VERSION (follow it):\n{feedback}\n"
    if banned:
        notes += f"\nNEVER USE THESE WORDS: {', '.join(banned)}\n"
    sem = asyncio.Semaphore(3)   # ponytail: free tiers rate-limit bursts; 3 in flight is enough

    # ── summary + skill order ──
    all_text = p.summary + "\n" + "\n".join(b.text for r in p.roles() for b in r.bullets)
    vocab = render(p)                     # everything the candidate's own resume says
    min_metrics = min(2, len(numbers(p.summary)))
    headline = p.headline
    opener = headline if headline and p.summary.lower().startswith(headline.lower()) else ""
    labels = [g.label for g in p.skills]

    def check_summary(o: _SummaryOut) -> List[str]:
        s = plain(o.summary)
        errs = []
        if not 200 <= len(s) <= 900:
            errs.append(f"summary is {len(s)} characters; write 200-900")
        if opener and not s.lower().startswith(opener.lower()):
            errs.append(f'summary must start with "{opener}"')
        new = numbers(s) - numbers(all_text)
        if new:
            errs.append(f"summary uses numbers not in the source: {', '.join(sorted(new))}")
        if banned_hits(s, banned):
            errs.append(f"summary uses banned words: {', '.join(banned_hits(s, banned))}")
        lifted = parroted(s, jd, vocab)
        if len(lifted) > PARROT_LIMIT:
            errs.append("summary claims things only the job description mentions, not the candidate's "
                        f"facts: {', '.join(lifted)}. Describe what the candidate has actually done.")
        if len(numbers(s)) < min_metrics:
            errs.append(f"summary must include at least {min_metrics} of the candidate's real metrics")
        return errs

    summary_prompt = f"""Rewrite the candidate's professional summary for the role of {title} at {company}.

SOURCE SUMMARY:
{p.summary}

CANDIDATE EXPERIENCE (facts you may draw on):
{chr(10).join(f"- {plain(b.text)}" for r in p.roles() for b in r.bullets)[:6000]}

JOB DESCRIPTION:
{jd}
{notes}
SKILL CATEGORIES: {labels}

Rules:
- 3 to 4 sentences, 200-900 characters, plain text, no markdown.
{f'- Start with exactly "{opener}".' if opener else ''}
- Use only facts and numbers that appear above; include {min_metrics or 'any'} of the strongest metrics.
- Echo the job's language only where the candidate's facts support it. Never claim a requirement
  from the job description that the facts above do not show.
- skill_order: the SKILL CATEGORIES labels, exactly as written, most relevant to this job first."""

    async def run_summary():
        async with sem:
            return await _ask(router, summary_prompt, _SummaryOut, check_summary, llm, SYSTEM)

    # ── one call per role ──
    async def run_role(c: Company, r: Role):
        if not r.bullets:
            return r.id, None, []
        src = {b.id: b for b in r.bullets}
        lo, hi = min(MIN_BULLETS, len(src)), min(MAX_BULLETS, len(src))

        def check(o: _RoleOut) -> List[str]:
            errs, seen = [], set()
            for b in o.bullets:
                if b.source_id not in src:
                    errs.append(f"{b.source_id} is not one of the listed ids")
                    continue
                if b.source_id in seen:
                    errs.append(f"{b.source_id} used twice")
                seen.add(b.source_id)
                new = numbers(b.text) - numbers(src[b.source_id].text)
                if new:
                    errs.append(f"{b.source_id} adds numbers not in its source: {', '.join(sorted(new))}")
                if banned_hits(b.text, banned):
                    errs.append(f"{b.source_id} uses banned words: {', '.join(banned_hits(b.text, banned))}")
                lifted = parroted(b.text, jd, vocab)
                if len(lifted) > PARROT_LIMIT:
                    errs.append(f"{b.source_id} claims things only the job description mentions: {', '.join(lifted)}")
                limit = _max_len(src[b.source_id].text)
                if len(plain(b.text)) > limit:
                    errs.append(f"{b.source_id} is {len(plain(b.text))} characters; keep it under {limit}")
            if not lo <= len(seen) <= hi:
                errs.append(f"pick between {lo} and {hi} bullets (you picked {len(seen)})")
            return errs

        listing = "\n".join(f"[{b.id}] {plain(split_label(b.text)[1])}" for b in r.bullets)
        prompt = f"""Tailor one job's bullets on a resume for the role of {title} at {company}.

THE CANDIDATE'S JOB: {r.title or ''} at {c.name} ({r.dates or c.dates})
SOURCE BULLETS:
{listing}

JOB DESCRIPTION:
{jd}
{notes}
Rules:
- Pick the {lo}-{hi} source bullets most relevant to this job, most relevant first.
- Reword each to foreground what this job cares about, using its terms where they are true.
- Keep every number exactly as in its source bullet. Add no new numbers, tools or claims, and
  never graft a job-description requirement onto a bullet that does not show it.
- Rewording must not make a bullet longer: no tacked-on clauses like "demonstrating ..." or
  "contributing to ...". Tighter is better.
- Plain text, no markdown, no leading label.
- source_id must be the bracketed id of the bullet you reworded."""
        async with sem:
            out, errs = await _ask(router, prompt, _RoleOut, check, llm, SYSTEM)
        return r.id, out, errs

    jobs = [run_role(c, r) for c in p.experience for r in c.roles]
    (s_out, s_errs), *role_results = await asyncio.gather(run_summary(), *jobs)

    # Nothing came back at all (rate limit, bad key, provider down): fail loudly.
    # Saving the untouched master as a "tailored" resume would hide the outage.
    answered = [o for _, o, _ in role_results if o is not None] + ([s_out] if s_out else [])
    if not answered:
        raise RuntimeError(f"{llm} returned no usable answer: {s_errs[0] if s_errs else 'unknown error'}")

    summary = p.summary
    skill_order = None
    if s_out is not None:
        if not s_errs:
            summary = plain(s_out.summary)
        skill_order = [l for l in s_out.skill_order if l in labels]

    bullets: Dict[str, List[str]] = {}
    fallbacks = 0
    for rid, out, errs in role_results:
        role = next(r for r in p.roles() if r.id == rid)
        src = {b.id: b for b in role.bullets}
        picked: List[str] = []
        used = set()
        for b in (out.bullets if out else []):
            if b.source_id not in src or b.source_id in used or len(picked) >= MAX_BULLETS:
                continue
            used.add(b.source_id)
            label, body = split_label(src[b.source_id].text)
            text = plain(b.text)
            ok = (text and not (numbers(text) - numbers(body)) and not banned_hits(text, banned)
                  and len(text) <= _max_len(body) and len(parroted(text, jd, vocab)) <= PARROT_LIMIT)
            if not ok:                        # this bullet failed twice -- keep the master wording
                text, fallbacks = body, fallbacks + 1
                if banned_hits(text, banned):
                    continue
            else:
                if body.endswith('.') and not text.endswith('.'):
                    text += '.'
                text = rebold(text, body)
            picked.append(f"**{label}:** {text}" if label else text)
        # Too few survivors: top up in master order. No answer at all: the whole
        # master role (capped), so a dead model costs tailoring, never content.
        want = min(MAX_BULLETS if out is None else MIN_BULLETS, len(role.bullets))
        for b in role.bullets:
            if len(picked) >= want:
                break
            if b.id not in used and not banned_hits(b.text, banned):
                used.add(b.id)
                picked.append(b.text)
                fallbacks += 1
        bullets[rid] = picked

    logger.info(f"[ResumeProfile] tailored {len(bullets)} roles; summary={'llm' if summary != p.summary else 'master'}; "
                f"{fallbacks} bullet(s) kept master wording")
    return render(p, summary=summary, bullets=bullets, skill_order=skill_order)
