import React, { useState, useEffect, useRef } from 'react'
import './index.css'
import { api, clearToken } from './api/client'
import Auth from './views/Auth'
import Dashboard from './views/Dashboard'
import JobList from './views/JobList'
import JobDetail from './views/JobDetail'
import Settings from './views/Settings'
import StoryBank from './views/StoryBank'
import Insights from './views/Insights'

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
const BotIcon = () => <Icon d="M12 2a2 2 0 012 2v1h3a2 2 0 012 2v11a2 2 0 01-2 2H7a2 2 0 01-2-2V7a2 2 0 012-2h3V4a2 2 0 012-2zM9 10v2m6-2v2" size={22} />
const SendIcon = () => <Icon d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" size={16} />
const XIcon = () => <Icon d="M18 6L6 18M6 6l12 12" size={18} />
const PlusIcon = () => <Icon d="M12 5v14M5 12h14" size={16} />

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
      const result = await api.trackUrl(url.trim())
      onAdded(result); onClose()
    } catch(e) { setError(e.message) }
    setLoading(false)
  }

  const submitPaste = async () => {
    if (!jdText.trim()) return
    setLoading(true); setError('')
    try {
      const result = await api.trackJdText(jdText.trim())
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
          <h3 className="modal-title">+ Track New Job</h3>
          <button className="btn btn-ghost btn-icon" onClick={onClose}><XIcon /></button>
        </div>

        {/* Mode tabs */}
        <div className="flex gap-sm" style={{ marginBottom:'1.25rem', flexWrap:'wrap' }}>
          <button className={`btn btn-sm ${mode==='url'?'btn-primary':'btn-outline'}`} onClick={() => { setMode('url'); setError('') }}>
            🔗 From URL
          </button>
          <button className={`btn btn-sm ${mode==='paste'?'btn-primary':'btn-outline'}`} onClick={() => { setMode('paste'); setError('') }}>
            📋 Paste JD
          </button>
          <button className={`btn btn-sm ${mode==='manual'?'btn-primary':'btn-outline'}`} onClick={() => { setMode('manual'); setError('') }}>
            ✏️ Manual
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
              🤖 AI fetches and auto-extracts company, title, salary, location, and JD.
            </p>
            <div style={{ padding:'0.5rem 0.75rem', background:'rgba(245,158,11,0.1)', borderRadius:'var(--radius-sm)', fontSize:'0.78rem', color:'var(--warning)', border:'1px solid rgba(245,158,11,0.2)' }}>
              ⚠️ <strong>LinkedIn</strong> links require login — use the <strong>📋 Paste JD</strong> tab instead.
            </div>
            {error && <p style={{ color:'var(--danger)', fontSize:'0.8rem' }}>⚠️ {error}</p>}
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
            {error && <p style={{ color:'var(--danger)', fontSize:'0.8rem' }}>⚠️ {error}</p>}
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
            {error && <p style={{ color:'var(--danger)', fontSize:'0.8rem' }}>⚠️ {error}</p>}
            <div className="flex gap-sm" style={{ justifyContent:'flex-end' }}>
              <button className="btn btn-outline btn-sm" onClick={onClose}>Cancel</button>
              <button className="btn btn-primary" onClick={submitManual} disabled={loading || !form.company || !form.title}>
                {loading ? 'Adding...' : '+ Add to Pipeline'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Floating Chat ─────────────────────────────────────────────────────────────
function FloatingChat() {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState([
    { role: 'bot', text: "Hi! I'm your Job Swarm assistant. Tell me what to do — e.g. \"Track LinkedIn job at Google\", \"Analyze the OpenAI role\", or \"What's pending review?\"" }
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const msgEnd = useRef(null)

  useEffect(() => { msgEnd.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const send = async () => {
    if (!input.trim() || loading) return
    const text = input.trim()
    setInput('')
    setMessages(m => [...m, { role: 'user', text }])
    setLoading(true)
    try {
      const res = await api.chat(text)
      setMessages(m => [...m, { role: 'bot', text: res.reply }])
    } catch {
      setMessages(m => [...m, { role: 'bot', text: 'Connection error — is the backend running?' }])
    }
    setLoading(false)
  }

  return (
    <>
      {open && (
        <div className="chat-window">
          <div className="chat-header">
            <div>
              <div className="chat-header-title">Swarm AI</div>
              <div className="chat-header-sub">Powered by Gemini - Natural language commands</div>
            </div>
            <button className="btn btn-ghost btn-icon" onClick={() => setOpen(false)}><XIcon /></button>
          </div>
          <div className="chat-messages scrollbar-thin">
            {messages.map((m, i) => (
              <div key={i} className={`chat-msg ${m.role}`}>{m.text}</div>
            ))}
            {loading && <div className="chat-msg bot" style={{ opacity: 0.6 }}>Thinking...</div>}
            <div ref={msgEnd} />
          </div>
          <div className="chat-input-area">
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && send()}
              placeholder="Ask or command anything..."
            />
            <button className="btn btn-primary btn-icon" onClick={send}><SendIcon /></button>
          </div>
        </div>
      )}
      <button className="chat-fab" onClick={() => setOpen(o => !o)}>
        <BotIcon />
      </button>
    </>
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

export default function App() {
  const [user, setUser] = useState(null)
  const [authChecked, setAuthChecked] = useState(false)
  const [view, setView] = useState('dashboard')
  const [selectedJobId, setSelectedJobId] = useState(null)
  const [showTrackModal, setShowTrackModal] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    const token = localStorage.getItem('hireos_token')
    if (!token) { setAuthChecked(true); return }
    api.me().then(data => { setUser(data.email); setAuthChecked(true) })
       .catch(() => { clearToken(); setAuthChecked(true) })
  }, [])

  const logout = () => { clearToken(); setUser(null) }

  if (!authChecked) return null
  if (!user) return <Auth onAuth={email => setUser(email)} />

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

      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-logo">
          <h2>HireOS</h2>
          <p>Autonomous Job Applications</p>
        </div>
        <div className="nav-section-label">Navigation</div>
        {NAV.map(n => (
          <button key={n.id} className={`nav-item ${(view === n.id || (view === 'job-detail' && n.id === 'jobs')) ? 'active' : ''}`}
            onClick={() => { setView(n.id); setSelectedJobId(null) }}>
            {n.icon}
            {n.label}
          </button>
        ))}
        <div style={{ marginTop:'auto', paddingTop:'1rem', borderTop:'1px solid var(--surface-border)' }}>
          <button className="btn btn-primary" style={{ width:'100%', gap:'0.4rem' }}
            onClick={() => setShowTrackModal(true)}>
            <PlusIcon /> Track New Job
          </button>
        </div>
      </aside>

      {/* Main */}
      <div className="main-area">
        <header className="topbar">
          <h1>{view === 'job-detail' ? 'Job Detail & Tracker' : currentNav?.label}</h1>
          <div className="topbar-actions">
            {view === 'job-detail' && (
              <button className="btn btn-outline btn-sm" onClick={goBack}>Back</button>
            )}
            <button className="btn btn-outline btn-sm" onClick={() => setShowTrackModal(true)}>
              <PlusIcon /> Track Job
            </button>
            <button className="btn btn-outline btn-sm" onClick={api.exportCSV}>Export CSV</button>
            <span style={{ fontSize: '0.78rem', color: 'var(--fg-subtle)' }}>{user}</span>
            <button className="btn btn-outline btn-sm" onClick={logout}>Logout</button>
          </div>
        </header>
        <div className="content scrollbar-thin">
          {view === 'dashboard' && <Dashboard onOpenJob={openJob} key={refreshKey} />}
          {view === 'jobs' && <JobList onOpenJob={openJob} key={refreshKey} />}
          {view === 'insights' && <Insights />}
          {view === 'stories' && <StoryBank />}
          {view === 'job-detail' && selectedJobId && <JobDetail jobId={selectedJobId} />}
          {view === 'settings' && <Settings />}
        </div>
      </div>

      {/* Floating Chat - always visible */}
      <FloatingChat />
    </div>
  )
}
