// Platform controls: which provider and model the free tier runs on, the keys
// behind them, and what free users are costing.
//
// The model list comes from each provider's own API, not a constant in here --
// a hardcoded id table goes stale silently and fails at a user's request time
// rather than ours.
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import AdminUsers from '../components/AdminUsers'

const LABELS = {
  gemini: 'Google Gemini',
  anthropic: 'Anthropic (Claude)',
  openai: 'OpenAI',
  openrouter: 'OpenRouter',
}

function ProviderCard({ p, onSaved }) {
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const save = async () => {
    setBusy(true); setMsg('')
    try {
      const res = await api.adminSetKey(p.provider, key)
      setKey('')
      setMsg(res.error ? res.error : `${res.model_count} models available`)
      onSaved()
    } catch (e) { setMsg(e.message) }
    setBusy(false)
  }

  return (
    <div className="panel" style={{
      padding: '1rem', display: 'flex', flexDirection: 'column', gap: '0.6rem',
      borderLeft: `3px solid ${p.active ? 'var(--primary)' : p.configured ? 'var(--success)' : 'var(--surface-border)'}`,
    }}>
      <div className="flex items-center justify-between" style={{ gap: '0.5rem' }}>
        <strong style={{ fontSize: '0.95rem' }}>{LABELS[p.provider] || p.provider}</strong>
        {p.active && <span className="badge" style={{ background: 'var(--primary)', color: 'var(--primary-fg)' }}>Active</span>}
      </div>
      <div style={{ fontSize: '0.8rem', color: p.error ? 'var(--danger)' : 'var(--fg-muted)' }}>
        {p.error ? p.error
          : p.configured ? `${p.key_masked} · ${p.model_count} models`
          : p.provider === 'openrouter' ? `No key · ${p.model_count} models listed publicly`
          : 'No key configured'}
      </div>
      <div className="flex items-center gap-sm">
        <input type="password" value={key} onChange={e => setKey(e.target.value)}
          placeholder={p.configured ? 'Replace key…' : 'Paste API key'}
          style={{
            flex: 1, padding: '0.45rem 0.6rem', fontSize: '0.85rem',
            borderRadius: 'var(--radius)', border: '1px solid var(--surface-border)',
            background: 'var(--bg)', color: 'var(--fg)',
          }} />
        <button className="btn btn-outline btn-sm" onClick={save} disabled={busy || !key.trim()}>
          {busy ? '…' : 'Save'}
        </button>
      </div>
      {msg && <div style={{ fontSize: '0.78rem', color: 'var(--fg-muted)' }}>{msg}</div>}
    </div>
  )
}

export default function Admin() {
  const [providers, setProviders] = useState(null)
  const [active, setActive] = useState({ provider: '', model: '' })
  const [provider, setProvider] = useState('')
  const [models, setModels] = useState([])
  const [model, setModel] = useState('')
  const [modelError, setModelError] = useState('')
  const [tab, setTab] = useState('users')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loadingModels, setLoadingModels] = useState(false)

  const load = () => {
    api.adminProviders()
      .then(d => {
        setProviders(d.providers)
        setActive({ provider: d.active_provider, model: d.active_model })
        setProvider(cur => cur || d.active_provider)
      })
      .catch(e => setError(e.message))
  }

  useEffect(load, [])

  const loadModels = async (p, refresh = false) => {
    if (!p) return
    await Promise.resolve()      // keep the state writes out of an effect body
    setLoadingModels(true); setModelError('')
    try {
      const d = await api.adminModels(p, refresh)
      setModels(d.models)
      setModelError(d.error || '')
      setModel(p === active.provider ? active.model : (d.models[0]?.id || ''))
    } catch (e) {
      setModelError(e.message)
    }
    setLoadingModels(false)
  }

  // The rule can't see past the async boundary inside loadModels, and fetching
  // the catalogue when the provider changes is exactly what an effect is for.
  // eslint-disable-next-line react-hooks/set-state-in-effect, react-hooks/exhaustive-deps
  useEffect(() => { loadModels(provider) }, [provider, active.provider])

  const saveModel = async () => {
    setError(''); setNotice('')
    try {
      await api.adminSetPlatformModel(provider, model)
      setNotice(`Free tier now runs on ${provider} · ${model}`)
      load()
    } catch (e) { setError(e.message) }
  }

  if (!providers) {
    return <div className="panel" style={{ padding: '1.5rem' }}>
      {error ? <div className="alert alert-danger">{error}</div>
             : <span className="text-muted text-sm">Loading…</span>}
    </div>
  }

  const TABS = [
    { id: 'users', label: 'Users & usage' },
    { id: 'models', label: 'Model & connectors' },
  ]

  return (
    <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      <div className="flex gap-sm" style={{ borderBottom: '1px solid var(--surface-border)' }}>
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            style={{
              padding: '0.6rem 0.9rem', background: 'none', border: 'none', cursor: 'pointer',
              fontSize: '0.9rem', fontWeight: tab === t.id ? 600 : 400,
              color: tab === t.id ? 'var(--primary)' : 'var(--fg-muted)',
              borderBottom: `2px solid ${tab === t.id ? 'var(--primary)' : 'transparent'}`,
              marginBottom: -1,
            }}>
            {t.label}
          </button>
        ))}
      </div>

      {notice && <div className="alert alert-info">{notice}</div>}
      {error && <div className="alert alert-danger">{error}</div>}

      {tab === 'users' && <AdminUsers />}

      {tab === 'models' && (
      <>
      {/* Active model */}
      <div className="panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div>
          <h3 style={{ fontSize: '1.05rem' }}>Free-tier model</h3>
          <p style={{ fontSize: '0.85rem', color: 'var(--fg-muted)', marginTop: 4 }}>
            Every user without their own API key runs on this. Currently{' '}
            <strong style={{ color: 'var(--fg)' }}>{active.provider} · {active.model || '(unset)'}</strong>.
          </p>
        </div>
        <div className="flex items-center gap-sm" style={{ flexWrap: 'wrap' }}>
          <select value={provider} onChange={e => setProvider(e.target.value)}
            style={{ padding: '0.45rem 0.6rem', borderRadius: 'var(--radius)', border: '1px solid var(--surface-border)', background: 'var(--bg)', color: 'var(--fg)' }}>
            {providers.map(p => <option key={p.provider} value={p.provider}>{LABELS[p.provider] || p.provider}</option>)}
          </select>
          <select value={model} onChange={e => setModel(e.target.value)} disabled={loadingModels || !models.length}
            style={{ flex: 1, minWidth: 260, padding: '0.45rem 0.6rem', borderRadius: 'var(--radius)', border: '1px solid var(--surface-border)', background: 'var(--bg)', color: 'var(--fg)' }}>
            {loadingModels && <option>Loading…</option>}
            {!loadingModels && !models.length && <option>No models — check the key</option>}
            {models.map(m => (
              <option key={m.id} value={m.id}>{m.label}{m.extra ? ` — ${m.extra}` : ''}</option>
            ))}
          </select>
          <button className="btn btn-ghost btn-sm" onClick={() => loadModels(provider, true)} disabled={loadingModels}>
            Refresh
          </button>
          <button className="btn btn-primary btn-sm" onClick={saveModel} disabled={!model || loadingModels}>
            Save
          </button>
        </div>
        {modelError && <div style={{ fontSize: '0.8rem', color: 'var(--danger)' }}>{modelError}</div>}
      </div>

      {/* Connectors */}
      <div>
        <h3 style={{ fontSize: '1.05rem', marginBottom: '0.75rem' }}>Connectors</h3>
        <div style={{ display: 'grid', gap: '0.75rem', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
          {providers.map(p => <ProviderCard key={p.provider} p={p} onSaved={load} />)}
        </div>
      </div>
      </>
      )}

    </div>
  )
}
