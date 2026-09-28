"""Structured resume generation: facts and layout never depend on the model.

    python test_resume_profile.py
"""
import asyncio
import json

import resume_profile as rp

# DOCX-export layout: company / dates / title / dates as indented cells.
CELLS = """Ada Lovelace

London, UK | ada@example.com | +44 1234

## SUMMARY
Lead Engineer building analytical engines for 12 years.

## EXPERIENCE

    Analytical Engines Ltd, UK

    Jan 2019 – Present

    Lead Engineer

    Jan 2022 – Present

Runs the engine team.

- **Scale:** Grew throughput **10x** on the same hardware.
- **Team:** Led 8 engineers.
- Shipped the difference engine to 3 banks.
- **Cost:** Cut compute spend by **$2M**.

    Senior Engineer

    Jan 2019 – Jan 2022

- **Build:** Wrote the first compiler in 6 months.

    Babbage & Co

    Jun 2015 – Dec 2018

    Research Intern



- Modeled Bernoulli numbers.

## SKILLS
- **Languages:** Python, Rust (async, no_std), SQL
- **Soft skills:** Mentoring

## EDUCATION

    MSc Mathematics

    2015

*University of London*
"""


def test_parse_cells():
    p = rp.parse_profile(CELLS)
    assert (p.name, p.headline) == ("Ada Lovelace", "Lead Engineer")
    assert [c.name for c in p.experience] == ["Analytical Engines Ltd, UK", "Babbage & Co"]
    primus = p.experience[0]
    assert [(r.title, r.dates) for r in primus.roles] == [("Lead Engineer", "Jan 2022 – Present"),
                                                         ("Senior Engineer", "Jan 2019 – Jan 2022")]
    assert primus.roles[0].blurb == "Runs the engine team." and len(primus.roles[0].bullets) == 4
    assert p.experience[1].roles[0].title == "Research Intern"
    assert p.skills[0].items == ["Python", "Rust (async, no_std)", "SQL"]
    assert [s.kind for s in p.sections] == ["summary", "experience", "skills", "verbatim"]


def test_render_roundtrip_and_order():
    p = rp.parse_profile(CELLS)
    p.experience.reverse()                          # stored out of order...
    md = rp.render(p)
    assert md.index("Analytical Engines") < md.index("Babbage")   # ...rendered newest first
    assert "### Analytical Engines Ltd, UK || Jan 2019 – Present" in md
    assert "**Lead Engineer** || Jan 2022 – Present" in md
    assert "### MSc Mathematics || 2015" in md
    q = rp.parse_profile(md)
    facts = lambda x: sorted((c.name, c.dates, tuple((r.title, r.dates, len(r.bullets)) for r in c.roles))
                             for c in x.experience)
    assert facts(q) == facts(p)


def test_build_profile_ignores_whitepapers():
    paper = "Engine White Paper\n\n## Executive Summary\nWe built an engine.\n\n## Architecture\n- parts"
    projects = "## PROJECTS\n### Loom\n- Wove patterns."
    main, extra = rp.pick_sources([paper, CELLS, projects])
    assert (main, extra) == (1, [2])
    p = rp.build_profile([paper, CELLS, projects])
    assert p.name == "Ada Lovelace" and p.sections[-1].heading == "PROJECTS"


def test_validators():
    assert rp.numbers("Grew **10x** to $2,000 in 3.5s") == {"10", "2000", "3.5"}
    assert rp.banned_hits("guaranteed zero errors", ["guarantee", "crisis"]) == ["guarantee"]
    assert rp.split_label("**Scale:** Grew **10x**") == ("Scale", "Grew **10x**")
    assert rp.parroted("Expert in experimental design and Python", "experimental design, python, sql",
                       "Python and Rust") == ["design", "experimental"]
    assert rp.rebold("Throughput rose 10x on old hardware", "Grew throughput **10x**") == \
        "Throughput rose **10x** on old hardware"


class FakeRouter:
    """Scripted structured_complete: returns answers in order, records prompts."""
    def __init__(self, answers):
        self.answers, self.prompts = answers, []

    async def structured_complete(self, prompt, model_cls, **_):
        self.prompts.append(prompt)
        key = "summary" if "professional summary" in prompt else prompt.split("SOURCE BULLETS:\n[")[1][:2]
        ans = self.answers[key]
        return model_cls(**(ans.pop(0) if isinstance(ans, list) else ans))


def test_tailor_validates_and_falls_back():
    p = rp.parse_profile(CELLS)
    good_summary = {"summary": "Lead Engineer " + "who builds analytical engines. " * 8,
                    "skill_order": ["Soft skills", "Languages", "Unknown"]}
    router = FakeRouter({
        "summary": good_summary,
        # r1: first answer invents "50" and uses a banned word; the re-ask still invents -> master wording
        "r1": [{"bullets": [{"source_id": "r1.b1", "text": "Grew throughput 50x, a guaranteed win"},
                            {"source_id": "r1.b4", "text": "Cut compute spend by $2M"},
                            {"source_id": "r1.b2", "text": "Led 8 engineers"}]},
               {"bullets": [{"source_id": "r1.b1", "text": "Grew throughput 50x"},
                            {"source_id": "r1.b4", "text": "Cut compute spend by $2M"},
                            {"source_id": "r1.b2", "text": "Led 8 engineers"}]}],
        # r2: a bogus id and nothing else -> topped up from the master
        "r2": {"bullets": [{"source_id": "zz", "text": "Invented"}]},
        "r3": {"bullets": [{"source_id": "r3.b1", "text": "Modeled Bernoulli numbers"}]},
    })
    md = asyncio.run(rp.tailor(router, p, "JD text", "Acme", "Engineer", banned=["guarantee"]))

    assert md.startswith("# Ada Lovelace\nLondon, UK | ada@example.com | +44 1234\n")
    assert "- **Scale:** Grew throughput **10x** on the same hardware." in md       # fell back
    assert "- **Cost:** Cut compute spend by **$2M**" in md                          # reworded, rebolded
    assert "50x" not in md and "guarantee" not in md.lower() and "Invented" not in md
    assert "- **Build:** Wrote the first compiler in 6 months." in md               # top-up
    assert md.index("**Soft skills:**") < md.index("**Languages:**")               # skill order
    assert any("BROKE THESE RULES" in x for x in router.prompts)   # r1 was re-asked


def test_tailor_fails_loudly_when_the_model_is_dead():
    class Dead:
        async def structured_complete(self, *a, **k):
            raise ValueError("provider down")
    p = rp.parse_profile(CELLS)
    try:
        asyncio.run(rp.tailor(Dead(), p, "JD", llm="x"))
        raise AssertionError("an outage must not be saved as a tailored resume")
    except RuntimeError as e:
        assert "provider down" in str(e)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("  PASS ", name)
    print("all passed")
