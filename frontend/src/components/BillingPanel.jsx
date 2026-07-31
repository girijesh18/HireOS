import React, { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'

const PRICE_LABEL = '$15'
const PRICE_PERIOD = 'per month'

// Stripe sends the browser back to #/settings?billing=success|cancelled. Read
// the flag once at module load and scrub the query string, so the panel can
// seed its own state from it instead of setting state inside an effect. Cleared
// after the first mount consumes it, so navigating back to Settings later in
// the session doesn't replay the message.
let billingRedirect = (() => {
  const flag = new URLSearchParams(window.location.search).get('billing')
  if (flag) {
    window.history.replaceState({}, '', window.location.pathname + window.location.hash)
  }
  return flag || ''
})()

function takeBillingRedirect() {
  const flag = billingRedirect
  billingRedirect = ''
  return flag
}

const REDIRECT_NOTICE = {
  cancelled: 'Checkout cancelled — no charge was made.',
  success: 'Payment received. Activating your subscription…',
}

const PRO_PERKS = [
  'Unlimited tailored resume generations',
  'Every model — Gemini, Groq, OpenRouter, local Ollama',
  'Cover letters, deep research, interview prep',
  'Cancel any time from the billing portal',
]

/**
 * Plan + usage panel. Checkout and cancellation are Stripe-hosted, so this
 * component never touches card data — it only redirects to a URL the backend
 * created and reports whatever the backend says the plan is.
 */
export default function BillingPanel() {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // Lazy initialiser: consumes the redirect flag during the first render.
  const [redirect] = useState(takeBillingRedirect)
  const [notice, setNotice] = useState(() => REDIRECT_NOTICE[redirect] || '')

  const load = useCallback(async () => {
    try {
      setStatus(await api.getBillingStatus())
      setError('')
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // The plan is set by the webhook, not by the redirect, so after a successful
  // checkout we poll until it lands rather than trusting the return URL.
  useEffect(() => {
    if (redirect !== 'success') return

    let tries = 0
    const tick = setInterval(async () => {
      tries += 1
      try {
        const s = await api.getBillingStatus()
        setStatus(s)
        if (s.plan === 'pro') {
          clearInterval(tick)
          setNotice('You’re on Pro. Unlimited resume generations are live.')
        }
      } catch { /* keep polling */ }
      if (tries >= 10) {
        clearInterval(tick)
        setNotice(n => n.startsWith('Payment received')
          ? 'Payment received. Your plan will activate shortly — refresh in a minute.'
          : n)
      }
    }, 2000)
    return () => clearInterval(tick)
  }, [redirect])

  const upgrade = async () => {
    setBusy(true); setError('')
    try {
      const { url } = await api.startCheckout()
      window.location.href = url
    } catch (e) {
      setError(e.message)
      setBusy(false)
    }
  }

  const manage = async () => {
    setBusy(true); setError('')
    try {
      const { url } = await api.openBillingPortal()
      window.location.href = url
    } catch (e) {
      setError(e.message)
      setBusy(false)
    }
  }

  if (!status) {
    return (
      <div className="panel" style={{ padding: '1.5rem' }}>
        {error ? <div className="alert alert-danger">{error}</div>
               : <span className="text-muted text-sm">Loading plan…</span>}
      </div>
    )
  }

  const isPro = status.plan === 'pro'
  const used = status.free_used ?? 0
  const limit = status.free_limit ?? 30
  const pct = Math.min(100, Math.round((used / limit) * 100))
  const exhausted = !isPro && used >= limit
  const meterColor = exhausted ? 'var(--danger)' : pct >= 80 ? 'var(--warning)' : 'var(--primary)'

  return (
    <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>

      {notice && <div className="alert alert-info">{notice}</div>}
      {error && <div className="alert alert-danger">{error}</div>}

      {/* Current plan */}
      <div className="panel" style={{
        padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem',
        borderLeft: `4px solid ${isPro ? 'var(--success)' : 'var(--primary)'}`,
      }}>
        <div className="flex justify-between items-start" style={{ gap: '1rem', flexWrap: 'wrap' }}>
          <div>
            <h3 style={{ fontSize: '1.1rem' }}>
              {isPro ? 'HireOS Pro' : 'Free plan'}
            </h3>
            <p style={{ fontSize: '0.85rem', color: 'var(--fg-muted)', marginTop: 4 }}>
              {isPro
                ? 'Unlimited resume generations.'
                : `${limit} resume generations, one time. They don’t reset.`}
            </p>
          </div>
          <span className="badge" style={{
            background: isPro ? 'var(--success-subtle)' : 'var(--surface-2)',
            color: isPro ? 'var(--success)' : 'var(--fg-muted)',
          }}>
            {isPro ? (status.status || 'active') : 'free'}
          </span>
        </div>

        {!isPro && (
          <div>
            <div className="flex justify-between" style={{ fontSize: '0.8rem', marginBottom: 6 }}>
              <span style={{ color: 'var(--fg-muted)' }}>Resume generations used</span>
              <span style={{ fontWeight: 600, color: exhausted ? 'var(--danger)' : 'var(--fg)' }}>
                {used} / {limit}
              </span>
            </div>
            <div style={{
              height: 8, borderRadius: 9999, background: 'var(--surface-2)',
              overflow: 'hidden', border: '1px solid var(--surface-border)',
            }}>
              <div style={{
                width: `${pct}%`, height: '100%', background: meterColor,
                transition: 'width 0.4s ease, background 0.3s ease',
              }} />
            </div>
            {exhausted && (
              <p style={{ fontSize: '0.8rem', color: 'var(--danger)', marginTop: 8 }}>
                You’ve used every free generation. Upgrade to keep tailoring resumes.
              </p>
            )}
          </div>
        )}

        {isPro && status.current_period_end && (
          <p style={{ fontSize: '0.8rem', color: 'var(--fg-subtle)' }}>
            {status.status === 'past_due'
              ? 'Payment failed — update your card in the billing portal to avoid losing access.'
              : `Renews ${new Date(status.current_period_end).toLocaleDateString()}`}
          </p>
        )}

        <div className="flex gap-sm" style={{ flexWrap: 'wrap' }}>
          {!isPro && (
            <button className="btn btn-primary" onClick={upgrade}
              disabled={busy || !status.billing_configured}>
              {busy ? 'Opening Stripe…' : `Upgrade to Pro — ${PRICE_LABEL}/mo`}
            </button>
          )}
          {status.has_subscription && (
            <button className="btn btn-outline" onClick={manage} disabled={busy}>
              {busy ? 'Opening…' : 'Manage billing'}
            </button>
          )}
        </div>

        {/* .alert is a flex row -- bare text and <code> would each become their
            own flex item and column-wrap. One child keeps it a paragraph. */}
        {!isPro && !status.billing_configured && (
          <div className="alert alert-warning">
            <span>
              Billing isn’t configured on this server yet. Set <code>STRIPE_SECRET_KEY</code>,{' '}
              <code>STRIPE_PRICE_ID</code> and <code>STRIPE_WEBHOOK_SECRET</code> to enable upgrades.
            </span>
          </div>
        )}
      </div>

      {/* What Pro gets you */}
      {!isPro && (
        <div className="panel" style={{ padding: '1.5rem' }}>
          <div className="flex justify-between items-start" style={{ gap: '1rem', flexWrap: 'wrap' }}>
            <h3 style={{ fontSize: '1.1rem' }}>What Pro includes</h3>
            <div style={{ textAlign: 'right' }}>
              <div style={{
                fontFamily: 'var(--font-display)', fontSize: '1.8rem',
                fontWeight: 800, lineHeight: 1,
              }}>{PRICE_LABEL}</div>
              <div style={{ fontSize: '0.75rem', color: 'var(--fg-subtle)' }}>{PRICE_PERIOD}</div>
            </div>
          </div>
          <ul style={{ listStyle: 'none', marginTop: '1rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {PRO_PERKS.map(p => (
              <li key={p} style={{ display: 'flex', gap: '0.55rem', alignItems: 'flex-start', fontSize: '0.87rem' }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--success)"
                  strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
                  style={{ flexShrink: 0, marginTop: 2 }}>
                  <path d="M20 6L9 17l-5-5" />
                </svg>
                <span>{p}</span>
              </li>
            ))}
          </ul>
          <p style={{ fontSize: '0.75rem', color: 'var(--fg-subtle)', marginTop: '1rem' }}>
            Payments are handled by Stripe. HireOS never sees or stores your card details.
          </p>
        </div>
      )}
    </div>
  )
}
