"""_parse_json regression tests.

Run: cd backend && python test_parse_json.py
"""
import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_parse_json.db")
os.environ.setdefault("LOG_DIR", "./logs")

from agents import _parse_json


def test_markdown_body_with_literal_newlines():
    """The failure users actually hit: a job_description written as markdown,
    with the line breaks left literal. Strict JSON rejects those, and the
    repair path below "recovered" by returning the first field alone -- a job
    saved with no description at all."""
    raw = '{"company": "AddBack", "job_description": " ## Core Mission\n* bullet one\n* bullet two", "remote": true}'
    out = _parse_json(raw)
    assert out["company"] == "AddBack"
    assert out["job_description"].endswith("bullet two"), out["job_description"]
    assert out["remote"] is True


def test_markdown_fence_is_stripped():
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_prose_around_the_object_is_ignored():
    assert _parse_json('Here is the JSON:\n{"a": 1}\nHope that helps!') == {"a": 1}


def test_truncated_output_still_yields_what_arrived():
    out = _parse_json('{"a": 1, "b": "unfinis')
    assert out["a"] == 1


def test_unparseable_output_raises():
    try:
        _parse_json("no json here at all")
        assert False, "must raise rather than invent a dict"
    except ValueError:
        pass


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except Exception as e:
                failures += 1
                print(f"  FAIL  {name}: {e.__class__.__name__}: {e}")
    print("FAILED" if failures else "all passed")
    sys.exit(1 if failures else 0)
