import React, { useEffect, useState, useRef } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api/client'

const STAGES = [
  { value: 'found',        label: '🔍 Found' },
  { value: 'pending',      label: '⏳ Resume Prepared' },
  { value: 'applied',      label: '📤 Applied' },
  { value: 'screening',    label: '📞 Recruiter Reached' },
  { value: 'interview_1',  label: '🎤 Interview 1' },
  { value: 'interview_2',  label: '🎤 Interview 2' },
  { value: 'offer',        label: '🎉 Offer' },
  { value: 'rejected',     label: '❌ Rejected' },
  { value: 'withdrawn',    label: '↩️ Withdrawn' },
]

function StageDropdown({ job, onUpdate }) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState({ top: 0, right: 0 })
  const [saving, setSaving] = useState(false)
  const btnRef = useRef()
  const menuRef = useRef()

  useEffect(() => {
    const handler = e => {
      if (
        menuRef.current && !menuRef.current.contains(e.target) &&
        btnRef.current && !btnRef.current.contains(e.target)
      ) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const toggle = e => {
    e.stopPropagation()
    if (!open) {
      const rect = btnRef.current.getBoundingClientRect()
      setPos({ top: rect.bottom + 4, right: window.innerWidth - rect.right })
    }
    setOpen(o => !o)
  }

  const change = async (newStatus, e) => {
    e.stopPropagation()
    if (newStatus === job.status) { setOpen(false); return }
    setSaving(true)
    try {
      await api.updateJob(job.id, { status: newStatus })
      onUpdate(job.id, newStatus)
    } catch(err) { console.error(err) }
    setSaving(false)
    setOpen(false)
  }

  return (
    <div onClick={e => e.stopPropagation()}>
      <button ref={btnRef} onClick={toggle}
        style={{
          background:'rgba(255,255,255,0.06)', border:'1px solid rgba(255,255,255,0.12)',
          borderRadius:6, padding:'4px 10px', cursor:'pointer', color:'var(--fg)',
          fontSize:'0.78rem', fontWeight:500, display:'flex', alignItems:'center', gap:4,
          opacity: saving ? 0.5 : 1
        }}>
        {saving ? '…' : 'Move ▾'}
      </button>
      {open && createPortal(
        <div ref={menuRef}
          onClick={e => e.stopPropagation()}
          style={{
            position:'fixed', top: pos.top, right: pos.right, zIndex:9999,
            background:'#1e2130', border:'1px solid rgba(255,255,255,0.12)',
            borderRadius:8, minWidth:190, boxShadow:'0 8px 24px rgba(0,0,0,0.6)',
            overflow:'hidden'
          }}>
          {STAGES.map(s => (
            <div key={s.value}
              onClick={e => change(s.value, e)}
              style={{
                padding:'8px 14px', fontSize:'0.82rem', cursor:'pointer',
                color: s.value === job.status ? 'var(--primary)' : 'var(--fg)',
                background: s.value === job.status ? 'rgba(99,102,241,0.12)' : 'transparent',
                fontWeight: s.value === job.status ? 600 : 400,
                transition:'background 0.1s'
              }}
              onMouseEnter={e => { if(s.value !== job.status) e.currentTarget.style.background='rgba(255,255,255,0.08)' }}
              onMouseLeave={e => { if(s.value !== job.status) e.currentTarget.style.background='transparent' }}>
              {s.label}
            </div>
          ))}
        </div>,
        document.body
      )}
    </div>
  )
}

const EMPTY_STATS = { total: 0, pending_review: 0, interviews_scheduled: 0, offers: 0 }

function ScoreRing({ score }) {
  if (!score) return <span className="text-subtle text-xs">Not analyzed</span>
  const r = 36; const circ = 2 * Math.PI * r
  const fill = score === null ? 0 : (score / 100) * circ
  const color = score >= 80 ? '#10b981' : score >= 60 ? '#f59e0b' : '#ef4444'
  return (
    <div className="score-ring-wrap">
      <div className="score-ring">
        <svg width="90" height="90" viewBox="0 0 90 90">
          <circle cx="45" cy="45" r={r} stroke="rgba(255,255,255,0.07)" strokeWidth="8" fill="none" />
          <circle cx="45" cy="45" r={r} stroke={color} strokeWidth="8" fill="none"
            strokeDasharray={`${fill} ${circ}`} strokeLinecap="round" />
        </svg>
        <div className="score-ring-text"><span className="score-number" style={{ color }}>{score}%</span></div>
      </div>
    </div>
  )
}

const BADGE_EMOJI = {
  found: '🔍', analyzing: '🤖', pending: '⏳', approved: '✅',
  applied: '📤', screening: '📞', interview_1: '🎤', interview_2: '🎤',
  offer: '🎉', rejected: '❌', withdrawn: '↩️'
}

export default function Dashboard({ onOpenJob }) {
  const [stats, setStats] = useState(EMPTY_STATS)
  const [jobs, setJobs] = useState([])
  const [followups, setFollowups] = useState(null)
  const [patterns, setPatterns] = useState(null)

  useEffect(() => {
    api.getStats().then(setStats).catch(() => {})
    api.listJobs({ limit: 20 }).then(setJobs).catch(() => {})
    api.getFollowupCadence().then(setFollowups).catch(() => {})
    api.getPatternAnalytics().then(setPatterns).catch(() => {})
  }, [])

  const handleStatusUpdate = (jobId, newStatus) => {
    setJobs(prev => prev.map(j => j.id === jobId ? { ...j, status: newStatus } : j))
  }

  const STATUS_COLOR = {
    found: 'var(--info)', analyzing: 'var(--purple)', pending: 'var(--warning)',
    approved: 'var(--primary)', applied: 'var(--success)', interview_1: 'var(--purple)',
    offer: 'var(--success)', rejected: 'var(--danger)'
  }

  const URGENCY_STYLE = {
    urgent: { bg:'rgba(239,68,68,0.15)', color:'#ef4444', label:'🔴 URGENT' },
    overdue: { bg:'rgba(245,158,11,0.15)', color:'#f59e0b', label:'🟡 OVERDUE' },
    waiting: { bg:'rgba(99,102,241,0.1)', color:'#818cf8', label:'⏳ Waiting' },
    cold: { bg:'rgba(255,255,255,0.05)', color:'var(--fg-subtle)', label:'❄️ Cold' },
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.75rem' }}>
      {/* Stats Row */}
      <div className="stats-grid fade-in">
        {[
          { label: 'Active Pipeline', value: stats.total, sub: 'Total applications tracked', color: 'var(--fg)' },
          { label: 'Pending Action', value: stats.pending_review, sub: 'Needs review', color: 'var(--warning)' },
          { label: 'Interviews', value: stats.interviews_scheduled, sub: 'Currently scheduled', color: 'var(--purple)' },
          { label: 'Total Offers', value: stats.offers, sub: 'Negotiation phase', color: 'var(--success)' },
        ].map((s, i) => (
          <div key={i} className="panel stat-card" style={{ flex:1, padding: '1.5rem', animationDelay: `${i * 0.05}s` }}>
            <div className="stat-label" style={{ fontSize:'0.85rem' }}>{s.label}</div>
            <div className="stat-value" style={{ color: s.color, fontSize:'2.5rem', margin:'0.25rem 0' }}>{s.value}</div>
            <div className="stat-sub" style={{ fontSize:'0.8rem' }}>{s.sub}</div>
          </div>
        ))}
      </div>

      {/* Clean elegant whitespace instead of progress bar */}

      {/* Two-column layout: Follow-up Cadence & Important Information */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'1.75rem' }}>

        {/* Follow-Up Cadence Tracker */}
        <div>
          <div className="flex justify-between items-center" style={{ marginBottom:'1rem' }}>
            <h3 style={{ fontSize:'1.1rem', fontWeight:600 }}>Action Needed</h3>
            {followups && (
              <div className="flex gap-sm">
                {followups.urgent > 0 && <span className="badge" style={{ background:'rgba(239,68,68,0.15)', color:'#ef4444', fontSize:'0.7rem' }}>{followups.urgent} urgent</span>}
                {followups.overdue > 0 && <span className="badge" style={{ background:'rgba(245,158,11,0.15)', color:'#f59e0b', fontSize:'0.7rem' }}>{followups.overdue} overdue</span>}
              </div>
            )}
          </div>
          {followups?.entries?.length > 0 ? (
            <div style={{ display:'flex', flexDirection:'column', gap:'0.75rem' }}>
              {followups.entries.slice(0, 4).map(e => {
                const s = URGENCY_STYLE[e.urgency] || URGENCY_STYLE.waiting
                return (
                  <div key={e.job_id} onClick={() => onOpenJob(e.job_id)}
                    style={{ padding:'1rem 1.25rem', background:'var(--surface)', border:'1px solid var(--surface-border)', borderRadius:'var(--radius-sm)', cursor:'pointer', transition:'transform 0.1s, border-color 0.2s', display:'flex', justifyContent:'space-between', alignItems:'center' }}
                    onMouseEnter={ev => ev.currentTarget.style.borderColor='rgba(255,255,255,0.15)'}
                    onMouseLeave={ev => ev.currentTarget.style.borderColor='var(--surface-border)'}>
                    <div>
                      <div style={{ fontWeight:600, fontSize:'0.9rem' }}>{e.company}</div>
                      <div style={{ fontSize:'0.8rem', color:'var(--fg-muted)', marginTop:2 }}>{e.title} • {e.days_since_applied}d ago</div>
                    </div>
                    <span style={{ fontSize:'0.75rem', fontWeight:600, color:s.color }}>{s.label}</span>
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="panel" style={{ textAlign:'center', padding:'2rem', color:'var(--fg-subtle)' }}>
              No critical actions pending.
            </div>
          )}
        </div>

        {/* Pattern Analytics Summary */}
        <div>
          <h3 style={{ fontSize:'1.1rem', fontWeight:600, marginBottom:'1rem' }}>AI Recommendations</h3>
          {patterns && !patterns.error && patterns.recommendations?.length > 0 ? (
            <div style={{ display:'flex', flexDirection:'column', gap:'0.75rem' }}>
              {patterns.recommendations.slice(0, 4).map((r, i) => (
                <div key={i} style={{ padding:'1rem 1.25rem', background: 'var(--surface)', border: `1px solid var(--surface-border)`, borderRadius:'var(--radius-sm)', fontSize:'0.85rem' }}>
                  <div style={{ fontWeight:600, marginBottom:4, color:'var(--fg)' }}>{r.impact === 'high' ? '🔴' : '🟡'} {r.action}</div>
                  <div style={{ color:'var(--fg-muted)', lineHeight:1.5 }}>{r.reasoning}</div>
                </div>
              ))}
            </div>
          ) : (
            <div className="panel" style={{ textAlign:'center', padding:'2rem', color:'var(--fg-subtle)' }}>
              {patterns?.error || 'Track more jobs to unlock insights.'}
            </div>
          )}
        </div>
      </div>

      <div>
        <div className="flex justify-between items-center" style={{ marginBottom: '1.25rem' }}>
          <h3 style={{ fontSize: '1.1rem', fontWeight:600 }}>Recent Activity</h3>
          <button className="btn btn-ghost btn-sm" onClick={() => document.querySelector('.nav-item:nth-child(2)').click()}>View All →</button>
        </div>
        <div className="jobs-list">
          {jobs.length === 0 && (
            <div className="panel" style={{ textAlign: 'center', padding: '2rem', color: 'var(--fg-subtle)' }}>
              No jobs tracked yet. Add your first job above.
            </div>
          )}
          {jobs.slice(0, 6).map(job => (
            <div key={job.id} onClick={() => onOpenJob(job.id)}
                 style={{ padding:'1.25rem', background:'var(--surface)', borderBottom:'1px solid var(--surface-border)', display:'grid', gridTemplateColumns:'1fr auto auto auto', gap:'1.5rem', alignItems:'center', cursor:'pointer', transition:'background 0.15s' }}
                 onMouseEnter={e => e.currentTarget.style.background='var(--surface-hover)'}
                 onMouseLeave={e => e.currentTarget.style.background='var(--surface)'}>
              <div>
                <div style={{ fontWeight: 600, fontSize: '1rem', color: 'var(--fg)', marginBottom:4 }}>{job.title}</div>
                <div style={{ fontSize: '0.85rem', color: 'var(--fg-muted)' }}>{job.company} {job.remote ? '• 🌐 Remote' : ''}</div>
              </div>
              <ScoreRing score={job.match_score} />
              <div style={{ display:'flex', alignItems:'center', gap:'0.6rem' }}>
                <span className={`badge badge-${job.status}`}>{BADGE_EMOJI[job.status]} {job.status.replace(/_/g, ' ')}</span>
                <StageDropdown job={job} onUpdate={handleStatusUpdate} />
              </div>
              <div style={{ fontSize: '0.85rem', color: 'var(--fg-subtle)', textAlign: 'right', minWidth:80 }}>
                {job.salary_min ? `$${(job.salary_min / 1000).toFixed(0)}K+` : '—'}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
