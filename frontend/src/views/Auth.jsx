import React, { useState } from 'react'
import { api, setToken } from '../api/client'

export default function Auth({ onAuth }) {
  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    if (mode === 'signup' && password !== confirm) { setError('Passwords do not match'); return }
    setLoading(true)
    try {
      const res = mode === 'login' ? await api.login(email, password) : await api.signup(email, password)
      setToken(res.token)
      onAuth(res.email)
    } catch (err) { setError(err.message) }
    setLoading(false)
  }

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      background: '#060c1a',
      overflow: 'hidden',
      position: 'relative',
    }}>

      {/* Background blobs */}
      <div style={{ position: 'absolute', inset: 0, overflow: 'hidden', pointerEvents: 'none' }}>
        <div style={{
          position: 'absolute', width: '600px', height: '600px',
          borderRadius: '50%', filter: 'blur(120px)', opacity: 0.25,
          background: 'radial-gradient(circle, #3b82f6, transparent)',
          top: '-150px', left: '-150px',
        }} />
        <div style={{
          position: 'absolute', width: '500px', height: '500px',
          borderRadius: '50%', filter: 'blur(100px)', opacity: 0.2,
          background: 'radial-gradient(circle, #06b6d4, transparent)',
          bottom: '-100px', right: '-100px',
        }} />
        <div style={{
          position: 'absolute', width: '300px', height: '300px',
          borderRadius: '50%', filter: 'blur(80px)', opacity: 0.12,
          background: 'radial-gradient(circle, #2563eb, transparent)',
          top: '50%', left: '50%', transform: 'translate(-50%,-50%)',
        }} />
      </div>

      {/* Left panel — branding */}
      <div style={{
        flex: '0 0 52%',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        padding: '3rem',
        position: 'relative',
        backgroundImage: `url('/auth-bg.png')`,
        backgroundSize: 'cover',
        backgroundPosition: 'center 35%',
      }}>
        {/* Dark overlay — heavier at bottom where text sits */}
        <div style={{ position: 'absolute', inset: 0, background: 'rgba(6,12,26,0.55)' }} />
        <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(to top, rgba(6,12,26,0.92) 0%, rgba(6,12,26,0.3) 50%, rgba(6,12,26,0.2) 100%)' }} />

        {/* Logo top-left */}
        <div style={{ position: 'relative', zIndex: 1 }}>
          <span style={{
            fontFamily: 'Outfit, sans-serif', fontSize: '1.5rem', fontWeight: 800,
            color: '#fff', letterSpacing: '-0.02em',
          }}>
            Hire<span style={{ color: '#22d3ee' }}>OS</span>
          </span>
        </div>

        {/* Bottom copy */}
        <div style={{ position: 'relative', zIndex: 1 }}>
          <div style={{
            display: 'inline-block',
            background: 'rgba(59,130,246,0.18)',
            border: '1px solid rgba(59,130,246,0.3)',
            borderRadius: '999px',
            padding: '4px 14px',
            fontSize: '0.75rem',
            fontWeight: 600,
            color: '#7dd3fc',
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
            marginBottom: '1rem',
          }}>
            AI-Powered · Agentic · Autonomous
          </div>
          <h1 style={{
            fontSize: '2.75rem', fontWeight: 800, lineHeight: 1.15,
            fontFamily: 'Outfit, sans-serif', color: '#fff',
            marginBottom: '1rem',
          }}>
            Land the job<br />
            <span style={{
              background: 'linear-gradient(135deg, #60a5fa 0%, #22d3ee 100%)',
              WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text',
            }}>smarter, faster.</span>
          </h1>
          <p style={{ color: 'rgba(255,255,255,0.55)', fontSize: '0.95rem', lineHeight: 1.7, maxWidth: '380px' }}>
            HireOS automates your entire job hunt — from tracking applications to generating tailored resumes, cover letters, and interview prep with AI.
          </p>

          {/* Stats row */}
          <div style={{ display: 'flex', gap: '2rem', marginTop: '2rem' }}>
            {[['AI Resume', 'tailored per job'], ['Auto Research', 'every company'], ['STAR Stories', 'interview-ready']].map(([title, sub]) => (
              <div key={title}>
                <div style={{ fontSize: '0.85rem', fontWeight: 700, color: '#e0f2fe' }}>{title}</div>
                <div style={{ fontSize: '0.72rem', color: 'rgba(255,255,255,0.4)', marginTop: '2px' }}>{sub}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Right panel — form */}
      <div style={{
        flex: '0 0 48%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '3rem 2rem',
        position: 'relative',
        zIndex: 1,
      }}>
        <div style={{ width: '100%', maxWidth: '360px' }}>

          <div style={{ marginBottom: '2rem' }}>
            <div style={{ marginBottom: '1.25rem' }}>
              <span style={{
                fontFamily: 'Outfit, sans-serif', fontSize: '1.75rem', fontWeight: 800,
                letterSpacing: '-0.03em',
                background: 'linear-gradient(135deg, #e8f1ff 0%, #22d3ee 100%)',
                WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text',
              }}>
                HireOS
              </span>
            </div>
            <h2 style={{
              fontSize: '1.6rem', fontWeight: 700,
              color: '#f1f5f9', marginBottom: '0.4rem',
              fontFamily: 'Outfit, sans-serif',
            }}>
              {mode === 'login' ? 'Welcome back' : 'Get started free'}
            </h2>
            <p style={{ color: '#4a7fa5', fontSize: '0.875rem' }}>
              {mode === 'login'
                ? 'Sign in to your HireOS account'
                : 'Create your account in seconds'}
            </p>
          </div>

          <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
              <label style={{ fontSize: '0.8rem', fontWeight: 500, color: '#7ba7c4' }}>Email address</label>
              <input
                type="email" value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="you@example.com"
                required autoFocus
                style={{
                  background: 'rgba(59,130,246,0.06)',
                  border: '1px solid rgba(59,130,246,0.2)',
                  borderRadius: '10px',
                  padding: '0.7rem 1rem',
                  color: '#f1f5f9',
                  fontSize: '0.9rem',
                  outline: 'none',
                  transition: 'border-color 0.2s',
                }}
                onFocus={e => e.target.style.borderColor = '#3b82f6'}
                onBlur={e => e.target.style.borderColor = 'rgba(59,130,246,0.2)'}
              />
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
              <label style={{ fontSize: '0.8rem', fontWeight: 500, color: '#7ba7c4' }}>Password</label>
              <input
                type="password" value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder={mode === 'signup' ? 'Minimum 6 characters' : '••••••••'}
                required
                style={{
                  background: 'rgba(59,130,246,0.06)',
                  border: '1px solid rgba(59,130,246,0.2)',
                  borderRadius: '10px',
                  padding: '0.7rem 1rem',
                  color: '#f1f5f9',
                  fontSize: '0.9rem',
                  outline: 'none',
                  transition: 'border-color 0.2s',
                }}
                onFocus={e => e.target.style.borderColor = '#3b82f6'}
                onBlur={e => e.target.style.borderColor = 'rgba(59,130,246,0.2)'}
              />
            </div>

            {mode === 'signup' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                <label style={{ fontSize: '0.8rem', fontWeight: 500, color: '#7ba7c4' }}>Confirm password</label>
                <input
                  type="password" value={confirm}
                  onChange={e => setConfirm(e.target.value)}
                  placeholder="••••••••" required
                  style={{
                    background: 'rgba(59,130,246,0.06)',
                    border: '1px solid rgba(59,130,246,0.2)',
                    borderRadius: '10px',
                    padding: '0.7rem 1rem',
                    color: '#f1f5f9',
                    fontSize: '0.9rem',
                    outline: 'none',
                    transition: 'border-color 0.2s',
                  }}
                  onFocus={e => e.target.style.borderColor = '#3b82f6'}
                  onBlur={e => e.target.style.borderColor = 'rgba(59,130,246,0.2)'}
                />
              </div>
            )}

            {error && (
              <div style={{
                padding: '0.6rem 0.875rem',
                background: 'rgba(239,68,68,0.08)',
                border: '1px solid rgba(239,68,68,0.25)',
                borderRadius: '8px',
                color: '#fca5a5',
                fontSize: '0.82rem',
              }}>
                {error}
              </div>
            )}

            <button
              type="submit" disabled={loading}
              style={{
                marginTop: '0.25rem',
                padding: '0.8rem',
                border: 'none',
                borderRadius: '10px',
                fontSize: '0.9rem',
                fontWeight: 600,
                cursor: loading ? 'not-allowed' : 'pointer',
                background: loading
                  ? 'rgba(59,130,246,0.3)'
                  : 'linear-gradient(135deg, #3b82f6 0%, #06b6d4 100%)',
                color: '#fff',
                boxShadow: loading ? 'none' : '0 0 24px rgba(59,130,246,0.4)',
                transition: 'all 0.2s',
                letterSpacing: '0.01em',
              }}
            >
              {loading ? 'Please wait...' : mode === 'login' ? 'Sign In →' : 'Create Account →'}
            </button>

            <div style={{
              borderTop: '1px solid rgba(255,255,255,0.06)',
              paddingTop: '1rem',
              textAlign: 'center',
              fontSize: '0.82rem',
              color: '#4a7fa5',
            }}>
              {mode === 'login' ? "Don't have an account? " : 'Already have an account? '}
              <button
                type="button"
                onClick={() => { setMode(mode === 'login' ? 'signup' : 'login'); setError('') }}
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: '#60a5fa', fontWeight: 600, fontSize: '0.82rem', padding: 0,
                  textDecoration: 'underline', textUnderlineOffset: '2px',
                }}
              >
                {mode === 'login' ? 'Sign Up' : 'Sign In'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
