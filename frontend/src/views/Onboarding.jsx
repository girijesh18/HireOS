// First-run setup. Two independent ways in, chosen by the user -- not a forced
// two-step wizard:
//
//   Upload documents  ->  we read them  ->  we ask only what they did not say
//   Answer questions  ->  straight to the interview
//
// Uploading is the better path and the UI says why, but neither is mandatory.
// Questions carry the profile key they fill, and those keys are invented per
// user by the model, so nothing here assumes what a person consists of.
import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { Composer, Transcript } from '../components/ChatBubbles'

const ACCEPT = '.pdf,.docx,.md,.txt,.html,.htm'

function Card({ title, blurb, badge, cta, onClick, disabled }) {
  return (
    <button onClick={onClick} disabled={disabled} className="panel"
      style={{
        padding: '1.5rem', textAlign: 'left', cursor: disabled ? 'default' : 'pointer',
        display: 'flex', flexDirection: 'column', gap: '0.6rem', minHeight: 190,
        border: '1px solid var(--surface-border)', background: 'var(--surface)',
        color: 'var(--fg)', width: '100%',
      }}>
      <div className="flex items-center justify-between" style={{ gap: '0.5rem' }}>
        <strong style={{ fontSize: '1.05rem' }}>{title}</strong>
        {badge && <span className="badge" style={{ background: 'var(--primary)', color: 'var(--primary-fg)' }}>{badge}</span>}
      </div>
      <p style={{ fontSize: '0.875rem', color: 'var(--fg-muted)', lineHeight: 1.55, flex: 1 }}>{blurb}</p>
      <span style={{ fontSize: '0.85rem', color: 'var(--primary)', fontWeight: 600 }}>{cta} →</span>
    </button>
  )
}

function Chooser({ onPick, onSkip }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      <div>
        <h2 style={{ fontSize: '1.45rem', marginBottom: '0.4rem' }}>Let’s set up your profile</h2>
        <p style={{ color: 'var(--fg-muted)', fontSize: '0.925rem', lineHeight: 1.6 }}>
          We use this every time you generate a resume. Pick whichever is easier —
          you can do the other one later.
        </p>
      </div>
      <div style={{ display: 'grid', gap: '1rem', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))' }}>
        <Card
          title="Upload your files"
          badge="Best results"
          blurb="Your resume, an old CV, a project write-up — anything. We read them first, then ask only about what they don’t already cover. More information helps us curate a better resume."
          cta="Upload files"
          onClick={() => onPick('upload')}
        />
        <Card
          title="Answer a few questions"
          blurb="No files handy? Answer 6–8 short questions instead. A phrase each is enough — we’ll build your profile from your answers."
          cta="Start answering"
          onClick={() => onPick('questions')}
        />
      </div>
      <div><button className="btn btn-ghost btn-sm" onClick={onSkip}>Skip for now</button></div>
    </div>
  )
}

function UploadStep({ onAnalyzed, onBack, onSkip }) {
  const [files, setFiles] = useState([])
  const [busy, setBusy] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.onboardingStatus()
      .then(s => setFiles((s.documents || []).map(d => ({ name: d.name, ok: true }))))
      .catch(() => {})
  }, [])

  const upload = async (fileList) => {
    const picked = Array.from(fileList || [])
    if (!picked.length) return
    setBusy(true); setError('')
    for (const file of picked) {
      const name = file.name.replace(/\.[^.]+$/, '')
      setFiles(f => [...f, { name, ok: null }])
      try {
        await api.uploadResumeFile(name, file)
        setFiles(f => f.map(x => (x.name === name && x.ok === null ? { ...x, ok: true } : x)))
      } catch (e) {
        setFiles(f => f.filter(x => !(x.name === name && x.ok === null)))
        setError(`${file.name}: ${e.message}`)
      }
    }
    setBusy(false)
  }

  const analyze = async () => {
    setAnalyzing(true); setError('')
    try {
      onAnalyzed(await api.analyzeOnboardingDocs())
    } catch (e) {
      setError(e.message)
      setAnalyzing(false)
    }
  }

  const uploaded = files.filter(f => f.ok).length

  if (analyzing) {
    return (
      <div className="panel" style={{ padding: '3rem 1.5rem', textAlign: 'center' }}>
        <h2 style={{ fontSize: '1.2rem', marginBottom: '0.5rem' }}>Reading your documents…</h2>
        <p style={{ color: 'var(--fg-muted)', fontSize: '0.9rem' }}>
          Pulling out what they already say, so we only ask you about the rest.
          This takes a few seconds.
        </p>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
      <div>
        <h2 style={{ fontSize: '1.35rem', marginBottom: '0.4rem' }}>Upload your files</h2>
        <p style={{ color: 'var(--fg-muted)', fontSize: '0.9rem', lineHeight: 1.6 }}>
          Your resume and anything else relevant — an old CV, a project write-up, a
          performance review.
          <br />
          <strong style={{ color: 'var(--fg)' }}>More information helps us curate a better resume.</strong>
        </p>
      </div>

      <label
        onDragOver={e => e.preventDefault()}
        onDrop={e => { e.preventDefault(); upload(e.dataTransfer.files) }}
        style={{
          border: '2px dashed var(--surface-border)', borderRadius: 'var(--radius)',
          padding: '2.5rem 1.5rem', textAlign: 'center', cursor: busy ? 'wait' : 'pointer',
          background: 'var(--surface-2)', display: 'block',
        }}>
        <div style={{ fontSize: '0.95rem', fontWeight: 600, marginBottom: '0.35rem' }}>
          {busy ? 'Extracting…' : 'Drop files here, or click to choose'}
        </div>
        <div style={{ fontSize: '0.8rem', color: 'var(--fg-muted)' }}>
          PDF, DOCX, Markdown, HTML or plain text
        </div>
        <input type="file" hidden multiple accept={ACCEPT} disabled={busy}
          onChange={e => { upload(e.target.files); e.target.value = null }} />
      </label>

      {error && <div className="alert alert-danger">{error}</div>}

      {files.length > 0 && (
        <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`} className="flex items-center gap-sm"
              style={{ fontSize: '0.85rem', color: f.ok ? 'var(--fg)' : 'var(--fg-muted)' }}>
              <span>{f.ok ? '✓' : '…'}</span>{f.name}
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center justify-between" style={{ gap: '1rem', flexWrap: 'wrap' }}>
        <div className="flex gap-sm">
          <button className="btn btn-ghost btn-sm" onClick={onBack}>Back</button>
          <button className="btn btn-ghost btn-sm" onClick={onSkip}>Skip for now</button>
        </div>
        <button className="btn btn-primary" onClick={analyze} disabled={busy || !uploaded}>
          {uploaded ? `Analyze ${uploaded} file${uploaded > 1 ? 's' : ''} & continue` : 'Add a file to continue'}
        </button>
      </div>
    </div>
  )
}

function Interview({ questions, extracted, onDone, onSkip, error: initialError }) {
  const [index, setIndex] = useState(0)
  const [facts, setFacts] = useState([])
  const [draft, setDraft] = useState('')
  const [messages, setMessages] = useState([])
  const [error, setError] = useState(initialError || '')
  const [saving, setSaving] = useState(false)
  const seeded = useRef(false)

  useEffect(() => {
    if (seeded.current || !questions?.length) return
    seeded.current = true
    const intro = extracted?.length
      ? `Got it — I picked up ${extracted.length} things from your files. Just ${questions.length} more, a few words each.`
      : `${questions.length} quick questions — a few words each is plenty.`
    setMessages([{ role: 'agent', text: intro }, { role: 'agent', text: questions[0].question }])
  }, [questions, extracted])

  const current = questions?.[index]

  const submit = async (skipped = false) => {
    if (!current || saving) return
    const answer = skipped ? '' : draft.trim()
    if (!answer && !skipped) return

    const next = answer
      ? [...facts, { key: current.key, label: current.label || current.key, value: answer }]
      : facts
    setFacts(next)
    setDraft('')
    setMessages(m => [...m, { role: 'user', text: answer || '(skipped)' }])

    if (index + 1 < questions.length) {
      setIndex(index + 1)
      setMessages(m => [...m, { role: 'agent', text: questions[index + 1].question }])
      return
    }

    setSaving(true)
    try {
      await api.saveOnboardingAnswers(next)
      onDone()
    } catch (e) {
      setError(e.message)
      setSaving(false)
    }
  }

  const finishNow = async () => {
    setSaving(true)
    try { await api.saveOnboardingAnswers(facts); onDone() }
    catch (e) { setError(e.message); setSaving(false) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div>
        <h2 style={{ fontSize: '1.35rem', marginBottom: '0.4rem' }}>
          {extracted?.length ? 'A few gaps to fill' : 'Tell me about you'}
        </h2>
        <p style={{ color: 'var(--fg-muted)', fontSize: '0.9rem' }}>
          Short answers. We keep this and use it every time you generate a resume.
          {questions?.length ? ` · ${Math.min(index + 1, questions.length)} of ${questions.length}` : ''}
        </p>
      </div>

      {extracted?.length > 0 && (
        <details className="panel" style={{ padding: '0.85rem 1rem' }}>
          <summary style={{ cursor: 'pointer', fontSize: '0.875rem', fontWeight: 600 }}>
            What we read from your files ({extracted.length})
          </summary>
          <ul style={{ listStyle: 'none', marginTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
            {extracted.map(f => (
              <li key={f.key} style={{ fontSize: '0.825rem', color: 'var(--fg-muted)' }}>
                <strong style={{ color: 'var(--fg)' }}>{f.label || f.key}:</strong> {f.value}
              </li>
            ))}
          </ul>
        </details>
      )}

      {error && <div className="alert alert-danger">{error}</div>}

      <div className="panel" style={{ display: 'flex', flexDirection: 'column', minHeight: 340 }}>
        <Transcript messages={messages} pending={saving && 'Saving your profile…'} />
        {current && (
          <Composer
            value={draft}
            onChange={setDraft}
            onSend={() => submit(false)}
            disabled={saving}
            placeholder={current.prefix ? `${current.prefix} …  e.g. ${current.placeholder}` : current.placeholder}
            sendLabel={saving ? 'Saving…' : (index + 1 === questions.length ? 'Finish' : 'Next')}
          />
        )}
      </div>

      <div className="flex items-center justify-between" style={{ gap: '1rem', flexWrap: 'wrap' }}>
        <button className="btn btn-ghost btn-sm" onClick={facts.length ? finishNow : onSkip} disabled={saving}>
          {facts.length ? 'Save and finish early' : 'Skip for now'}
        </button>
        {current && (
          <button className="btn btn-outline btn-sm" onClick={() => submit(true)} disabled={saving}>
            Skip this question
          </button>
        )}
      </div>
    </div>
  )
}

export default function Onboarding({ onFinish }) {
  const [stage, setStage] = useState('choose')   // choose | upload | interview
  const [questions, setQuestions] = useState(null)
  const [extracted, setExtracted] = useState([])
  const [error, setError] = useState('')

  const skip = async () => {
    try { await api.skipOnboarding() } catch { /* skipping must never block */ }
    onFinish()
  }

  const startQuestions = async () => {
    setStage('interview'); setQuestions(null); setExtracted([])
    try {
      const { questions: qs } = await api.onboardingQuestions()
      setQuestions(qs)
    } catch (e) {
      setError(e.message); setQuestions([])
    }
  }

  const afterAnalysis = (result) => {
    setExtracted(result.extracted || [])
    setQuestions(result.questions || [])
    setStage('interview')
  }

  return (
    <div className="fade-in" style={{ maxWidth: 760, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      {stage === 'choose' && (
        <Chooser onPick={p => (p === 'upload' ? setStage('upload') : startQuestions())} onSkip={skip} />
      )}
      {stage === 'upload' && (
        <UploadStep onAnalyzed={afterAnalysis} onBack={() => setStage('choose')} onSkip={skip} />
      )}
      {stage === 'interview' && questions === null && (
        <div className="panel" style={{ padding: '3rem 1.5rem', textAlign: 'center' }}>
          <h2 style={{ fontSize: '1.2rem', marginBottom: '0.5rem' }}>Writing your questions…</h2>
          <p style={{ color: 'var(--fg-muted)', fontSize: '0.9rem' }}>One moment.</p>
        </div>
      )}
      {stage === 'interview' && questions !== null && (
        questions.length
          ? <Interview questions={questions} extracted={extracted} onDone={onFinish} onSkip={skip} error={error} />
          : <div className="alert alert-danger">Couldn’t load questions. {error}
              <button className="btn btn-outline btn-sm" style={{ marginLeft: '0.75rem' }} onClick={skip}>Skip for now</button>
            </div>
      )}
    </div>
  )
}
