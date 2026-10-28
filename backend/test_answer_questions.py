"""Unit test for _validate_answers — the grounding/constraint gate for extension autofill."""
from main import _validate_answers, merge_contact


def test_validate_answers():
    questions = [
        {"id": "q1", "label": "Years of Python?", "type": "text", "options": None, "maxlen": None},
        {"id": "q2", "label": "Notice period", "type": "select", "options": ["Immediate", "2 weeks", "1 month"], "maxlen": None},
        {"id": "q3", "label": "Why us?", "type": "textarea", "options": None, "maxlen": 10},
        {"id": "q4", "label": "Employee ID?", "type": "text", "options": None, "maxlen": None},
    ]
    answers = [
        {"id": "q1", "answer": "6 years"},
        {"id": "q2", "answer": "2 WEEKS"},          # case-insensitive → canonical option
        {"id": "q3", "answer": "Because of the mission"},  # truncated to maxlen
        {"id": "q4", "answer": None},                # ungrounded stays null
        {"id": "ghost", "answer": "dropped"},        # unknown id dropped
    ]
    out = {a["id"]: a["answer"] for a in _validate_answers(answers, questions)}
    assert out == {"q1": "6 years", "q2": "2 weeks", "q3": "Because of", "q4": None}

    # invented option → null; missing question row → null
    out2 = {a["id"]: a["answer"] for a in _validate_answers(
        [{"id": "q2", "answer": "3 months"}], questions)}
    assert out2["q2"] is None and out2["q1"] is None


def test_contact_prefers_form_and_parsed_resume():
    # The regex read that went to the apply agent on 2026-10-24.
    read = {"name": "**Lead Data Scientist** (Jan 2024 \u2013 Present)", "email": "g@x.com",
            "phone": "+1 (555) 010-0199", "location": "Singh Kitchener, Ontario, Canada", "linkedin": ""}
    form = {"phone": "5195550142", "street_address": "12 Example St", "postal_code": "A1A 1A1",
            "linkedin_url": "https://www.linkedin.com/in/girijesh-singh/", "github_url": " "}
    c = merge_contact(read, "Girijesh Singh", "Kitchener, Ontario, Canada | +1 (555) 010-0199 | g@x.com", form)
    assert (c["first_name"], c["last_name"]) == ("Girijesh", "Singh")
    assert (c["city"], c["province"], c["country"]) == ("Kitchener", "Ontario", "Canada")
    assert c["phone"] == "5195550142" and c["street_address"] == "12 Example St" and c["postal_code"] == "A1A 1A1"
    assert c["linkedin"].endswith("girijesh-singh/") and "github" not in c and c["email"] == "g@x.com"
    assert c["problems"] == []
    # Without the parsed resume and form, the raw regex read must be flagged, not typed.
    bad = merge_contact(read, "", "", {})
    assert any(x.startswith("name") for x in bad["problems"])
    # Right name, but the name leaked into the location (the old "Singh Kitchener").
    leak = merge_contact({**read, "name": "Girijesh Singh"}, "", "", {})
    assert [x for x in leak["problems"] if x.startswith(("name", "location"))] == \
        ["location looks wrong: 'Singh Kitchener, Ontario, Canada'"]
    one = merge_contact(read, "Girijesh", "Kitchener, Ontario, Canada", form)        # a one-word name
    assert (one["first_name"], one["last_name"]) == ("Girijesh", "")


if __name__ == "__main__":
    test_validate_answers()
    test_contact_prefers_form_and_parsed_resume()
    print("ok")


def test_errors_reach_the_ops_buffer():
    import main
    from loguru import logger
    before = len(main.OPS_ERRORS)
    logger.error("resume generation failed for job 7")
    logger.complete()
    assert len(main.OPS_ERRORS) == before + 1 and "job 7" in main.OPS_ERRORS[-1]["message"]
    assert main.ops_errors.__name__ == "ops_errors"
