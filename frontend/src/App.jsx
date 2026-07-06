import React, { useState, useEffect } from 'react'
import './index.css'
import { api, setToken, clearToken } from './api/client'
import Auth from './views/Auth'
import Dashboard from './views/Dashboard'
import JobList from './views/JobList'
import JobDetail from './views/JobDetail'
import Settings from './views/Settings'
import StoryBank from './views/StoryBank'
import Insights from './views/Insights'
import { usePreferredLlm, setPreferredLlm, getLlmOptions, getPreferredLlm } from './model'

// ── Theme (dark default, persisted) ───────────────────────────────────────────
const THEME_KEY = 'hireos_theme'
function applyTheme(t) {
  document.documentElement.dataset.theme = t
  localStorage.setItem(THEME_KEY, t)
}
applyTheme(localStorage.getItem(THEME_KEY) || 'dark') // before first paint

function ThemeToggle() {
  const [theme, setTheme] = useState(() => localStorage.getItem(THEME_KEY) || 'dark')
  const toggle = () => {
    const next = theme === 'dark' ? 'light' : 'dark'
    applyTheme(next)
    setTheme(next)
  }
  return (
    <button className="btn btn-ghost btn-icon" onClick={toggle}
      title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
      {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
    </button>
  )
}

// ── Global model selector (top bar) — drives every LLM op across the app ───────
function ModelSelector() {
  const llm = usePreferredLlm()
  const [providers, setProviders] = useState([])
  useEffect(() => { api.getProviders().then(r => setProviders(r.available || [])).catch(() => {}) }, [])
  const isAvail = v => providers.some(p => v === p || v.startsWith(p + '-') || v.startsWith(p + ':'))
  return (
    <select className="btn btn-outline btn-sm" style={{ maxWidth: 220 }} value={llm}
      onChange={e => setPreferredLlm(e.target.value)} title="Model used across the whole platform">
      {getLlmOptions().map(o => (
        <option key={o.value} value={o.value} disabled={!isAvail(o.value)}>
          {o.label}{!isAvail(o.value) ? ' (Unavailable)' : ''}
        </option>
      ))}
    </select>
  )
}

// ── Icons ─────────────────────────────────────────────────────────────────────
const Icon = ({ d, size = 18 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d={d} />
  </svg>
)
const HomeIcon = () => <Icon d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
const ListIcon = () => <Icon d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
const BookIcon = () => <Icon d="M2 3h6a4 4 0 014 4v14a4 4 0 00-4-4H2z M22 3h-6a4 4 0 00-4 4v14a4 4 0 014-4h6z" />
const SettingsIcon = () => <Icon d="M12 15a3 3 0 100-6 3 3 0 000 6z M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" />

const PulseIcon = () => <Icon d="M22 12h-4l-3 9L9 3l-3 9H2" />
const XIcon = () => <Icon d="M18 6L6 18M6 6l12 12" size={18} />
const PlusIcon = () => <Icon d="M12 5v14M5 12h14" size={16} />
const SunIcon = () => <Icon d="M12 17a5 5 0 100-10 5 5 0 000 10zM12 1v2m0 18v2M4.22 4.22l1.42 1.42m12.72 12.72l1.42 1.42M1 12h2m18 0h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" size={16} />
const MoonIcon = () => <Icon d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" size={16} />
const LinkIcon = () => <Icon d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71" size={14} />
const ClipboardIcon = () => <Icon d="M16 4h2a2 2 0 012 2v14a2 2 0 01-2 2H6a2 2 0 01-2-2V6a2 2 0 012-2h2M9 2h6a1 1 0 011 1v2a1 1 0 01-1 1H9a1 1 0 01-1-1V3a1 1 0 011-1z" size={14} />
const EditIcon = () => <Icon d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z" size={14} />
const MenuIcon = () => <Icon d="M3 12h18M3 6h18M3 18h18" size={20} />

// ── Track Job Modal ───────────────────────────────────────────────────────────
function TrackJobModal({ onClose, onAdded }) {
  const [mode, setMode] = useState('url') // 'url' | 'paste' | 'manual'
  const [url, setUrl] = useState('')
  const [jdText, setJdText] = useState('')
  const [form, setForm] = useState({ company:'', title:'', url:'', platform:'', location:'', remote:false, salary_min:'', salary_max:'', priority:'medium' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submitUrl = async () => {
    if (!url.trim()) return
    setLoading(true); setError('')
    try {
      const result = await api.trackUrl(url.trim(), getPreferredLlm())
      onAdded(result); onClose()
    } catch(e) { setError(e.message) }
    setLoading(false)
  }

  const submitPaste = async () => {
    if (!jdText.trim()) return
    setLoading(true); setError('')
    try {
      const result = await api.trackJdText(jdText.trim(), getPreferredLlm())
      onAdded(result); onClose()
    } catch(e) { setError(e.message) }
    setLoading(false)
  }

  const submitManual = async () => {
    if (!form.company || !form.title) return
    setLoading(true); setError('')
    try {
      const job = await api.createJob({
        ...form,
        salary_min: form.salary_min ? parseInt(form.salary_min) : null,
        salary_max: form.salary_max ? parseInt(form.salary_max) : null,
      })
      onAdded({ job_id: job.id, job }); onClose()
    } catch(e) { setError(e.message) }
    setLoading(false)
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header">
          <h3 className="modal-title">Track New Job</h3>
          <button className="btn btn-ghost btn-icon" onClick={onClose}><XIcon /></button>
        </div>

        {/* Mode tabs */}
        <div className="flex gap-sm" style={{ marginBottom:'1.25rem', flexWrap:'wrap' }}>
          <button className={`btn btn-sm ${mode==='url'?'btn-primary':'btn-outline'}`} onClick={() => { setMode('url'); setError('') }}>
            <LinkIcon /> From URL
          </button>
          <button className={`btn btn-sm ${mode==='paste'?'btn-primary':'btn-outline'}`} onClick={() => { setMode('paste'); setError('') }}>
            <ClipboardIcon /> Paste JD
          </button>
          <button className={`btn btn-sm ${mode==='manual'?'btn-primary':'btn-outline'}`} onClick={() => { setMode('manual'); setError('') }}>
            <EditIcon /> Manual
          </button>
        </div>

        {/* URL mode */}
        {mode === 'url' && (
          <div style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>
            <div className="form-group">
              <label className="form-label">Job Posting URL</label>
              <input value={url} onChange={e => setUrl(e.target.value)}
                placeholder="https://jobs.lever.co/company/job-id"
                onKeyDown={e => e.key === 'Enter' && submitUrl()} />
            </div>
            <p style={{ fontSize:'0.78rem', color:'var(--fg-subtle)' }}>
              AI fetches and auto-extracts company, title, salary, location, and JD.
            </p>
            <div className="alert alert-warning">
              <span><strong>LinkedIn</strong> links require login — use the <strong>Paste JD</strong> tab instead.</span>
            </div>
            {error && <p className="alert alert-danger">{error}</p>}
            <div className="flex gap-sm" style={{ justifyContent:'flex-end' }}>
              <button className="btn btn-outline btn-sm" onClick={onClose}>Cancel</button>
              <button className="btn btn-primary" onClick={submitUrl} disabled={loading || !url.trim()}>
                {loading ? 'Extracting...' : 'Track This Job'}
              </button>
            </div>
          </div>
        )}

        {/* Paste JD mode — works for LinkedIn, Greenhouse, any ATS */}
        {mode === 'paste' && (
          <div style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>
            <div className="form-group">
              <label className="form-label">Paste Job Description Text</label>
              <textarea rows={11} value={jdText} onChange={e => setJdText(e.target.value)}
                placeholder="Paste the full job description here — from LinkedIn, Greenhouse, Lever, or any ATS. The AI will extract company, title, salary, tech stack, and more automatically."
                style={{ resize:'vertical', fontFamily:'monospace', fontSize:'0.8rem', lineHeight:1.6 }} />
            </div>
            <p style={{ fontSize:'0.78rem', color:'var(--fg-subtle)' }}>
              <strong>LinkedIn tip:</strong> Open the job listing, scroll to the description, select all the text, and paste here.
            </p>
            {error && <p className="alert alert-danger">{error}</p>}
            <div className="flex gap-sm" style={{ justifyContent:'flex-end' }}>
              <button className="btn btn-outline btn-sm" onClick={onClose}>Cancel</button>
              <button className="btn btn-primary" onClick={submitPaste} disabled={loading || !jdText.trim()}>
                {loading ? 'Extracting...' : 'Extract & Track'}
              </button>
            </div>
          </div>
        )}

        {/* Manual entry mode */}
        {mode === 'manual' && (
          <div style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>
            <div className="form-grid">
              <div className="form-group"><label className="form-label">Company *</label><input value={form.company} onChange={e => setForm(f=>({...f,company:e.target.value}))} placeholder="Google" /></div>
              <div className="form-group"><label className="form-label">Job Title *</label><input value={form.title} onChange={e => setForm(f=>({...f,title:e.target.value}))} placeholder="Principal AI Engineer" /></div>
              <div className="form-group"><label className="form-label">Job URL</label><input value={form.url} onChange={e => setForm(f=>({...f,url:e.target.value}))} placeholder="https://..." /></div>
              <div className="form-group"><label className="form-label">Platform</label>
                <select value={form.platform} onChange={e => setForm(f=>({...f,platform:e.target.value}))}>
                  {['','linkedin','greenhouse','lever','workday','ashby','direct','other'].map(p => (
                    <option key={p} value={p}>{p || '-- select --'}</option>
                  ))}
                </select>
              </div>
              <div className="form-group"><label className="form-label">Min Salary ($)</label><input type="number" value={form.salary_min} onChange={e => setForm(f=>({...f,salary_min:e.target.value}))} placeholder="180000" /></div>
              <div className="form-group"><label className="form-label">Max Salary ($)</label><input type="number" value={form.salary_max} onChange={e => setForm(f=>({...f,salary_max:e.target.value}))} placeholder="250000" /></div>
              <div className="form-group"><label className="form-label">Priority</label>
                <select value={form.priority} onChange={e => setForm(f=>({...f,priority:e.target.value}))}>
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </select>
              </div>
              <div className="form-group" style={{ justifyContent:'flex-end', paddingTop:'1.5rem' }}>
                <label style={{ display:'flex', alignItems:'center', gap:8, cursor:'pointer' }}>
                  <input type="checkbox" checked={form.remote} onChange={e => setForm(f=>({...f,remote:e.target.checked}))} /> Remote
                </label>
              </div>
            </div>
            {error && <p className="alert alert-danger">{error}</p>}
            <div className="flex gap-sm" style={{ justifyContent:'flex-end' }}>
              <button className="btn btn-outline btn-sm" onClick={onClose}>Cancel</button>
              <button className="btn btn-primary" onClick={submitManual} disabled={loading || !form.company || !form.title}>
                {loading ? 'Adding...' : 'Add to Pipeline'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── App Shell ─────────────────────────────────────────────────────────────────
const NAV = [
  { id: 'dashboard', label: 'Dashboard', icon: <HomeIcon /> },
  { id: 'jobs', label: 'All Applications', icon: <ListIcon /> },
  { id: 'insights', label: 'Insights', icon: <PulseIcon /> },
  { id: 'stories', label: 'Story Bank', icon: <BookIcon /> },
  { id: 'settings', label: 'Settings', icon: <SettingsIcon /> },
]

const TOP_VIEWS = ['dashboard', 'jobs', 'insights', 'stories', 'settings']

// URL-hash routing so the current view survives a page refresh and works with browser back/forward.
function parseHash() {
  const h = window.location.hash.replace(/^#\/?/, '')
  if (!h) return { view: 'dashboard', jobId: null }
  const [seg, id] = h.split('/')
  if (seg === 'job' && id && /^\d+$/.test(id)) return { view: 'job-detail', jobId: parseInt(id, 10) }
  if (TOP_VIEWS.includes(seg)) return { view: seg, jobId: null }
  return { view: 'dashboard', jobId: null }
}

function hashFor(view, jobId) {
  if (view === 'job-detail' && jobId) return `#/job/${jobId}`
  return `#/${view}`
}

export default function App() {
  const [user, setUser] = useState(null)
  const [authChecked, setAuthChecked] = useState(false)
  const [view, setView] = useState(() => parseHash().view)
  const [selectedJobId, setSelectedJobId] = useState(() => parseHash().jobId)
  const [showTrackModal, setShowTrackModal] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const [authError, setAuthError] = useState('')

  useEffect(() => {
    // Pick up an SSO redirect (?auth_token / ?auth_error) before checking session.
    const params = new URLSearchParams(window.location.search)
    const ssoToken = params.get('auth_token')
    const ssoError = params.get('auth_error')
    if (ssoToken) setToken(ssoToken)
    if (ssoError) setAuthError(ssoError)
    if (ssoToken || ssoError) {
      window.history.replaceState({}, '', window.location.pathname + window.location.hash)
    }

    const token = localStorage.getItem('hireos_token')
    if (!token) { setAuthChecked(true); return }
    api.me().then(data => { setUser(data.email); setAuthChecked(true) })
       .catch(() => { clearToken(); setAuthChecked(true) })
  }, [])

  // Keep the URL hash in sync with the current view (so refresh restores it).
  // Skip if the base route already matches — JobDetail owns a sub-segment (#/job/<id>/<tab>)
  // that we must not clobber.
  useEffect(() => {
    const cur = parseHash()
    if (cur.view === view && cur.jobId === selectedJobId) return
    window.location.hash = hashFor(view, selectedJobId)
  }, [view, selectedJobId])

  // React to browser back/forward and manual hash edits
  useEffect(() => {
    const onHash = () => {
      const { view: v, jobId } = parseHash()
      setView(v)
      setSelectedJobId(jobId)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const logout = () => { clearToken(); setUser(null) }

  if (!authChecked) return null
  if (!user) return <Auth onAuth={email => setUser(email)} ssoError={authError} />

  const openJob = (id) => { setSelectedJobId(id); setView('job-detail') }
  const goBack = () => { setSelectedJobId(null); setView('jobs') }
  const handleJobAdded = (result) => {
    setRefreshKey(k => k + 1)
    if (result?.job_id) openJob(result.job_id)
  }

  const currentNav = NAV.find(n => n.id === view || (view === 'job-detail' && n.id === 'jobs'))

  return (
    <div id="app-shell">
      {showTrackModal && <TrackJobModal onClose={() => setShowTrackModal(false)} onAdded={handleJobAdded} />}

      {/* Mobile sidebar backdrop */}
      {sidebarOpen && <div className="sidebar-overlay" onClick={() => setSidebarOpen(false)} />}

      {/* Sidebar */}
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="sidebar-logo">
          <span className="logo-word">
            Hire<em>OS</em>
          </span>
        </div>
        <div className="nav-section-label">Navigation</div>
        {NAV.map(n => (
          <button key={n.id} className={`nav-item ${(view === n.id || (view === 'job-detail' && n.id === 'jobs')) ? 'active' : ''}`}
            onClick={() => { setView(n.id); setSelectedJobId(null); setSidebarOpen(false) }}>
            {n.icon}
            {n.label}
          </button>
        ))}
        <div style={{ marginTop:'auto', paddingTop:'1rem', borderTop:'1px solid var(--surface-border)' }}>
          <button className="btn btn-primary" style={{ width:'100%', gap:'0.4rem' }}
            onClick={() => { setShowTrackModal(true); setSidebarOpen(false) }}>
            <PlusIcon /> Track New Job
          </button>
        </div>
      </aside>

      {/* Main */}
      <div className="main-area">
        <header className="topbar">
          <div className="flex items-center gap-sm" style={{ minWidth: 0 }}>
            <button className="btn btn-ghost btn-icon hamburger" onClick={() => setSidebarOpen(true)} title="Menu"><MenuIcon /></button>
            <h1>{view === 'job-detail' ? 'Job Detail & Tracker' : currentNav?.label}</h1>
          </div>
          <div className="topbar-actions">
            {view === 'job-detail' && (
              <button className="btn btn-outline btn-sm" onClick={goBack}>Back</button>
            )}
            <ModelSelector />
            <button className="btn btn-outline btn-sm" onClick={() => setShowTrackModal(true)}>
              <PlusIcon /> Track Job
            </button>
            <button className="btn btn-outline btn-sm" onClick={api.exportCSV}>Export CSV</button>
            <ThemeToggle />
            <span className="topbar-user">{user}</span>
            <button className="btn btn-outline btn-sm" onClick={logout}>Logout</button>
          </div>
        </header>
        <div className="content scrollbar-thin">
          {view === 'dashboard' && <Dashboard onOpenJob={openJob} key={refreshKey} />}
          {view === 'jobs' && <JobList onOpenJob={openJob} key={refreshKey} />}
          {view === 'insights' && <Insights />}
          {view === 'stories' && <StoryBank />}
          {view === 'job-detail' && selectedJobId && <JobDetail key={selectedJobId} jobId={selectedJobId} />}
          {view === 'settings' && <Settings />}
        </div>
      </div>

    </div>
  )
}
