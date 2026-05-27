import React, { useEffect, useState } from 'react'
import { api } from '../api/client'

const ACTION_COLORS = {
  chat:           '#6366f1',
  resume:         '#10b981',
  cover_letter:   '#f59e0b',
  evaluation:     '#a855f7',
  gap_analysis:   '#38bdf8',
  research:       '#ef4444',
  linkedin:       '#0ea5e9',
  interview_prep: '#f97316',
}

const ACTION_LABELS = {
  chat:           '💬 Chat',
  resume:         '📄 Resume',
  cover_letter:   '✉️ Cover Letter',
  evaluation:     '🔬 Evaluation',
  gap_analysis:   '📊 Gap Analysis',
  research:       '🔍 Research',
  linkedin:       '🔗 LinkedIn',
  interview_prep: '🎤 Interview Prep',
}

function MomentumBar({ score }) {
  const color = score >= 7 ? '#10b981' : score >= 4 ? '#f59e0b' : '#ef4444'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
      <div style={{ flex: 1, height: 8, background: 'rgba(255,255,255,0.08)', borderRadius: 4, overflow: 'hidden' }}>
        <div style={{ width: `${score * 10}%`, height: '100%', background: color, borderRadius: 4, transition: 'width 0.6s ease' }} />
      </div>
      <span style={{ color, fontWeight: 700, fontSize: '1.1rem', minWidth: 32 }}>{score}/10</span>
    </div>
  )
}

function ActivityBar({ by_day }) {
  if (!by_day?.length) return null
  const max = Math.max(...by_day.map(d => d.count), 1)
  const recent = by_day.slice(-14)
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 48 }}>
      {recent.map((d, i) => (
        <div key={i} title={`${d.date}: ${d.count}`} style={{
          flex: 1, background: d.count > 0 ? '#6366f1' : 'rgba(255,255,255,0.06)',
          height: `${Math.max((d.count / max) * 100, d.count > 0 ? 8 : 4)}%`,
          borderRadius: 3, minHeight: 4, cursor: 'default', opacity: 0.85,
          transition: 'height 0.3s'
        }} />
      ))}
    </div>
  )
}

export default function Insights() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [history, setHistory] = useState([])
  const [histPage, setHistPage] = useState(1)
  const [searchQ, setSearchQ] = useState('')
  const [searchResults, setSearchResults] = useState(null)
  const [searching, setSearching] = useState(false)

  useEffect(() => {
    setLoading(true)
    api.getInsights()
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    api.getInsightsHistory(histPage, 20)
      .then(r => setHistory(r.items || r))
      .catch(() => {})
  }, [histPage])

  const doSearch = async () => {
    if (!searchQ.trim()) { setSearchResults(null); return }
    setSearching(true)
    try {
      const r = await api.searchInsights(searchQ)
      setSearchResults(r)
    } catch { setSearchResults([]) }
    setSearching(false)
  }

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 300, color: 'var(--fg-subtle)' }}>
      <div>Generating AI insights… this takes ~15s</div>
    </div>
  )

  if (error) return (
    <div className="panel" style={{ padding: '2rem', color: 'var(--danger)', textAlign: 'center' }}>
      ⚠️ {error} — make sure backend is running and GEMINI_API_KEY is set.
    </div>
  )

  const { stats, narrative } = data || {}

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.75rem' }}>

      {/* Top stats row */}
      <div className="stats-grid fade-in">
        {[
          { label: 'Total Actions', value: stats?.total_interactions ?? 0, color: 'var(--fg)' },
          { label: 'Active Days', value: stats?.active_days ?? 0, color: 'var(--primary)' },
          { label: 'Top LLM', value: stats?.most_used_llm ?? '—', color: 'var(--success)', raw: true },
          { label: 'Momentum', value: narrative?.momentum_score ?? '—', color: narrative?.momentum_score >= 7 ? 'var(--success)' : narrative?.momentum_score >= 4 ? 'var(--warning)' : 'var(--danger)' },
        ].map((s, i) => (
          <div key={i} className="panel stat-card" style={{ flex: 1, padding: '1.5rem' }}>
            <div className="stat-label" style={{ fontSize: '0.85rem' }}>{s.label}</div>
            <div className="stat-value" style={{ color: s.color, fontSize: s.raw ? '1.6rem' : '2.5rem', margin: '0.25rem 0', textTransform: s.raw ? 'capitalize' : 'none' }}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* Momentum + activity */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.75rem' }}>

        {/* Narrative */}
        <div className="panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <h3 style={{ fontSize: '1.05rem', fontWeight: 700, marginBottom: 4 }}>🤖 AI Assessment</h3>
          {narrative?.momentum_score != null && (
            <div>
              <div style={{ fontSize: '0.78rem', color: 'var(--fg-subtle)', marginBottom: 6 }}>Momentum Score</div>
              <MomentumBar score={narrative.momentum_score} />
              {narrative.momentum_rationale && (
                <div style={{ fontSize: '0.8rem', color: 'var(--fg-muted)', marginTop: 6 }}>{narrative.momentum_rationale}</div>
              )}
            </div>
          )}
          {narrative?.summary && (
            <div style={{ fontSize: '0.85rem', color: 'var(--fg)', lineHeight: 1.6, borderTop: '1px solid var(--surface-border)', paddingTop: '0.75rem' }}>
              {narrative.summary}
            </div>
          )}
          {narrative?.warning && (
            <div style={{ padding: '0.6rem 0.9rem', background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.25)', borderRadius: 8, fontSize: '0.8rem', color: '#ef4444' }}>
              ⚠️ {narrative.warning}
            </div>
          )}
        </div>

        {/* Activity breakdown */}
        <div className="panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <h3 style={{ fontSize: '1.05rem', fontWeight: 700, marginBottom: 4 }}>Activity (last 14 days)</h3>
          <ActivityBar by_day={stats?.by_day} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 4 }}>
            {Object.entries(stats?.by_action || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
              <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{ width: 8, height: 8, borderRadius: '50%', background: ACTION_COLORS[k] || '#6366f1', flexShrink: 0 }} />
                <span style={{ fontSize: '0.8rem', color: 'var(--fg-muted)', flex: 1 }}>{ACTION_LABELS[k] || k}</span>
                <span style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--fg)' }}>{v}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Recommendations + what you're doing */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.75rem' }}>
        <div className="panel" style={{ padding: '1.5rem' }}>
          <h3 style={{ fontSize: '1.05rem', fontWeight: 700, marginBottom: '1rem' }}>🎯 Top 3 Actions</h3>
          {narrative?.top_3_recommendations?.length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {narrative.top_3_recommendations.map((r, i) => (
                <div key={i} style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-start' }}>
                  <span style={{ background: 'var(--primary)', color: '#fff', borderRadius: '50%', width: 22, height: 22, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.7rem', fontWeight: 700, flexShrink: 0 }}>{i+1}</span>
                  <span style={{ fontSize: '0.85rem', color: 'var(--fg)', lineHeight: 1.5 }}>{r}</span>
                </div>
              ))}
            </div>
          ) : <div style={{ color: 'var(--fg-subtle)', fontSize: '0.85rem' }}>No recommendations yet.</div>}
        </div>

        <div className="panel" style={{ padding: '1.5rem' }}>
          <h3 style={{ fontSize: '1.05rem', fontWeight: 700, marginBottom: '0.75rem' }}>📌 What You're Doing</h3>
          {narrative?.what_youre_doing && (
            <p style={{ fontSize: '0.85rem', color: 'var(--fg)', lineHeight: 1.6, margin: 0 }}>{narrative.what_youre_doing}</p>
          )}
          {narrative?.gaps && (
            <>
              <h4 style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--warning)', margin: '1rem 0 0.4rem' }}>Gaps</h4>
              <p style={{ fontSize: '0.83rem', color: 'var(--fg-muted)', lineHeight: 1.5, margin: 0 }}>{narrative.gaps}</p>
            </>
          )}
        </div>
      </div>

      {/* Top companies */}
      {stats?.top_companies?.length > 0 && (
        <div className="panel" style={{ padding: '1.25rem 1.5rem' }}>
          <h3 style={{ fontSize: '1.05rem', fontWeight: 700, marginBottom: '0.75rem' }}>🏢 Most Active Companies</h3>
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            {stats.top_companies.map(c => (
              <span key={c} className="badge" style={{ background: 'rgba(99,102,241,0.12)', color: 'var(--primary)', fontSize: '0.8rem' }}>{c}</span>
            ))}
          </div>
        </div>
      )}

      {/* History + search */}
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem', gap: '1rem' }}>
          <h3 style={{ fontSize: '1.05rem', fontWeight: 700 }}>📋 Interaction History</h3>
          <div style={{ display: 'flex', gap: '0.5rem', flex: 1, maxWidth: 380 }}>
            <input
              value={searchQ}
              onChange={e => setSearchQ(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && doSearch()}
              placeholder="Semantic search… (e.g. 'Anthropic resume')"
              style={{ flex: 1, fontSize: '0.82rem' }}
            />
            <button className="btn btn-outline btn-sm" onClick={doSearch} disabled={searching}>
              {searching ? '…' : 'Search'}
            </button>
            {searchResults && (
              <button className="btn btn-ghost btn-sm" onClick={() => { setSearchResults(null); setSearchQ('') }}>Clear</button>
            )}
          </div>
        </div>

        <div className="panel" style={{ overflow: 'hidden' }}>
          {(searchResults || history).length === 0 ? (
            <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--fg-subtle)', fontSize: '0.85rem' }}>
              {searchResults ? 'No results.' : 'No interactions logged yet — start chatting or triggering agents.'}
            </div>
          ) : (
            <>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--surface-border)', color: 'var(--fg-subtle)' }}>
                    <th style={{ textAlign: 'left', padding: '10px 14px', fontWeight: 600 }}>Action</th>
                    <th style={{ textAlign: 'left', padding: '10px 14px', fontWeight: 600 }}>Company / Job</th>
                    <th style={{ textAlign: 'left', padding: '10px 14px', fontWeight: 600 }}>LLM</th>
                    <th style={{ textAlign: 'left', padding: '10px 14px', fontWeight: 600 }}>When</th>
                  </tr>
                </thead>
                <tbody>
                  {(searchResults || history).map((item, i) => (
                    <tr key={item.id || i} style={{ borderBottom: '1px solid var(--surface-border)' }}
                      onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-hover)'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                      <td style={{ padding: '10px 14px' }}>
                        <span style={{ color: ACTION_COLORS[item.action_type] || 'var(--fg)' }}>
                          {ACTION_LABELS[item.action_type] || item.action_type}
                        </span>
                      </td>
                      <td style={{ padding: '10px 14px', color: 'var(--fg-muted)' }}>
                        {item.company || '—'}{item.title ? ` · ${item.title}` : ''}
                      </td>
                      <td style={{ padding: '10px 14px', color: 'var(--fg-subtle)', textTransform: 'capitalize' }}>
                        {item.llm_used || '—'}
                      </td>
                      <td style={{ padding: '10px 14px', color: 'var(--fg-subtle)' }}>
                        {item.created_at ? new Date(item.created_at).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!searchResults && (
                <div style={{ display: 'flex', justifyContent: 'center', gap: '0.5rem', padding: '0.75rem' }}>
                  <button className="btn btn-ghost btn-sm" onClick={() => setHistPage(p => Math.max(1, p-1))} disabled={histPage === 1}>← Prev</button>
                  <span style={{ fontSize: '0.8rem', color: 'var(--fg-subtle)', alignSelf: 'center' }}>Page {histPage}</span>
                  <button className="btn btn-ghost btn-sm" onClick={() => setHistPage(p => p+1)} disabled={history.length < 20}>Next →</button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
