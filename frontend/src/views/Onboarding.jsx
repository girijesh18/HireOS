// First-run setup, two steps: upload a resume, then review the application
// form we filled from it. Skippable: a hard gate at signup costs more in
// drop-off than it wins.
import { useState } from 'react'
import { api } from '../api/client'
import ApplicationProfileForm, { SECTIONS } from '../components/ApplicationProfileForm'

const ACCEPT = '.pdf,.docx,.md,.txt,.html,.htm'
const FIELDS = SECTIONS.flatMap(s => s.fields.map(({ key, label, options }) => ({ key, label, options: options || [] })))

export default function Onboarding({ onFinish }) {
  const [stage, setStage] = useState('upload')   // upload | reading | form
  const [prefill, setPrefill] = useState({ filled: 0 })
  const [error, setError] = useState('')

  const finish = async () => {
    try { await api.skipOnboarding() } catch { /* marking done must never block */ }
    onFinish()
  }

  const upload = async (file) => {
    if (!file) return
    setStage('reading'); setError('')
    try {
      await api.uploadResumeFile(file.name.replace(/\.[^.]+$/, ''), file)
      setPrefill(await api.prefillApplicationProfile(FIELDS))
      setStage('form')
    } catch (e) {
      setError(e.message); setStage('upload')
    }
  }

  return (
    <div className="fade-in" style={{ maxWidth: 760, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
      {stage === 'upload' && <>
        <div>
          <h2 style={{ fontSize: '1.35rem', marginBottom: '0.4rem' }}>Upload your resume</h2>
          <p style={{ color: 'var(--fg-muted)', fontSize: '0.9rem' }}>
            We fill your application details from it. You review, then you’re done.
          </p>
        </div>
        <label
          onDragOver={e => e.preventDefault()}
          onDrop={e => { e.preventDefault(); upload(e.dataTransfer.files[0]) }}
          style={{
            border: '2px dashed var(--surface-border)', borderRadius: 'var(--radius)',
            padding: '2.5rem 1.5rem', textAlign: 'center', cursor: 'pointer',
            background: 'var(--surface-2)', display: 'block',
          }}>
          <div style={{ fontSize: '0.95rem', fontWeight: 600, marginBottom: '0.35rem' }}>Drop your resume here, or click to choose</div>
          <div style={{ fontSize: '0.8rem', color: 'var(--fg-muted)' }}>PDF, DOCX, Markdown, HTML or plain text</div>
          <input type="file" hidden accept={ACCEPT} onChange={e => { upload(e.target.files[0]); e.target.value = null }} />
        </label>
        {error && <div className="alert alert-danger">{error}</div>}
        <div><button className="btn btn-ghost btn-sm" onClick={finish}>Skip for now</button></div>
      </>}

      {stage === 'reading' && (
        <div className="panel" style={{ padding: '3rem 1.5rem', textAlign: 'center' }}>
          <h2 style={{ fontSize: '1.2rem', marginBottom: '0.5rem' }}>Reading your resume…</h2>
          <p style={{ color: 'var(--fg-muted)', fontSize: '0.9rem' }}>Filling your application details. A few seconds.</p>
        </div>
      )}

      {stage === 'form' && <>
        <div>
          <h2 style={{ fontSize: '1.35rem', marginBottom: '0.4rem' }}>Check your details</h2>
          <p style={{ color: 'var(--fg-muted)', fontSize: '0.9rem' }}>
            {prefill.failed ? 'Your resume is saved, but we couldn’t read it automatically right now.'
              : prefill.filled ? `We filled ${prefill.filled} field${prefill.filled > 1 ? 's' : ''} from your resume.`
              : 'Your resume didn’t cover these fields.'}
            {' '}Fill the rest, then save. You can change it later in Settings.
          </p>
        </div>
        <ApplicationProfileForm onSaved={finish} />
        <div><button className="btn btn-ghost btn-sm" onClick={finish}>Skip for now</button></div>
      </>}
    </div>
  )
}
