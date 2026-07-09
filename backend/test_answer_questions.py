"""Unit test for _validate_answers — the grounding/constraint gate for extension autofill."""
from main import _validate_answers


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


if __name__ == "__main__":
    test_validate_answers()
    print("ok")
