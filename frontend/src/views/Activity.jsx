import React, { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'

const SEVERITY = {
  high: { label: 'Needs you', color: 'var(--danger)', bg: 'var(--danger-subtle)' },
  medium: { label: 'Check', color: 'var(--warning)', bg: 'var(--warning-subtle)' },
  low: { label: 'Review', color: 'var(--info)', bg: 'var(--info-subtle)' },
  info: { label: 'Info', color: 'var(--fg-muted)', bg: 'transparent' },
}

// Agent outcomes, as logged by /api/apply/{id}/result ("Apply agent: <outcome>").
// Status colors, each also named in the legend and tooltip.
const OUTCOMES = [
  { key: 'submitted', label: 'Submitted', color: 'var(--success)' },
  { key: 'needs human', label: 'Needs you', color: 'var(--warning)' },
  { key: 'failed', label: 'Failed', color: 'var(--danger)' },
  { key: 'skipped', label: 'Skipped', color: 'var(--fg-subtle)' },
]

const dayKey = (iso) => iso.slice(0, 10)
const fmtTime = (iso) => iso ? new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''

function OutcomeChart({ events }) {
  const days = [...Array(7)].map((_, i) => {
    const d = new Date(); d.setDate(d.getDate() - (6 - i)); return d.toISOString().slice(0, 10)
  })
  const counts = Object.fromEntries(days.map(d => [d, Object.fromEntries(OUTCOMES.map(o => [o.key, 0]))]))
  for (const e of events) {
    const m = e.event_title?.match(/^Apply agent: (.+)$/)
    if (m && counts[dayKey(e.created_at)]?.[m[1]] !== undefined) counts[dayKey(e.created_at)][m[1]]++
  }
  const max = Math.max(1, ...days.map(d => OUTCOMES.reduce((s, o) => s + counts[d][o.key], 0)))
  const H = 120
  return (
    <div>
      <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', fontSize: '0.75rem', color: 'var(--fg-muted)', marginBottom: '0.75rem' }}>
        {OUTCOMES.map(o => (
          <span key={o.key} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 10, height: 10, background: o.color }} />{o.label}
          </span>
        ))}
      </div>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 12, height: H, borderBottom: '1px solid var(--border)' }}>
        {days.map(d => {
          const total = OUTCOMES.reduce((s, o) => s + counts[d][o.key], 0)
          const tip = `${d}\n` + OUTCOMES.map(o => `${o.label}: ${counts[d][o.key]}`).join('\n')
          return (
            <div key={d} title={tip} style={{ flex: 1, height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', alignItems: 'center', cursor: 'default' }}>
              {total > 0 && <span style={{ fontSize: '0.7rem', color: 'var(--fg-muted)', marginBottom: 2 }}>{total}</span>}
              <div style={{ width: '60%', maxWidth: 28, display: 'flex', flexDirection: 'column-reverse', gap: 2 }}>
                {OUTCOMES.filter(o => counts[d][o.key]).map((o, i, arr) => (
                  <div key={o.key} style={{ height: (counts[d][o.key] / max) * (H - 20), background: o.color,
                    borderRadius: i === arr.length - 1 ? '4px 4px 0 0' : 0 }} />
                ))}
              </div>
            </div>
          )
        })}
      </div>
      <div style={{ display: 'flex', gap: 12, marginTop: 4 }}>
        {days.map(d => (
          <span key={d} style={{ flex: 1, textAlign: 'center', fontSize: '0.7rem', color: 'var(--fg-subtle)' }}>
            {new Date(d + 'T12:00').toLocaleDateString([], { weekday: 'short' })}
          </span>
        ))}
      </div>
    </div>
  )
}

/**
 * Everything the apply agent and HireOS did, plus derived flags that need a look.
 * Flags clear themselves when the cause is fixed; Dismiss only hides one.
 */
export default function Activity({ onOpenJob }) {
  const [flags, setFlags] = useState(null)
  const [events, setEvents] = useState([])
  const [status, setStatus] = useState(null)
  const [days, setDays] = useState(7)
  const [source, setSource] = useState('')
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(() => {
    Promise.all([api.getFlags(), api.getActivity({ days, source }), api.getApplyStatus()])
      .then(([f, e, s]) => { setFlags(f); setEvents(e); setStatus(s); setError('') })
      .catch(e => setError(e.message))
  }, [days, source])

  useEffect(() => { load() }, [load])

  const act = async (fn) => {
    try { await fn(); load() } catch (e) { setError(e.message) }
  }

  const q = query.trim().toLowerCase()
  const shown = q ? events.filter(e => [e.company, e.title, e.event_title, e.description, e.platform]
    .some(v => (v || '').toLowerCase().includes(q))) : events

  return (
    <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
        <h2 style={{ fontSize: '1.25rem' }}>Activity & flags</h2>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          {status?.paused && <span className="badge" style={{ color: 'var(--warning)' }}>Auto-apply paused</span>}
          <button className="btn btn-outline btn-sm" onClick={load}>Refresh</button>
        </div>
      </div>
      {error && <div className="alert alert-error">{error}</div>}

      <div className="panel" style={{ padding: '1.5rem' }}>
        <h3 style={{ fontSize: '1rem', marginBottom: '1rem' }}>Flags {flags && <span style={{ color: 'var(--fg-subtle)', fontWeight: 400 }}>({flags.length})</span>}</h3>
        {!flags ? <div style={{ color: 'var(--fg-muted)' }}>Loading…</div>
          : !flags.length ? <div style={{ color: 'var(--fg-muted)', fontSize: '0.85rem' }}>Nothing needs you.</div>
          : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              {flags.map(f => {
                const sev = SEVERITY[f.severity]
                return (
                  <div key={f.id} style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', padding: '0.6rem 0.75rem',
                    background: sev.bg, borderLeft: `3px solid ${sev.color}` }}>
                    <span style={{ fontSize: '0.7rem', fontWeight: 700, color: sev.color, minWidth: 64 }}>{sev.label}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      {f.job_id && (
                        <a href={`#/job/${f.job_id}`} onClick={e => { e.preventDefault(); onOpenJob(f.job_id) }}
                          style={{ fontWeight: 600, fontSize: '0.85rem' }}>{f.company} — {f.title}</a>
                      )}
                      <div style={{ fontSize: '0.8rem', color: 'var(--fg-muted)', overflowWrap: 'anywhere' }}>{f.message}</div>
                    </div>
                    {f.kind === 'needs_human' && (
                      <button className="btn btn-outline btn-sm" title="Send it back to the agent with fresh attempts"
                        onClick={() => act(() => api.approveJob(f.job_id))}>Retry</button>
                    )}
                    <button className="btn btn-ghost btn-sm" onClick={() => act(() => api.dismissFlag(f.id))}>Dismiss</button>
                  </div>
                )
              })}
            </div>
          )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '1.5rem' }}>
        <div className="panel" style={{ padding: '1.5rem' }}>
          <h3 style={{ fontSize: '1rem', marginBottom: '1rem' }}>Agent outcomes, last 7 days</h3>
          <OutcomeChart events={events} />
        </div>
        <div className="panel" style={{ padding: '1.5rem' }}>
          <h3 style={{ fontSize: '1rem', marginBottom: '1rem' }}>Today's caps</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
            {Object.entries(status?.today || {}).map(([p, u]) => (
              <div key={p} title={`${p}: ${u.used} of ${u.cap}`}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem' }}>
                  <span>{p}</span><span style={{ color: 'var(--fg-muted)' }}>{u.used} / {u.cap}</span>
                </div>
                <div style={{ height: 6, background: 'var(--bg-subtle)', overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${u.cap ? Math.min(100, (u.used / u.cap) * 100) : 0}%`,
                    background: u.cap && u.used >= u.cap ? 'var(--warning)' : 'var(--accent)' }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <h3 style={{ fontSize: '1rem', marginRight: 'auto' }}>Event log</h3>
          <input type="text" placeholder="Search company, event, note…" value={query} onChange={e => setQuery(e.target.value)} style={{ width: 220 }} />
          <select value={source} onChange={e => setSource(e.target.value)}>
            <option value="">All sources</option>
            <option value="agent">Agent</option>
            <option value="user">Me</option>
          </select>
          <select value={days} onChange={e => setDays(Number(e.target.value))}>
            {[1, 7, 30, 90].map(d => <option key={d} value={d}>Last {d} day{d > 1 ? 's' : ''}</option>)}
          </select>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', fontSize: '0.8rem', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--fg-subtle)' }}>
                <th style={{ padding: '0.4rem' }}>When</th><th style={{ padding: '0.4rem' }}>Job</th>
                <th style={{ padding: '0.4rem' }}>Event</th><th style={{ padding: '0.4rem' }}>Details</th>
                <th style={{ padding: '0.4rem' }}>By</th>
              </tr>
            </thead>
            <tbody>
              {shown.map(e => (
                <tr key={e.id} style={{ borderTop: '1px solid var(--border)', verticalAlign: 'top' }}>
                  <td style={{ padding: '0.4rem', whiteSpace: 'nowrap', color: 'var(--fg-muted)' }}>{fmtTime(e.created_at)}</td>
                  <td style={{ padding: '0.4rem' }}>
                    <a href={`#/job/${e.job_id}`} onClick={ev => { ev.preventDefault(); onOpenJob(e.job_id) }}>{e.company}</a>
                    <div style={{ color: 'var(--fg-subtle)' }}>{e.title} · {e.platform}</div>
                  </td>
                  <td style={{ padding: '0.4rem' }}>{e.event_title}</td>
                  <td style={{ padding: '0.4rem', color: 'var(--fg-muted)', overflowWrap: 'anywhere' }}>{e.description}</td>
                  <td style={{ padding: '0.4rem', color: 'var(--fg-subtle)' }}>{e.source}</td>
                </tr>
              ))}
              {!shown.length && <tr><td colSpan={5} style={{ padding: '0.75rem', color: 'var(--fg-muted)' }}>No events.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
