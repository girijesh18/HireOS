import React, { useState } from 'react'
import { api, setToken } from '../api/client'

const SSO_ERRORS = {
  invalid_state: 'Sign-in session expired. Please try again.',
  email_unverified: 'Your provider account has no verified email.',
  token_exchange_failed: 'Could not complete sign-in with the provider.',
  oauth_failed: 'Could not reach the sign-in provider. Try again.',
  access_denied: 'Sign-in was cancelled.',
}

export default function Auth({ onAuth, ssoError }) {
  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState(ssoError ? (SSO_ERRORS[ssoError] || 'Sign-in failed. Please try again.') : '')
  const [loading, setLoading] = useState(false)

  const ssoLogin = (provider) => { window.location.href = `/auth/oauth/${provider}/login` }

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    if ((mode === 'signup' || mode === 'reset') && password !== confirm) { setError('Passwords do not match'); return }
    setLoading(true)
    try {
      if (mode === 'reset') {
        await api.resetPassword(email, password)
        setMode('login')
        setError('Password reset successfully. Please login.')
        setLoading(false)
        return
      }
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
        backgroundPosition: 'center center',
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
        background: 'linear-gradient(160deg, #051022 0%, #091830 50%, #060c1a 100%)',
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
              {mode === 'login' ? 'Welcome back' : mode === 'reset' ? 'Reset Password' : 'Get started free'}
            </h2>
            <p style={{ color: '#4a7fa5', fontSize: '0.875rem' }}>
              {mode === 'login'
                ? 'Sign in to your HireOS account'
                : mode === 'reset'
                  ? 'Set a new password for your account'
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
                placeholder={mode === 'signup' || mode === 'reset' ? 'Minimum 6 characters' : '••••••••'}
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

            {mode === 'login' && (
              <div style={{ textAlign: 'right', marginTop: '-0.5rem' }}>
                <button type="button" onClick={() => { setMode('reset'); setError(''); setPassword(''); setConfirm(''); }} style={{ background: 'none', border: 'none', color: '#60a5fa', fontSize: '0.75rem', cursor: 'pointer', padding: 0 }}>Forgot password?</button>
              </div>
            )}

            {(mode === 'signup' || mode === 'reset') && (
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
              {loading ? 'Please wait...' : mode === 'login' ? 'Sign In →' : mode === 'reset' ? 'Reset Password →' : 'Create Account →'}
            </button>

            {/* SSO divider */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', margin: '0.25rem 0' }}>
              <div style={{ flex: 1, height: '1px', background: 'rgba(255,255,255,0.08)' }} />
              <span style={{ fontSize: '0.72rem', color: '#4a7fa5', letterSpacing: '0.04em' }}>OR CONTINUE WITH</span>
              <div style={{ flex: 1, height: '1px', background: 'rgba(255,255,255,0.08)' }} />
            </div>

            {/* SSO buttons */}
            <div style={{ display: 'flex', gap: '0.75rem' }}>
              <button
                type="button" onClick={() => ssoLogin('google')}
                style={{
                  flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem',
                  padding: '0.7rem', borderRadius: '10px', cursor: 'pointer',
                  background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.12)',
                  color: '#e2e8f0', fontSize: '0.85rem', fontWeight: 600,
                }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23z"/><path fill="#FBBC05" d="M5.84 14.1a6.6 6.6 0 0 1 0-4.2V7.06H2.18a11 11 0 0 0 0 9.88l3.66-2.84z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1A11 11 0 0 0 2.18 7.06l3.66 2.84C6.71 7.3 9.14 5.38 12 5.38z"/></svg>
                Google
              </button>
              <button
                type="button" onClick={() => ssoLogin('github')}
                style={{
                  flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem',
                  padding: '0.7rem', borderRadius: '10px', cursor: 'pointer',
                  background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.12)',
                  color: '#e2e8f0', fontSize: '0.85rem', fontWeight: 600,
                }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="#fff"><path d="M12 1a11 11 0 0 0-3.48 21.44c.55.1.75-.24.75-.53v-1.86c-3.06.67-3.71-1.47-3.71-1.47-.5-1.27-1.22-1.61-1.22-1.61-1-.68.08-.67.08-.67 1.1.08 1.68 1.13 1.68 1.13.98 1.68 2.57 1.19 3.2.91.1-.71.38-1.19.69-1.46-2.44-.28-5.01-1.22-5.01-5.43 0-1.2.43-2.18 1.13-2.95-.11-.28-.49-1.4.11-2.91 0 0 .92-.3 3.02 1.13a10.5 10.5 0 0 1 5.5 0c2.1-1.43 3.02-1.13 3.02-1.13.6 1.51.22 2.63.11 2.91.7.77 1.13 1.75 1.13 2.95 0 4.22-2.58 5.15-5.03 5.42.4.34.75 1.01.75 2.04v3.03c0 .29.2.64.76.53A11 11 0 0 0 12 1z"/></svg>
                GitHub
              </button>
            </div>

            <div style={{
              borderTop: '1px solid rgba(255,255,255,0.06)',
              paddingTop: '1rem',
              textAlign: 'center',
              fontSize: '0.82rem',
              color: '#4a7fa5',
            }}>
              {mode === 'login' ? "Don't have an account? " : mode === 'reset' ? "Remembered your password? " : 'Already have an account? '}
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
