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
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: '#060c1a',
      backgroundImage: `
        radial-gradient(ellipse 90% 60% at 20% -10%, rgba(59,130,246,0.30) 0%, transparent 60%),
        radial-gradient(ellipse 70% 50% at 80% 110%, rgba(6,182,212,0.22) 0%, transparent 55%)
      `,
      padding: '1rem',
    }}>
      <div style={{
        width: '100%',
        maxWidth: '420px',
        background: 'rgba(15,20,32,0.85)',
        border: '1px solid rgba(59,130,246,0.2)',
        borderRadius: '20px',
        padding: '2.5rem 2rem',
        backdropFilter: 'blur(20px)',
        boxShadow: '0 0 0 1px rgba(59,130,246,0.08), 0 24px 64px rgba(0,0,0,0.4), 0 0 80px rgba(59,130,246,0.08)',
      }}>

        {/* Logo */}
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: '0.5rem' }}>
            <img src="/logo-full.png" alt="HireOS" style={{
              height: '56px', objectFit: 'contain',
              background: 'white', borderRadius: '12px', padding: '6px 14px',
              boxShadow: '0 4px 24px rgba(59,130,246,0.25)',
            }} />
          </div>
          <p style={{ color: 'var(--fg-subtle)', fontSize: '0.82rem' }}>
            Autonomous Job Applications
          </p>
        </div>

        {/* Mode tabs */}
        <div style={{
          display: 'flex',
          background: 'rgba(255,255,255,0.04)',
          borderRadius: '10px',
          padding: '4px',
          marginBottom: '1.75rem',
          border: '1px solid rgba(59,130,246,0.15)',
        }}>
          {['login', 'signup'].map(m => (
            <button
              key={m}
              onClick={() => { setMode(m); setError('') }}
              style={{
                flex: 1,
                padding: '0.5rem',
                border: 'none',
                borderRadius: '8px',
                fontSize: '0.875rem',
                fontWeight: 500,
                cursor: 'pointer',
                transition: 'all 0.2s',
                background: mode === m
                  ? 'linear-gradient(135deg, #3b82f6 0%, #06b6d4 100%)'
                  : 'transparent',
                color: mode === m ? '#fff' : 'var(--fg-muted)',
                boxShadow: mode === m ? '0 2px 12px rgba(59,130,246,0.35)' : 'none',
              }}
            >
              {m === 'login' ? 'Sign In' : 'Sign Up'}
            </button>
          ))}
        </div>

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
              style={{ borderColor: 'rgba(59,130,246,0.25)' }}
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
              style={{ borderColor: 'rgba(59,130,246,0.25)' }}
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
                style={{ borderColor: 'rgba(59,130,246,0.25)' }}
              />
            </div>
          )}

          {error && (
            <p style={{
              color: '#f87171',
              fontSize: '0.82rem',
              margin: 0,
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
              marginTop: '0.5rem',
              padding: '0.7rem',
              border: 'none',
              borderRadius: '10px',
              fontSize: '0.9rem',
              fontWeight: 600,
              cursor: loading ? 'not-allowed' : 'pointer',
              background: loading
                ? 'rgba(59,130,246,0.4)'
                : 'linear-gradient(135deg, #3b82f6 0%, #06b6d4 100%)',
              color: '#fff',
              boxShadow: loading ? 'none' : '0 4px 20px rgba(59,130,246,0.4)',
              transition: 'all 0.2s',
            }}
          >
            {loading ? 'Please wait...' : mode === 'login' ? 'Sign In' : 'Create Account'}
          </button>
        </form>
      </div>
    </div>
  )
}
