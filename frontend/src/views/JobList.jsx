import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../api/client'
import SmartSearchBar from '../components/SmartSearchBar'

const STATUSES = ['found','analyzing','pending','approved','applied','screening','interview_1','interview_2','offer','rejected','withdrawn']
const BADGE_EMOJI = { found:'🔍', analyzing:'🤖', pending:'⏳', approved:'✅', applied:'📤', screening:'📞', interview_1:'🎤', interview_2:'🎤', offer:'🎉', rejected:'❌', withdrawn:'↩️' }

const LLM_OPTIONS = [
  { value:'gemini-3.5-flash', label:'Gemini 3.5 Flash (Fastest · New)' },
  { value:'gemini-3.1-pro-preview', label:'Gemini 3.1 Pro Preview (Most Capable)' },
  { value:'gemini-3-flash-preview', label:'Gemini 3 Flash Preview' },
  { value:'gemini-3.1-flash-lite', label:'Gemini 3.1 Flash Lite' },
  { value:'gemini-2.5-flash', label:'Gemini 2.5 Flash (Recommended)' },
  { value:'gemini-2.5-flash-lite', label:'Gemini 2.5 Flash Lite' },
  { value:'gemini-2.5-pro', label:'Gemini 2.5 Pro' },
  { value:'gemini-2.0-flash', label:'Gemini 2.0 Flash' },
  { value:'gemini-2.0-flash-lite', label:'Gemini 2.0 Flash Lite' },
  { value:'groq', label:'Groq (Llama 3 · Fast)' },
  { value:'openrouter', label:'OpenRouter (Free)' },
  { value:'nvidia', label:'NVIDIA (MiniMax-M3)' },
  { value:'claude', label:'Claude Sonnet (Anthropic)' },
  { value:'ollama', label:'Ollama (Local)' },
]

export default function JobList({ onOpenJob }) {
  const [jobs, setJobs] = useState([])
  const [searchQuery, setSearchQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState({ status: '', platform: '', starred: '', remote: '' })
  const [activeProviders, setActiveProviders] = useState([])
  const [globalModel, setGlobalModel] = useState(localStorage.getItem('preferredLlm') || 'gemini-2.5-flash')

  useEffect(() => {
    api.getProviders().then(setActiveProviders).catch(() => {})
  }, [])

  const handleModelChange = (e) => {
    const val = e.target.value;
    setGlobalModel(val);
    localStorage.setItem('preferredLlm', val);
  }

  // Load jobs -- uses search endpoint when query is set, otherwise list
  const loadJobs = useCallback(async (q = searchQuery, filters = filter) => {
    setLoading(true)
    try {
      const params = {}
      if (filters.status) params.status = filters.status
      if (filters.platform) params.platform = filters.platform
      if (filters.starred) params.starred = filters.starred === 'true'
      if (filters.remote) params.remote = filters.remote === 'true'

      let data
      if (q.trim()) {
        data = await api.searchJobs(q, params)
      } else {
        data = await api.listJobs(params)
      }
      setJobs(data)
    } catch {
      setJobs([])
    }
    setLoading(false)
  }, [searchQuery, filter])

  useEffect(() => { loadJobs() }, [])

  const handleSearch = (q) => {
    setSearchQuery(q)
    loadJobs(q, filter)
  }

  const handleClear = () => {
    setSearchQuery('')
    loadJobs('', filter)
  }

  const handleFilterChange = (key, val) => {
    const newFilter = { ...filter, [key]: val }
    setFilter(newFilter)
    loadJobs(searchQuery, newFilter)
  }

  const toggleStar = async (e, job) => {
    e.stopPropagation()
    try {
      await api.updateJob(job.id, { starred: !job.starred })
      setJobs(js => js.map(j => j.id === job.id ? { ...j, starred: !j.starred } : j))
    } catch {}
  }

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>

      {/* Smart Search Bar */}
      <div className="panel" style={{ padding:'1.25rem' }}>
        <SmartSearchBar
          onSearch={handleSearch}
          onClear={handleClear}
          placeholder="Search roles, companies, tech stack, salary..."
          autoFocus={false}
        />
      </div>

      {/* Filters row */}
      <div className="panel" style={{ padding:'0.875rem 1.25rem' }}>
        <div style={{ display:'flex', gap:'0.75rem', alignItems:'center', flexWrap:'wrap' }}>
          <span style={{ fontSize:'0.75rem', color:'var(--fg-subtle)', fontWeight:600, textTransform:'uppercase', letterSpacing:'0.06em', flexShrink:0 }}>
            Filter:
          </span>
          <select style={{ width:160 }} value={filter.status} onChange={e => handleFilterChange('status', e.target.value)}>
            <option value="">All Statuses</option>
            {STATUSES.map(s => <option key={s} value={s}>{BADGE_EMOJI[s]} {s.replace('_',' ')}</option>)}
          </select>
          <select style={{ width:140 }} value={filter.platform} onChange={e => handleFilterChange('platform', e.target.value)}>
            <option value="">All Platforms</option>
            {['linkedin','greenhouse','lever','workday','ashby','direct','other'].map(p => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          <select style={{ width:110 }} value={filter.remote} onChange={e => handleFilterChange('remote', e.target.value)}>
            <option value="">All Locations</option>
            <option value="true">Remote Only</option>
            <option value="false">Onsite</option>
          </select>
          <select style={{ width:120 }} value={filter.starred} onChange={e => handleFilterChange('starred', e.target.value)}>
            <option value="">All Jobs</option>
            <option value="true">Starred Only</option>
          </select>
          <select style={{ width:160, background: 'var(--surface-hover)', border: '1px solid var(--primary)' }} value={globalModel} onChange={handleModelChange}>
            {LLM_OPTIONS.map(l => {
              const isFunctional = activeProviders.some(p => l.value === p || l.value.startsWith(p + '-'));
              return <option key={l.value} value={l.value} disabled={!isFunctional} style={{ color: isFunctional ? 'inherit' : 'var(--fg-subtle)' }}>
                {l.label} {!isFunctional && '(Unavailable)'}
              </option>
            })}
          </select>
          <div style={{ marginLeft:'auto', display:'flex', alignItems:'center', gap:'0.5rem' }}>
            <span style={{ fontSize:'0.8rem', color:'var(--fg-subtle)' }}>
              {loading ? 'Loading...' : `${jobs.length} result${jobs.length !== 1 ? 's' : ''}`}
              {searchQuery && ` for "${searchQuery}"`}
            </span>
            {searchQuery && (
              <button className="btn btn-ghost btn-sm" onClick={handleClear} style={{ fontSize:'0.75rem' }}>
                Clear search
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Jobs Table */}
      <div className="panel">
        <div style={{
          padding:'0.625rem 1.25rem',
          borderBottom:'1px solid var(--surface-border)',
          fontSize:'0.7rem', color:'var(--fg-subtle)',
          display:'grid', gridTemplateColumns:'1fr 130px 90px 110px 80px 36px',
          letterSpacing:'0.06em', textTransform:'uppercase', fontWeight:700
        }}>
          <span>Role</span>
          <span>Status</span>
          <span>Match</span>
          <span>Platform</span>
          <span>Salary</span>
          <span></span>
        </div>

        {loading ? (
          <div style={{ padding:'3rem', textAlign:'center', color:'var(--fg-subtle)' }}>
            <div style={{ display:'inline-block', width:20, height:20, border:'2px solid var(--surface-border)', borderTopColor:'var(--primary)', borderRadius:'50%', animation:'spin 0.6s linear infinite', marginBottom:12 }} />
            <p>Searching your pipeline...</p>
          </div>
        ) : jobs.length === 0 ? (
          <div style={{ padding:'3rem', textAlign:'center', color:'var(--fg-subtle)' }}>
            {searchQuery
              ? <>No jobs match "<strong style={{ color:'var(--fg)' }}>{searchQuery}</strong>". Try a different keyword or clear the search.</>
              : 'No jobs tracked yet. Click "Track New Job" to add your first one.'}
          </div>
        ) : (
          jobs.map(job => (
            <div
              key={job.id}
              onClick={() => onOpenJob(job.id)}
              style={{
                padding:'0.875rem 1.25rem',
                borderBottom:'1px solid var(--surface-border)',
                display:'grid', gridTemplateColumns:'1fr 130px 90px 110px 80px 36px',
                alignItems:'center', cursor:'pointer', transition:'background 0.12s'
              }}
              onMouseEnter={e => e.currentTarget.style.background='var(--surface-hover)'}
              onMouseLeave={e => e.currentTarget.style.background='transparent'}
            >
              {/* Role */}
              <div>
                <div style={{ display:'flex', alignItems:'center', gap:6 }}>
                  <span style={{ fontWeight:600, fontSize:'0.875rem' }}>{job.title}</span>
                  {job.remote && <span style={{ fontSize:'0.65rem', color:'var(--info)', background:'var(--info-subtle)', padding:'1px 5px', borderRadius:4 }}>Remote</span>}
                </div>
                <div style={{ fontSize:'0.75rem', color:'var(--fg-muted)', marginTop:1 }}>{job.company}</div>
              </div>
              {/* Status */}
              <span className={`badge badge-${job.status}`} style={{ fontSize:'0.72rem' }}>
                {BADGE_EMOJI[job.status]} {job.status?.replace('_',' ')}
              </span>
              {/* Match Score */}
              <span style={{
                color: job.match_score >= 80 ? 'var(--success)' : job.match_score >= 60 ? 'var(--warning)' : job.match_score ? 'var(--danger)' : 'var(--fg-subtle)',
                fontWeight: job.match_score ? 700 : 400, fontSize:'0.875rem'
              }}>
                {job.match_score ? `${job.match_score}%` : '--'}
              </span>
              {/* Platform */}
              <span style={{ color:'var(--fg-muted)', fontSize:'0.8rem', textTransform:'capitalize' }}>
                {job.platform || '--'}
              </span>
              {/* Salary */}
              <span style={{ color: job.salary_min ? 'var(--success)' : 'var(--fg-subtle)', fontSize:'0.8rem', fontWeight: job.salary_min ? 600 : 400 }}>
                {job.salary_min ? `$${Math.round(job.salary_min/1000)}K` : '--'}
              </span>
              {/* Star */}
              <button
                onClick={e => toggleStar(e, job)}
                style={{ background:'none', border:'none', cursor:'pointer', fontSize:'1rem', padding:2, borderRadius:4, opacity: job.starred ? 1 : 0.3, transition:'opacity 0.15s' }}
                title={job.starred ? 'Unstar' : 'Star'}
              >
                {job.starred ? '⭐' : '☆'}
              </button>
            </div>
          ))
        )}
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
