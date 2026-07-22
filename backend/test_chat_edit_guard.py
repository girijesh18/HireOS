"""Guards that keep a generated/edited resume from silently losing content.

Run: python test_chat_edit_guard.py
"""
from agents import _guard_truncation, _section_headings, _missing_sections

ORIGINAL = """# Jane Doe
jane@x.com | 555-1234

## SUMMARY
*Senior engineer.*

## EXPERIENCE
### Acme, Remote || Jan 2023 – Present
**Staff Engineer**
- **Scale:** cut p99 latency 40%.

## SKILLS
- Python, PyTorch

## PUBLICATIONS
- A paper.
"""


def expect_reject(edited, why):
    try:
        _guard_truncation(edited, ORIGINAL)
    except ValueError:
        return
    raise AssertionError(f"should have rejected: {why}")


# ── chat-edit guard ───────────────────────────────────────────────────────────
ok = ORIGINAL.replace("cut p99 latency 40%", "reduced p99 latency by 40%")
assert _guard_truncation(ok, ORIGINAL) == ok

expect_reject("", "empty reply")
expect_reject("## SUMMARY\n*Senior engineer.*", "truncated to a fragment")
expect_reject(
    ORIGINAL.replace("## SKILLS\n- Python, PyTorch\n", "").replace("## PUBLICATIONS\n- A paper.\n", "")
    + "- filler " * 200,
    "two sections dropped",
)
assert _guard_truncation("## X", "") == "## X"   # no original → nothing to protect

# ── section extraction ────────────────────────────────────────────────────────
assert _section_headings(ORIGINAL) == ["SUMMARY", "EXPERIENCE", "SKILLS", "PUBLICATIONS"]
# ### company lines are not sections
assert "ACME, REMOTE || JAN 2023 – PRESENT" not in _section_headings(ORIGINAL)
assert _section_headings("## A\n## A\n## B") == ["A", "B"]     # de-duplicated

# ── coverage diff ─────────────────────────────────────────────────────────────
assert _missing_sections(ORIGINAL, ORIGINAL) == []
assert _missing_sections(ORIGINAL, ORIGINAL.replace("## PUBLICATIONS", "## PAPERS")) == ["PUBLICATIONS"]
# a renamed-but-equivalent heading counts as covered
assert _missing_sections("## WORK EXPERIENCE\nx", "## EXPERIENCE\nx") == []
assert _missing_sections("## EXPERIENCE\nx", "## SKILLS\nx") == ["EXPERIENCE"]

print("ok")
