import React, { useState } from 'react'
import { api, setToken } from '../api/client'

const GoogleIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23z"/><path fill="#FBBC05" d="M5.84 14.1a6.6 6.6 0 0 1 0-4.2V7.06H2.18a11 11 0 0 0 0 9.88l3.66-2.84z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1A11 11 0 0 0 2.18 7.06l3.66 2.84C6.71 7.3 9.14 5.38 12 5.38z"/></svg>
)

const GitHubIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M12 1a11 11 0 0 0-3.48 21.44c.55.1.75-.24.75-.53v-1.86c-3.06.67-3.71-1.47-3.71-1.47-.5-1.27-1.22-1.61-1.22-1.61-1-.68.08-.67.08-.67 1.1.08 1.68 1.13 1.68 1.13.98 1.68 2.57 1.19 3.2.91.1-.71.38-1.19.69-1.46-2.44-.28-5.01-1.22-5.01-5.43 0-1.2.43-2.18 1.13-2.95-.11-.28-.49-1.4.11-2.91 0 0 .92-.3 3.02 1.13a10.5 10.5 0 0 1 5.5 0c2.1-1.43 3.02-1.13 3.02-1.13.6 1.51.22 2.63.11 2.91.7.77 1.13 1.75 1.13 2.95 0 4.22-2.58 5.15-5.03 5.42.4.34.75 1.01.75 2.04v3.03c0 .29.2.64.76.53A11 11 0 0 0 12 1z"/></svg>
)

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
      alignItems: 'center',
      justifyContent: 'center',
      background: 'var(--bg)',
      padding: '2rem',
    }}>
      <div className="panel fade-in" style={{ width: '100%', maxWidth: '400px', padding: '2.5rem' }}>

        <div style={{ marginBottom: '1.75rem', textAlign: 'center' }}>
          <span className="logo-word" style={{ fontSize: '1.75rem' }}>Hire<em>OS</em></span>
        </div>

        <div style={{ marginBottom: '1.5rem' }}>
          <h2 style={{
            fontSize: '1.4rem', fontWeight: 700,
            fontFamily: 'var(--font-display)',
            color: 'var(--fg)', marginBottom: '0.35rem',
          }}>
            {mode === 'login' ? 'Welcome back' : mode === 'reset' ? 'Reset Password' : 'Get started free'}
          </h2>
          <p className="text-muted text-sm">
            {mode === 'login'
              ? 'Sign in to your HireOS account'
              : mode === 'reset'
                ? 'Set a new password for your account'
                : 'Create your account in seconds'}
          </p>
        </div>

        <form onSubmit={submit} className="flex flex-col gap-md">
          <div className="form-group">
            <label className="form-label">Email address</label>
            <input
              type="email" value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder="you@example.com"
              required autoFocus
            />
          </div>

          <div className="form-group">
            <label className="form-label">Password</label>
            <input
              type="password" value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder={mode === 'signup' || mode === 'reset' ? 'Minimum 6 characters' : '••••••••'}
              required
            />
          </div>

          {mode === 'login' && (
            <div style={{ textAlign: 'right', marginTop: '-0.5rem' }}>
              <button type="button" onClick={() => { setMode('reset'); setError(''); setPassword(''); setConfirm(''); }} className="btn btn-ghost btn-sm" style={{ padding: 0 }}>Forgot password?</button>
            </div>
          )}

          {(mode === 'signup' || mode === 'reset') && (
            <div className="form-group">
              <label className="form-label">Confirm password</label>
              <input
                type="password" value={confirm}
                onChange={e => setConfirm(e.target.value)}
                placeholder="••••••••" required
              />
            </div>
          )}

          {error && (
            <div className="alert alert-danger">
              {error}
            </div>
          )}

          <button
            type="submit" disabled={loading}
            className="btn btn-primary"
            style={{ width: '100%', marginTop: '0.25rem', padding: '0.7rem' }}
          >
            {loading ? 'Please wait...' : mode === 'login' ? 'Sign In →' : mode === 'reset' ? 'Reset Password →' : 'Create Account →'}
          </button>

          {/* SSO divider */}
          <div className="flex items-center gap-sm" style={{ margin: '0.25rem 0' }}>
            <div style={{ flex: 1, height: '1px', background: 'var(--surface-border)' }} />
            <span className="text-subtle text-xs">OR CONTINUE WITH</span>
            <div style={{ flex: 1, height: '1px', background: 'var(--surface-border)' }} />
          </div>

          {/* SSO buttons */}
          <div className="flex gap-sm">
            <button
              type="button" onClick={() => ssoLogin('google')}
              className="btn btn-outline"
              style={{ flex: 1 }}
            >
              <GoogleIcon />
              Google
            </button>
            <button
              type="button" onClick={() => ssoLogin('github')}
              className="btn btn-outline"
              style={{ flex: 1 }}
            >
              <GitHubIcon />
              GitHub
            </button>
          </div>

          <div className="text-sm text-muted" style={{ borderTop: '1px solid var(--surface-border)', paddingTop: '1rem', textAlign: 'center' }}>
            {mode === 'login' ? "Don't have an account? " : mode === 'reset' ? "Remembered your password? " : 'Already have an account? '}
            <button
              type="button"
              onClick={() => { setMode(mode === 'login' ? 'signup' : 'login'); setError('') }}
              className="btn btn-ghost btn-sm"
              style={{ padding: 0, textDecoration: 'underline', textUnderlineOffset: '2px' }}
            >
              {mode === 'login' ? 'Sign Up' : 'Sign In'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
