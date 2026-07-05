"""
HireOS integration wrapper around the vendored hiring-agent scorer.

Usage:  python evaluate.py <resume.pdf>
Runs the full Resume-to-Score pipeline (PDF extract -> GitHub enrich -> LLM
evaluation) and prints ONE line of JSON to stdout: the machine-readable score.
All of the pipeline's own stdout chatter is swallowed; logging still goes to
stderr so failures are debuggable. Exit code is always 0 — a scoring failure is
reported as {"error": ...} JSON, never a crash, so the caller can treat ATS
scoring as best-effort.
"""

import sys
import io
import json
import contextlib

import score  # vendored orchestrator (score.main)

# Category maxima, mirrored from score.print_evaluation_results.
CATEGORY_MAXES = {
    "open_source": 35,
    "self_projects": 30,
    "production": 25,
    "technical_skills": 10,
}
MAX_POSSIBLE = sum(CATEGORY_MAXES.values()) + 20  # 100 categories + 20 bonus


def compute_total(ev) -> float:
    """Overall score with the same capping rules the CLI printout uses."""
    total = 0.0
    if ev.scores:
        for name, cat in ev.scores.model_dump().items():
            cap = CATEGORY_MAXES.get(name, cat["max"])
            total += min(cat["score"], cap)
    if ev.bonus_points:
        total += ev.bonus_points.total
    if ev.deductions:
        total -= ev.deductions.total
    return min(round(total, 1), MAX_POSSIBLE)


def main(pdf_path: str) -> dict:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):  # mute the pipeline's print()s
        ev = score.main(pdf_path)
    if ev is None:
        return {"error": "extraction_failed"}
    return {
        "total": compute_total(ev),
        "max": 100,
        "scores": ev.scores.model_dump() if ev.scores else {},
        "bonus_points": ev.bonus_points.model_dump() if ev.bonus_points else None,
        "deductions": ev.deductions.model_dump() if ev.deductions else None,
        "key_strengths": ev.key_strengths or [],
        "areas_for_improvement": ev.areas_for_improvement or [],
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "no_pdf_path"}))
        sys.exit(0)
    try:
        result = main(sys.argv[1])
    except Exception as e:  # never crash the caller's background task
        result = {"error": str(e)}
    print(json.dumps(result))
