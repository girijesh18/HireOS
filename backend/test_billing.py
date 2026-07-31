"""Checks for the billing paths that must not be wrong: webhook signature
verification, webhook idempotency, and the free-tier quota accounting.

Run: python test_billing.py
"""
import hashlib
import hmac
import json
import os
import time

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_billing.db")
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test_secret"
os.environ["BILLING_UNLIMITED_EMAILS"] = "owner@hireos.test"

import billing  # noqa: E402
from database import Base, SessionLocal, StripeEvent, User, engine  # noqa: E402


def _sign(payload: bytes, secret: str, timestamp: int) -> str:
    digest = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def test_signature_accepts_valid():
    payload = b'{"id":"evt_1","type":"checkout.session.completed"}'
    header = _sign(payload, "whsec_test_secret", int(time.time()))
    billing.verify_webhook(payload, header)  # must not raise


def test_signature_rejects_tampered_body():
    payload = b'{"id":"evt_1","type":"checkout.session.completed"}'
    header = _sign(payload, "whsec_test_secret", int(time.time()))
    try:
        billing.verify_webhook(payload + b" ", header)
    except billing.StripeError:
        return
    raise AssertionError("tampered payload passed signature check")


def test_signature_rejects_wrong_secret():
    payload = b'{"id":"evt_1"}'
    header = _sign(payload, "whsec_attacker", int(time.time()))
    try:
        billing.verify_webhook(payload, header)
    except billing.StripeError:
        return
    raise AssertionError("signature from the wrong secret was accepted")


def test_signature_rejects_replay():
    payload = b'{"id":"evt_1"}'
    stale = int(time.time()) - billing.WEBHOOK_TOLERANCE_SECONDS - 60
    try:
        billing.verify_webhook(payload, _sign(payload, "whsec_test_secret", stale))
    except billing.StripeError:
        return
    raise AssertionError("replayed old signature was accepted")


def test_quota_blocks_at_limit_and_pro_is_unlimited():
    db = fresh_db()
    try:
        free = User(email="free@test.local", password_hash="", free_resumes_used=0, plan="free")
        db.add(free)
        db.commit()

        assert billing.can_generate_resume(free)
        assert billing.resumes_remaining(free) == billing.FREE_RESUME_LIMIT

        for _ in range(billing.FREE_RESUME_LIMIT):
            billing.consume_resume_credit(db, free.id)
        db.refresh(free)

        assert free.free_resumes_used == billing.FREE_RESUME_LIMIT
        assert not billing.can_generate_resume(free), "quota did not block at the limit"
        assert billing.resumes_remaining(free) == 0

        # Upgrading lifts the block, and Pro usage is not counted.
        free.plan = "pro"
        db.commit()
        assert billing.can_generate_resume(free)
        assert billing.resumes_remaining(free) is None
        billing.consume_resume_credit(db, free.id)
        db.refresh(free)
        assert free.free_resumes_used == billing.FREE_RESUME_LIMIT, "pro usage burned a free credit"
    finally:
        db.close()


def test_unlimited_email_bypasses_quota():
    db = fresh_db()
    try:
        owner = User(email="owner@hireos.test", password_hash="",
                     free_resumes_used=999, plan="free")
        db.add(owner)
        db.commit()
        assert billing.is_pro(owner)
        assert billing.can_generate_resume(owner)
    finally:
        db.close()


def test_webhook_is_idempotent():
    db = fresh_db()
    try:
        user = User(email="buyer@test.local", password_hash="",
                    stripe_customer_id="cus_123", plan="free")
        db.add(user)
        db.commit()

        event = {
            "id": "evt_dup",
            "type": "checkout.session.completed",
            "data": {"object": {
                "customer": "cus_123",
                "subscription": "sub_123",
                "client_reference_id": str(user.id),
            }},
        }

        assert billing.record_event_once(db, event["id"], event["type"]) is True
        billing.apply_event(db, event)
        db.refresh(user)
        assert user.plan == "pro"

        # Stripe redelivers the same event — must be skipped, not reapplied.
        assert billing.record_event_once(db, event["id"], event["type"]) is False
        assert db.query(StripeEvent).count() == 1
    finally:
        db.close()


def test_cancellation_drops_to_free():
    db = fresh_db()
    try:
        user = User(email="churn@test.local", password_hash="", plan="pro",
                    stripe_customer_id="cus_9", stripe_subscription_id="sub_9",
                    free_resumes_used=30)
        db.add(user)
        db.commit()

        billing.apply_event(db, {
            "id": "evt_cancel",
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_9", "customer": "cus_9", "status": "canceled"}},
        })
        db.refresh(user)

        assert user.plan == "free"
        assert user.stripe_subscription_id is None
        # Free tier is lifetime, so a churned user who already spent it stays blocked.
        assert not billing.can_generate_resume(user)
    finally:
        db.close()


def test_past_due_keeps_access():
    db = fresh_db()
    try:
        user = User(email="dunning@test.local", password_hash="", plan="pro",
                    stripe_customer_id="cus_7")
        db.add(user)
        db.commit()

        billing.apply_event(db, {
            "id": "evt_pastdue",
            "type": "customer.subscription.updated",
            "data": {"object": {"id": "sub_7", "customer": "cus_7", "status": "past_due"}},
        })
        db.refresh(user)
        assert user.plan == "pro", "past_due should keep access while Stripe retries the card"
    finally:
        db.close()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
