"""Guarded resume edits: only the patched text changes, and no fact is invented."""
import asyncio, json

import pytest

import resume_edit as re_
from resume_edit import EditError, apply_patches, check

MD = """# Girijesh Singh
Kitchener, Ontario | g@x.com

## SUMMARY
Lead data scientist, 8 years, building retrieval systems.

## EXPERIENCE
### Primus Software, Toronto || Jan 2024 – Present
**Lead Data Scientist**
- **Retrieval:** Built a graph RAG engine that cut context **40%**.
- **Forecasting:** Shipped demand models in Python.
"""
MASTER = MD + "\n- Mentored 4 engineers. Used PyTorch and LangChain.\n"


def test_patch_changes_only_its_span():
    new = apply_patches(MD, [{"find": "Shipped demand models in Python.",
                              "replace": "Shipped demand forecasting models in Python."}])
    assert new.replace("demand forecasting models", "demand models") == MD
    assert check(MD, new, [{"find": "Shipped demand models in Python.",
                            "replace": "Shipped demand forecasting models in Python."}], MASTER, "", []) == []


def test_patch_must_be_exact_and_unique_and_below_header():
    with pytest.raises(EditError):
        apply_patches(MD, [{"find": "not in the resume", "replace": "x"}])
    with pytest.raises(EditError):
        apply_patches(MD, [{"find": "- **", "replace": "* **"}])           # ambiguous: every bullet
    with pytest.raises(EditError):
        apply_patches(MD, [{"find": "Girijesh Singh", "replace": "G. Singh"}])
    with pytest.raises(EditError):
        apply_patches(MD, [])


def test_invented_facts_are_caught():
    def problems(find, rep):
        return check(MD, apply_patches(MD, [{"find": find, "replace": rep}]), [{"find": find, "replace": rep}],
                     MASTER, "We want Kubernetes and experimental design expertise.", [])
    assert any("numbers" in p for p in problems("cut context **40%**", "cut context **60%**"))
    assert any("experimental" in p for p in problems("Shipped demand models in Python.",
                                                     "Shipped demand models in Python with experimental design."))
    assert any("Kubernetes" in p for p in problems("Shipped demand models in Python.",
                                                   "Shipped demand models in Python on Kubernetes."))
    # facts that are in the master resume are fine
    assert problems("Shipped demand models in Python.", "Shipped demand models in Python and PyTorch.") == []
    assert any("Lead Data Scientist" in p for p in problems("Lead data scientist, 8 years", "Data scientist, 8 years"))
    assert any("company/date" in p for p in problems("Jan 2024 – Present", "Jan 2023 – Present"))


def test_edit_retries_once_with_the_reason_then_refuses():
    calls = []

    class Router:
        async def complete(self, prompt, **kw):
            calls.append(prompt)
            bad = {"edits": [{"find": "cut context **40%**", "replace": "cut context **70%**"}], "cannot": ""}
            good = {"edits": [{"find": "Shipped demand models in Python.",
                               "replace": "Shipped demand models in Python, mentoring 4 engineers."}], "cannot": ""}
            if "You check one resume edit" in prompt:
                return json.dumps({"unsupported": []})
            return json.dumps(bad if len(calls) == 1 else good)

    new, edits = asyncio.run(re_.edit(Router(), MD, "mention mentoring", MASTER, "", [], "local"))
    assert "mentoring 4 engineers" in new and "rejected: new numbers" in calls[1]

    class Refuses:
        async def complete(self, prompt, **kw):
            return json.dumps({"edits": [], "cannot": "no Kubernetes in the master resume"})
    with pytest.raises(EditError, match="Kubernetes"):
        asyncio.run(re_.edit(Refuses(), MD, "add Kubernetes", MASTER, "", [], "local"))


def test_verifier_rejects_overstated_claims():
    class Router:
        async def complete(self, prompt, **kw):
            if "You check one resume edit" in prompt:
                return json.dumps({"unsupported": ["insurance risk modelling — source has claims routing only"]})
            return json.dumps({"edits": [{"find": "building retrieval systems",
                                          "replace": "specializing in insurance risk modelling"}], "cannot": ""})
    with pytest.raises(EditError, match="risk modelling"):
        asyncio.run(re_.edit(Router(), MD, "emphasise risk modelling", MASTER, "", [], "local"))
