"""Stripe billing — plan gating, Checkout, Billing Portal, webhook verification.

ponytail: talks to Stripe's REST API over httpx (already a dependency) rather
than pulling in the stripe SDK. This is three API calls and one HMAC check; a
package for that is a package to keep upgraded. Reach for the SDK if we ever
need proration maths, invoice rendering, or its retry/idempotency helpers.

Nothing here trusts the client. `plan` is written only from a signature-verified
webhook, and the checkout session is always created against the server's own
idea of which user is calling.
"""
import hashlib
import hmac
import os
import time
from datetime import datetime
from typing import Optional, Tuple

import httpx
from loguru import logger
from sqlalchemy.orm import Session

from database import User

STRIPE_API = "https://api.stripe.com/v1"

# Free tier: 30 resume generations, lifetime, never resets.
FREE_RESUME_LIMIT = int(os.getenv("FREE_RESUME_LIMIT", "30"))

# Escape hatch for the operator's own accounts, so running the product you own
# doesn't burn a trial allowance. Comma-separated emails, matched lowercase.
UNLIMITED_EMAILS = {
    e.strip().lower()
    for e in os.getenv("BILLING_UNLIMITED_EMAILS", "").split(",")
    if e.strip()
}

# Stripe rejects a webhook whose timestamp is too old — this is the replay window.
WEBHOOK_TOLERANCE_SECONDS = 300


def _secret_key() -> str:
    return os.getenv("STRIPE_SECRET_KEY", "")


def _price_id() -> str:
    return os.getenv("STRIPE_PRICE_ID", "")


def _webhook_secret() -> str:
    return os.getenv("STRIPE_WEBHOOK_SECRET", "")


def is_configured() -> bool:
    """True when Stripe is wired up. When false the app still runs: the free
    tier works and the upgrade path reports itself as unavailable, rather than
    500ing on a missing key."""
    return bool(_secret_key() and _price_id())


class StripeError(RuntimeError):
    pass


def _request(method: str, path: str, data: Optional[dict] = None) -> dict:
    """Form-encoded call to the Stripe API. Stripe uses HTTP basic auth with the
    secret key as the username and an empty password."""
    if not _secret_key():
        raise StripeError("Stripe is not configured (STRIPE_SECRET_KEY unset)")
    try:
        res = httpx.request(
            method,
            f"{STRIPE_API}{path}",
            data=data or {},
            auth=(_secret_key(), ""),
            timeout=20.0,
        )
    except httpx.HTTPError as e:
        raise StripeError(f"Could not reach Stripe: {e}") from e

    body = res.json() if res.content else {}
    if res.status_code >= 400:
        msg = (body.get("error") or {}).get("message") or f"Stripe returned {res.status_code}"
        # Stripe error messages are safe to surface: they describe the request,
        # never the key.
        raise StripeError(msg)
    return body


# ── Plan / quota ──────────────────────────────────────────────────────────────

def is_pro(user: User) -> bool:
    if (user.email or "").lower() in UNLIMITED_EMAILS:
        return True
    return user.plan == "pro"


def resumes_remaining(user: User) -> Optional[int]:
    """Generations left on the free tier, or None when the user is unlimited."""
    if is_pro(user):
        return None
    return max(0, FREE_RESUME_LIMIT - (user.free_resumes_used or 0))


def can_generate_resume(user: User) -> bool:
    return is_pro(user) or (user.free_resumes_used or 0) < FREE_RESUME_LIMIT


def consume_resume_credit(db: Session, user_id: int) -> None:
    """Count one generation against the free allowance.

    Called only after the resume row is committed, so a failed generation never
    costs the user a credit. Pro users are not counted at all — if they later
    cancel, they fall back to whatever they had used while free.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user or is_pro(user):
        return
    user.free_resumes_used = (user.free_resumes_used or 0) + 1
    db.commit()


def plan_snapshot(user: User) -> dict:
    """What the billing UI needs, in one object."""
    return {
        "plan": "pro" if is_pro(user) else "free",
        "status": user.plan_status,
        "free_limit": FREE_RESUME_LIMIT,
        "free_used": min(user.free_resumes_used or 0, FREE_RESUME_LIMIT),
        "resumes_remaining": resumes_remaining(user),
        "current_period_end": (
            user.current_period_end.isoformat() if user.current_period_end else None
        ),
        "billing_configured": is_configured(),
        "has_subscription": bool(user.stripe_subscription_id),
    }


# ── Customer / Checkout / Portal ──────────────────────────────────────────────

def ensure_customer(db: Session, user: User) -> str:
    """Stripe customer id for this user, creating one on first use."""
    if user.stripe_customer_id:
        return user.stripe_customer_id
    body = _request("POST", "/customers", {
        "email": user.email,
        "metadata[user_id]": str(user.id),
    })
    user.stripe_customer_id = body["id"]
    db.commit()
    return user.stripe_customer_id


def create_checkout_session(db: Session, user: User, success_url: str, cancel_url: str) -> str:
    """Hosted Checkout URL for the Pro subscription."""
    if not _price_id():
        raise StripeError("Stripe is not configured (STRIPE_PRICE_ID unset)")
    customer_id = ensure_customer(db, user)
    body = _request("POST", "/checkout/sessions", {
        "mode": "subscription",
        "customer": customer_id,
        "line_items[0][price]": _price_id(),
        "line_items[0][quantity]": "1",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "allow_promotion_codes": "true",
        # Echoed back on the webhook so we can map the session to a user even if
        # the customer record is somehow missing its metadata.
        "client_reference_id": str(user.id),
        "metadata[user_id]": str(user.id),
        "subscription_data[metadata][user_id]": str(user.id),
    })
    return body["url"]


def create_portal_session(db: Session, user: User, return_url: str) -> str:
    """Billing Portal URL — Stripe hosts cancellation, card updates, invoices."""
    if not user.stripe_customer_id:
        raise StripeError("No billing account yet — subscribe first")
    body = _request("POST", "/billing_portal/sessions", {
        "customer": user.stripe_customer_id,
        "return_url": return_url,
    })
    return body["url"]


# ── Webhook ───────────────────────────────────────────────────────────────────

def verify_webhook(payload: bytes, signature_header: str) -> None:
    """Raise unless `payload` carries a valid, recent Stripe signature.

    Header looks like `t=<unix>,v1=<hex>,v1=<hex>`. Stripe signs
    "<timestamp>.<raw body>" with the endpoint secret. The raw bytes matter —
    re-serialising the JSON changes the digest and every event fails.
    """
    secret = _webhook_secret()
    if not secret:
        raise StripeError("Stripe is not configured (STRIPE_WEBHOOK_SECRET unset)")
    if not signature_header:
        raise StripeError("Missing Stripe-Signature header")

    timestamp = None
    signatures = []
    for part in signature_header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)

    if not timestamp or not signatures:
        raise StripeError("Malformed Stripe-Signature header")

    try:
        sent_at = int(timestamp)
    except ValueError:
        raise StripeError("Malformed Stripe-Signature timestamp")

    if abs(time.time() - sent_at) > WEBHOOK_TOLERANCE_SECONDS:
        raise StripeError("Stripe signature timestamp outside tolerance")

    expected = hmac.new(
        secret.encode(),
        f"{timestamp}.".encode() + payload,
        hashlib.sha256,
    ).hexdigest()

    # compare_digest, not ==, so a wrong signature can't be recovered by timing.
    if not any(hmac.compare_digest(expected, sig) for sig in signatures):
        raise StripeError("Stripe signature mismatch")


def _resolve_user(db: Session, *, user_id=None, customer_id=None) -> Optional[User]:
    if user_id:
        user = db.query(User).filter(User.id == int(user_id)).first()
        if user:
            return user
    if customer_id:
        return db.query(User).filter(User.stripe_customer_id == customer_id).first()
    return None


def _period_end(sub: dict) -> Optional[datetime]:
    ts = sub.get("current_period_end")
    if not ts:
        # Stripe moved this onto the subscription item in newer API versions.
        items = (sub.get("items") or {}).get("data") or []
        ts = items[0].get("current_period_end") if items else None
    return datetime.utcfromtimestamp(ts) if ts else None


# Statuses that still entitle the user to Pro. `past_due` stays entitled on
# purpose: Stripe is retrying the card, and cutting access off mid-dunning
# punishes people for an expired card rather than for not paying.
ENTITLED_STATUSES = {"active", "trialing", "past_due"}


def apply_event(db: Session, event: dict) -> Tuple[bool, str]:
    """Apply a verified webhook. Returns (changed, human-readable summary)."""
    event_type = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}

    if event_type == "checkout.session.completed":
        user = _resolve_user(
            db,
            user_id=obj.get("client_reference_id") or (obj.get("metadata") or {}).get("user_id"),
            customer_id=obj.get("customer"),
        )
        if not user:
            return False, "no matching user"
        user.stripe_customer_id = obj.get("customer") or user.stripe_customer_id
        user.stripe_subscription_id = obj.get("subscription") or user.stripe_subscription_id
        user.plan = "pro"
        user.plan_status = "active"
        db.commit()
        return True, f"user {user.id} upgraded to pro"

    if event_type in ("customer.subscription.created",
                      "customer.subscription.updated",
                      "customer.subscription.deleted"):
        user = _resolve_user(
            db,
            user_id=(obj.get("metadata") or {}).get("user_id"),
            customer_id=obj.get("customer"),
        )
        if not user:
            return False, "no matching user"

        status = obj.get("status", "")
        entitled = event_type != "customer.subscription.deleted" and status in ENTITLED_STATUSES

        user.plan = "pro" if entitled else "free"
        user.plan_status = status or ("canceled" if not entitled else None)
        user.current_period_end = _period_end(obj)
        user.stripe_subscription_id = obj.get("id") if entitled else None
        db.commit()
        return True, f"user {user.id} plan={user.plan} status={status}"

    return False, f"ignored {event_type}"


def record_event_once(db: Session, event_id: str, event_type: str) -> bool:
    """Claim an event id. False means it was already processed — skip it.

    Stripe retries until it sees a 2xx and can redeliver after one, so without
    this a retried `checkout.session.completed` would be applied twice.
    """
    from database import StripeEvent
    if db.query(StripeEvent).filter(StripeEvent.id == event_id).first():
        return False
    db.add(StripeEvent(id=event_id, event_type=event_type))
    try:
        db.commit()
    except Exception as e:
        # Two concurrent deliveries raced on the primary key; the other one won.
        db.rollback()
        logger.info(f"[Stripe] duplicate event {event_id} lost the insert race: {e}")
        return False
    return True
