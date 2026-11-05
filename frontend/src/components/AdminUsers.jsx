// Every account, and everything worth watching about one of them.
// Deliberately shows no key material: only which providers a user configured.
import { Fragment, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'

const num = (n) => (n || 0).toLocaleString()

function ago(ts) {
  if (!ts) return 'never'
  const then = new Date(ts.replace(' ', 'T'))
  const mins = Math.floor((Date.now() - then.getTime()) / 60000)
  if (Number.isNaN(mins)) return '—'
  if (mins < 60) return `${Math.max(mins, 0)}m ago`
  if (mins < 1440) return `${Math.floor(mins / 60)}h ago`
  return `${Math.floor(mins / 1440)}d ago`
}

function Stat({ label, value, tone }) {
  return (
    <div className="panel" style={{ padding: '0.85rem 1rem', flex: '1 1 130px', minWidth: 120 }}>
      <div style={{ fontFamily: 'var(--font-serif)', fontSize: '1.5rem', fontWeight: 400, color: tone || 'var(--fg)' }}>{value}</div>
      <div style={{ fontSize: '0.72rem', color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</div>
    </div>
  )
}

function Bar({ pct, exhausted }) {
  return (
    <div title={`${pct}% of the free budget`} style={{
      height: 6, width: 74, background: 'var(--surface-2)',
      overflow: 'hidden', border: '1px solid var(--surface-border)', display: 'inline-block',
      verticalAlign: 'middle',
    }}>
      <div style={{
        width: `${Math.min(pct, 100)}%`, height: '100%',
        background: exhausted ? 'var(--danger)' : pct >= 80 ? 'var(--warning)' : 'var(--primary)',
      }} />
    </div>
  )
}

const inputStyle = {
  width: 165, padding: '0.4rem 0.55rem', fontSize: '0.85rem',
  borderRadius: 'var(--radius)', border: '1px solid var(--surface-border)',
  background: 'var(--bg)', color: 'var(--fg)',
}

// Blank means "use the default"; anything else must be a whole number >= 0.
const toLimit = (v) => (String(v).trim() === '' ? null : Math.max(0, parseInt(v, 10) || 0))

function DefaultLimits({ limits, onSaved }) {
  const [inp, setInp] = useState(limits.input)
  const [out, setOut] = useState(limits.output)
  const [msg, setMsg] = useState('')
  const save = async () => {
    try {
      await api.adminSetDefaultLimits(toLimit(inp) ?? 0, toLimit(out) ?? 0)
      setMsg('Saved'); onSaved()
    } catch (e) { setMsg(e.message) }
  }
  return (
    <div className="panel" style={{ padding: '1rem 1.25rem', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.6rem' }}>
      <strong style={{ fontSize: '0.9rem', marginRight: '0.5rem' }}>Default token budget per user</strong>
      <label style={{ fontSize: '0.8rem', color: 'var(--fg-muted)' }}>Input tokens
        <input type="number" min="0" value={inp} onChange={e => setInp(e.target.value)} style={{ ...inputStyle, marginLeft: 6 }} />
      </label>
      <label style={{ fontSize: '0.8rem', color: 'var(--fg-muted)' }}>Output tokens
        <input type="number" min="0" value={out} onChange={e => setOut(e.target.value)} style={{ ...inputStyle, marginLeft: 6 }} />
      </label>
      <button className="btn btn-primary btn-sm" onClick={save}>Save</button>
      <span style={{ fontSize: '0.78rem', color: 'var(--fg-muted)' }}>{msg || 'Applies to everyone on the platform key without their own limit.'}</span>
    </div>
  )
}

function Controls({ d, onChanged, onDeleted }) {
  const [inp, setInp] = useState(d.custom_limits ? d.token_limits.input : '')
  const [out, setOut] = useState(d.custom_limits ? d.token_limits.output : '')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const run = async (fn, done) => {
    setBusy(true); setMsg('')
    try { await fn(); setMsg(done); onChanged() } catch (e) { setMsg(e.message) } finally { setBusy(false) }
  }
  const saveLimits = () => {
    const i = toLimit(inp), o = toLimit(out)
    run(() => api.adminUpdateUser(d.id, { set_limits: true, token_limit_input: i, token_limit_output: o }),
      i === null && o === null ? 'Back on the default budget' : 'Limit saved')
  }
  const remove = async () => {
    if (window.prompt(`This permanently deletes ${d.email} and all their jobs, resumes and settings.\nType the email to confirm:`) !== d.email) return
    setBusy(true)
    try { await api.adminDeleteUser(d.id); onDeleted() } catch (e) { setMsg(e.message); setBusy(false) }
  }

  return (
    <Section title="Controls">
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.6rem' }}>
        <span style={{ fontSize: '0.82rem', color: 'var(--fg-muted)' }}>Token budget</span>
        <label style={{ fontSize: '0.8rem', color: 'var(--fg-muted)' }}>Input tokens
          <input type="number" min="0" placeholder={`default ${num(d.default_limits?.input ?? d.token_limits.input)}`} value={inp}
            onChange={e => setInp(e.target.value)} style={{ ...inputStyle, marginLeft: 6 }} aria-label="Input token limit" />
        </label>
        <label style={{ fontSize: '0.8rem', color: 'var(--fg-muted)' }}>Output tokens
          <input type="number" min="0" placeholder={`default ${num(d.default_limits?.output ?? d.token_limits.output)}`} value={out}
            onChange={e => setOut(e.target.value)} style={{ ...inputStyle, marginLeft: 6 }} aria-label="Output token limit" />
        </label>
        <button className="btn btn-outline btn-sm" disabled={busy} onClick={saveLimits}>Save limit</button>
        <button className="btn btn-ghost btn-sm" disabled={busy}
          onClick={() => run(() => api.adminUpdateUser(d.id, { reset_usage: true }), 'Usage reset to 0')}>Reset usage</button>
      </div>
      <div style={{ fontSize: '0.75rem', color: 'var(--fg-subtle)' }}>
        Input = what we send the model, output = what it writes. Leave both blank to use the default. Now: {num(d.token_limits.input)} / {num(d.token_limits.output)}{d.custom_limits ? ' (custom)' : ' (default)'}.
      </div>
      {!d.is_admin && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.6rem', marginTop: '0.4rem' }}>
          <button className="btn btn-outline btn-sm" disabled={busy}
            onClick={() => run(() => api.adminUpdateUser(d.id, { blocked: !d.blocked }), d.blocked ? 'Unblocked' : 'Blocked')}>
            {d.blocked ? 'Unblock user' : 'Block user'}
          </button>
          <button className="btn btn-sm" disabled={busy} onClick={remove}
            style={{ background: 'var(--danger)', color: '#fff', border: '1px solid var(--danger)' }}>Delete user</button>
        </div>
      )}
      {msg && <span style={{ fontSize: '0.8rem', color: 'var(--fg-muted)' }}>{msg}</span>}
    </Section>
  )
}

function Section({ title, children, empty }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      <strong style={{ fontSize: '0.8rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--fg-muted)' }}>{title}</strong>
      {children || <span style={{ fontSize: '0.82rem', color: 'var(--fg-subtle)' }}>{empty}</span>}
    </div>
  )
}

function Rows({ items, cols }) {
  if (!items?.length) return null
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
        <tbody>
          {items.map((it, i) => (
            <tr key={i} style={{ borderTop: '1px solid var(--surface-border)' }}>
              {cols.map((c, j) => (
                <td key={j} style={{ padding: '0.4rem 0.5rem', color: j ? 'var(--fg-muted)' : 'var(--fg)', whiteSpace: 'nowrap' }}>
                  {c(it)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function UserDetail({ id, onClose, onChanged }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [rev, setRev] = useState(0)

  useEffect(() => {
    // Clearing before refetching is the point: opening a second user must not
    // show the first one's numbers while the request is in flight.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setD(null); setErr('')
    api.adminUserDetail(id).then(setD).catch(e => setErr(e.message))
  }, [id, rev])

  const changed = () => { setRev(r => r + 1); onChanged() }

  return (
    <div className="panel" style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
      <div className="flex items-center justify-between" style={{ gap: '1rem' }}>
        <strong style={{ fontSize: '1rem' }}>{d?.email || `User #${id}`}{d?.blocked && <span className="badge" style={{ marginLeft: 8, background: 'var(--danger)', color: '#fff' }}>blocked</span>}</strong>
        <button className="btn btn-ghost btn-sm" onClick={onClose}>Close</button>
      </div>
      {err && <div className="alert alert-danger">{err}</div>}
      {!d && !err && <span className="text-muted text-sm">Loading…</span>}
      {d && (
        <>
          <div className="flex" style={{ gap: '0.6rem', flexWrap: 'wrap' }}>
            <Stat label="Input tokens" value={num(d.input_tokens)} />
            <Stat label="Output tokens" value={num(d.output_tokens)} />
            <Stat label="Jobs" value={d.counts.jobs} />
            <Stat label="Resumes" value={d.counts.resumes} />
            <Stat label="Stories" value={d.counts.stories} />
            <Stat label="Last active" value={ago(d.last_active)} />
          </div>

          <Section title="Account">
            <div style={{ fontSize: '0.85rem', color: 'var(--fg-muted)', lineHeight: 1.7 }}>
              Signed up <strong style={{ color: 'var(--fg)' }}>{d.created_at?.slice(0, 10) || '—'}</strong> via {d.signup_method}
              {' · '}plan <strong style={{ color: 'var(--fg)' }}>{d.plan}</strong>{d.plan_status ? ` (${d.plan_status})` : ''}
              {' · '}{d.own_providers.length
                ? <>own keys: <strong style={{ color: 'var(--fg)' }}>{d.own_providers.join(', ')}</strong> (not metered)</>
                : <>on the platform key{d.quota_exhausted ? <strong style={{ color: 'var(--danger)' }}> — quota spent</strong> : ''}</>}
              {' · '}onboarding {d.onboarding_done ? 'done' : 'not finished'} ({d.profile_facts} facts)
              {d.is_admin && ' · admin'}
            </div>
          </Section>

          <Controls key={rev} d={d} onChanged={changed} onDeleted={() => { onChanged(); onClose() }} />

          <Section title={`Profile facts (${d.facts.length})`} empty="Nothing captured yet.">
            {d.facts.length > 0 && (
              <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
                {d.facts.map(f => (
                  <li key={f.key} style={{ fontSize: '0.82rem', color: 'var(--fg-muted)' }}>
                    <strong style={{ color: 'var(--fg)' }}>{f.label || f.key}:</strong> {f.value}
                    {f.source === 'document' && <span style={{ color: 'var(--fg-subtle)' }}> (from a document)</span>}
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title={`Documents (${d.documents.length})`} empty="No documents uploaded.">
            <Rows items={d.documents} cols={[
              x => x.name, x => x.type, x => `${num(x.chars)} chars`,
              x => (x.active ? 'active' : 'inactive'), x => x.created_at?.slice(0, 10),
            ]} />
          </Section>

          <Section title={`Jobs (${d.counts.jobs})`} empty="No jobs tracked.">
            {Object.keys(d.job_status_mix).length > 0 && (
              <div style={{ fontSize: '0.8rem', color: 'var(--fg-muted)', marginBottom: '0.3rem' }}>
                {Object.entries(d.job_status_mix).map(([k, v]) => `${k}: ${v}`).join(' · ')}
              </div>
            )}
            <Rows items={d.jobs} cols={[
              x => x.company, x => x.title, x => x.status,
              x => (x.match_score != null ? `${x.match_score}%` : '—'), x => x.created_at?.slice(0, 10),
            ]} />
          </Section>

          <Section title={`Resumes (${d.counts.resumes})`} empty="No resumes generated.">
            <Rows items={d.resumes} cols={[
              x => `job ${x.job_id} v${x.version}`, x => x.llm_used || '—',
              x => `${num(x.chars)} chars`, x => (x.ats_total != null ? `ATS ${x.ats_total}` : '—'),
              x => x.created_at?.slice(0, 10),
            ]} />
          </Section>

          <Section title="Agent tasks" empty="No agent runs.">
            <Rows items={d.tasks} cols={[
              x => x.task_type,
              x => <span style={{ color: x.status === 'failed' ? 'var(--danger)' : 'inherit' }}>{x.status}</span>,
              x => (x.error || '').slice(0, 60), x => x.updated_at?.slice(0, 16).replace('T', ' '),
            ]} />
          </Section>

          <Section title="Recent activity" empty="Nothing yet.">
            <Rows items={d.events} cols={[
              x => x.type, x => (x.title || '').slice(0, 60), x => x.created_at?.slice(0, 16).replace('T', ' '),
            ]} />
          </Section>
        </>
      )}
    </div>
  )
}

export default function AdminUsers() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [q, setQ] = useState('')
  const [sort, setSort] = useState('tokens')
  const [openId, setOpenId] = useState(null)

  const load = () => api.adminUsers().then(setData).catch(e => setError(e.message))
  useEffect(() => { load() }, [])

  const rows = useMemo(() => {
    if (!data) return []
    const needle = q.trim().toLowerCase()
    const list = data.users.filter(u => !needle || (u.email || '').toLowerCase().includes(needle))
    const by = {
      tokens: (a, b) => (b.input_tokens + b.output_tokens) - (a.input_tokens + a.output_tokens),
      recent: (a, b) => String(b.last_active || '').localeCompare(String(a.last_active || '')),
      newest: (a, b) => b.id - a.id,
      jobs: (a, b) => b.counts.jobs - a.counts.jobs,
    }
    return [...list].sort(by[sort] || by.tokens)
  }, [data, q, sort])

  if (error) return <div className="alert alert-danger">{error}</div>
  if (!data) return <div className="panel" style={{ padding: '1.5rem' }}><span className="text-muted text-sm">Loading users…</span></div>

  const t = data.totals
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div className="flex" style={{ gap: '0.6rem', flexWrap: 'wrap' }}>
        <Stat label="Accounts" value={t.accounts} />
        <Stat label="Active" value={t.active} />
        <Stat label="On our key" value={t.on_platform_key} />
        <Stat label="Pro" value={t.pro} />
        <Stat label="Quota spent" value={t.exhausted} tone={t.exhausted ? 'var(--danger)' : undefined} />
        <Stat label="Input tokens" value={num(t.input_tokens)} />
        <Stat label="Output tokens" value={num(t.output_tokens)} />
        <Stat label="Resumes" value={t.resumes} />
      </div>

      <DefaultLimits limits={data.limits} onSaved={load} />

      <div className="flex items-center gap-sm" style={{ flexWrap: 'wrap' }}>
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Filter by email…"
          style={{
            flex: 1, minWidth: 200, padding: '0.45rem 0.6rem', fontSize: '0.85rem',
            borderRadius: 'var(--radius)', border: '1px solid var(--surface-border)',
            background: 'var(--bg)', color: 'var(--fg)',
          }} />
        <select value={sort} onChange={e => setSort(e.target.value)}
          style={{ padding: '0.45rem 0.6rem', borderRadius: 'var(--radius)', border: '1px solid var(--surface-border)', background: 'var(--bg)', color: 'var(--fg)' }}>
          <option value="tokens">Most tokens</option>
          <option value="recent">Recently active</option>
          <option value="newest">Newest</option>
          <option value="jobs">Most jobs</option>
        </select>
        <span style={{ fontSize: '0.8rem', color: 'var(--fg-muted)' }}>{rows.length} shown</span>
      </div>

      <div className="panel" style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--fg-muted)' }}>
              {['Account', 'Plan', 'Key', 'Free budget', 'Tokens (in/out)', 'Jobs', 'Resumes', 'Profile', 'Last active', ''].map(h => (
                <th key={h} style={{ padding: '0.55rem 0.6rem', fontWeight: 600, whiteSpace: 'nowrap' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(u => (
              <Fragment key={u.id}>
              <tr style={{ borderTop: '1px solid var(--surface-border)' }}>
                <td style={{ padding: '0.5rem 0.6rem' }}>
                  {u.email}
                  {u.is_admin && <span className="badge" style={{ marginLeft: 6, background: 'var(--surface-2)', color: 'var(--fg-muted)' }}>admin</span>}
                  {u.blocked && <span className="badge" style={{ marginLeft: 6, background: 'var(--danger)', color: '#fff' }}>blocked</span>}
                  <div style={{ fontSize: '0.72rem', color: 'var(--fg-subtle)' }}>
                    #{u.id} · {u.signup_method} · joined {u.created_at?.slice(0, 10) || '—'}
                  </div>
                </td>
                <td style={{ padding: '0.5rem 0.6rem', color: 'var(--fg-muted)' }}>{u.plan}</td>
                <td style={{ padding: '0.5rem 0.6rem', color: 'var(--fg-muted)', whiteSpace: 'nowrap' }}>
                  {u.own_providers.length ? u.own_providers.join(', ') : 'platform'}
                </td>
                <td style={{ padding: '0.5rem 0.6rem' }}>
                  {u.own_providers.length
                    ? <span style={{ color: 'var(--fg-subtle)' }}>not metered</span>
                    : <><Bar pct={u.quota_pct} exhausted={u.quota_exhausted} />
                       <span style={{ marginLeft: 6, color: u.quota_exhausted ? 'var(--danger)' : 'var(--fg-muted)' }}>{u.quota_pct}%</span></>}
                </td>
                <td style={{ padding: '0.5rem 0.6rem', color: 'var(--fg-muted)', whiteSpace: 'nowrap' }}>
                  {num(u.input_tokens)} / {num(u.output_tokens)}
                </td>
                <td style={{ padding: '0.5rem 0.6rem', color: 'var(--fg-muted)' }}>{u.counts.jobs}</td>
                <td style={{ padding: '0.5rem 0.6rem', color: 'var(--fg-muted)' }}>{u.counts.resumes}</td>
                <td style={{ padding: '0.5rem 0.6rem', color: 'var(--fg-muted)', whiteSpace: 'nowrap' }}>
                  {u.onboarding_done ? `${u.profile_facts} facts` : <span style={{ color: 'var(--fg-subtle)' }}>not done</span>}
                </td>
                <td style={{ padding: '0.5rem 0.6rem', color: 'var(--fg-muted)', whiteSpace: 'nowrap' }}>{ago(u.last_active)}</td>
                <td style={{ padding: '0.5rem 0.6rem' }}>
                  <button className="btn btn-outline btn-sm"
                    onClick={() => setOpenId(openId === u.id ? null : u.id)}>
                    {openId === u.id ? 'Hide' : 'View'}
                  </button>
                </td>
              </tr>
              {openId === u.id && (
                <tr><td colSpan={10} style={{ padding: '0 0.6rem 0.8rem' }}>
                  <UserDetail id={u.id} onClose={() => setOpenId(null)} onChanged={load} />
                </td></tr>
              )}
              </Fragment>
            ))}
            {!rows.length && (
              <tr><td colSpan={10} style={{ padding: '1rem 0.6rem', color: 'var(--fg-muted)' }}>No accounts match.</td></tr>
            )}
          </tbody>
        </table>
      </div>

    </div>
  )
}
