"""Auth boundaries that a mistake would quietly widen.

Covers the three pieces added for MCP access:
  - the SSO return_to allowlist (an open redirect here leaks a session JWT)
  - the signed OAuth state that carries it
  - MCP token revocation (a year-long token must be killable)

Run with a scratch DB:
    DATABASE_URL=sqlite:///./test_auth.db python test_auth_hardening.py
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(tempfile.mkdtemp(), "auth.db"))
os.environ["OAUTH_RETURN_ALLOWLIST"] = "https://hireos-mcp.fly.dev/oauth/sso-return"

from database import SessionLocal, User, Settings, init_db  # noqa: E402
import main  # noqa: E402


def test_return_to_allowlist_is_exact():
    allowed = "https://hireos-mcp.fly.dev/oauth/sso-return"
    assert main._allowed_return_to(allowed)
    assert main._allowed_return_to(allowed + "/")  # trailing slash only
    # Every one of these would hand a session JWT to somebody else.
    for bad in [
        "https://hireos-mcp.fly.dev/oauth/sso-return/../../evil",
        "https://hireos-mcp.fly.dev/oauth/sso-return?next=https://evil.example",
        "https://hireos-mcp.fly.dev.evil.example/oauth/sso-return",
        "https://evil.example/oauth/sso-return",
        "http://hireos-mcp.fly.dev/oauth/sso-return",  # downgraded scheme
        "",
        None,
    ]:
        assert not main._allowed_return_to(bad), f"should reject {bad!r}"


def test_oauth_state_round_trip():
    state = main._make_oauth_state("google", "https://hireos-mcp.fly.dev/oauth/sso-return", "nonce-1")
    payload = main._oauth_state_payload(state, "google")
    assert payload["rt"] == "https://hireos-mcp.fly.dev/oauth/sso-return"
    assert payload["rs"] == "nonce-1"
    # A state minted for one provider must not validate for the other.
    assert main._oauth_state_payload(state, "github") is None
    assert main._oauth_state_payload("garbage", "google") is None
    assert main._oauth_state_payload("", "google") is None


def _fake_credentials(token):
    class C:
        credentials = token
    return C()


def test_mcp_token_revocation():
    init_db()
    db = SessionLocal()
    db.query(Settings).delete()
    db.query(User).filter(User.email == "mcp@test.local").delete()
    user = User(email="mcp@test.local", password_hash="")
    db.add(user)
    db.commit()
    db.refresh(user)

    jti = "jti-one"
    db.add(Settings(key=main.MCP_TOKEN_JTI_KEY, value=jti, user_id=user.id))
    db.commit()
    token = main._create_token(user.email, days=365, extra={"typ": "mcp", "jti": jti})
    assert main.get_current_user(_fake_credentials(token), db).id == user.id

    # Regenerating rotates the jti, so the old token stops working.
    row = db.query(Settings).filter(Settings.key == main.MCP_TOKEN_JTI_KEY,
                                    Settings.user_id == user.id).first()
    row.value = "jti-two"
    db.commit()
    try:
        main.get_current_user(_fake_credentials(token), db)
        raise AssertionError("revoked MCP token should not authenticate")
    except Exception as e:
        assert getattr(e, "status_code", None) == 401, e

    # A normal session token has no jti and is unaffected by rotation.
    session = main._create_token(user.email)
    assert main.get_current_user(_fake_credentials(session), db).id == user.id
    db.close()


if __name__ == "__main__":
    test_return_to_allowlist_is_exact()
    test_oauth_state_round_trip()
    test_mcp_token_revocation()
    print("ok")
