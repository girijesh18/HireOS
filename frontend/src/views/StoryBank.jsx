import React, { useEffect, useState } from 'react'
import { api } from '../api/client'

export default function StoryBank() {
  const [stories, setStories] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const [toast, setToast] = useState(null)

  const showToast = (message) => {
    setToast(message)
    setTimeout(() => setToast(null), 3000)
  }

  const load = async () => {
    setLoading(true)
    try {
      const data = await api.getStoryBank(filter)
      setStories(data)
    } catch (e) {
      showToast('Error loading stories')
    }
    setLoading(false)
  }

  useEffect(() => { load() }, [filter])

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text)
    showToast('Copied to clipboard!')
  }

  return (
    <div style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      {toast && (
        <div style={{
          position: 'fixed', bottom: '2rem', right: '2rem', zIndex: 1000,
          background: 'var(--success)', color: 'var(--primary-fg)', padding: '0.75rem 1.25rem',
          borderRadius: 'var(--radius)', boxShadow: 'var(--shadow-lg)',
          animation: 'slideUp 0.3s ease'
        }}>
          {toast}
        </div>
      )}

      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '1.5rem', fontWeight: 600 }}>Global Story Bank</h2>
          <p style={{ color: 'var(--fg-subtle)', margin: '4px 0 0 0', fontSize: '0.9rem' }}>
            Your repository of STAR stories across all applications. Use these to ace your interviews.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem' }}>
          <input 
            value={filter} 
            onChange={e => setFilter(e.target.value)}
            placeholder="Search by tag (e.g. leadership)..." 
            style={{ width: 250 }}
          />
          <button className="btn btn-outline" onClick={load}>Refresh</button>
        </div>
      </header>

      {loading ? (
        <div style={{ textAlign: 'center', padding: '4rem' }}>Loading stories...</div>
      ) : stories.length === 0 ? (
        <div style={{ 
          textAlign: 'center', padding: '5rem', background: 'var(--bg-2)', 
          borderRadius: 'var(--radius)', border: '1px dashed var(--surface-border)'
        }}>
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '1rem', color: 'var(--fg-subtle)' }}>
            <svg width={40} height={40} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
          </div>
          <h3>Your Story Bank is empty</h3>
          <p style={{ color: 'var(--fg-subtle)' }}>
            Run the <strong>Interview Prep</strong> agent on a job to automatically generate STAR stories.
          </p>
        </div>
      ) : (
        <div className="form-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(400px, 1fr))' }}>
          {stories.map(story => (
            <div key={story.id} className="card hover-card" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <h4 style={{ margin: 0, color: 'var(--primary)' }}>{story.title}</h4>
                <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                  {story.tags?.map(t => (
                    <span key={t} className="badge badge-outline" style={{ fontSize: '0.7rem' }}>{t}</span>
                  ))}
                </div>
              </div>
              
              <div style={{ fontSize: '0.875rem' }}>
                <div style={{ marginBottom: '0.75rem' }}>
                  <strong style={{ display: 'block', fontSize: '0.75rem', textTransform: 'uppercase', color: 'var(--fg-subtle)', marginBottom: 4 }}>Situation</strong>
                  {story.situation}
                </div>
                <div style={{ marginBottom: '0.75rem' }}>
                  <strong style={{ display: 'block', fontSize: '0.75rem', textTransform: 'uppercase', color: 'var(--fg-subtle)', marginBottom: 4 }}>Action</strong>
                  {story.action}
                </div>
                <div style={{ marginBottom: '0.75rem' }}>
                  <strong style={{ display: 'block', fontSize: '0.75rem', textTransform: 'uppercase', color: 'var(--fg-subtle)', marginBottom: 4 }}>Result</strong>
                  {story.result}
                </div>
                {story.reflection && (
                  <div style={{ padding: '0.75rem', background: 'var(--surface-2)', borderRadius: 'var(--radius-sm)', borderLeft: '3px solid var(--primary)' }}>
                    <strong style={{ display: 'block', fontSize: '0.75rem', textTransform: 'uppercase', color: 'var(--primary)', marginBottom: 4 }}>Senior Reflection</strong>
                    {story.reflection}
                  </div>
                )}
              </div>

              <div style={{ marginTop: 'auto', display: 'flex', gap: '0.5rem' }}>
                <button className="btn btn-sm btn-ghost" onClick={() => copyToClipboard(`${story.title}\n\nS: ${story.situation}\nT: ${story.task}\nA: ${story.action}\nR: ${story.result}`)}>
                  <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                  Copy STAR
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
