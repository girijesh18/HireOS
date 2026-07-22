"""Guard against the chat editor silently destroying a resume.

Run: python test_chat_edit_guard.py
"""
from agents import _guard_truncation

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
"""


def expect_reject(edited, why):
    try:
        _guard_truncation(edited, ORIGINAL)
    except ValueError:
        return
    raise AssertionError(f"should have rejected: {why}")


# Accepts a real edit (same shape, one bullet reworded).
ok = ORIGINAL.replace("cut p99 latency 40%", "reduced p99 latency by 40%")
assert _guard_truncation(ok, ORIGINAL) == ok

expect_reject("", "empty reply")
expect_reject("## SUMMARY\n*Senior engineer.*", "truncated to a fragment")
expect_reject(
    ORIGINAL.replace("## SKILLS\n- Python, PyTorch\n", "").replace("## SUMMARY\n*Senior engineer.*\n", "")
    + "- filler " * 200,
    "two sections dropped",
)
# No original to compare against — pass through rather than block a first draft.
assert _guard_truncation("## X", "") == "## X"

print("ok")
