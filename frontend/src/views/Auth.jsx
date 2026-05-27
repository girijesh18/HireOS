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
    if (mode === 'signup' && password !== confirm) {
      setError('Passwords do not match')
      return
    }
    setLoading(true)
    try {
      const res = mode === 'login'
        ? await api.login(email, password)
        : await api.signup(email, password)
      setToken(res.token)
      onAuth(res.email)
    } catch (err) {
      setError(err.message)
    }
    setLoading(false)
  }

  return (
    <div style={{ minHeight: '100vh', display: 'flex', background: '#060c1a' }}>

      {/* Left — brand image */}
      <div style={{
        flex: '0 0 55%',
        backgroundImage: `url('/auth-bg.png')`,
        backgroundSize: 'cover',
        backgroundPosition: 'center',
        position: 'relative',
        overflow: 'hidden',
      }}>
        <div style={{
          position: 'absolute', inset: 0,
          background: 'linear-gradient(to right, transparent 70%, #060c1a 100%)',
        }} />
        <div style={{
          position: 'absolute', bottom: '3rem', left: '3rem', right: '4rem',
        }}>
          <h1 style={{
            fontSize: '2.5rem', fontWeight: 800, lineHeight: 1.2,
            fontFamily: 'Outfit, sans-serif',
            background: 'linear-gradient(135deg, #fff 0%, #a5d8ff 100%)',
            WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text',
            textShadow: 'none',
          }}>
            HireOS
          </h1>
          <p style={{ color: 'rgba(255,255,255,0.7)', fontSize: '1rem', marginTop: '0.5rem', fontWeight: 400 }}>
            Autonomous AI-powered job application tracking — from first look to offer.
          </p>
        </div>
      </div>

      {/* Right — form */}
      <div style={{
        flex: '0 0 45%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '2rem',
        background: '#060c1a',
      }}>
        <div style={{ width: '100%', maxWidth: '380px' }}>

          {/* Logo */}
          <div style={{ marginBottom: '2.5rem' }}>
            <img src="/logo-full.png" alt="HireOS" style={{
              height: '36px', objectFit: 'contain',
              background: 'white', borderRadius: '8px', padding: '4px 12px',
              boxShadow: '0 2px 16px rgba(59,130,246,0.2)',
            }} />
            <p style={{ color: 'var(--fg-subtle)', fontSize: '0.82rem', marginTop: '0.6rem' }}>
              Autonomous Job Applications
            </p>
          </div>

          <h2 style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--fg)', marginBottom: '0.4rem' }}>
            {mode === 'login' ? 'Welcome back' : 'Create account'}
          </h2>
          <p style={{ color: 'var(--fg-subtle)', fontSize: '0.85rem', marginBottom: '1.75rem' }}>
            {mode === 'login' ? 'Sign in to your HireOS account' : 'Start tracking your job applications'}
          </p>

          <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div className="form-group">
              <label className="form-label">Email</label>
              <input
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="you@example.com"
                required
                autoFocus
              />
            </div>

            <div className="form-group">
              <label className="form-label">Password</label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder={mode === 'signup' ? 'Min 6 characters' : '••••••••'}
                required
              />
            </div>

            {mode === 'signup' && (
              <div className="form-group">
                <label className="form-label">Confirm Password</label>
                <input
                  type="password"
                  value={confirm}
                  onChange={e => setConfirm(e.target.value)}
                  placeholder="••••••••"
                  required
                />
              </div>
            )}

            {error && (
              <p style={{
                color: '#f87171', fontSize: '0.82rem', margin: 0,
                padding: '0.5rem 0.75rem',
                background: 'rgba(239,68,68,0.1)',
                borderRadius: '8px',
                border: '1px solid rgba(239,68,68,0.2)',
              }}>
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={loading}
              style={{
                marginTop: '0.25rem',
                padding: '0.75rem',
                border: 'none',
                borderRadius: '10px',
                fontSize: '0.9rem',
                fontWeight: 600,
                cursor: loading ? 'not-allowed' : 'pointer',
                background: loading ? 'rgba(59,130,246,0.4)' : 'linear-gradient(135deg, #3b82f6 0%, #06b6d4 100%)',
                color: '#fff',
                boxShadow: loading ? 'none' : '0 4px 20px rgba(59,130,246,0.35)',
                transition: 'all 0.2s',
              }}
            >
              {loading ? 'Please wait...' : mode === 'login' ? 'Sign In' : 'Create Account'}
            </button>

            <p style={{ textAlign: 'center', fontSize: '0.82rem', color: 'var(--fg-subtle)', marginTop: '0.5rem' }}>
              {mode === 'login' ? "Don't have an account? " : 'Already have an account? '}
              <button
                type="button"
                onClick={() => { setMode(mode === 'login' ? 'signup' : 'login'); setError('') }}
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: '#3b82f6', fontWeight: 600, fontSize: '0.82rem', padding: 0,
                }}
              >
                {mode === 'login' ? 'Sign Up' : 'Sign In'}
              </button>
            </p>
          </form>
        </div>
      </div>
    </div>
  )
}
