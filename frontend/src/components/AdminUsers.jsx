// Every account, and everything worth watching about one of them.
// Deliberately shows no key material: only which providers a user configured.
import { useEffect, useMemo, useState } from 'react'
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
      <div style={{ fontSize: '1.35rem', fontWeight: 700, color: tone || 'var(--fg)' }}>{value}</div>
      <div style={{ fontSize: '0.72rem', color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</div>
    </div>
  )
}

function Bar({ pct, exhausted }) {
  return (
    <div title={`${pct}% of the free budget`} style={{
      height: 6, width: 74, borderRadius: 9999, background: 'var(--surface-2)',
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

function UserDetail({ id, onClose }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    // Clearing before refetching is the point: opening a second user must not
    // show the first one's numbers while the request is in flight.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setD(null); setErr('')
    api.adminUserDetail(id).then(setD).catch(e => setErr(e.message))
  }, [id])

  return (
    <div className="panel" style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
      <div className="flex items-center justify-between" style={{ gap: '1rem' }}>
        <strong style={{ fontSize: '1rem' }}>{d?.email || `User #${id}`}</strong>
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

  useEffect(() => { api.adminUsers().then(setData).catch(e => setError(e.message)) }, [])

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
              <tr key={u.id} style={{ borderTop: '1px solid var(--surface-border)' }}>
                <td style={{ padding: '0.5rem 0.6rem' }}>
                  {u.email}
                  {u.is_admin && <span className="badge" style={{ marginLeft: 6, background: 'var(--surface-2)', color: 'var(--fg-muted)' }}>admin</span>}
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
            ))}
            {!rows.length && (
              <tr><td colSpan={10} style={{ padding: '1rem 0.6rem', color: 'var(--fg-muted)' }}>No accounts match.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {openId && <UserDetail id={openId} onClose={() => setOpenId(null)} />}
    </div>
  )
}
