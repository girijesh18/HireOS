"""Resume consistency eval: the same jobs across models, old pipeline vs new.

    python eval_resume.py COMPONENTS.json JOBS.json --models gemini-2.5-flash,openrouter \
        [--pipelines new,old] [--jobs 3] [--banned guarantee,catastrophic] [--out DIR]

COMPONENTS.json: [{"name", "text"}] -- the user's active master-resume components.
JOBS.json:       [{"company", "title", "jd"}].
Keys come from the environment (.env). Every output is checked against facts
taken from the parsed profile; nothing here trusts the LLM.
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import tempfile
import time

from dotenv import load_dotenv

load_dotenv()

import resume_profile as rp  # noqa: E402
from llm_router import LLMRouter  # noqa: E402


def checks(md: str, profile: rp.Profile, all_source: str, banned) -> dict:
    """Invariants a correct resume must hold, whatever model wrote it."""
    lines = [l.strip() for l in md.split("\n")]
    body = md.split("\n## ", 1)[1] if "\n## " in md else md
    out = {}
    out["header"] = lines[0] == f"# {profile.name}" and (len(lines) > 1 and lines[1] == profile.contact)

    # Every company, role title and date from the profile, in its canonical line.
    missing = []
    for c in profile.experience:
        if not any(l.startswith("###") and c.name in l and (not c.dates or c.dates in l) for l in lines):
            missing.append(f"{c.name} || {c.dates}")
        for r in c.roles:
            if r.title and not any(r.title in l and (not r.dates or r.dates in l or len(c.roles) == 1)
                                   for l in lines if l.startswith("**") or l.startswith("###")):
                missing.append(f"{r.title} || {r.dates}")
    out["facts"] = not missing
    out["facts_missing"] = missing

    # Reverse-chronological company order.
    pos = []
    for c in profile.experience:
        idx = next((i for i, l in enumerate(lines) if l.startswith("###") and c.name in l), None)
        pos.append((idx, rp._end_key(c.dates)))
    seen = [e for i, e in sorted((p for p in pos if p[0] is not None), key=lambda p: p[0])]
    out["chronology"] = seen == sorted(seen, reverse=True) and len(seen) == len(pos)

    new_nums = rp.numbers(body) - rp.numbers(all_source)
    out["no_new_numbers"] = not new_nums
    out["new_numbers"] = sorted(new_nums)[:10]
    out["no_banned"] = not rp.banned_hits(body, banned)

    summ = re.search(r"^## [^\n]*SUMMARY[^\n]*\n+(.+)", md, re.M)
    out["summary_opener"] = bool(summ) and summ.group(1).lower().startswith(profile.headline.lower())

    bad = [l for l in lines if re.match(r"^###\s+\*\*", l) or re.search(r"\|\|\s*\*", l)
           or re.match(r"^\*\s.*\S\*$", l)]
    out["markup"] = not bad
    out["sections"] = [l[3:] for l in lines if l.startswith("## ")] == [s.heading.upper() for s in profile.sections]
    return out


def tailoring(md: str, profile: rp.Profile) -> dict:
    """How much the model actually changed. Master wording passes every fact
    check trivially, so an eval must also show the tailoring happened."""
    master = {b.text for r in profile.roles() for b in r.bullets}
    exp = next((sec for sec in re.split(r"\n## ", md) if "EXPERIENCE" in sec.split("\n")[0]), md)
    bullets = [l[2:] for l in exp.split("\n") if l.startswith("- ")]
    return {"summary_tailored": profile.summary not in md,
            "bullets_reworded": f"{sum(b not in master for b in bullets)}/{len(bullets)}"}


def pages(md: str) -> int:
    from doc_generator import _resume_md_to_html
    from weasyprint import HTML
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        HTML(string=_resume_md_to_html(md)).write_pdf(f.name)
        info = subprocess.run(["pdfinfo", f.name], capture_output=True, text=True).stdout
    m = re.search(r"Pages:\s+(\d+)", info)
    return int(m.group(1)) if m else -1


async def run_old(router, comps, job, llm):
    """The pre-profile pipeline: free-form markdown + header swap, fed the
    components production's classifier would have kept."""
    import main
    from agents import ResumeTailoringAgent
    agent = ResumeTailoringAgent(router)
    texts = [c["text"] for c in comps if main._classify_component(c["name"], c["text"]) == "resume"]
    contact = main.extract_contact_info("\n".join(texts))
    md = await agent.tailor(job_description=job["jd"] or "", master_resume="\n\n".join(texts),
                            contact_facts=contact, company=job["company"], title=job["title"], llm=llm)
    return agent.enforce_header(md, contact)


async def main_(a):
    comps = json.load(open(a.components))
    texts = [c["text"] for c in comps]
    profile = rp.build_profile(texts)
    assert profile, "no parseable resume among the components"
    all_source = "\n".join(texts)
    jobs = json.load(open(a.jobs))[: a.jobs_n]
    banned = [b for b in a.banned.split(",") if b]
    os.makedirs(a.out, exist_ok=True)
    router = LLMRouter(allow_env=True)
    router._fallback_chain = lambda llm: [llm]   # grade the named model, not whoever answered for it
    rows = []
    for llm in a.models.split(","):
        for pipe in a.pipelines.split(","):
            for n, job in enumerate(jobs):
                t0 = time.time()
                try:
                    if pipe == "new":
                        md = await rp.tailor(router, profile, job["jd"] or "", job["company"], job["title"],
                                             banned=banned, llm=llm)
                    else:
                        md = await run_old(router, comps, job, llm)
                    res = checks(md, profile, all_source, banned)
                    res["pages"] = pages(md)
                    if pipe == "new":
                        res.update(tailoring(md, profile))
                except Exception as e:
                    md, res = "", {"error": str(e)[:200]}
                res.update(model=llm, pipeline=pipe, job=job["company"], secs=round(time.time() - t0))
                fn = f"{a.out}/{pipe}_{re.sub(r'[^\w.-]', '_', llm)}_{n}.md"
                open(fn, "w").write(md)
                rows.append(res)
                print(json.dumps(res), flush=True)

    keys = ["header", "facts", "chronology", "no_new_numbers", "no_banned", "summary_opener", "markup", "sections"]
    print("\nmodel | pipeline | " + " | ".join(keys) + " | errors")
    for llm in a.models.split(","):
        for pipe in a.pipelines.split(","):
            rs = [r for r in rows if r["model"] == llm and r["pipeline"] == pipe]
            ok = [r for r in rs if "error" not in r]
            cells = [f"{sum(bool(r.get(k)) for r in ok)}/{len(rs)}" for k in keys]
            print(f"{llm} | {pipe} | " + " | ".join(cells) + f" | {len(rs) - len(ok)}")
    json.dump(rows, open(f"{a.out}/results.json", "w"), indent=1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("components")
    ap.add_argument("jobs")
    ap.add_argument("--models", default="gemini-2.5-flash")
    ap.add_argument("--pipelines", default="new,old")
    ap.add_argument("--jobs", dest="jobs_n", type=int, default=3)
    ap.add_argument("--banned", default="guarantee,catastrophic")
    ap.add_argument("--out", default="eval_out")
    asyncio.run(main_(ap.parse_args()))
