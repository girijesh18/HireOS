"""Loaded by pytest before any test module. Tests must never open the live swarm.db:
a test that imports main without setting DATABASE_URL would otherwise bind the app
to it, and a later drop_all wipes production (happened 2026-10-24)."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_default.db")
assert "swarm.db" not in os.environ["DATABASE_URL"], "tests must not use the live database"
