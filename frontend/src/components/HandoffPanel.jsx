import React, { useEffect, useState } from 'react'
import { api } from '../api/client'

/**
 * Jobs the apply agent handed back, grouped by why. Each row has what you need to
 * apply yourself: the posting, the tailored resume, the answers the agent already
 * worked out, and a button to mark it applied (or send it back to the agent).
 */
export default function HandoffPanel({ onOpenJob }) {
  const [groups, setGroups] = useState(null)
  const [open, setOpen] = useState({})

  const load = () => api.getHandoff().then(setGroups).catch(() => setGroups([]))
  useEffect(() => { load() }, [])

  const act = async (fn) => { await fn(); load() }

  if (!groups || groups.length === 0) return null
  const total = groups.reduce((n, g) => n + g.jobs.length, 0)

  return (
    <div className="panel fade-in" style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div>
        <h3 style={{ fontSize: '1rem' }}>Needs you — {total} job{total === 1 ? '' : 's'}</h3>
        <p style={{ fontSize: '0.8rem', color: 'var(--fg-subtle)', marginTop: 4 }}>
          The agent couldn't finish these. Resume and answers are ready — open the posting, apply, then click Applied.
        </p>
      </div>
      {groups.map(g => (
        <div key={g.cause} style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <div style={{ fontSize: '0.8rem', fontWeight: 700, color: 'var(--fg-muted)' }}>{g.label} ({g.jobs.length})</div>
          {g.jobs.map(j => (
            <div key={j.id} style={{ border: '1px solid var(--surface-border)', padding: '0.6rem 0.75rem' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', alignItems: 'center' }}>
                <a href="#" onClick={e => { e.preventDefault(); onOpenJob?.(j.id) }} style={{ fontWeight: 600, flex: '1 1 14rem', display: 'inline-flex', alignItems: 'center', minHeight: 44 }}>
                  #{j.id} {j.company} · {j.title}
                </a>
                <a className="btn btn-outline btn-sm" href={j.careers_url && /linkedin\.com/.test(j.url) ? j.careers_url : j.url}
                  target="_blank" rel="noreferrer">Posting ↗</a>
                {j.has_pdf && <button className="btn btn-outline btn-sm"
                  onClick={() => api.downloadFile(j.id, `resume_v${j.resume_version}.pdf`)}>PDF</button>}
                {j.has_docx && <button className="btn btn-outline btn-sm"
                  onClick={() => api.downloadFile(j.id, `resume_v${j.resume_version}.docx`)}>DOCX</button>}
                {!j.resume_version && <span style={{ fontSize: '0.75rem', color: 'var(--warning)' }}>no resume yet</span>}
                {g.cause === 'workday' && <button className="btn btn-outline btn-sm"
                  onClick={() => act(() => api.approveJob(j.id))}>Retry with agent</button>}
                <button className="btn btn-primary btn-sm"
                  onClick={() => act(() => api.updateJob(j.id, { status: 'applied' }))}>Applied ✓</button>
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--fg-subtle)', marginTop: 4, overflowWrap: 'anywhere' }}>
                {j.note}
                {j.answers.length > 0 && (
                  <a href="#" style={{ marginLeft: 6, display: 'inline-flex', alignItems: 'center', minHeight: 44 }} onClick={e => { e.preventDefault(); setOpen(o => ({ ...o, [j.id]: !o[j.id] })) }}>
                    {open[j.id] ? 'hide' : 'show'} {j.answers.length} answers
                  </a>
                )}
              </div>
              {open[j.id] && (
                <ul style={{ fontSize: '0.75rem', margin: '0.4rem 0 0 1rem' }}>
                  {j.answers.map((a, i) => <li key={i}><strong>{a.label}:</strong> {String(a.answer)}</li>)}
                </ul>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
