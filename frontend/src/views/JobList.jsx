import React, { useEffect, useMemo, useState, useCallback } from 'react'
import { api } from '../api/client'
import SmartSearchBar from '../components/SmartSearchBar'

const STATUSES = ['found','analyzing','pending','approved','applying','waiting_human','needs_human','applied','screening','interview_1','interview_2','offer','rejected','withdrawn','archived']
const StarIcon = ({ filled }) => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden="true">
    <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z" />
  </svg>
)

// created_at is when HireOS found / tracked the job (listed_at is never filled).
const added = (j) => (j.created_at ? new Date(j.created_at.replace(' ', 'T') + (/[Z+]/.test(j.created_at.slice(19)) ? '' : 'Z')) : null)
const SORTS = {
  newest: (a, b) => (added(b) || 0) - (added(a) || 0),
  oldest: (a, b) => (added(a) || 0) - (added(b) || 0),
  match: (a, b) => (b.match_score ?? -1) - (a.match_score ?? -1),
  updated: (a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')),
}
const SORT_KEY = 'hireos_jobs_sort'

function addedLabel(j) {
  const d = added(j)
  if (!d) return '--'
  const days = Math.floor((Date.now() - d.getTime()) / 86400000)
  if (days < 1) return 'Today'
  if (days < 2) return 'Yesterday'
  if (days < 7) return `${days}d ago`
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', ...(days > 300 ? { year: 'numeric' } : {}) })
}

const ArchiveIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <rect x="3" y="4" width="18" height="4" /><path d="M5 8v12h14V8M10 12h4" />
  </svg>
)
const RestoreIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" />
  </svg>
)
const GRID = '28px 1fr 130px 90px 110px 80px 90px 80px'
const iconBtn = { background:'none', border:'none', cursor:'pointer', minWidth:36, minHeight:44, display:'inline-flex', alignItems:'center', justifyContent:'center' }

export default function JobList({ onOpenJob }) {
  const [jobs, setJobs] = useState([])
  const [searchQuery, setSearchQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState({ status: '', platform: '', starred: '', remote: '' })
  const [sort, setSortState] = useState(() => {
    try { return SORTS[localStorage.getItem(SORT_KEY)] ? localStorage.getItem(SORT_KEY) : 'newest' } catch { return 'newest' }
  })
  const setSort = (v) => { setSortState(v); try { localStorage.setItem(SORT_KEY, v) } catch { /* private mode */ } }
  const sorted = useMemo(() => [...jobs].sort(SORTS[sort]), [jobs, sort])
  const [selected, setSelected] = useState(() => new Set())
  const [notice, setNotice] = useState('')
  const toggleSel = (id) => setSelected(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })
  const allSelected = sorted.length > 0 && sorted.every(j => selected.has(j.id))

  // Archive or restore; the job leaves the current view either way unless it still matches the filter.
  const setArchived = async (ids, archived) => {
    try {
      const { changed } = await api.archiveJobs(ids, archived)
      const viewingArchived = filter.status === 'archived'
      setJobs(js => js.filter(j => !ids.includes(j.id) || j.status === 'applying' || (archived ? viewingArchived : !viewingArchived)))
      setSelected(new Set())
      setNotice(`${changed} job${changed === 1 ? '' : 's'} ${archived ? 'archived' : 'restored'}${archived ? ' — find them under All Statuses → archived' : ''}`)
      setTimeout(() => setNotice(''), 5000)
    } catch (e) { setNotice(e.message) }
  }

  // Model selection now lives in the global top-bar selector (App.jsx).

  // Load jobs -- uses search endpoint when query is set, otherwise list
  const loadJobs = useCallback(async (q = searchQuery, filters = filter) => {
    setLoading(true)
    try {
      const params = { limit: 2000 }   // ponytail: everything in one list; paginate past a few thousand jobs
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
    setSelected(new Set())
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
            {STATUSES.map(s => <option key={s} value={s}>{s.replace('_',' ')}</option>)}
          </select>
          <select style={{ width:140 }} value={filter.platform} onChange={e => handleFilterChange('platform', e.target.value)}>
            <option value="">All Platforms</option>
            {['linkedin','greenhouse','lever','workday','ashby','direct','other'].map(p => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          <select style={{ width:140 }} value={filter.remote} onChange={e => handleFilterChange('remote', e.target.value)}>
            <option value="">All Locations</option>
            <option value="true">Remote Only</option>
            <option value="false">Onsite</option>
          </select>
          <select style={{ width:120 }} value={filter.starred} onChange={e => handleFilterChange('starred', e.target.value)}>
            <option value="">All Jobs</option>
            <option value="true">Starred Only</option>
          </select>
          <select style={{ width:165 }} value={sort} onChange={e => setSort(e.target.value)} aria-label="Sort jobs">
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="match">Best match</option>
            <option value="updated">Recently updated</option>
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

      {(selected.size > 0 || notice) && (
        <div className="panel" style={{ padding:'0.6rem 1.25rem', display:'flex', alignItems:'center', gap:'0.75rem', flexWrap:'wrap' }}>
          {selected.size > 0 && <>
            <strong style={{ fontSize:'0.85rem' }}>{selected.size} selected</strong>
            {filter.status === 'archived'
              ? <button className="btn btn-outline btn-sm" onClick={() => setArchived([...selected], false)}>Restore {selected.size} selected</button>
              : <button className="btn btn-primary btn-sm" onClick={() => setArchived([...selected], true)}>Archive {selected.size} selected</button>}
            <button className="btn btn-ghost btn-sm" onClick={() => setSelected(new Set())}>Clear</button>
          </>}
          {notice && <span style={{ fontSize:'0.8rem', color:'var(--fg-muted)' }}>{notice}</span>}
        </div>
      )}

      {/* Jobs Table */}
      <div className="panel">
        <div className="job-table-head" style={{
          padding:'0.625rem 1.25rem',
          borderBottom:'1px solid var(--surface-border)',
          fontSize:'0.7rem', color:'var(--fg-subtle)',
          display:'grid', gridTemplateColumns:GRID,
          letterSpacing:'0.06em', textTransform:'uppercase', fontWeight:700
        }}>
          <input type="checkbox" aria-label="Select all" checked={allSelected}
            onChange={() => setSelected(allSelected ? new Set() : new Set(sorted.map(j => j.id)))} />
          <span>Role</span>
          <span>Status</span>
          <span>Match</span>
          <span>Platform</span>
          <span>Salary</span>
          <span>Added</span>
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
          sorted.map(job => (
            <div
              key={job.id}
              className="job-table-row"
              onClick={() => onOpenJob(job.id)}
              style={{
                padding:'0.875rem 1.25rem',
                borderBottom:'1px solid var(--surface-border)',
                display:'grid', gridTemplateColumns:GRID,
                alignItems:'center', cursor:'pointer', transition:'background 0.12s'
              }}
              onMouseEnter={e => e.currentTarget.style.background='var(--surface-hover)'}
              onMouseLeave={e => e.currentTarget.style.background='transparent'}
            >
              <input type="checkbox" aria-label={`Select ${job.title}`} checked={selected.has(job.id)}
                onClick={e => e.stopPropagation()} onChange={() => toggleSel(job.id)} />
              {/* Role */}
              <div className="job-role">
                <div style={{ display:'flex', alignItems:'center', gap:6 }}>
                  <span style={{ fontWeight:600, fontSize:'0.875rem' }}>{job.title}</span>
                  {job.remote && <span style={{ fontSize:'0.65rem', color:'var(--info)', background:'var(--info-subtle)', padding:'1px 5px', }}>Remote</span>}
                </div>
                <div style={{ fontSize:'0.75rem', color:'var(--fg-muted)', marginTop:1 }}>{job.company}</div>
              </div>
              {/* Status */}
              <span className={`badge badge-${job.status}`} style={{ fontSize:'0.72rem' }}>
                {job.status?.replace('_',' ')}
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
              {/* Added */}
              <span title={added(job)?.toLocaleString() || ''} style={{ color:'var(--fg-muted)', fontSize:'0.8rem', whiteSpace:'nowrap' }}>
                {addedLabel(job)}
              </span>
              <div className="job-actions" style={{ display:'flex' }}>
                <button
                  onClick={e => toggleStar(e, job)}
                  style={{ ...iconBtn, color: job.starred ? 'var(--fg)' : 'var(--fg-muted)' }}
                  aria-label={job.starred ? 'Unstar' : 'Star'}
                  title={job.starred ? 'Unstar' : 'Star'}
                >
                  <StarIcon filled={job.starred} />
                </button>
                {job.status !== 'applying' && (
                  <button
                    onClick={e => { e.stopPropagation(); setArchived([job.id], job.status !== 'archived') }}
                    style={{ ...iconBtn, color:'var(--fg-muted)' }}
                    aria-label={job.status === 'archived' ? 'Restore' : 'Archive as irrelevant'}
                    title={job.status === 'archived' ? 'Restore' : 'Archive as irrelevant'}
                  >
                    {job.status === 'archived' ? <RestoreIcon /> : <ArchiveIcon />}
                  </button>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
