import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../api/client'
import { usePreferredLlm } from '../model'
import ResumeEditor from '../components/ResumeEditor'

const PencilIcon = () => <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>

const JOB_TABS = ['dashboard', 'intelligence', 'documents']

// Persist the active tab in the URL hash (#/job/<id>/<tab-slug>) so refresh keeps the tab.
const tabToSlug = (t) => t.replace(/ /g, '-')
const slugToTab = (s) => s.replace(/-/g, ' ')
function tabFromHash() {
  const parts = window.location.hash.replace(/^#\/?/, '').split('/')
  if (parts[0] === 'job' && parts[2]) {
    const t = slugToTab(parts[2])
    if (JOB_TABS.includes(t)) return t
  }
  return 'dashboard'
}

const STATUSES = ['found','analyzing','pending','approved','applied','screening','interview_1','interview_2','offer','rejected','withdrawn']
const BADGE_EMOJI = { found:'🔍', analyzing:'🤖', pending:'⏳', approved:'✅', applied:'📤', screening:'📞', interview_1:'🎤', interview_2:'🎤', offer:'🎉', rejected:'❌', withdrawn:'↩️' }
const EVENT_ICONS = {
  status_change:'🔄', note:'📝', recruiter_contact:'📞', interview_scheduled:'📅',
  interview_completed:'✅', offer_received:'🎉', follow_up_sent:'📧',
  agent_action:'🤖', document_generated:'📄', application_submitted:'📤'
}

function Spinner({ small }) {
  return (
    <span style={{
      display:'inline-block', width: small ? 14 : 20, height: small ? 14 : 20,
      border: '2px solid var(--surface-border)',
      borderTopColor: 'var(--primary)',
      borderRadius:'50%', animation:'spin 0.6s linear infinite', flexShrink:0
    }} />
  )
}

function Toast({ message, type = 'success', onClose }) {
  useEffect(() => {
    if (type === 'error') return  // errors stay until clicked
    const t = setTimeout(onClose, 4000)
    return () => clearTimeout(t)
  }, [onClose, type])
  const colors = { success:'var(--success)', error:'var(--danger)', info:'var(--primary)' }
  return (
    <div onClick={onClose} style={{
      position:'fixed', bottom:'5rem', right:'2rem', zIndex:300,
      background:'var(--bg-2)', border:`1px solid ${colors[type]}`,
      borderRadius:'var(--radius)', padding:'0.875rem 1.25rem',
      boxShadow:'var(--shadow-lg)', maxWidth:420,
      animation:'slideUp 0.25s ease', color:'var(--fg)', fontSize:'0.875rem',
      cursor: type === 'error' ? 'pointer' : 'default',
    }}>
      {message}
      {type === 'error' && <span style={{marginLeft:12, opacity:0.5, fontSize:'0.75rem'}}>(click to dismiss)</span>}
    </div>
  )
}

function InlineNameEdit({ initialValue, defaultLabel, onSave }) {
  const [editing, setEditing] = useState(false)
  const [val, setVal] = useState(initialValue || '')
  if (editing) {
    return (
      <input 
        autoFocus
        value={val}
        onChange={e => setVal(e.target.value)}
        onBlur={() => { setEditing(false); if(val !== initialValue) onSave(val); }}
        onKeyDown={e => { if(e.key === 'Enter') e.target.blur(); }}
        style={{ fontSize:'0.875rem', padding:'2px 6px', border:'1px solid var(--primary)', borderRadius:'4px', background:'var(--bg)', color:'var(--fg)' }}
      />
    )
  }
  return (
    <div className="flex items-center gap-sm" style={{ fontWeight:600, fontSize:'0.875rem', cursor:'pointer', display:'inline-flex' }} onClick={() => { setVal(initialValue || ''); setEditing(true); }} title="Click to rename">
      {initialValue || defaultLabel} <span style={{ opacity:0.5 }}><PencilIcon /></span>
    </div>
  )
}

export default function JobDetail({ jobId }) {
  const [job, setJob] = useState(null)
  const [events, setEvents] = useState([])
  const [resumes, setResumes] = useState([])
  const [coverLetters, setCoverLetters] = useState([])
  const [activeTab, setActiveTabState] = useState(() => tabFromHash())
  const setActiveTab = (tab) => {
    setActiveTabState(tab)
    const parts = window.location.hash.replace(/^#\/?/, '').split('/')
    if (parts[0] === 'job' && parts[1]) {
      window.location.hash = `#/job/${parts[1]}/${tabToSlug(tab)}`
    }
  }
  const [editing, setEditing] = useState(false)
  const [edits, setEdits] = useState({})
  const [newEvent, setNewEvent] = useState({ title:'', description:'', event_type:'note' })
  const [addingEvent, setAddingEvent] = useState(false)
  const [loading, setLoading] = useState({ analyze:false, resume:false, cover:false, compare:false, evaluate:false, linkedin:false, research:false, interviewPrep:false })
  const selectedLlm = usePreferredLlm()
  const [feedback, setFeedback] = useState('')
  const [compareResults, setCompareResults] = useState(null)
  const [compareTask, setCompareTask] = useState('resume')
  const [toast, setToast] = useState(null)
  const [trackUrl, setTrackUrl] = useState('')
  const [editingResume, setEditingResume] = useState(null)
  // Career-ops enhanced state
  const [evalReport, setEvalReport] = useState(null)
  const [linkedInData, setLinkedInData] = useState(null)
  const [researchData, setResearchData] = useState(null)
  const [interviewData, setInterviewData] = useState(null)
  const [expandedBlocks, setExpandedBlocks] = useState({})
  const [activeProviders, setActiveProviders] = useState([])

  // Sync tab when the user uses browser back/forward or edits the hash
  useEffect(() => {
    const onHash = () => setActiveTabState(tabFromHash())
    window.addEventListener('hashchange', onHash)
    
    api.getProviders().then(res => setActiveProviders(res.available || [])).catch(() => {})
    
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const showToast = (message, type='success') => setToast({ message, type })
  const toggleBlock = (key) => setExpandedBlocks(b => ({ ...b, [key]: !b[key] }))

  const loadAdditionalData = useCallback(async () => {
    try {
      const [resReport, linkReport, prepReport] = await Promise.all([
        api.getDeepResearch(jobId).catch(() => null),
        api.getLinkedIn(jobId).catch(() => null),
        api.getInterviewPrep(jobId).catch(() => null)
      ])
      if (resReport && resReport.status === 'completed') setResearchData(resReport.research)
      if (linkReport && linkReport.status === 'completed') setLinkedInData(linkReport.outreach)
      if (prepReport && prepReport.status === 'completed') setInterviewData(prepReport.prep)
    } catch(e) {}
  }, [jobId])

  const load = useCallback(async () => {
    try {
      const [j, evs, rv, cl, evals, fups] = await Promise.all([
        api.getJob(jobId),
        api.getEvents(jobId),
        api.getResumes(jobId),
        api.getCoverLetters(jobId),
        api.getEvaluations(jobId),
        api.getFollowups(jobId)
      ])
      setJob(j)
      setEvents(evs)
      setResumes(rv)
      setCoverLetters(cl)
      
      if (evals && evals.length > 0) {
        setEvalReport(evals[0])
      }
      
      await loadAdditionalData()
    } catch (e) {
      showToast(`Failed to load job: ${e.message}`, 'error')
    }
  }, [jobId, loadAdditionalData])

  useEffect(() => { load() }, [load])

  // ATS score is written ~30-60s AFTER the resume itself is ready, so re-fetch
  // the resume list a few times until the newest version has its score.
  const pollForAtsScore = useCallback((tries = 8) => {
    if (tries <= 0) return
    setTimeout(async () => {
      try {
        const rv = await api.getResumes(jobId)
        setResumes(rv)
        if (rv[0]?.ats_score) return
      } catch {}
      pollForAtsScore(tries - 1)
    }, 12000)
  }, [jobId])

  // Unified background task polling
  useEffect(() => {
    let interval;
    const pollTasks = async () => {
      try {
        const tasks = await api.getTasks(jobId)
        
        const byType = type => tasks.find(t => t.task_type === type)
        const isProcessing = type => byType(type)?.status === 'processing'
        const failMsg = type => byType(type)?.status === 'failed' ? (byType(type)?.error_message || `${type} failed`) : null

        const isEval = isProcessing('evaluate')
        const isLinkedIn = isProcessing('linkedin')
        const isResearch = isProcessing('research')
        const isPrep = isProcessing('interview_prep')
        const isAnalysis = isProcessing('analyze')
        const isResume = isProcessing('resume')
        const isCover = isProcessing('cover_letter')

        setLoading(prev => {
          if (prev.evaluate && !isEval) {
            const err = failMsg('evaluate')
            err ? showToast(err, 'error') : (showToast('A-G Evaluation Complete!'), load())
          }
          if (prev.linkedin && !isLinkedIn) {
            const err = failMsg('linkedin')
            err ? showToast(err, 'error') : (showToast('LinkedIn Outreach Generated!'), loadAdditionalData())
          }
          if (prev.research && !isResearch) {
            const err = failMsg('research')
            err ? showToast(err, 'error') : (showToast('Deep Research Complete!'), loadAdditionalData())
          }
          if (prev.interviewPrep && !isPrep) {
            const err = failMsg('interview_prep')
            err ? showToast(err, 'error') : (showToast('Interview Prep Complete!'), loadAdditionalData())
          }
          if (prev.analyze && !isAnalysis) {
            const err = failMsg('analyze')
            err ? showToast(err, 'error') : (showToast('Gap Analysis Complete!'), load())
          }
          if (prev.resume && !isResume) {
            const err = failMsg('resume')
            err ? showToast(err, 'error') : (showToast('Resume Generation Complete!'), load(), pollForAtsScore())
          }
          if (prev.cover && !isCover) {
            const err = failMsg('cover_letter')
            err ? showToast(err, 'error') : (showToast('Cover Letter Generation Complete!'), load())
          }
          return {
            ...prev, evaluate: !!isEval, linkedin: !!isLinkedIn, research: !!isResearch,
            interviewPrep: !!isPrep, analyze: !!isAnalysis, resume: !!isResume, cover: !!isCover
          }
        })
      } catch (e) {}
    }

    interval = setInterval(pollTasks, 4000)
    pollTasks()
    return () => clearInterval(interval)
  }, [jobId, load, loadAdditionalData])

  const saveEdits = async () => {
    try {
      const updated = await api.updateJob(job.id, edits)
      setJob(updated)
      showToast('Changes saved')
    } catch (e) {
      showToast(e.message, 'error')
    }
    setEditing(false); setEdits({})
  }

  const addEvent = async () => {
    if (!newEvent.title) return
    try {
      const ev = await api.addEvent(job.id, { ...newEvent, source:'user' })
      setEvents(e => [ev, ...e])
      showToast('Activity logged')
    } catch {
      setEvents(e => [{ id: Date.now(), ...newEvent, source:'user', created_at: new Date().toISOString() }, ...e])
    }
    setNewEvent({ title:'', description:'', event_type:'note' })
    setAddingEvent(false)
  }

  const changeStatus = async (status) => {
    const old = job.status
    setJob(j => ({ ...j, status }))
    try {
      await api.updateJob(job.id, { status })
      setEvents(e => [{ id: Date.now(), event_type:'status_change', title:`Status → ${status}`, description:`Moved from ${old} to ${status}`, source:'user', created_at: new Date().toISOString() }, ...e])
    } catch (err) {
      showToast(err.message, 'error')
    }
  }

  // ── Agent Actions ──────────────────────────────────────────────────────────

  const runAutoPilot = async () => {
    try {
      setLoading(p => ({ ...p, analyze:true, evaluate:true, research:true, linkedin:true, interviewPrep:true }))
      showToast('Auto-Pilot Engaged! Agents are working in parallel.')
      api.analyzeJob(jobId, selectedLlm).catch(() => {})
      api.evaluateJob(jobId, selectedLlm).catch(() => {})
      api.deepResearch(jobId, selectedLlm).catch(() => {})
      api.generateLinkedIn(jobId, 'hiring_manager', selectedLlm).catch(() => {})
      api.interviewPrep(jobId, selectedLlm).catch(() => {})
      setActiveTab('intelligence')
    } catch(e) { showToast(e.message, 'error') }
  }

  const runAnalysis = async () => {
    setLoading(l => ({ ...l, analyze:true }))
    showToast('Started Gap Analysis in the background...')
    try {
      await api.analyzeJob(job.id, selectedLlm)
    } catch (e) {
      showToast(e.message, 'error')
      setLoading(l => ({ ...l, analyze:false }))
    }
  }

  const runResumeGen = async () => {
    setLoading(l => ({ ...l, resume:true }))
    showToast('Started Resume Generation in the background...')
    try {
      await api.generateResume(job.id, { llm: selectedLlm, feedback })
      setFeedback('')
    } catch (e) {
      showToast(e.message, 'error')
      setLoading(l => ({ ...l, resume:false }))
    }
  }

  const runCoverLetterGen = async () => {
    setLoading(l => ({ ...l, cover:true }))
    showToast('Started Cover Letter Generation in the background...')
    try {
      await api.generateCoverLetter(job.id, { llm: selectedLlm, feedback })
      setFeedback('')
    } catch (e) {
      showToast(e.message, 'error')
      setLoading(l => ({ ...l, cover:false }))
    }
  }

  const runCompare = async () => {
    setLoading(l => ({ ...l, compare:true }))
    try {
      const result = await api.compareLLMs(job.id, compareTask, ['gemini','groq','openrouter'])
      setCompareResults(result)
      showToast(`Comparison complete — ${result.results.length} LLMs compared`)
    } catch (e) {
      showToast(e.message, 'error')
    }
    setLoading(l => ({ ...l, compare:false }))
  }

  const runEvaluation = async () => {
    setLoading(l => ({ ...l, evaluate:true }))
    showToast('Started A-G Evaluation in the background...')
    try {
      await api.evaluateJob(job.id, selectedLlm)
    } catch (e) {
      showToast(e.message, 'error')
      setLoading(l => ({ ...l, evaluate:false }))
    }
  }

  const runLinkedIn = async () => {
    setLoading(l => ({ ...l, linkedin:true }))
    showToast('Started LinkedIn Outreach Generation...')
    try {
      await api.generateLinkedIn(job.id, 'hiring_manager', selectedLlm)
    } catch (e) {
      showToast(e.message, 'error')
      setLoading(l => ({ ...l, linkedin:false }))
    }
  }

  const runResearch = async () => {
    setLoading(l => ({ ...l, research:true }))
    showToast('Started Deep Research in the background...')
    try {
      await api.deepResearch(job.id, selectedLlm)
    } catch (e) {
      showToast(e.message, 'error')
      setLoading(l => ({ ...l, research:false }))
    }
  }

  const runInterviewPrep = async () => {
    setLoading(l => ({ ...l, interviewPrep:true }))
    showToast('Started Interview Prep Generation...')
    try {
      await api.interviewPrep(job.id, selectedLlm)
    } catch (e) {
      showToast(e.message, 'error')
      setLoading(l => ({ ...l, interviewPrep:false }))
    }
  }

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text)
    showToast('Copied to clipboard!', 'info')
  }

  const TABS = JOB_TABS

  if (!job) return (
    <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:300, gap:'1rem' }}>
      <Spinner /> <span style={{ color:'var(--fg-muted)' }}>Loading job details…</span>
    </div>
  )

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:'1.25rem' }}>
      {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
      {editingResume && (
        <ResumeEditor 
          jobId={job.id} 
          initialMarkdown={editingResume.content_md} 
          llm={selectedLlm}
          onSave={() => { setEditingResume(null); load(); }}
          onClose={() => setEditingResume(null)}
        />
      )}

      {/* Header Card */}
      <div className="panel" style={{ padding:'1.5rem' }}>
        <div className="flex justify-between items-center" style={{ flexWrap:'wrap', gap:'1rem' }}>
          <div>
            <div className="flex items-center gap-sm" style={{ marginBottom:'0.4rem' }}>
              {job.starred && <span>⭐</span>}
              <span className={`badge badge-${job.status}`}>{job.status?.replace('_',' ')}</span>
              {job.priority === 'high' && <span className="badge" style={{ background:'var(--danger-subtle)', color:'var(--danger)' }}>High Priority</span>}
              {job.remote && <span className="badge" style={{ background:'var(--info-subtle)', color:'var(--info)' }}>Remote</span>}
            </div>
            <h2 style={{ fontSize:'1.5rem', marginBottom:'0.25rem' }}>{job.title}</h2>
            <p style={{ color:'var(--fg-muted)' }}>{job.company} • {job.location || 'Location TBD'}</p>
            {(job.salary_min || job.salary_max) && (
              <p style={{ color:'var(--success)', fontWeight:600, marginTop:'0.25rem', fontSize:'0.9rem' }}>
                ${job.salary_min ? `${Math.round(job.salary_min/1000)}K` : '?'} – ${job.salary_max ? `${Math.round(job.salary_max/1000)}K` : '?'}
              </p>
            )}
          </div>
          <div style={{ display:'flex', flexDirection:'column', gap:'0.75rem', alignItems:'flex-end' }}>
            <select value={job.status} onChange={e => changeStatus(e.target.value)} style={{ width:190, fontWeight:600 }}>
              {STATUSES.map(s => <option key={s} value={s}>{BADGE_EMOJI[s]} {s.replace('_',' ')}</option>)}
            </select>
            <div className="flex gap-sm">
              <span title="Model — change it from the selector in the top bar" style={{ fontSize:'0.75rem', color:'var(--fg-muted)', border:'1px solid var(--surface-border)', padding:'4px 10px', borderRadius:'999px', alignSelf:'center', whiteSpace:'nowrap' }}>
                {selectedLlm}
              </span>
              {job.url && <a href={job.url} target="_blank" rel="noreferrer" className="btn btn-outline btn-sm">View Job</a>}
              <button className="btn btn-primary btn-sm" onClick={() => changeStatus('approved')}>Approve & Apply</button>
              <button className="btn btn-primary btn-sm" onClick={runAutoPilot}>Auto-Pilot</button>
            </div>
          </div>
        </div>
        {job.recruiter_name && (
          <div style={{ marginTop:'1rem', paddingTop:'1rem', borderTop:'1px solid var(--surface-border)', fontSize:'0.85rem', color:'var(--fg-muted)' }}>
            Recruiter: <strong style={{ color:'var(--fg)' }}>{job.recruiter_name}</strong>
            {job.recruiter_email && <> • <a href={`mailto:${job.recruiter_email}`} style={{ color:'var(--primary)' }}>{job.recruiter_email}</a></>}
          </div>
        )}

        {/* Archetype & Legitimacy badges */}
        {(job.archetype || job.posting_legitimacy) && (
          <div style={{ marginTop:'0.75rem', display:'flex', gap:'0.5rem', flexWrap:'wrap' }}>
            {job.archetype && <span className="badge" style={{ background:'var(--purple-subtle)', color:'var(--purple)', fontSize:'0.75rem' }}>{job.archetype}</span>}
            {job.posting_legitimacy && <span className="badge" style={{
              background: job.posting_legitimacy === 'High Confidence' ? 'var(--success-subtle)' : job.posting_legitimacy === 'Suspicious' ? 'var(--danger-subtle)' : 'var(--warning-subtle)',
              color: job.posting_legitimacy === 'High Confidence' ? 'var(--success)' : job.posting_legitimacy === 'Suspicious' ? 'var(--danger)' : 'var(--warning)',
              fontSize:'0.75rem'
            }}>{job.posting_legitimacy}</span>}
          </div>
        )}

      </div>

      {/* Tabs */}
      <div className="flex gap-sm" style={{ borderBottom:'1px solid var(--surface-border)' }}>
        {TABS.map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)}
            style={{ padding:'0.5rem 1rem', background:'none', border:'none', cursor:'pointer',
              color: activeTab===tab ? 'var(--fg)' : 'var(--fg-subtle)',
              borderBottom: activeTab===tab ? '2px solid var(--primary)' : '2px solid transparent',
              fontWeight: activeTab===tab ? 600 : 400, fontSize:'0.875rem', textTransform:'capitalize', transition:'all 0.15s' }}>
            {tab}
          </button>
        ))}
      </div>

      {/* ── Overview Tab ─────────────────────────────────────────────────── */}
      {activeTab === 'dashboard' && (
        <div className="panel" style={{ padding:'1.5rem', display:'flex', flexDirection:'column', gap:'1rem' }}>
          <div className="flex justify-between items-center">
            <h3>Overview & Notes</h3>
            <button className="btn btn-outline btn-sm" onClick={() => setEditing(!editing)}>
              {editing ? 'Cancel' : 'Edit'}
            </button>
          </div>
          {editing ? (
            <div style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>
              <div className="form-grid">
                <div className="form-group"><label className="form-label">Recruiter Name</label><input value={edits.recruiter_name ?? job.recruiter_name ?? ''} onChange={e => setEdits(d=>({...d,recruiter_name:e.target.value}))} /></div>
                <div className="form-group"><label className="form-label">Recruiter Email</label><input value={edits.recruiter_email ?? job.recruiter_email ?? ''} onChange={e => setEdits(d=>({...d,recruiter_email:e.target.value}))} /></div>
              </div>
              <div className="form-grid">
                <div className="form-group"><label className="form-label">Applied At</label><input type="datetime-local" value={edits.applied_at ?? ''} onChange={e => setEdits(d=>({...d,applied_at:e.target.value}))} /></div>
                <div className="form-group"><label className="form-label">Follow-up Due</label><input type="date" value={edits.follow_up_due ?? ''} onChange={e => setEdits(d=>({...d,follow_up_due:e.target.value}))} /></div>
              </div>
              <div className="form-group"><label className="form-label">Notes</label><textarea rows={4} value={edits.notes ?? job.notes ?? ''} onChange={e => setEdits(d=>({...d,notes:e.target.value}))} style={{ resize:'vertical' }} /></div>
              <div className="flex gap-sm" style={{ justifyContent:'flex-end' }}>
                <button className="btn btn-primary btn-sm" onClick={saveEdits}>Save Changes</button>
              </div>
            </div>
          ) : (
            <div>
              {job.job_description && (
                <div style={{ marginBottom: '2rem' }}>
                  <div style={{
                    fontSize: '0.75rem',
                    fontWeight: 700,
                    color: 'var(--primary)',
                    marginBottom: '1rem',
                    textTransform: 'uppercase',
                    letterSpacing: '0.2em',
                    opacity: 0.8
                  }}>
                    Job Intelligence
                  </div>
                  <div style={{
                    color: 'var(--fg)',
                    fontSize: '0.95rem',
                    lineHeight: 1.8,
                    whiteSpace: 'pre-wrap',
                    maxHeight: '600px',
                    overflowY: 'auto',
                    backgroundColor: 'var(--surface-2)',
                    padding: '2rem',
                    borderRadius: 'var(--radius-lg)',
                    border: '1px solid var(--surface-border)',
                    boxShadow: 'var(--shadow-md)',
                    fontFamily: 'unset',
                    letterSpacing: '0.01em',
                  }}>
                    <div dangerouslySetInnerHTML={{ __html: job.job_description
                      .replace(/</g, '&lt;').replace(/>/g, '&gt;')
                      .replace(/^### (.*$)/gim, '<h3 style="margin: 2rem 0 1rem; color: var(--fg); font-size: 1.1rem; font-weight: 700;">$1</h3>')
                      .replace(/^## (.*$)/gim, '<h2 style="margin: 2.5rem 0 1.25rem; color: var(--fg); font-size: 1.4rem; font-weight: 800; border-bottom: 1px solid var(--surface-border); padding-bottom: 0.5rem;">$1</h2>')
                      .replace(/^# (.*$)/gim, '<h1 style="margin: 3rem 0 1.5rem; color: var(--fg); font-size: 1.8rem; font-weight: 900;">$1</h1>')
                      .replace(/^\* (.*$)/gim, '<div style="display: flex; gap: 0.75rem; margin-bottom: 0.5rem;"><span style="color: var(--primary)">•</span><span>$1</span></div>')
                      .replace(/\*\*(.*?)\*\*/g, '<strong style="color: var(--fg); font-weight: 600;">$1</strong>')
                      .replace(/\*(.*?)\*/g, '<em style="color: var(--fg-muted)">$1</em>')
                      .split('\n\n').map(p => p.trim() ? `<p style="margin-bottom: 1.25rem;">${p}</p>` : '').join('')
                    }} />
                  </div>
                </div>
              )}
              <div style={{ fontSize:'0.8rem', fontWeight:600, color:'var(--fg-muted)', marginBottom:'0.5rem' }}>NOTES</div>
              <div style={{ color:'var(--fg-muted)', lineHeight:1.7, whiteSpace:'pre-wrap' }}>
                {job.notes || <span style={{ color:'var(--fg-subtle)' }}>No notes yet. Click Edit to add notes.</span>}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Evaluation Tab (A-G Blocks) ──────────────────────────────────── */}
      {activeTab === 'intelligence' && (
        <div style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>
          {!evalReport ? (
            <div className="panel" style={{ padding:'2rem', textAlign:'center' }}>
              <h3>Full A-G Structured Evaluation</h3>
              <p style={{ color:'var(--fg-muted)', margin:'0.5rem 0 1.5rem', maxWidth:500, marginInline:'auto' }}>
                Career-ops inspired 7-block evaluation: Role Summary, CV Match, Level Strategy, Comp & Demand, Personalization, Interview Prep, and Posting Legitimacy.
              </p>
              <button className="btn btn-primary" onClick={runEvaluation} disabled={loading.evaluate}>
                {loading.evaluate ? <><Spinner small /> Running Evaluation…</> : 'Run A-G Evaluation'}
              </button>
            </div>
          ) : (
            <>
              {/* Global Score Card */}
              <div className="panel" style={{ padding:'1.5rem', background:'var(--surface-2)' }}>
                <div className="flex items-center gap-md" style={{ flexWrap:'wrap' }}>
                  <div style={{ textAlign:'center' }}>
                    <div style={{ fontSize:'2.5rem', fontWeight:800, color: evalReport.global_score >= 85 ? 'var(--success)' : evalReport.global_score >= 70 ? 'var(--warning)' : 'var(--danger)' }}>
                      {evalReport.global_score}/100
                    </div>
                    <div style={{ fontSize:'0.75rem', color:'var(--fg-muted)' }}>Global Score</div>
                  </div>
                  <div style={{ flex:1 }}>
                    <h3 style={{ marginBottom:4 }}>{evalReport.summary}</h3>
                    <div className="flex gap-sm" style={{ marginTop:8 }}>
                      {evalReport.block_a_role_summary?.archetype && <span className="badge" style={{ background:'var(--purple-subtle)', color:'var(--purple)' }}>{evalReport.block_a_role_summary.archetype}</span>}
                      {evalReport.block_a_role_summary?.seniority && <span className="badge" style={{ background:'var(--warning-subtle)', color:'var(--warning)' }}>{evalReport.block_a_role_summary.seniority}</span>}
                      {evalReport.block_g_legitimacy?.tier && <span className="badge" style={{
                        background: evalReport.block_g_legitimacy.tier === 'High Confidence' ? 'var(--success-subtle)' : 'var(--warning-subtle)',
                        color: evalReport.block_g_legitimacy.tier === 'High Confidence' ? 'var(--success)' : 'var(--warning)'
                      }}>{evalReport.block_g_legitimacy.tier}</span>}
                    </div>
                  </div>
                </div>
              </div>

              {/* Block A: Role Summary */}
              {evalReport.block_a_role_summary && (
                <div className="panel" style={{ padding:'1.25rem' }}>
                  <button onClick={() => toggleBlock('a')} style={{ all:'unset', cursor:'pointer', display:'flex', alignItems:'center', gap:8, width:'100%', fontWeight:600 }}>
                    <span>{expandedBlocks.a ? '▼' : '▶'}</span>
                    <span>Block A — Role Summary</span>
                  </button>
                  {expandedBlocks.a && (
                    <div style={{ marginTop:'1rem', display:'grid', gridTemplateColumns:'1fr 1fr', gap:'0.75rem' }}>
                      {Object.entries(evalReport.block_a_role_summary).map(([k, v]) => (
                        <div key={k} style={{ padding:'0.5rem 0.75rem', background:'var(--surface-2)', borderRadius:'var(--radius-sm)' }}>
                          <div style={{ fontSize:'0.7rem', color:'var(--fg-subtle)', textTransform:'uppercase', letterSpacing:'0.05em' }}>{k.replace(/_/g, ' ')}</div>
                          <div style={{ fontSize:'0.875rem', marginTop:2 }}>{String(v)}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Block B: CV Match */}
              {evalReport.block_b_cv_match && (
                <div className="panel" style={{ padding:'1.25rem' }}>
                  <button onClick={() => toggleBlock('b')} style={{ all:'unset', cursor:'pointer', display:'flex', alignItems:'center', gap:8, width:'100%', fontWeight:600 }}>
                    <span>{expandedBlocks.b ? '▼' : '▶'}</span>
                    <span>Block B — CV Match ({evalReport.block_b_cv_match.requirements?.length || 0} requirements)</span>
                  </button>
                  {expandedBlocks.b && (
                    <div style={{ marginTop:'1rem' }}>
                      {evalReport.block_b_cv_match.requirements?.map((r, i) => (
                        <div key={i} style={{ padding:'0.75rem', marginBottom:6, borderRadius:'var(--radius-sm)', background: r.match_strength === 'strong' ? 'var(--success-subtle)' : r.match_strength === 'partial' ? 'var(--warning-subtle)' : 'var(--danger-subtle)', border: `1px solid ${r.match_strength === 'strong' ? 'var(--success)' : r.match_strength === 'partial' ? 'var(--warning)' : 'var(--danger)'}` }}>
                          <div style={{ fontWeight:600, fontSize:'0.85rem', marginBottom:4 }}>{r.jd_requirement}</div>
                          <div style={{ fontSize:'0.8rem', color:'var(--fg-muted)' }}>{r.resume_evidence}</div>
                        </div>
                      ))}
                      {evalReport.block_b_cv_match.gaps?.length > 0 && (
                        <div style={{ marginTop:'1rem' }}>
                          <h4 style={{ color:'var(--danger)', marginBottom:8 }}>Gap Mitigations</h4>
                          {evalReport.block_b_cv_match.gaps.map((g, i) => (
                            <div key={i} style={{ padding:'0.75rem', marginBottom:6, background:'var(--surface-2)', borderRadius:'var(--radius-sm)' }}>
                              <div style={{ fontWeight:600, fontSize:'0.85rem', color: g.severity === 'hard_blocker' ? 'var(--danger)' : 'var(--warning)' }}>
                                {g.gap}
                              </div>
                              {g.mitigation && <div style={{ fontSize:'0.8rem', color:'var(--fg-muted)', marginTop:4 }}>{g.mitigation}</div>}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Block C: Level & Strategy */}
              {evalReport.block_c_level_strategy && (
                <div className="panel" style={{ padding:'1.25rem' }}>
                  <button onClick={() => toggleBlock('c')} style={{ all:'unset', cursor:'pointer', display:'flex', alignItems:'center', gap:8, width:'100%', fontWeight:600 }}>
                    <span>{expandedBlocks.c ? '▼' : '▶'}</span>
                    <span>Block C — Level & Strategy</span>
                  </button>
                  {expandedBlocks.c && (
                    <div style={{ marginTop:'1rem', display:'flex', flexDirection:'column', gap:'0.75rem' }}>
                      {Object.entries(evalReport.block_c_level_strategy).map(([k, v]) => (
                        <div key={k} style={{ padding:'0.75rem', background:'var(--surface-2)', borderRadius:'var(--radius-sm)' }}>
                          <div style={{ fontSize:'0.7rem', color:'var(--fg-subtle)', textTransform:'uppercase', letterSpacing:'0.05em' }}>{k.replace(/_/g, ' ')}</div>
                          <div style={{ fontSize:'0.875rem', marginTop:4, lineHeight:1.6 }}>{String(v)}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Block D: Comp & Demand */}
              {evalReport.block_d_comp_demand && (
                <div className="panel" style={{ padding:'1.25rem' }}>
                  <button onClick={() => toggleBlock('d')} style={{ all:'unset', cursor:'pointer', display:'flex', alignItems:'center', gap:8, width:'100%', fontWeight:600 }}>
                    <span>{expandedBlocks.d ? '▼' : '▶'}</span>
                    <span>Block D — Comp & Demand</span>
                  </button>
                  {expandedBlocks.d && (
                    <div style={{ marginTop:'1rem', display:'grid', gridTemplateColumns:'1fr 1fr', gap:'0.75rem' }}>
                      {Object.entries(evalReport.block_d_comp_demand).map(([k, v]) => (
                        <div key={k} style={{ padding:'0.75rem', background:'var(--surface-2)', borderRadius:'var(--radius-sm)' }}>
                          <div style={{ fontSize:'0.7rem', color:'var(--fg-subtle)', textTransform:'uppercase' }}>{k.replace(/_/g, ' ')}</div>
                          <div style={{ fontSize:'0.875rem', marginTop:4, fontWeight: k === 'estimated_salary_range' ? 700 : 400, color: k === 'estimated_salary_range' ? 'var(--success)' : 'inherit' }}>{String(v)}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Block E: Personalization Plan */}
              {evalReport.block_e_personalization && (
                <div className="panel" style={{ padding:'1.25rem' }}>
                  <button onClick={() => toggleBlock('e')} style={{ all:'unset', cursor:'pointer', display:'flex', alignItems:'center', gap:8, width:'100%', fontWeight:600 }}>
                    <span>{expandedBlocks.e ? '▼' : '▶'}</span>
                    <span>Block E — Personalization Plan</span>
                  </button>
                  {expandedBlocks.e && (
                    <div style={{ marginTop:'1rem', display:'grid', gridTemplateColumns:'1fr 1fr', gap:'1rem' }}>
                      <div>
                        <h4 style={{ marginBottom:8, fontSize:'0.85rem' }}>CV Changes</h4>
                        {(evalReport.block_e_personalization.cv_changes || []).map((c, i) => (
                          <div key={i} style={{ padding:'0.5rem 0.75rem', marginBottom:4, background:'var(--success-subtle)', borderRadius:'var(--radius-sm)', fontSize:'0.83rem', border:'1px solid var(--success)' }}>{i+1}. {c}</div>
                        ))}
                      </div>
                      <div>
                        <h4 style={{ marginBottom:8, fontSize:'0.85rem' }}>LinkedIn Changes</h4>
                        {(evalReport.block_e_personalization.linkedin_changes || []).map((c, i) => (
                          <div key={i} style={{ padding:'0.5rem 0.75rem', marginBottom:4, background:'var(--primary-subtle)', borderRadius:'var(--radius-sm)', fontSize:'0.83rem', border:'1px solid var(--primary)' }}>{i+1}. {c}</div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Block G: Posting Legitimacy */}
              {evalReport.block_g_legitimacy && (
                <div className="panel" style={{ padding:'1.25rem' }}>
                  <button onClick={() => toggleBlock('g')} style={{ all:'unset', cursor:'pointer', display:'flex', alignItems:'center', gap:8, width:'100%', fontWeight:600 }}>
                    <span>{expandedBlocks.g ? '▼' : '▶'}</span>
                    <span>Block G — Posting Legitimacy ({evalReport.block_g_legitimacy.tier})</span>
                  </button>
                  {expandedBlocks.g && (
                    <div style={{ marginTop:'1rem' }}>
                      {evalReport.block_g_legitimacy.signals?.map((s, i) => (
                        <div key={i} style={{ padding:'0.75rem', marginBottom:6, background:'var(--surface-2)', borderRadius:'var(--radius-sm)', display:'flex', alignItems:'center', gap:8 }}>
                          <div>
                            <div style={{ fontWeight:600, fontSize:'0.85rem', color: s.weight === 'Positive' ? 'var(--success)' : s.weight === 'Concerning' ? 'var(--danger)' : 'var(--fg)' }}>{s.signal}</div>
                            <div style={{ fontSize:'0.8rem', color:'var(--fg-muted)' }}>{s.finding}</div>
                          </div>
                        </div>
                      ))}
                      {evalReport.block_g_legitimacy.context_notes && (
                        <div className="alert alert-warning" style={{ marginTop:8, fontSize:'0.83rem' }}>{evalReport.block_g_legitimacy.context_notes}</div>
                      )}
                    </div>
                  )}
                </div>
              )}

              <button className="btn btn-outline btn-sm" style={{ alignSelf:'flex-start' }} onClick={runEvaluation} disabled={loading.evaluate}>
                {loading.evaluate ? <><Spinner small /> Re-evaluating…</> : 'Re-run Evaluation'}
              </button>
            </>
          )}
        </div>
      )}

      {/* ── Interview Prep Tab ──────────────────────────────────────────────── */}
      {activeTab === 'intelligence' && (
        <div style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>
          {!interviewData ? (
            <div className="panel" style={{ padding:'2rem', textAlign:'center' }}>
              <h3>Interview Prep — STAR+Reflection Stories</h3>
              <p style={{ color:'var(--fg-muted)', margin:'0.5rem 0 1.5rem', maxWidth:500, marginInline:'auto' }}>
                Generate powerful STAR stories with reflection. Each story maps to a JD requirement and builds your persistent story bank.
              </p>
              <button className="btn btn-primary" onClick={runInterviewPrep} disabled={loading.interviewPrep}>
                {loading.interviewPrep ? <><Spinner small /> Generating…</> : 'Generate Interview Stories'}
              </button>
            </div>
          ) : (
            <>
              {/* STAR Stories */}
              {interviewData.stories?.map((s, i) => (
                <div key={i} className="panel" style={{ padding:'1.25rem' }}>
                  <div className="flex justify-between items-center" style={{ marginBottom:'0.75rem' }}>
                    <div>
                      <h4 style={{ fontSize:'0.95rem' }}>{s.title}</h4>
                      <span style={{ fontSize:'0.75rem', color:'var(--fg-subtle)' }}>Maps to: {s.jd_requirement}</span>
                    </div>
                    <div className="flex gap-sm">
                      {(s.tags || []).map(t => <span key={t} className="badge" style={{ background:'var(--primary-subtle)', color:'var(--primary)', fontSize:'0.7rem' }}>{t}</span>)}
                    </div>
                  </div>
                  <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'0.75rem' }}>
                    {['situation','task','action','result','reflection'].map(part => (
                      <div key={part} style={{ padding:'0.75rem', background: part === 'reflection' ? 'var(--purple-subtle)' : 'var(--surface-2)', borderRadius:'var(--radius-sm)', gridColumn: part === 'action' || part === 'reflection' ? '1 / -1' : 'auto', border: part === 'reflection' ? '1px solid var(--purple)' : 'none' }}>
                        <div style={{ fontSize:'0.7rem', color:'var(--fg-subtle)', textTransform:'uppercase', letterSpacing:'0.05em', marginBottom:4 }}>{part === 'reflection' ? 'Reflection (seniority signal)' : part.charAt(0).toUpperCase() + part.slice(1)}</div>
                        <div style={{ fontSize:'0.83rem', lineHeight:1.6, color:'var(--fg-muted)' }}>{s[part]}</div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}

              {/* Red Flag Questions */}
              {interviewData.red_flag_questions?.length > 0 && (
                <div className="panel" style={{ padding:'1.25rem' }}>
                  <h4 style={{ color:'var(--danger)', marginBottom:'0.75rem' }}>Red Flag Questions</h4>
                  {interviewData.red_flag_questions.map((q, i) => (
                    <div key={i} style={{ padding:'0.75rem', marginBottom:6, background:'var(--danger-subtle)', borderRadius:'var(--radius-sm)', border:'1px solid var(--danger)' }}>
                      <div style={{ fontWeight:600, fontSize:'0.875rem', marginBottom:4 }}>{q.question}</div>
                      {q.why_they_ask && <div style={{ fontSize:'0.8rem', color:'var(--fg-subtle)', marginBottom:4 }}>Why: {q.why_they_ask}</div>}
                      <div style={{ fontSize:'0.8rem', color:'var(--success)' }}>{q.suggested_response}</div>
                      {q.pitfall && <div style={{ fontSize:'0.8rem', color:'var(--danger)', marginTop:4 }}>Avoid: {q.pitfall}</div>}
                    </div>
                  ))}
                </div>
              )}

              {/* Case Study */}
              {interviewData.case_study && (
                <div className="panel" style={{ padding:'1.25rem', background:'var(--primary-subtle)', border:'1px solid var(--primary)' }}>
                  <h4 style={{ marginBottom:'0.75rem' }}>Recommended Case Study</h4>
                  <div style={{ fontWeight:600, fontSize:'0.9rem', marginBottom:4 }}>{interviewData.case_study.project}</div>
                  <div style={{ fontSize:'0.83rem', color:'var(--fg-muted)', marginBottom:8 }}>{interviewData.case_study.framing}</div>
                  {interviewData.case_study.key_decisions && (
                    <div style={{ marginTop:8 }}>
                      <span style={{ fontSize:'0.75rem', color:'var(--fg-subtle)' }}>Key Decisions:</span>
                      {interviewData.case_study.key_decisions.map((d, i) => (
                        <span key={i} className="badge" style={{ margin:'4px 4px 0 0', fontSize:'0.75rem' }}>{d}</span>
                      ))}
                    </div>
                  )}
                </div>
              )}

              <button className="btn btn-outline btn-sm" style={{ alignSelf:'flex-start' }} onClick={runInterviewPrep} disabled={loading.interviewPrep}>
                {loading.interviewPrep ? <><Spinner small /> Generating…</> : 'Generate More Stories'}
              </button>
            </>
          )}
        </div>
      )}

      {/* ── LinkedIn Outreach Tab ───────────────────────────────────────────── */}
      {activeTab === 'intelligence' && (
        <div style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>
          {!linkedInData ? (
            <div className="panel" style={{ padding:'2rem', textAlign:'center' }}>
              <h3>LinkedIn Outreach Messages</h3>
              <p style={{ color:'var(--fg-muted)', margin:'0.5rem 0 1.5rem', maxWidth:500, marginInline:'auto' }}>
                Generate targeted connection request messages for 4 contact types: Recruiter, Hiring Manager, Peer, and Interviewer. All within LinkedIn's 300-char limit.
              </p>
              <button className="btn btn-primary" onClick={runLinkedIn} disabled={loading.linkedin}>
                {loading.linkedin ? <><Spinner small /> Generating…</> : 'Generate Messages'}
              </button>
            </div>
          ) : (
            <>
              {linkedInData.recommended_target && (
                <div className="alert alert-success">
                  <span style={{ fontSize:'0.85rem' }}><strong>Recommended:</strong> {linkedInData.recommended_target}</span>
                </div>
              )}
              {linkedInData.messages && Object.entries(linkedInData.messages).map(([type, data]) => (
                <div key={type} className="panel" style={{ padding:'1.25rem' }}>
                  <div className="flex justify-between items-center" style={{ marginBottom:'0.75rem' }}>
                    <h4 style={{ textTransform:'capitalize', fontSize:'0.95rem' }}>
                      {type.replace(/_/g, ' ')}
                    </h4>
                    <button className="btn btn-outline btn-sm" onClick={() => copyToClipboard(data.message)} style={{ gap:4 }}>
                      Copy
                    </button>
                  </div>
                  <div style={{ padding:'1rem', background:'var(--surface-2)', borderRadius:'var(--radius)', fontSize:'0.9rem', lineHeight:1.6, fontStyle:'italic', position:'relative' }}>
                    "{data.message}"
                    <div style={{ position:'absolute', bottom:4, right:8, fontSize:'0.7rem', color: (data.message || '').length > 300 ? 'var(--danger)' : 'var(--fg-subtle)' }}>
                      {(data.message || '').length}/300
                    </div>
                  </div>
                  {data.search_query && (
                    <div style={{ marginTop:8, fontSize:'0.78rem', color:'var(--fg-subtle)' }}>
                      LinkedIn search: <code style={{ background:'var(--surface-2)', padding:'2px 6px', borderRadius:4 }}>{data.search_query}</code>
                    </div>
                  )}
                </div>
              ))}
              <button className="btn btn-outline btn-sm" style={{ alignSelf:'flex-start' }} onClick={runLinkedIn} disabled={loading.linkedin}>
                {loading.linkedin ? <><Spinner small /> Regenerating…</> : 'Regenerate Messages'}
              </button>
            </>
          )}
        </div>
      )}

      {/* ── Deep Research Tab ───────────────────────────────────────────────── */}
      {activeTab === 'intelligence' && (
        <div style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>
          {!researchData ? (
            <div className="panel" style={{ padding:'2rem', textAlign:'center' }}>
              <h3>Deep Company Research</h3>
              <p style={{ color:'var(--fg-muted)', margin:'0.5rem 0 1.5rem', maxWidth:500, marginInline:'auto' }}>
                6-axis intelligence: AI Strategy, Recent Moves, Engineering Culture, Challenges, Competitors, and your Candidate Angle.
              </p>
              <button className="btn btn-primary" onClick={runResearch} disabled={loading.research}>
                {loading.research ? <><Spinner small /> Researching…</> : 'Run Deep Research'}
              </button>
            </div>
          ) : (
            <>
              {[{key:'ai_strategy', title:'AI Strategy'}, {key:'recent_moves', title:'Recent Moves'}, {key:'engineering_culture', title:'Engineering Culture'}, {key:'likely_challenges', title:'Likely Challenges'}, {key:'competitive_landscape', title:'Competitive Landscape'}, {key:'candidate_angle', title:'Your Candidate Angle'}].map(section => (
                researchData[section.key] && (
                  <div key={section.key} className="panel" style={{ padding:'1.25rem', ...(section.key === 'candidate_angle' ? { background:'var(--primary-subtle)', border:'1px solid var(--primary)' } : {}) }}>
                    <h4 style={{ marginBottom:'0.75rem' }}>{section.title}</h4>
                    <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'0.75rem' }}>
                      {Object.entries(researchData[section.key]).map(([k, v]) => (
                        <div key={k} style={{ padding:'0.75rem', background:'var(--surface-2)', borderRadius:'var(--radius-sm)' }}>
                          <div style={{ fontSize:'0.7rem', color:'var(--fg-subtle)', textTransform:'uppercase', letterSpacing:'0.05em' }}>{k.replace(/_/g, ' ')}</div>
                          <div style={{ fontSize:'0.83rem', marginTop:4, lineHeight:1.6 }}>
                            {Array.isArray(v) ? v.map((item, i) => <div key={i} style={{ marginBottom:2 }}>• {item}</div>) : String(v)}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )
              ))}
              <button className="btn btn-outline btn-sm" style={{ alignSelf:'flex-start' }} onClick={runResearch} disabled={loading.research}>
                {loading.research ? <><Spinner small /> Re-researching…</> : 'Refresh Research'}
              </button>
            </>
          )}
        </div>
      )}

      {/* ── Gap Analysis Tab ─────────────────────────────────────────────── */}
      {activeTab === 'intelligence' && (
        <div style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>
          <div className="panel" style={{ padding:'1.5rem' }}>
            <div className="flex items-center gap-md" style={{ flexWrap:'wrap' }}>
              {job.match_score != null ? (
                <div style={{ textAlign:'center' }}>
                  <div style={{ position:'relative', width:100, height:100 }}>
                    <svg width="100" height="100" viewBox="0 0 100 100" style={{ transform:'rotate(-90deg)' }}>
                      <circle cx="50" cy="50" r="40" stroke="rgba(255,255,255,0.07)" strokeWidth="10" fill="none" />
                      <circle cx="50" cy="50" r="40"
                        stroke={job.match_score>=80?'#10b981':job.match_score>=60?'#f59e0b':'#ef4444'}
                        strokeWidth="10" fill="none"
                        strokeDasharray={`${(job.match_score/100)*(2*Math.PI*40)} ${2*Math.PI*40}`}
                        strokeLinecap="round" />
                    </svg>
                    <div style={{ position:'absolute', inset:0, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center' }}>
                      <span style={{ fontSize:'1.5rem', fontWeight:800, color: job.match_score>=80?'var(--success)':job.match_score>=60?'var(--warning)':'var(--danger)' }}>{job.match_score}%</span>
                    </div>
                  </div>
                  <p style={{ fontSize:'0.75rem', color:'var(--fg-muted)', marginTop:4 }}>Match Score</p>
                </div>
              ) : (
                <div style={{ textAlign:'center', padding:'1.5rem' }}>
                  <p style={{ color:'var(--fg-subtle)', fontSize:'0.875rem' }}>No analysis yet</p>
                </div>
              )}
              <div style={{ flex:1 }}>
                <h3 style={{ marginBottom:'0.5rem' }}>AI Fit Assessment</h3>
                <p style={{ color:'var(--fg-muted)', fontSize:'0.875rem' }}>Compares your Master Resume against this Job Description</p>
                <button className="btn btn-primary btn-sm" style={{ marginTop:'0.75rem' }} onClick={runAnalysis} disabled={loading.analyze}>
                  {loading.analyze ? <><Spinner small /> Analyzing…</> : 'Run Analysis'}
                </button>
              </div>
            </div>
          </div>

          {(job.strengths?.length > 0 || job.gaps?.length > 0) && (
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'1rem' }}>
              <div className="panel" style={{ padding:'1.25rem' }}>
                <h4 style={{ color:'var(--success)', marginBottom:'0.75rem' }}>Strengths ({job.strengths?.length || 0})</h4>
                {(job.strengths || []).map((s, i) => (
                  <div key={i} style={{ padding:'0.5rem 0', borderBottom:'1px solid var(--surface-border)', fontSize:'0.845rem', color:'var(--fg-muted)' }}>• {s}</div>
                ))}
              </div>
              <div className="panel" style={{ padding:'1.25rem' }}>
                <h4 style={{ color:'var(--danger)', marginBottom:'0.75rem' }}>Gaps ({job.gaps?.length || 0})</h4>
                {(job.gaps || []).map((g, i) => (
                  <div key={i} style={{ padding:'0.5rem 0', borderBottom:'1px solid var(--surface-border)', fontSize:'0.845rem', color:'var(--fg-muted)' }}>• {g}</div>
                ))}
              </div>
            </div>
          )}

          {job.action_items?.length > 0 && (
            <div className="panel" style={{ padding:'1.25rem' }}>
              <h4 style={{ color:'var(--warning)', marginBottom:'0.75rem' }}>Action Items ({job.action_items.length})</h4>
              {(job.action_items || []).map((a, i) => (
                <div key={i} style={{ padding:'0.6rem', borderRadius:'var(--radius-sm)', background:'var(--warning-subtle)', marginBottom:'0.5rem', fontSize:'0.845rem', color:'var(--fg)' }}>
                  {i+1}. {a}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Documents Tab ────────────────────────────────────────────────── */}
      {activeTab === 'documents' && (
        <div style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>
          {/* Feedback input */}
          <div className="panel" style={{ padding:'1.25rem', display:'flex', flexDirection:'column', gap:'0.75rem' }}>
            <h4>Generate Documents</h4>
            <div className="form-group">
              <label className="form-label">Refinement Feedback (optional)</label>
              <textarea rows={2} value={feedback} onChange={e => setFeedback(e.target.value)}
                placeholder="e.g. 'Emphasize MLOps more', 'Add more GCP keywords', 'Make the tone more confident'"
                style={{ resize:'vertical' }} />
            </div>
            <div className="flex gap-sm">
              <button className="btn btn-primary" onClick={runResumeGen} disabled={loading.resume}>
                {loading.resume ? <><Spinner small /> Generating…</> : 'Generate Tailored Resume'}
              </button>
              <button className="btn btn-outline" onClick={runCoverLetterGen} disabled={loading.cover}>
                {loading.cover ? <><Spinner small /> Generating…</> : 'Generate Cover Letter'}
              </button>
            </div>
            <div className="alert alert-info" style={{ fontSize:'0.75rem' }}>
              Generate a resume first, then generate the cover letter. It uses your tailored resume as context.
            </div>
          </div>

          {/* Resumes */}
          {resumes.length > 0 && (
            <div className="panel" style={{ padding:'1.25rem' }}>
              <h4 style={{ marginBottom:'0.75rem' }}>Resume Versions ({resumes.length})</h4>
              {resumes.map(r => (
                <div key={r.id} style={{ padding:'0.75rem', background:'var(--surface-2)', borderRadius:'var(--radius-sm)', marginBottom:'0.5rem', display:'flex', justifyContent:'space-between', alignItems:'center' }}>
                  <div>
                    <InlineNameEdit 
                      initialValue={r.name} 
                      defaultLabel={`Resume v${r.version}`} 
                      onSave={async (newName) => {
                        try {
                          await api.updateResume(job.id, r.id, { name: newName });
                          load();
                        } catch(e) { showToast(e.message, 'error'); }
                      }}
                    />
                    <div style={{ fontSize:'0.75rem', color:'var(--fg-muted)', marginTop:4 }}>{r.llm_used} • {new Date(r.created_at).toLocaleString()}</div>
                    {r.ats_score && r.ats_score.total != null && (
                      <details style={{ marginTop:6 }}>
                        <summary style={{ cursor:'pointer', fontSize:'0.8rem', fontWeight:700,
                          color: r.ats_score.total>=70?'var(--success)':r.ats_score.total>=50?'var(--warning)':'var(--danger)' }}>
                          ATS Score: {r.ats_score.total}/100
                        </summary>
                        <div style={{ fontSize:'0.75rem', color:'var(--fg-muted)', marginTop:6, lineHeight:1.5 }}>
                          {r.ats_score.scores && Object.entries(r.ats_score.scores).map(([k,v]) => (
                            <div key={k}>• {k.replace(/_/g,' ')}: <strong>{v.score}/{v.max}</strong></div>
                          ))}
                          {r.ats_score.areas_for_improvement?.length > 0 && (
                            <div style={{ marginTop:6 }}><strong>Fix:</strong> {r.ats_score.areas_for_improvement.join(' · ')}</div>
                          )}
                        </div>
                      </details>
                    )}
                  </div>
                  <div className="flex gap-sm" style={{ alignItems:'center' }}>
                    {r.llm_used && (
                      <span title="Model used to generate this resume" style={{ fontSize:'0.7rem', color:'var(--fg-muted)', background:'var(--surface-hover)', border:'1px solid var(--surface-border)', padding:'2px 8px', borderRadius:'999px', whiteSpace:'nowrap' }}>
                        {r.llm_used}
                      </span>
                    )}
                    {r.content_md && <button className="btn btn-primary btn-sm" onClick={() => setEditingResume(r)}>Edit</button>}
                    {r.pdf_path && <button className="btn btn-outline btn-sm" onClick={() => api.downloadFile(job.id, `resume_v${r.version}.pdf`)}>PDF</button>}
                    {r.docx_path && <button className="btn btn-outline btn-sm" onClick={() => api.downloadFile(job.id, `resume_v${r.version}.docx`)}>DOCX</button>}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Cover Letters */}
          {coverLetters.length > 0 && (
            <div className="panel" style={{ padding:'1.25rem' }}>
              <h4 style={{ marginBottom:'0.75rem' }}>Cover Letter Versions ({coverLetters.length})</h4>
              {coverLetters.map(c => (
                <div key={c.id} style={{ padding:'0.75rem', background:'var(--surface-2)', borderRadius:'var(--radius-sm)', marginBottom:'0.5rem', display:'flex', justifyContent:'space-between', alignItems:'center' }}>
                  <div>
                    <InlineNameEdit 
                      initialValue={c.name} 
                      defaultLabel={`Cover Letter v${c.version}`} 
                      onSave={async (newName) => {
                        try {
                          await api.updateCoverLetter(job.id, c.id, { name: newName });
                          load();
                        } catch(e) { showToast(e.message, 'error'); }
                      }}
                    />
                    <div style={{ fontSize:'0.75rem', color:'var(--fg-muted)', marginTop:4 }}>{c.llm_used} • {new Date(c.created_at).toLocaleString()}</div>
                  </div>
                  <div className="flex gap-sm">
                    {c.pdf_path && <button className="btn btn-outline btn-sm" onClick={() => api.downloadFile(job.id, `cover_letter_v${c.version}.pdf`)}>PDF</button>}
                  </div>
                </div>
              ))}
            </div>
          )}

          {resumes.length === 0 && coverLetters.length === 0 && (
            <div className="panel" style={{ padding:'2rem', textAlign:'center', color:'var(--fg-subtle)' }}>
              <p>No documents generated yet. Use the buttons above to create your first tailored resume.</p>
            </div>
          )}
        </div>
      )}

      {/* ── Timeline Tab ─────────────────────────────────────────────────── */}
      {activeTab === 'dashboard' && (
        <div style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>
          {addingEvent ? (
            <div className="panel" style={{ padding:'1.25rem', display:'flex', flexDirection:'column', gap:'0.75rem' }}>
              <h4>Log Activity</h4>
              <div className="form-grid">
                <div className="form-group">
                  <label className="form-label">Event Type</label>
                  <select value={newEvent.event_type} onChange={e => setNewEvent(n=>({...n,event_type:e.target.value}))}>
                    {['note','recruiter_contact','interview_scheduled','interview_completed','offer_received','follow_up_sent'].map(t => (
                      <option key={t} value={t}>{EVENT_ICONS[t]} {t.replace(/_/g,' ')}</option>
                    ))}
                  </select>
                </div>
                <div className="form-group"><label className="form-label">Title</label><input value={newEvent.title} onChange={e => setNewEvent(n=>({...n,title:e.target.value}))} placeholder="e.g. Spoke with recruiter" /></div>
              </div>
              <div className="form-group"><label className="form-label">Description (optional)</label><textarea rows={2} value={newEvent.description} onChange={e => setNewEvent(n=>({...n,description:e.target.value}))} style={{ resize:'vertical' }} /></div>
              <div className="flex gap-sm" style={{ justifyContent:'flex-end' }}>
                <button className="btn btn-outline btn-sm" onClick={() => setAddingEvent(false)}>Cancel</button>
                <button className="btn btn-primary btn-sm" onClick={addEvent}>Add to Timeline</button>
              </div>
            </div>
          ) : (
            <button className="btn btn-outline" style={{ alignSelf:'flex-start' }} onClick={() => setAddingEvent(true)}>+ Log Activity</button>
          )}

          <div className="panel" style={{ padding:'1.25rem' }}>
            <div className="timeline" style={{ paddingLeft:'0.5rem' }}>
              {events.map(ev => (
                <div key={ev.id} className="timeline-item">
                  <div className={`timeline-dot ${ev.source}`}>
                    <span style={{ fontSize:'0.75rem' }}>{EVENT_ICONS[ev.event_type] ?? '•'}</span>
                  </div>
                  <div className="timeline-body">
                    <div className="timeline-title">{ev.title}</div>
                    {ev.description && <div className="timeline-desc">{ev.description}</div>}
                    <div className="timeline-time">
                      {new Date(ev.created_at).toLocaleString()} • {ev.source === 'agent' ? '🤖 by Agent' : '👤 by You'}
                    </div>
                  </div>
                </div>
              ))}
              {events.length === 0 && <p style={{ color:'var(--fg-subtle)', fontSize:'0.875rem' }}>No activity logged yet.</p>}
            </div>
          </div>
        </div>
      )}

      {/* ── LLM Compare Tab ──────────────────────────────────────────────── */}
      {activeTab === 'intelligence' && (
        <div style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>
          <div className="panel" style={{ padding:'1.25rem', display:'flex', flexDirection:'column', gap:'0.75rem' }}>
            <h4>Compare LLMs Side-by-Side</h4>
            <p style={{ fontSize:'0.875rem', color:'var(--fg-muted)' }}>
              Fan out the same generation task to multiple LLMs simultaneously. Compare quality, style, and speed.
            </p>
            <div className="flex gap-sm items-center">
              <select value={compareTask} onChange={e => setCompareTask(e.target.value)}>
                <option value="resume">Resume Tailoring</option>
                <option value="cover_letter">Cover Letter</option>
              </select>
              <button className="btn btn-primary" onClick={runCompare} disabled={loading.compare}>
                {loading.compare ? <><Spinner small /> Comparing…</> : 'Compare Gemini vs Groq vs OpenRouter'}
              </button>
            </div>
          </div>

          {compareResults && (
            <div style={{ display:'grid', gridTemplateColumns: `repeat(${compareResults.results.length}, 1fr)`, gap:'1rem' }}>
              {compareResults.results.map((r, i) => (
                <div key={i} className="panel" style={{ padding:'1.25rem' }}>
                  <div className="flex justify-between items-center" style={{ marginBottom:'0.75rem' }}>
                    <h4 style={{ fontSize:'0.875rem', textTransform:'uppercase', letterSpacing:'0.05em' }}>{r.provider}</h4>
                    <span style={{ fontSize:'0.75rem', color: r.error ? 'var(--danger)' : 'var(--success)' }}>
                      {r.error ? 'Error' : `${r.latency_ms}ms`}
                    </span>
                  </div>
                  {r.error ? (
                    <p style={{ color:'var(--danger)', fontSize:'0.8rem' }}>{r.error}</p>
                  ) : (
                    <div style={{ fontSize:'0.8rem', color:'var(--fg-muted)', lineHeight:1.6, whiteSpace:'pre-wrap', maxHeight:400, overflowY:'auto' }}
                      className="scrollbar-thin">
                      {r.text}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes slideUp { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:translateY(0); } }
      `}</style>
    </div>
  )
}
