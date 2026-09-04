// Chat transcript primitives, lifted out of ResumeEditor so the onboarding
// interview can reuse them. ResumeEditor's own copies are bound to a jobId and
// to api.resumeChat; these take only what they render.
import { useEffect, useRef } from 'react'

export function Bubble({ role, children }) {
  const isUser = role === 'user'
  return (
    <div style={{
      alignSelf: isUser ? 'flex-end' : 'flex-start',
      backgroundColor: isUser ? 'var(--primary)' : 'var(--surface-2)',
      color: isUser ? 'var(--primary-fg)' : 'var(--fg)',
      padding: '0.75rem 1rem', borderRadius: '1rem', maxWidth: '90%',
      fontSize: '0.875rem', whiteSpace: 'pre-wrap',
    }}>
      {children}
    </div>
  )
}

export function Transcript({ messages, pending, children }) {
  const endRef = useRef(null)
  // The ResumeEditor transcript doesn't scroll itself, which means a long
  // conversation walks off the bottom. Cheap to fix here.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length, pending])

  return (
    <div style={{
      flex: 1, overflowY: 'auto', padding: '1rem',
      display: 'flex', flexDirection: 'column', gap: '1rem',
    }}>
      {messages.map((m, i) => <Bubble key={i} role={m.role}>{m.text}</Bubble>)}
      {pending && (
        <div style={{ alignSelf: 'flex-start', padding: '0.5rem', color: 'var(--fg-subtle)', fontSize: '0.875rem' }}>
          {typeof pending === 'string' ? pending : 'Thinking…'}
        </div>
      )}
      {children}
      <div ref={endRef} />
    </div>
  )
}

export function Composer({ value, onChange, onSend, disabled, placeholder, sendLabel = 'Send', rows = 2 }) {
  return (
    <div style={{ padding: '1rem', borderTop: '1px solid var(--surface-border)' }}>
      <textarea
        value={value}
        onChange={e => onChange(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend() } }}
        placeholder={placeholder}
        rows={rows}
        disabled={disabled}
        style={{
          width: '100%', padding: '0.75rem', borderRadius: 'var(--radius)',
          border: '1px solid var(--surface-border)', background: 'var(--bg)',
          color: 'var(--fg)', resize: 'none', fontSize: '0.9rem',
        }}
      />
      <button className="btn btn-primary" onClick={onSend} disabled={disabled}
        style={{ width: '100%', marginTop: '0.5rem' }}>
        {sendLabel}
      </button>
    </div>
  )
}
