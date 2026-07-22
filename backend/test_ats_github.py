"""Checks on the GitHub enrichment path that made ATS scoring hang past its
300s budget: it must never sleep on a spent rate limit, and it must fan the
per-repo contributor calls out instead of walking them one at a time.

Run: python test_ats_github.py
"""

import sys
import os
import time
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "hiring_agent"))

import github


class _Resp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code=200, headers=None, payload=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload if payload is not None else []

    def json(self):
        return self._payload


def test_exhausted_rate_limit_raises_instead_of_sleeping():
    reset_at = int(time.time()) + 3600  # an hour out — the old code slept for it
    github.requests = types.SimpleNamespace(
        get=lambda *a, **k: _Resp(headers={
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Limit": "60",
            "X-RateLimit-Reset": str(reset_at),
        }),
        exceptions=types.SimpleNamespace(RequestException=Exception),
    )

    started = time.monotonic()
    try:
        github._fetch_github_api("https://api.github.com/users/x/repos")
        raise AssertionError("expected RateLimited")
    except github.RateLimited:
        pass
    elapsed = time.monotonic() - started
    assert elapsed < 1, f"rate-limit path blocked for {elapsed:.1f}s — it must not sleep"


def test_contributors_are_fetched_concurrently():
    repos = [{"name": f"repo{i}", "fork": False} for i in range(16)]

    def fake_api(api_url, params=None):
        if api_url.endswith("/repos"):
            return 200, repos
        time.sleep(0.2)  # stand in for a real contributors round-trip
        return 200, [{"login": "someone", "contributions": 3}]

    github._fetch_github_api = fake_api

    started = time.monotonic()
    projects = github.fetch_all_github_repos("https://github.com/someone")
    elapsed = time.monotonic() - started

    assert len(projects) == 16, f"expected 16 projects, got {len(projects)}"
    # Serial would be 16 * 0.2 = 3.2s; 8 workers should land near 0.4s.
    assert elapsed < 1.5, f"contributor fetches look serial ({elapsed:.1f}s)"


def test_spent_quota_midway_still_returns_projects():
    repos = [{"name": f"repo{i}", "fork": False} for i in range(4)]

    def fake_api(api_url, params=None):
        if api_url.endswith("/repos"):
            return 200, repos
        raise github.RateLimited("quota gone")

    github._fetch_github_api = fake_api

    projects = github.fetch_all_github_repos("https://github.com/someone")
    assert len(projects) == 4, "a spent quota should degrade enrichment, not drop repos"
    assert all(p["contributor_count"] == 0 for p in projects)


if __name__ == "__main__":
    test_exhausted_rate_limit_raises_instead_of_sleeping()
    test_contributors_are_fetched_concurrently()
    test_spent_quota_midway_still_returns_projects()
    print("ok")
