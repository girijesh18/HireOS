import React, { useState, useEffect } from 'react'
import { api } from '../api/client'

// One editor per target field. Lists are one item per line; maps are "key = value" per line.
const lines = (t) => String(t || '').split('\n').map(s => s.trim()).filter(Boolean)
const LIST_FIELDS = [
  ['title_keywords', 'Title must contain one of', 'data scientist\nmachine learning'],
  ['exclude_keywords', 'Skip titles containing', 'intern\njunior'],
  ['locations', 'Locations (city, "remote", "canada")', 'toronto\nremote\ncanada'],
  ['workday_searches', 'Search each Workday site for', 'data scientist'],
]

function toForm(t) {
  t = t || {}
  const f = Object.fromEntries(LIST_FIELDS.map(([k]) => [k, (t[k] || []).join('\n')]))
  f.companies = Object.entries(t.companies || {}).flatMap(([ats, slugs]) => slugs.map(s => `${ats} = ${s}`)).join('\n')
  f.workday = Object.entries(t.workday || {}).map(([name, url]) => `${name} = ${url}`).join('\n')
  f.linkedin = (t.linkedin_searches || []).map(s => [s.keywords, s.location, s.limit].filter(x => x !== '' && x != null).join(' | ')).join('\n')
  return f
}

function fromForm(f) {
  const t = Object.fromEntries(LIST_FIELDS.map(([k]) => [k, lines(f[k])]))
  t.companies = {}
  for (const l of lines(f.companies)) {
    const [ats, slug] = l.split('=').map(s => s.trim())
    ;(t.companies[ats.toLowerCase()] ||= []).push(slug)
  }
  t.workday = Object.fromEntries(lines(f.workday).map(l => {
    const i = l.lastIndexOf('=')
    return [l.slice(0, i).trim(), l.slice(i + 1).trim()]
  }))
  t.linkedin_searches = lines(f.linkedin).map(l => {
    const [keywords, location = '', limit = ''] = l.split('|').map(s => s.trim())
    return { keywords, location, ...(limit ? { limit: Number(limit) } : {}) }
  })
  return t
}

/**
 * Where scheduled discovery looks for jobs: weekdays at 08:00 and 16:00 (your timezone, set under
 * Auto-Apply), plus job-alert emails. New jobs are scored and get a tailored resume on their own.
 */
export default function DiscoveryPanel() {
  const [form, setForm] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [runsHere, setRunsHere] = useState(true)

  useEffect(() => {
    api.getDiscoveryTargets().then(r => { setForm(toForm(r.targets)); setRunsHere(r.runs_here !== false) })
      .catch(e => setError(e.message))
  }, [])

  const flash = (msg) => { setError(''); setNotice(msg); setTimeout(() => setNotice(''), 4000) }

  const save = async () => {
    setBusy(true)
    try {
      const r = await api.saveDiscoveryTargets(fromForm(form))
      setForm(toForm(r.targets))
      flash('Saved')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const runNow = async () => {
    setBusy(true)
    try {
      const r = await api.runDiscovery()
      flash(r.started ? 'Discovery started — new resumes arrive on Telegram' : 'A discovery run is already going')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!form) return <div style={{ color: 'var(--fg-muted)' }}>{error || 'Loading…'}</div>

  const area = (key, label, placeholder, help) => (
    <div className="form-group" key={key}>
      <label className="form-label">{label}</label>
      <textarea rows={4} value={form[key]} placeholder={placeholder}
        onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
        style={{ fontFamily: 'monospace', fontSize: '0.8rem' }} />
      {help && <span style={{ fontSize: '0.7rem', color: 'var(--fg-subtle)', marginTop: 2 }}>{help}</span>}
    </div>
  )

  return (
    <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      <div className="panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div>
          <h3 style={{ fontSize: '1rem' }}>What to look for</h3>
          <p style={{ fontSize: '0.8rem', color: 'var(--fg-subtle)', marginTop: 4 }}>
            Discovery runs on weekdays at 08:00 and 16:00, reads your job-alert emails, then checks the company
            boards and LinkedIn searches below. Each new job is scored, and the ones at or above your minimum
            score get a tailored resume for you to approve. One item per line, not case-sensitive.
          </p>
        </div>
        {!runsHere && (
          <p style={{ fontSize: '0.8rem', color: 'var(--warning)', margin: 0 }}>
            Scheduled discovery isn't available on this server: it runs on a self-hosted HireOS. You can
            still save these settings; add jobs here by pasting a job URL.
          </p>
        )}
        <div className="form-grid">{LIST_FIELDS.map(([k, label, ph]) => area(k, label, ph))}</div>
      </div>

      <div className="panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <h3 style={{ fontSize: '1rem' }}>Where to look</h3>
        <div className="form-grid">
          {area('companies', 'Company boards (ats = slug)', 'greenhouse = acme\nlever = example\nashby = startup',
            'The slug is the part of the careers URL after boards.greenhouse.io/, jobs.lever.co/ or jobs.ashbyhq.com/.')}
          {area('workday', 'Workday sites (company = URL)', 'Acme = https://acme.wd3.myworkdayjobs.com/External',
            'Also used to find a LinkedIn job on the company\'s own Workday site.')}
          {area('linkedin', 'LinkedIn searches (keywords | location | how many)', 'data scientist | Toronto, Ontario, Canada | 5',
            'Uses LinkedIn\'s public job pages; never signs in to LinkedIn. At most 10 per search.')}
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
        <button className="btn btn-primary" onClick={save} disabled={busy}>Save discovery settings</button>
        {runsHere && <button className="btn btn-outline" onClick={runNow} disabled={busy}>Run discovery now</button>}
        {notice && <span style={{ fontSize: '0.8rem', color: 'var(--fg-muted)' }}>{notice}</span>}
        {error && <span style={{ fontSize: '0.8rem', color: 'var(--danger)' }}>{error}</span>}
      </div>
    </div>
  )
}
