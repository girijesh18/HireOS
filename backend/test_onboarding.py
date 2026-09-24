"""Onboarding + admin-panel tests.

Run: cd backend && python test_onboarding.py
"""
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///./test_onboarding.db"
os.environ["ADMIN_EMAILS"] = "boss@hireos.test"
os.environ["LOG_DIR"] = "./logs"

import io
import json

import connectors
import main
from database import Base, MasterResumeComponent, SessionLocal, Settings, User, engine, init_db
from fastapi import HTTPException


def fresh_db():
    Base.metadata.drop_all(bind=engine)
    init_db()
    return SessionLocal()


def make_user(db, email="a@b.com"):
    u = User(email=email, password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# Dynamic keys on purpose: nothing in the design may assume a fixed field set.
ANSWERS = [
    {"key": "target_role", "label": "Target role", "value": "Staff Data Engineer in fintech"},
    {"key": "visa_status", "label": "Work authorization", "value": "needs sponsorship"},
    {"key": "weird_bespoke_key_42", "label": "Conference talks", "value": "spoke at PyCon 2025"},
    {"key": "blank", "label": "Ignored", "value": ""},
]


# ── The trap this whole design turns on ───────────────────────────────────────

def test_rendered_context_classifies_as_resume_not_qa():
    """If the classifier files this as "qa", _resume_components drops it and the
    onboarding answers never reach a single prompt -- silently."""
    md = main._render_career_context(ANSWERS)
    assert main._classify_component(main.ONBOARDING_COMPONENT_NAME, md) == "resume", md


def test_arbitrary_keys_survive_the_round_trip():
    """The schema is per-user: a key no one anticipated must persist and render."""
    md = main._render_career_context(ANSWERS)
    assert "spoke at PyCon 2025" in md
    assert "Conference talks" in md


def test_document_facts_are_not_repeated_into_the_prompt():
    """Facts read out of an uploaded document must not be rendered back: that
    document is already a master-resume component, so restating it would make
    every generation pay twice for the same content."""
    facts = [
        {"key": "current_company", "label": "Current company", "value": "Zenith", "source": "document"},
        {"key": "target_role", "label": "Target role", "value": "Staff DE", "source": "answer"},
    ]
    md = main._render_career_context(facts)
    assert "Staff DE" in md, "what the user told us must be in the prompt"
    assert "Zenith" not in md, "document facts must not be duplicated into the prompt"


def test_answering_a_document_key_promotes_it_into_the_prompt():
    """If the user answers about something the document covered, that is them
    correcting it -- it has to start reaching the prompt."""
    existing = [{"key": "location", "label": "Location", "value": "Bengaluru", "source": "document"}]
    merged = main._merge_facts(existing, [{"key": "location", "label": "Location",
                                           "value": "moving to Toronto", "source": "answer"}])
    assert merged[0]["source"] == "answer"
    assert "moving to Toronto" in main._render_career_context(merged)


def test_merge_keeps_order_and_ignores_blanks():
    base = [{"key": "a", "label": "A", "value": "1", "source": "answer"}]
    merged = main._merge_facts(base, [
        {"key": "b", "label": "B", "value": "2"},
        {"key": "a", "label": "A", "value": ""},          # blank must not wipe "1"
    ])
    assert [f["key"] for f in merged] == ["a", "b"]
    assert merged[0]["value"] == "1"


def test_rendered_context_avoids_both_qa_heuristics():
    md = main._render_career_context(ANSWERS)
    import re
    assert len(re.findall(r"\*\*q\d", md.lower())) < 3, "Q-labels would trip the qa classifier"
    assert md.count("?") <= 40, "too many question marks trips the qa classifier"


def test_unanswered_questions_are_dropped():
    md = main._render_career_context(ANSWERS)
    assert "Staff Data Engineer in fintech" in md
    assert "Ignored" not in md, "a skipped question must not leave a stub bullet"


# ── Persistence ───────────────────────────────────────────────────────────────

def test_answers_reach_the_master_resume():
    db = fresh_db()
    u = make_user(db)
    main._upsert_career_context(db, u.id, main._render_career_context(ANSWERS))
    master = main.get_master_resume(db, u.id)
    assert "Staff Data Engineer in fintech" in master, master
    db.close()


def test_resaving_upserts_instead_of_duplicating():
    db = fresh_db()
    u = make_user(db)
    main._upsert_career_context(db, u.id, "## Professional Summary\n\n- First.")
    main._upsert_career_context(db, u.id, "## Professional Summary\n\n- Second.")
    rows = db.query(MasterResumeComponent).filter(
        MasterResumeComponent.user_id == u.id,
        MasterResumeComponent.name == main.ONBOARDING_COMPONENT_NAME,
    ).all()
    assert len(rows) == 1, f"expected one component, got {len(rows)}"
    assert "Second" in rows[0].content_text
    db.close()


def test_answers_round_trip_for_editing():
    db = fresh_db()
    u = make_user(db)
    main._set_user_setting(db, u.id, main.ONBOARDING_PROFILE_KEY, json.dumps({"facts": ANSWERS}))
    assert main._profile_facts(db, u.id)[0]["key"] == "target_role"
    db.close()


def test_corrupt_profile_row_does_not_explode():
    db = fresh_db()
    u = make_user(db)
    main._set_user_setting(db, u.id, main.ONBOARDING_PROFILE_KEY, "not json{")
    assert main._profile_facts(db, u.id) == []
    db.close()


# ── docx ingestion ────────────────────────────────────────────────────────────

def test_docx_upload_extracts_text():
    from docx import Document

    doc = Document()
    doc.add_paragraph("Girijesh Singh")
    doc.add_paragraph("Senior ML Engineer, 8 years")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Python"
    table.rows[0].cells[1].text = "PyTorch"
    buf = io.BytesIO()
    doc.save(buf)

    # The extraction path from upload_resume_file, exercised directly -- the
    # endpoint around it is plain FastAPI plumbing.
    parsed = Document(io.BytesIO(buf.getvalue()))
    blocks = [p.text for p in parsed.paragraphs]
    for t in parsed.tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    text = "\n".join(b for b in blocks if b.strip())
    assert "Senior ML Engineer" in text
    assert "Python | PyTorch" in text


# ── Admin ─────────────────────────────────────────────────────────────────────

def test_require_admin_rejects_everyone_not_on_the_list():
    db = fresh_db()
    boss = make_user(db, "boss@hireos.test")
    randomer = make_user(db, "randomer@x.com")
    assert main.require_admin(boss) is boss
    try:
        main.require_admin(randomer)
        raise AssertionError("a non-admin with a valid JWT must be rejected")
    except HTTPException as e:
        assert e.status_code == 403, e.status_code
    db.close()


def test_auth_me_carries_the_flags_the_ui_branches_on():
    """Caught in a browser run: the nav and the model picker both branch on
    /auth/me, and login set neither -- an admin saw no Admin item until they
    happened to reload, and a platform-key user saw a model picker the backend
    silently overrides."""
    db = fresh_db()
    boss = make_user(db, "boss@hireos.test")
    plain = make_user(db, "plain@x.com")
    main.set_platform_setting(db, "platform_key_gemini", "platform-key")

    me = main.me(db=db, current_user=boss)
    assert me["is_admin"] is True
    assert me["on_platform_key"] is True, "no own key -> the picker must be hidden"

    db.add(Settings(key="gemini_api_key", value="own-key", user_id=plain.id))
    db.commit()
    me2 = main.me(db=db, current_user=plain)
    assert me2["is_admin"] is False
    assert me2["on_platform_key"] is False, "own key -> the picker must stay"
    db.close()


def test_platform_settings_are_global_and_invisible_to_users():
    db = fresh_db()
    u = make_user(db)
    main.set_platform_setting(db, "platform_provider", "anthropic")
    main.set_platform_setting(db, "platform_model", "claude-opus-5")
    assert main.platform_selection(db) == ("anthropic", "claude-opus-5")
    # ROUTER_PROVIDER maps our name onto the one the router dispatches on.
    assert main.platform_llm(db) == "claude:claude-opus-5"
    # A global row must not leak into any per-user settings query.
    assert db.query(Settings).filter(Settings.user_id == u.id).count() == 0
    db.close()


def test_platform_selection_defaults_before_an_admin_picks_one():
    db = fresh_db()
    assert main.platform_selection(db) == (connectors.DEFAULT_PROVIDER, connectors.DEFAULT_MODEL)
    db.close()


def test_saved_key_wins_over_the_env_bootstrap():
    db = fresh_db()
    os.environ["PLATFORM_GEMINI_API_KEY"] = "from-env"
    assert main.platform_key_for(db, "gemini") == "from-env"
    main.set_platform_setting(db, "platform_key_gemini", "from-db")
    assert main.platform_key_for(db, "gemini") == "from-db"
    del os.environ["PLATFORM_GEMINI_API_KEY"]
    db.close()


def test_own_key_is_not_metered_platform_key_is():
    db = fresh_db()
    byok = make_user(db, "byok@x.com")
    freeloader = make_user(db, "free@x.com")
    db.add(Settings(key="gemini_api_key", value="user-own-key", user_id=byok.id))
    db.commit()
    main.set_platform_setting(db, "platform_key_gemini", "platform-key")

    own = main.get_llm_router(db, byok.id)
    assert own.on_usage is None, "a user on their own key must not be metered"
    assert own.gemini_key == "user-own-key"

    metered = main.get_llm_router(db, freeloader.id)
    assert metered.on_usage is not None, "a user on the platform key must be metered"
    assert metered.gemini_key == "platform-key"

    assert not main.on_platform_key(db, byok.id)
    assert main.on_platform_key(db, freeloader.id)
    db.close()


def test_github_token_survives_the_swap_to_the_platform_key():
    """github_token is not a provider key -- if it were treated as one, having
    it would wrongly count as BYOK, and losing it would silently drop project
    context from every free-tier resume."""
    db = fresh_db()
    u = make_user(db)
    db.add(Settings(key="github_token", value="ghp_x", user_id=u.id))
    db.commit()
    main.set_platform_setting(db, "platform_key_gemini", "platform-key")

    assert main.on_platform_key(db, u.id), "a github token alone is not BYOK"
    router = main.get_llm_router(db, u.id)
    assert router.github_token == "ghp_x", "github context must survive the swap"
    assert router.gemini_key == "platform-key"
    db.close()


def test_free_users_get_the_admins_model_whatever_they_ask_for():
    db = fresh_db()
    u = make_user(db)
    main.set_platform_setting(db, "platform_key_gemini", "platform-key")
    main.set_platform_setting(db, "platform_provider", "gemini")
    main.set_platform_setting(db, "platform_model", "gemini-2.5-flash")
    assert main.resolve_llm(db, u.id, requested="claude-opus-5") == "gemini:gemini-2.5-flash"
    db.close()


def test_a_provider_with_no_key_cannot_be_made_active():
    """OpenRouter lists its catalogue publicly but still needs a key to answer a
    prompt. Selecting it keyless left the free tier with an empty router --
    caught in a live run, so it gets a test."""
    db = fresh_db()
    boss = make_user(db, "boss@hireos.test")
    try:
        main.admin_set_platform_model(
            main.PlatformModelIn(provider="openrouter", model="anything"), db=db, _=boss)
        raise AssertionError("a keyless provider must not become the free-tier provider")
    except HTTPException as e:
        assert e.status_code == 400
        assert "API key" in e.detail, e.detail
    db.close()


def test_gemini_reports_a_bad_key_as_a_key_problem_not_an_http_code():
    import httpx as _httpx

    class FakeResponse:
        status_code = 400
        text = '{"error":{"status":"INVALID_ARGUMENT","message":"API_KEY_INVALID"}}'

    err = _httpx.HTTPStatusError("bad", request=None, response=FakeResponse())
    assert connectors._http_error("gemini", err) == "gemini: key rejected (invalid API key)"


def test_connector_reports_a_bad_key_instead_of_raising():
    models, error = connectors.list_models("gemini", "", refresh=True)
    assert models == [] and error == "No API key configured", (models, error)
    models, error = connectors.list_models("nonsense", "k", refresh=True)
    assert "Unknown provider" in error


def test_connector_cache_holds_then_refreshes():
    calls = {"n": 0}

    def counting_fetch(key):
        calls["n"] += 1
        return [{"id": "m1", "label": "M1", "extra": None}]

    original = connectors._FETCHERS["openai"]
    connectors._FETCHERS["openai"] = counting_fetch
    try:
        connectors.list_models("openai", "sk-test", refresh=True)
        connectors.list_models("openai", "sk-test")          # cached
        assert calls["n"] == 1, f"expected one fetch, got {calls['n']}"
        connectors.list_models("openai", "sk-test", refresh=True)
        assert calls["n"] == 2, "refresh=1 must bust the cache"
    finally:
        connectors._FETCHERS["openai"] = original


def test_gemini_parser_drops_models_that_cannot_generate():
    """The list endpoint also returns embedding models; putting one in a text
    picker means a runtime failure for whoever selects it."""
    payload = {"models": [
        {"name": "models/gemini-2.5-flash", "displayName": "Gemini 2.5 Flash",
         "supportedGenerationMethods": ["generateContent", "countTokens"]},
        {"name": "models/text-embedding-004", "displayName": "Embedding",
         "supportedGenerationMethods": ["embedContent"]},
    ]}

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return payload

    original = connectors.httpx.get
    connectors.httpx.get = lambda *a, **k: FakeResp()
    try:
        models, error = connectors.list_models("gemini", "key", refresh=True)
    finally:
        connectors.httpx.get = original
    assert error is None
    assert [m["id"] for m in models] == ["gemini-2.5-flash"], models


def test_model_catalogue_uses_the_users_own_key_then_the_platforms():
    """The picker must list what the user's own key can reach; only a user
    without one falls back to the platform's. A masked display value is not a
    key -- treating it as one lists the wrong catalogue."""
    db = fresh_db()
    u = make_user(db, "catalogue@x.com")
    seen = []
    original = connectors.list_models

    def fake(provider, key, refresh=False):
        seen.append(key)
        return [{"id": "some/model", "label": "Some Model", "extra": "free"}], None

    connectors.list_models = fake
    try:
        main.set_platform_setting(db, "platform_key_openrouter", "platform-key")
        out = main.get_provider_models(provider="openrouter", refresh=False, db=db, current_user=u)
        assert out["models"][0]["extra"] == "free", "pricing must reach the picker"

        db.add(Settings(key="openrouter_api_key", value="sk-or-mine", user_id=u.id))
        db.commit()
        main.get_provider_models(provider="openrouter", refresh=False, db=db, current_user=u)

        db.query(Settings).filter(Settings.user_id == u.id).update({"value": "sk-or\u2022\u2022\u2022\u2022"})
        db.commit()
        main.get_provider_models(provider="openrouter", refresh=False, db=db, current_user=u)
    finally:
        connectors.list_models = original

    assert seen == ["platform-key", "sk-or-mine", "platform-key"], seen

    try:
        main.get_provider_models(provider="nope", refresh=False, db=db, current_user=u)
        assert False, "an unknown provider must be rejected, not fetched"
    except HTTPException as e:
        assert e.status_code == 400
    db.close()


def test_openrouter_defaults_come_from_the_live_catalogue():
    """A hardcoded free id rots: the previous default 404'd for every user on
    "OpenRouter (Free)" long after OpenRouter retired it."""
    import llm_router

    catalogue = [
        {"id": "cohere/north-mini-code:free", "label": "x", "extra": "free"},
        {"id": "qwen/qwen3.8-27b:free", "label": "x", "extra": "free"},
        {"id": "meta-llama/llama-4-70b:free", "label": "x", "extra": "free"},
        {"id": "openai/gpt-5", "label": "x", "extra": "$1.00 in / $2.00 out per M"},
    ]
    original = connectors.list_models
    try:
        paid = llm_router._OPENROUTER_PAID_FALLBACK

        connectors.list_models = lambda p, k, refresh=False: (catalogue, None)
        # Preferred family first, then the rest, and a paid id last -- the free
        # pool 429s often enough that one candidate is not a plan.
        assert llm_router._openrouter_defaults("k") == [
            "meta-llama/llama-4-70b:free", "qwen/qwen3.8-27b:free",
            "cohere/north-mini-code:free", paid,
        ]
        assert "openai/gpt-5" not in llm_router._openrouter_defaults("k"), "paid model is not free"

        # No free model, or the catalogue call itself failing, must still leave a
        # servable id behind rather than a dead one.
        connectors.list_models = lambda p, k, refresh=False: ([], "openrouter: HTTP 500")
        assert llm_router._openrouter_defaults("k") == [paid]

        def boom(*a, **k):
            raise RuntimeError("network down")

        connectors.list_models = boom
        assert llm_router._openrouter_defaults("k") == [paid]
    finally:
        connectors.list_models = original


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
