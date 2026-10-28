"""drop_all must refuse the live database file name, wherever it lives."""
import os, subprocess, sys, tempfile


def test_drop_all_refuses_live_db():
    with tempfile.TemporaryDirectory() as d:
        code = ("from database import Base, engine\n"
                "Base.metadata.create_all(bind=engine)\n"
                "try:\n    Base.metadata.drop_all(bind=engine)\nexcept RuntimeError as e:\n    print('refused', e)\n")
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{d}/swarm.db", "PYTHONPATH": os.path.dirname(os.path.abspath(__file__))}
        out = subprocess.run([sys.executable, "-c", code], cwd=d, env=env, capture_output=True, text=True)
        assert "refused" in out.stdout, out.stderr[-500:]
