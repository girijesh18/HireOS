import React, { useState, useEffect } from 'react'
import { api } from '../api/client'

// Platforms shown in the caps editor. Anything else falls under "default".
const CAP_PLATFORMS = [
  { id: 'linkedin', label: 'LinkedIn' },
  { id: 'indeed', label: 'Indeed' },
  { id: 'greenhouse', label: 'Greenhouse' },
  { id: 'lever', label: 'Lever' },
  { id: 'ashby', label: 'Ashby' },
  { id: 'default', label: 'Everything else' },
]

// A cap is either n or [lo, hi]; the editor shows both as "lo–hi" text.
const capToText = (c) => (Array.isArray(c) ? `${c[0]}-${c[1]}` : String(c ?? ''))
function textToCap(t) {
  const m = String(t).trim().match(/^(\d+)\s*[-–]\s*(\d+)$/)
  if (m) return [Number(m[1]), Number(m[2])]
  const n = Number(String(t).trim())
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : null
}

/**
 * Auto-apply controls for the HireOS apply worker. The backend enforces all of
 * it -- the agent only asks /api/apply/next for work -- so this panel is the one
 * place volume and approval are decided.
 */
export default function AutoApplyPanel() {
  const [cfg, setCfg] = useState(null)
  const [status, setStatus] = useState(null)
  const [caps, setCaps] = useState({})
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    Promise.all([api.getApplyConfig(), api.getApplyStatus()])
      .then(([c, s]) => {
        if (cancelled) return
        setCfg(c)
        setStatus(s)
        setCaps(Object.fromEntries(CAP_PLATFORMS.map(p => [p.id, capToText(c.caps[p.id])])))
      })
      .catch(e => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [])

  const save = async () => {
    const parsed = {}
    for (const p of CAP_PLATFORMS) {
      const cap = textToCap(caps[p.id])
      if (cap === null) { setError(`${p.label}: enter a number like 15 or a range like 10-20`); return }
      parsed[p.id] = cap
    }
    setBusy(true)
    try {
      setCfg(await api.saveApplyConfig({ auto_apply: cfg.auto_apply, paused: cfg.paused,
                                         min_score: Number(cfg.min_score), max_attempts: Number(cfg.max_attempts),
                                         auto_min_score: Number(cfg.auto_min_score ?? 85),
                                         claim_ttl_minutes: Number(cfg.claim_ttl_minutes),
                                         max_posting_age_hours: Number(cfg.max_posting_age_hours ?? 72),
                                         timezone: cfg.timezone, caps: parsed }))
      setStatus(await api.getApplyStatus())
      setError('')
      setNotice('Saved')
      setTimeout(() => setNotice(''), 2500)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!cfg) return <div style={{ color: 'var(--fg-muted)' }}>{error || 'Loading…'}</div>

  const queue = status?.queue || {}
  return (
    <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      <div className="panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div>
          <h3 style={{ fontSize: '1rem' }}>Agent</h3>
          <p style={{ fontSize: '0.8rem', color: 'var(--fg-subtle)', marginTop: 4 }}>
            Paused stops the apply agent from getting any job, whatever the caps say. Scheduled runs keep firing but do nothing.
          </p>
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', cursor: 'pointer' }}>
          <input type="checkbox" checked={cfg.paused}
            onChange={e => setCfg(c => ({ ...c, paused: e.target.checked }))} />
          <span><strong>Pause auto-apply</strong></span>
        </label>
        <div className="form-grid">
          <div className="form-group">
            <label className="form-label">Attempts before a job goes to "Needs you"</label>
            <input type="number" min="1" max="10" value={cfg.max_attempts}
              onChange={e => setCfg(c => ({ ...c, max_attempts: e.target.value }))} />
          </div>
          <div className="form-group">
            <label className="form-label">Minutes before a stuck application is retried</label>
            <input type="number" min="10" max="1440" value={cfg.claim_ttl_minutes}
              onChange={e => setCfg(c => ({ ...c, claim_ttl_minutes: e.target.value }))} />
          </div>
        </div>
      </div>

      <div className="panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div>
          <h3 style={{ fontSize: '1rem' }}>Approval</h3>
          <p style={{ fontSize: '0.8rem', color: 'var(--fg-subtle)', marginTop: 4 }}>
            The apply agent only works on approved jobs that have a tailored resume.
            With auto-approval on, any analysed job at or above the minimum match score is approved for you.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          {[[false, 'Review mode', 'Nothing is applied until you approve the resume on Telegram'],
            [true, 'Auto mode', 'Jobs at or above the auto score are applied on their own, weekdays 8:00–20:00, one every 20 minutes']].map(([auto, name, what]) => (
            <button key={name} type="button" className={`btn btn-sm ${cfg.auto_apply === auto ? 'btn-primary' : 'btn-outline'}`}
              onClick={() => setCfg(c => ({ ...c, auto_apply: auto }))} title={what}>{name}</button>
          ))}
          <span style={{ fontSize: '0.8rem', color: 'var(--fg-subtle)', alignSelf: 'center' }}>
            {cfg.auto_apply ? `Jobs scoring ${cfg.auto_min_score ?? 85}+ are applied on their own during work hours; the rest wait for your approval.`
              : 'Nothing is applied until you approve the resume on Telegram.'}
          </span>
        </div>
        <div className="form-grid">
          <div className="form-group">
            <label className="form-label">Tailor a resume at or above (score)</label>
            <input type="number" min="0" max="100" value={cfg.min_score}
              onChange={e => setCfg(c => ({ ...c, min_score: e.target.value }))} />
          </div>
          <div className="form-group">
            <label className="form-label">Auto mode applies on its own at or above</label>
            <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
              <button type="button" className="btn btn-outline btn-sm" aria-label="Lower by 5"
                onClick={() => setCfg(c => ({ ...c, auto_min_score: Math.max(Number(c.min_score), Number(c.auto_min_score ?? 85) - 5) }))}>−5</button>
              <input type="number" min="0" max="100" value={cfg.auto_min_score ?? 85} style={{ width: '5rem' }}
                onChange={e => setCfg(c => ({ ...c, auto_min_score: e.target.value }))} />
              <button type="button" className="btn btn-outline btn-sm" aria-label="Raise by 5"
                onClick={() => setCfg(c => ({ ...c, auto_min_score: Math.min(100, Number(c.auto_min_score ?? 85) + 5) }))}>+5</button>
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Only find postings newer than (hours)</label>
            <input type="number" min="1" max="720" value={cfg.max_posting_age_hours ?? 72}
              onChange={e => setCfg(c => ({ ...c, max_posting_age_hours: e.target.value }))} />
          </div>
          <div className="form-group">
            <label className="form-label">Timezone (when the daily caps reset)</label>
            <input type="text" value={cfg.timezone} placeholder="America/Toronto"
              onChange={e => setCfg(c => ({ ...c, timezone: e.target.value }))} />
          </div>
        </div>
      </div>

      <div className="panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div>
          <h3 style={{ fontSize: '1rem' }}>Daily caps</h3>
          <p style={{ fontSize: '0.8rem', color: 'var(--fg-subtle)', marginTop: 4 }}>
            Applications per day, per platform. A range like <code>10-20</code> picks a different number each day.
            Use <code>0</code> to stop a platform entirely.
          </p>
        </div>
        <div className="form-grid">
          {CAP_PLATFORMS.map(p => {
            const today = status?.today?.[p.id]
            return (
              <div className="form-group" key={p.id}>
                <label className="form-label">{p.label}</label>
                <input type="text" value={caps[p.id] ?? ''}
                  onChange={e => setCaps(c => ({ ...c, [p.id]: e.target.value }))} />
                {today && p.id !== 'default' && (
                  <span style={{ fontSize: '0.7rem', color: 'var(--fg-subtle)', marginTop: 2 }}>
                    Today: {today.used} of {today.cap}
                  </span>
                )}
              </div>
            )
          })}
        </div>
      </div>

      <div className="panel" style={{ padding: '1.5rem', display: 'flex', gap: '2rem', flexWrap: 'wrap' }}>
        {[['pending', 'Awaiting approval'], ['approved', 'Approved, in queue'],
          ['applying', 'Being applied'], ['waiting_human', 'Waiting for you'], ['needs_human', 'Needs you']].map(([k, label]) => (
          <div key={k}>
            <div style={{ fontFamily: 'var(--font-serif)', fontSize: '2rem', fontWeight: 400 }}>{queue[k] || 0}</div>
            <div style={{ fontSize: '0.75rem', color: 'var(--fg-subtle)' }}>{label}</div>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
        <button className="btn btn-primary" onClick={save} disabled={busy}>
          {busy ? 'Saving…' : 'Save auto-apply settings'}
        </button>
        {notice && <span style={{ fontSize: '0.8rem', color: 'var(--fg-muted)' }}>{notice}</span>}
        {error && <span style={{ fontSize: '0.8rem', color: 'var(--danger)' }}>{error}</span>}
      </div>
    </div>
  )
}
