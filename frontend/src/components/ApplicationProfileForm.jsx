import React, { useState, useEffect } from 'react'
import { api } from '../api/client'

// The apply agent answers form questions from this first, then the resume.
// Keys are sent to the model as "key with spaces: value", so name them plainly.
const SECTIONS = [
  { title: 'Work eligibility', fields: [
    { key: 'current_location', label: 'Current city, province/state, country', placeholder: 'Toronto, ON, Canada' },
    { key: 'legally_authorized_to_work_in_canada', label: 'Authorized to work in Canada?', options: ['Yes', 'No'] },
    { key: 'legally_authorized_to_work_in_us', label: 'Authorized to work in the US?', options: ['Yes', 'No'] },
    { key: 'requires_visa_sponsorship_now_or_in_future', label: 'Need visa sponsorship now or in future?', options: ['Yes', 'No'] },
    { key: 'immigration_status', label: 'Status (citizen, PR, work permit…)', placeholder: 'Permanent Resident' },
    { key: 'security_clearance', label: 'Security clearance', placeholder: 'None' },
  ]},
  { title: 'Compensation & availability', fields: [
    { key: 'salary_expectation', label: 'Salary expectation (exact text the agent may enter)', placeholder: 'CAD 150,000 – 170,000 base' },
    { key: 'notice_period', label: 'Notice period', placeholder: '2 weeks' },
    { key: 'earliest_start_date', label: 'Earliest start date', placeholder: 'Immediately / 2026-12-01' },
    { key: 'willing_to_relocate', label: 'Willing to relocate?', options: ['Yes', 'No', 'Within Canada only'] },
    { key: 'work_mode_preference', label: 'Remote / hybrid / onsite', options: ['Remote', 'Hybrid', 'Onsite', 'Any'] },
    { key: 'willing_to_travel', label: 'Travel willingness', placeholder: 'Up to 25%' },
  ]},
  { title: 'Experience & education', fields: [
    { key: 'total_years_of_experience', label: 'Total years of experience', placeholder: '7' },
    { key: 'years_of_experience_by_skill', label: 'Years per skill', placeholder: 'Python 7, SQL 7, ML 6, LLMs 3', wide: true },
    { key: 'highest_degree', label: 'Highest degree, field, school, year', placeholder: 'M.Sc. Data Science, …, 2020', wide: true },
    { key: 'certifications', label: 'Certifications', placeholder: 'AWS ML Specialty', wide: true },
  ]},
  { title: 'Contact & links', fields: [
    { key: 'preferred_name', label: 'Preferred name' },
    { key: 'pronouns', label: 'Pronouns (blank = not given)' },
    { key: 'phone', label: 'Phone' },
    { key: 'linkedin_url', label: 'LinkedIn URL' },
    { key: 'github_url', label: 'GitHub URL' },
    { key: 'portfolio_url', label: 'Portfolio / website' },
    { key: 'how_did_you_hear_about_us', label: '"How did you hear about us?" default', placeholder: 'Company careers page' },
  ]},
  { title: 'Address & common screening questions', note: 'Asked on most bank / enterprise forms (Workday, SuccessFactors). Blank stops the agent at that field.', fields: [
    { key: 'street_address', label: 'Street address (line 1)', placeholder: '123 King St W, Unit 4' },
    { key: 'postal_code', label: 'Postal / ZIP code', placeholder: 'N2G 1A1' },
    { key: 'phone_device_type', label: 'Phone type', options: ['Mobile', 'Home', 'Work'] },
    { key: 'familiarity_with_the_company_default', label: '"How familiar are you with us?" default', options: ['Very familiar', 'Somewhat familiar', 'Not familiar'] },
    { key: 'related_to_a_current_employee', label: 'Related to a current employee?', options: ['Yes', 'No'] },
    { key: 'age_18_or_older', label: '18 or older?', options: ['Yes', 'No'] },
    { key: 'consent_to_background_check', label: 'Consent to a background check?', options: ['Yes', 'No'] },
  ]},
  { title: 'Voluntary self-identification (EEO)', note: 'Blank means "decline to answer". These are only used on voluntary questions.', fields: [
    { key: 'eeo_gender', label: 'Gender' },
    { key: 'eeo_race_ethnicity', label: 'Race / ethnicity' },
    { key: 'eeo_hispanic_or_latino', label: 'Hispanic or Latino?' },
    { key: 'eeo_disability', label: 'Disability' },
    { key: 'eeo_veteran_status', label: 'Veteran status' },
    { key: 'eeo_indigenous', label: 'Indigenous identity (Canada)' },
  ]},
]

export default function ApplicationProfileForm() {
  const [p, setP] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    api.getApplicationProfile()
      .then(d => { if (!cancelled) setP({ custom_qa: [], ...d }) })
      .catch(e => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [])

  const set = (key, value) => setP(x => ({ ...x, [key]: value }))
  const setQa = (i, field, value) => setP(x => ({ ...x, custom_qa: x.custom_qa.map((r, j) => j === i ? { ...r, [field]: value } : r) }))

  const save = async () => {
    setBusy(true)
    try {
      setP(await api.saveApplicationProfile({ ...p, custom_qa: p.custom_qa.filter(r => r.q?.trim() && r.a?.trim()) }))
      setError('')
      setNotice('Saved')
      setTimeout(() => setNotice(''), 2500)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!p) return <div style={{ color: 'var(--fg-muted)' }}>{error || 'Loading…'}</div>

  return (
    <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      <p style={{ fontSize: '0.8rem', color: 'var(--fg-subtle)' }}>
        Your answers to common application questions. The apply agent uses these before anything else and
        leaves a field blank (and flags the job for you) when a required answer isn't here or in your resume.
      </p>

      {SECTIONS.map(sec => (
        <div key={sec.title} className="panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div>
            <h3 style={{ fontSize: '1rem' }}>{sec.title}</h3>
            {sec.note && <p style={{ fontSize: '0.8rem', color: 'var(--fg-subtle)', marginTop: 4 }}>{sec.note}</p>}
          </div>
          <div className="form-grid">
            {sec.fields.map(f => (
              <div className="form-group" key={f.key} style={f.wide ? { gridColumn: '1 / -1' } : undefined}>
                <label className="form-label">{f.label}</label>
                {f.options ? (
                  <select value={p[f.key] || ''} onChange={e => set(f.key, e.target.value)}>
                    <option value="">— not set —</option>
                    {f.options.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                ) : (
                  <input type="text" value={p[f.key] || ''} placeholder={f.placeholder || ''}
                    onChange={e => set(f.key, e.target.value)} />
                )}
              </div>
            ))}
          </div>
        </div>
      ))}

      <div className="panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div>
          <h3 style={{ fontSize: '1rem' }}>Custom questions</h3>
          <p style={{ fontSize: '0.8rem', color: 'var(--fg-subtle)', marginTop: 4 }}>
            Anything forms keep asking: "Are you 18 or older?", "Do you have a non-compete?", "Previously worked here?"
          </p>
        </div>
        {p.custom_qa.map((r, i) => (
          <div key={i} style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', alignItems: 'flex-start' }}>
            <input type="text" placeholder="Question" value={r.q || ''} style={{ flex: '1 1 12rem', minWidth: 0 }} onChange={e => setQa(i, 'q', e.target.value)} />
            <input type="text" placeholder="Answer" value={r.a || ''} style={{ flex: '1 1 10rem', minWidth: 0 }} onChange={e => setQa(i, 'a', e.target.value)} />
            <button className="btn btn-ghost btn-sm" aria-label="Remove question" onClick={() => setP(x => ({ ...x, custom_qa: x.custom_qa.filter((_, j) => j !== i) }))}>&times;</button>
          </div>
        ))}
        <button className="btn btn-outline btn-sm" style={{ alignSelf: 'flex-start' }}
          onClick={() => setP(x => ({ ...x, custom_qa: [...x.custom_qa, { q: '', a: '' }] }))}>+ Add question</button>
      </div>

      <div className="panel" style={{ padding: '1.5rem' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', cursor: 'pointer' }}>
          <input type="checkbox" checked={!!p._allow_drafts} onChange={e => set('_allow_drafts', e.target.checked)} />
          <span><strong>Let the agent draft open-ended answers</strong> ("Why this company?") from my resume.
            Facts are never drafted; every drafted answer is logged on the job for review.</span>
        </label>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
        <button className="btn btn-primary" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save application profile'}</button>
        {notice && <span style={{ fontSize: '0.8rem', color: 'var(--fg-muted)' }}>{notice}</span>}
        {error && <span style={{ fontSize: '0.8rem', color: 'var(--danger)' }}>{error}</span>}
      </div>
    </div>
  )
}
