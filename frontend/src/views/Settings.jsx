import React, { useState, useEffect } from 'react'
import { api } from '../api/client'
import { setCustomNvidiaModels } from '../llmOptions'
import BillingPanel from '../components/BillingPanel'

const LLM_PROVIDERS = [
  { key:'gemini_api_key', label:'Gemini API Key', provider:'Google Gemini', placeholder:'AIza...' },
  { key:'groq_api_key', label:'Groq API Key', provider:'Groq (Free Tier)', placeholder:'gsk_...' },
  { key:'openrouter_api_key', label:'OpenRouter API Key', provider:'OpenRouter (Free Models)', placeholder:'sk-or-...' },
  { key:'together_api_key', label:'Together AI Key', provider:'Together AI', placeholder:'...' },
  { key:'nvidia_api_key', label:'NVIDIA API Key', provider:'NVIDIA (MiniMax-M3)', placeholder:'nvapi-...' },
  { key:'ollama_base_url', label:'Ollama Base URL', provider:'Local Ollama', placeholder:'http://localhost:11434' },
]
// Catalogue entry -> picker label. `extra` is the provider's own note: an
// OpenRouter price ("free", "$0.20 in / $0.60 out per M") or a context window.
const modelLabel = (m) => (m.extra ? `${m.label} · ${m.extra}` : m.label)

// Free first, then cheapest per input token. Providers that publish no price
// (NVIDIA, Gemini) all sort equal and fall back to their own order.
const sortModels = (models) => [...models].sort((a, b) => {
  const cost = (m) => (m.extra === 'free' ? -1 : (m.price_in ?? Number.MAX_SAFE_INTEGER))
  return cost(a) - cost(b) || (a.label || a.id).localeCompare(b.label || b.id)
})

const GITHUB = [
  { key:'github_token', label:'GitHub Token', placeholder:'ghp_...' },
  { key:'github_username', label:'GitHub Username', placeholder:'your-username' },
]
const CAPTCHA = [
  { key:'twocaptcha_api_key', label:'2Captcha API Key', placeholder:'...' },
]
const STEALTH = [
  { key:'proxy_url', label:'Residential Proxy URL', placeholder:'http://user:pass@host:port' },
]
const HUNT_FILTERS = [
  { key:'hunt_keywords', label:'Job Keywords (comma separated)', placeholder:'Principal AI Engineer, ML Architect, Staff Data Scientist' },
  { key:'hunt_exclude', label:'Exclude Keywords', placeholder:'Staffing, Recruiter, Contract Only' },
  { key:'hunt_salary_min', label:'Minimum Salary ($)', placeholder:'180000' },
  { key:'hunt_locations', label:'Preferred Locations', placeholder:'Remote, San Francisco, New York' },
  { key:'hunt_remote_only', label:'Remote Only?', placeholder:'true or false' },
  { key:'hunt_platforms', label:'Target Platforms', placeholder:'linkedin, greenhouse, lever, workday' },
]

const DEFAULT_PDF_STYLE = {
  fontFamily: 'Cambria, Georgia, serif',
  fontSize: '10.5',
  sectionColor: '#2E74B5',
  marginTop: '1.4',
  marginBottom: '1.4',
  marginLeft: '1.2',
  marginRight: '1.2',
}

const FONT_OPTIONS = [
  { value: 'Cambria, Georgia, serif', label: 'Cambria (Serif)' },
  { value: 'Georgia, Times New Roman, serif', label: 'Georgia (Serif)' },
  { value: 'Arial, Helvetica, sans-serif', label: 'Arial (Sans-serif)' },
  { value: 'Calibri, Arial, sans-serif', label: 'Calibri (Sans-serif)' },
  { value: 'Garamond, Times New Roman, serif', label: 'Garamond (Serif)' },
]

export default function Settings() {
  const [activeCategory, setActiveCategory] = useState('resume')
  const [values, setValues] = useState({})
  const [pdfStyle, setPdfStyle] = useState(DEFAULT_PDF_STYLE)
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)
  const [providers, setProviders] = useState([])
  const [resumeComponents, setResumeComponents] = useState([])
  const [newTextName, setNewTextName] = useState('')
  const [newTextContent, setNewTextContent] = useState('')
  const [isAddingText, setIsAddingText] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [nvModels, setNvModels] = useState([])   // custom models: {id, label, provider}
  const [nvId, setNvId] = useState('')
  const [nvProvider, setNvProvider] = useState('nvidia')
  const [catalog, setCatalog] = useState([])      // live model list for nvProvider
  const [catalogErr, setCatalogErr] = useState('')
  const [catalogBusy, setCatalogBusy] = useState(false)
  const [mcpToken, setMcpToken] = useState(null)      // full token, only right after creation
  const [mcpExists, setMcpExists] = useState(false)
  const [mcpBusy, setMcpBusy] = useState(false)

  useEffect(() => {
    Promise.all([
      api.getSettings(),
      api.getProviders(),
      api.getResumeComponents()
    ]).then(([s, p, rc]) => {
      setValues(s);
      setProviders(p.available || []);
      setResumeComponents(rc);
      if (s.resume_pdf_style) {
        try { setPdfStyle({ ...DEFAULT_PDF_STYLE, ...JSON.parse(s.resume_pdf_style) }) } catch {}
      }
      try {
        const m = JSON.parse(s.custom_nvidia_models || '[]');
        setNvModels(m);
        setCustomNvidiaModels(m);   // mirror to localStorage for the model dropdowns
      } catch {}
    }).catch(err => {
      console.error("Failed to load settings:", err);
    }).finally(() => setLoading(false))
    api.getMcpToken().then(r => setMcpExists(!!r.exists)).catch(() => {})
  }, [])

  const generateMcpToken = async () => {
    setMcpBusy(true)
    try {
      const r = await api.createMcpToken()
      setMcpToken(r.token)
      setMcpExists(true)
    } catch (err) {
      console.error('MCP token generation failed:', err)
    }
    setMcpBusy(false)
  }

  const revokeMcpToken = async () => {
    setMcpBusy(true)
    try {
      await api.revokeMcpToken()
      setMcpToken(null)
      setMcpExists(false)
    } catch (err) {
      console.error('MCP token revoke failed:', err)
    }
    setMcpBusy(false)
  }

  const save = async () => {
    const merged = { ...values, resume_pdf_style: JSON.stringify(pdfStyle) }
    const settings = Object.entries(merged).map(([key, value]) => ({ key, value: value || '' }))
    try {
      await api.saveSettings(settings)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch (err) {
      alert(err.message)
    }
  }

  const handleFileUpload = async (e) => {
    const file = e.target.files[0]
    if (!file) return
    setIsUploading(true)
    try {
      const name = file.name.split('.')[0]
      const newComp = await api.uploadResumeFile(name, file)
      setResumeComponents(prev => [...prev, newComp])
    } catch (err) {
      alert("Upload failed: " + err.message)
    } finally {
      setIsUploading(false)
      e.target.value = null
    }
  }

  const addTextComponent = async () => {
    if (!newTextName || !newTextContent) return
    try {
      const newComp = await api.addTextResumeComponent({
        name: newTextName,
        content_text: newTextContent,
        type: 'text'
      })
      setResumeComponents(prev => [...prev, newComp])
      setNewTextName(''); setNewTextContent(''); setIsAddingText(false)
    } catch (err) {
      alert("Failed to add text block: " + err.message)
    }
  }

  const toggleComponent = async (id, isActive) => {
    try {
      const updated = await api.updateResumeComponent(id, { is_active: !isActive })
      setResumeComponents(prev => prev.map(c => c.id === id ? updated : c))
    } catch {}
  }

  const deleteComponent = async (id) => {
    if (!window.confirm("Delete this resume component?")) return
    try {
      await api.deleteResumeComponent(id)
      setResumeComponents(prev => prev.filter(c => c.id !== id))
    } catch {}
  }

  const migrateLegacyResume = async () => {
    const legacy = values.master_resume
    if (!legacy) return
    try {
      const newComp = await api.addTextResumeComponent({
        name: "Migrated Master Resume",
        content_text: legacy,
        type: 'text'
      })
      setResumeComponents(prev => [...prev, newComp])
      const newValues = { ...values, master_resume: '' }
      setValues(newValues)
      await api.saveSettings(Object.entries(newValues).map(([key, value]) => ({ key, value: value || '' })))
    } catch (err) {
      alert("Migration failed: " + err.message)
    }
  }

  // Live catalogue for the selected provider. The backend caches it for an
  // hour, so switching providers back and forth costs nothing.
  const loadCatalog = (provider, refresh = false) => {
    setCatalogBusy(true); setCatalogErr('')
    api.getProviderModels(provider, refresh)
      .then(r => { setCatalog(sortModels(r.models || [])); setCatalogErr(r.error || '') })
      .catch(e => { setCatalog([]); setCatalogErr(e.message) })
      .finally(() => setCatalogBusy(false))
  }
  useEffect(() => { loadCatalog(nvProvider) }, [nvProvider])

  const syncNvModels = (list) => {
    const json = JSON.stringify(list)
    setNvModels(list)
    setCustomNvidiaModels(list)                                    // localStorage → model dropdowns
    setValues(v => ({ ...v, custom_nvidia_models: json }))
    // Persist immediately so a page reload / Settings re-open doesn't wipe it
    // (hydration reads this key back from the server on mount).
    api.saveSettings([{ key: 'custom_nvidia_models', value: json }]).catch(() => {})
  }
  const addModel = (id, label) => {
    if (!id || nvModels.some(m => m.id === id && (m.provider || 'nvidia') === nvProvider)) return
    syncNvModels([...nvModels, { id, label: label || id, provider: nvProvider }])
  }

  // Search over both the display name and the id: people paste ids as often as
  // they type "llama". Capped so a 460-model list stays responsive.
  const q = nvId.trim().toLowerCase()
  const visibleCatalog = (q
    ? catalog.filter(m => m.id.toLowerCase().includes(q) || (m.label || '').toLowerCase().includes(q))
    : catalog).slice(0, 200)
  const removeNvModel = (id, provider) =>
    syncNvModels(nvModels.filter(m => !(m.id === id && (m.provider || 'nvidia') === provider)))

  const Field = ({ f }) => (
    <div className="form-group" key={f.key}>
      <label className="form-label">{f.label}</label>
      <input
        type={f.key.includes('key') || f.key.includes('token') || f.key.includes('secret') ? 'password' : 'text'}
        value={values[f.key] ?? ''}
        onChange={e => setValues(v => ({...v, [f.key]:e.target.value}))}
        placeholder={f.placeholder}
      />
      {f.provider && <span style={{ fontSize:'0.7rem', color:'var(--fg-subtle)', marginTop:2 }}>Provider: {f.provider}</span>}
    </div>
  )

  const Section = ({ title, description, fields }) => (
    <div className="panel" style={{ padding:'1.5rem', display:'flex', flexDirection:'column', gap:'1rem' }}>
      <div>
        <h3 style={{ fontSize:'1rem' }}>{title}</h3>
        {description && <p style={{ fontSize:'0.8rem', color:'var(--fg-subtle)', marginTop:4 }}>{description}</p>}
      </div>
      <div className="form-grid">
        {fields.map(f => <Field key={f.key} f={f} />)}
      </div>
    </div>
  )

  const CATEGORIES = [
    { id: 'resume', label: 'Resume Intelligence' },
    { id: 'models', label: 'AI Models' },
    { id: 'strategy', label: 'Job Discovery' },
    { id: 'stealth', label: 'Stealth & Integrations' },
    { id: 'billing', label: 'Plan & Billing' },
  ]

  if (loading) return <div style={{ color:'var(--fg-muted)', padding:'2rem' }}>Loading settings…</div>

  return (
    <div style={{ display:'flex', flexDirection:'column', height: '100%' }}>
      {/* Top Header */}
      <div className="flex justify-between items-center mb-lg">
        <div>
          <h2 style={{ fontSize: '1.75rem', fontWeight: 800 }}>Settings</h2>
          <p style={{ color:'var(--fg-muted)', fontSize:'0.875rem', marginTop:4 }}>
            Manage your AI personas, API keys, and automation preferences.
          </p>
        </div>
        <button className="btn btn-primary" onClick={save} style={{ height: 42, padding: '0 1.5rem' }}>
          {saved ? 'Configuration Saved' : 'Save Changes'}
        </button>
      </div>

      <div style={{ display: 'flex', gap: '2rem', flex: 1, minHeight: 0 }}>
        {/* Local Settings Sidebar */}
        <div style={{ width: 220, display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          {CATEGORIES.map(cat => (
            <button
              key={cat.id}
              onClick={() => setActiveCategory(cat.id)}
              className={`nav-item ${activeCategory === cat.id ? 'active' : ''}`}
              style={{ padding: '0.75rem 1rem' }}
            >
              {cat.label}
            </button>
          ))}
          <div style={{ marginTop: 'auto', padding: '1.25rem', background: 'var(--surface)', borderRadius: 'var(--radius)', border: '1px solid var(--surface-border)' }}>
            <div style={{ fontSize: '0.7rem', color: 'var(--fg-muted)', textTransform: 'uppercase', fontWeight: 700, marginBottom: 8 }}>LLM Status</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {providers.map(p => (
                <span key={p} className="badge" style={{ background:'var(--success-subtle)', color:'var(--success)', fontSize: '0.6rem' }}>{p}</span>
              ))}
              {providers.length === 0 && <span className="text-xs text-subtle">No active models</span>}
            </div>
          </div>
        </div>

        {/* Categories Content */}
        <div style={{ flex: 1, overflowY: 'auto', paddingRight: '1rem', display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          {activeCategory === 'billing' && <BillingPanel />}

          {activeCategory === 'resume' && (
            <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
              <div className="panel" style={{ padding:'1.5rem', display:'flex', flexDirection:'column', gap:'1.5rem', borderLeft: '4px solid var(--primary)' }}>
                <div className="flex justify-between items-start">
                  <div>
                    <h3 style={{ fontSize:'1.1rem', color:'var(--primary)' }}>Master Resume Sources</h3>
                    <p style={{ fontSize:'0.85rem', color:'var(--fg-muted)', marginTop:4 }}>
                      Combine multiple profiles, projects, and PDFs. The AI agents will treat these as your primary source of truth.
                    </p>
                  </div>
                  <div className="flex gap-sm">
                    <label className="btn btn-outline btn-sm" style={{ cursor:'pointer' }}>
                      {isUploading ? 'Extracting...' : 'Upload File'}
                      <input type="file" hidden accept=".pdf,.md,.txt,.html,.htm" onChange={handleFileUpload} disabled={isUploading} />
                    </label>
                    <button className="btn btn-primary btn-sm" onClick={() => setIsAddingText(true)}>Add Block</button>
                  </div>
                </div>

                {resumeComponents.length === 0 && values.master_resume && (
                  <div style={{ padding: '1rem', background: 'var(--primary-subtle)', borderRadius: 'var(--radius-sm)', border: '1px dashed var(--primary)' }}>
                    <div style={{ fontWeight: 600, fontSize: '0.9rem', marginBottom: 4 }}>Legacy Resume Found</div>
                    <p style={{ fontSize: '0.8rem', color: 'var(--fg-muted)' }}>You have an existing master resume in the old format. Migrate it now to enable multi-file support.</p>
                    <button className="btn btn-primary btn-sm" style={{ marginTop: '0.75rem' }} onClick={migrateLegacyResume}>Migrate to System</button>
                  </div>
                )}

                <div style={{ display:'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap:'1rem' }}>
                  {resumeComponents.map(c => (
                    <div key={c.id} className="panel" style={{
                      padding:'1rem',
                      background: c.is_active ? 'var(--surface-hover)' : 'transparent',
                      opacity: c.is_active ? 1 : 0.6,
                      border: c.is_active ? '1px solid var(--surface-border)' : '1px solid transparent'
                    }}>
                      <div className="flex items-center justify-between mb-sm">
                        <div className="flex items-center gap-sm">
                          <span style={{ fontSize: '1.2rem' }}>{c.type === 'file' ? '📄' : '📝'}</span>
                          <span style={{ fontWeight: 700, fontSize: '0.9rem' }}>{c.name}</span>
                        </div>
                        <button className="btn btn-icon btn-ghost" style={{ color:'var(--danger)', padding:4 }} onClick={() => deleteComponent(c.id)}>
                          <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0-1 14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2L4 6"/></svg>
                        </button>
                      </div>
                      <div className="flex items-center justify-between">
                        <span style={{ fontSize: '0.7rem', color:'var(--fg-subtle)' }}>
                          {c.type === 'file' ? `Source: ${c.original_filename}` : 'Manual text block'}
                        </span>
                        <label className="flex items-center gap-xs" style={{ cursor:'pointer', fontSize:'0.75rem', fontWeight: 600, color: c.is_active ? 'var(--success)' : 'var(--fg-subtle)' }}>
                          <input type="checkbox" checked={c.is_active} onChange={() => toggleComponent(c.id, c.is_active)} />
                          {c.is_active ? 'Enabled' : 'Disabled'}
                        </label>
                      </div>
                    </div>
                  ))}
                </div>

                {isAddingText && (
                  <div className="panel" style={{ padding:'1.25rem', background:'var(--bg-2)', border: '1px solid var(--primary)' }}>
                    <h4 style={{ marginBottom: '1rem', fontSize: '1rem' }}>New Knowledge Block</h4>
                    <div style={{ marginBottom:'1rem' }}>
                      <label className="form-label">Block Name</label>
                      <input value={newTextName} onChange={e => setNewTextName(e.target.value)} placeholder="e.g. Scaling Engineering Highlights" />
                    </div>
                    <div style={{ marginBottom:'1rem' }}>
                      <label className="form-label">Content (Markdown)</label>
                      <textarea rows={10} value={newTextContent} onChange={e => setNewTextContent(e.target.value)} placeholder="# Achievements..." />
                    </div>
                    <div className="flex justify-end gap-sm">
                      <button className="btn btn-outline btn-sm" onClick={() => setIsAddingText(false)}>Cancel</button>
                      <button className="btn btn-primary btn-sm" onClick={addTextComponent}>Add to Knowledge</button>
                    </div>
                  </div>
                )}
              </div>

              {/* Resume Style Guide */}
              <div className="panel" style={{ padding: '1.5rem' }}>
                <div style={{ marginBottom: '1rem' }}>
                  <div style={{ fontWeight: 700, fontSize: '1rem', marginBottom: '0.25rem' }}>AI Content Instructions</div>
                  <p style={{ fontSize: '0.82rem', color: 'var(--fg-muted)', marginBottom: '1rem' }}>
                    Paste your formatting rules, section order, tone instructions, and content constraints.
                    The AI follows these exactly every time it generates a resume.
                  </p>
                  <textarea
                    rows={10}
                    value={values.resume_style_guide || ''}
                    onChange={e => setValues(v => ({ ...v, resume_style_guide: e.target.value }))}
                    placeholder={`Example:\n- Section order: Summary → Experience → Technical Expertise → Education → Leadership\n- Bullet points start with strong action verbs\n- Keep to 1 page maximum\n- Dates format: Month YYYY – Month YYYY\n- Quantify every achievement with real metrics`}
                    style={{ width: '100%', fontFamily: 'monospace', fontSize: '0.82rem' }}
                  />
                </div>
              </div>

              {/* PDF Layout */}
              <div className="panel" style={{ padding: '1.5rem' }}>
                <div style={{ fontWeight: 700, fontSize: '1rem', marginBottom: '0.25rem' }}>PDF Layout</div>
                <p style={{ fontSize: '0.82rem', color: 'var(--fg-muted)', marginBottom: '1.25rem' }}>
                  Controls the visual appearance of generated PDFs. Adjust to match your preferred resume style.
                </p>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1rem' }}>
                  <div className="form-group">
                    <label className="form-label">Font Family</label>
                    <select
                      value={pdfStyle.fontFamily}
                      onChange={e => setPdfStyle(p => ({ ...p, fontFamily: e.target.value }))}
                      style={{ width: '100%', padding: '0.5rem', background: 'var(--bg-2)', border: '1px solid var(--surface-border)', borderRadius: 'var(--radius-sm)', color: 'var(--fg)', fontSize: '0.875rem' }}
                    >
                      {FONT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                  <div className="form-group">
                    <label className="form-label">Font Size (pt)</label>
                    <select
                      value={pdfStyle.fontSize}
                      onChange={e => setPdfStyle(p => ({ ...p, fontSize: e.target.value }))}
                      style={{ width: '100%', padding: '0.5rem', background: 'var(--bg-2)', border: '1px solid var(--surface-border)', borderRadius: 'var(--radius-sm)', color: 'var(--fg)', fontSize: '0.875rem' }}
                    >
                      {['9.5','10','10.5','11','11.5','12'].map(s => <option key={s} value={s}>{s}pt</option>)}
                    </select>
                  </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: '1rem', marginBottom: '1rem' }}>
                  {[
                    { key: 'marginTop', label: 'Top Margin (cm)' },
                    { key: 'marginBottom', label: 'Bottom Margin (cm)' },
                    { key: 'marginLeft', label: 'Left Margin (cm)' },
                    { key: 'marginRight', label: 'Right Margin (cm)' },
                  ].map(({ key, label }) => (
                    <div className="form-group" key={key}>
                      <label className="form-label">{label}</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0.5"
                        max="4"
                        value={pdfStyle[key]}
                        onChange={e => setPdfStyle(p => ({ ...p, [key]: e.target.value }))}
                      />
                    </div>
                  ))}
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', alignItems: 'end' }}>
                  <div className="form-group">
                    <label className="form-label">Section Header Color</label>
                    <div className="flex items-center gap-sm">
                      <input
                        type="color"
                        value={pdfStyle.sectionColor}
                        onChange={e => setPdfStyle(p => ({ ...p, sectionColor: e.target.value }))}
                        style={{ width: 42, height: 36, padding: 2, border: '1px solid var(--surface-border)', borderRadius: 'var(--radius-sm)', background: 'var(--bg-2)', cursor: 'pointer' }}
                      />
                      <input
                        type="text"
                        value={pdfStyle.sectionColor}
                        onChange={e => setPdfStyle(p => ({ ...p, sectionColor: e.target.value }))}
                        style={{ flex: 1 }}
                        placeholder="#2E74B5"
                      />
                    </div>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <div style={{ fontSize: '0.75rem', color: 'var(--fg-muted)' }}>Preview</div>
                    <div style={{
                      padding: '4px 8px',
                      fontWeight: 700,
                      fontSize: '0.75rem',
                      letterSpacing: '0.06em',
                      textTransform: 'uppercase',
                      color: pdfStyle.sectionColor,
                      borderBottom: `1.5px solid ${pdfStyle.sectionColor}`,
                      fontFamily: pdfStyle.fontFamily,
                    }}>
                      PROFESSIONAL EXPERIENCE
                    </div>
                  </div>
                </div>

                <div style={{ marginTop: '1.25rem', padding: '0.75rem 1rem', background: 'var(--bg-2)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--surface-border)', fontSize: '0.78rem', color: 'var(--fg-muted)' }}>
                  <strong style={{ color: 'var(--fg)' }}>Note:</strong> The AI uses a fixed structural format internally (section headers, company/date layout). These controls only affect how it looks in the exported PDF — not the content or the order of information.
                </div>
              </div>
            </div>
          )}

          {activeCategory === 'models' && (
            <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
              <Section
                title="Google Gemini"
                description="Google's most capable models. Recommendation: Use 'gemini-1.5-pro' for complex tailoring."
                fields={[LLM_PROVIDERS[0]]}
              />
              <Section
                title="Groq & OpenRouter"
                description="Fast and versatile providers. Perfect for quick job analysis."
                fields={[LLM_PROVIDERS[1], LLM_PROVIDERS[2]]}
              />
              <Section
                title="Self-Hosted & Others"
                description="Connect to your local Ollama instance or Together AI."
                fields={[LLM_PROVIDERS[3], LLM_PROVIDERS[4]]}
              />

              <div className="panel" style={{ padding:'1.5rem', display:'flex', flexDirection:'column', gap:'1rem' }}>
                <div>
                  <h3 style={{ fontSize:'1rem' }}>Custom Models</h3>
                  <p style={{ fontSize:'0.8rem', color:'var(--fg-subtle)', marginTop:4 }}>
                    Add any model id from <strong>build.nvidia.com</strong> (e.g. <code>meta/llama-3.1-405b-instruct</code>)
                    or <strong>openrouter.ai/models</strong> (e.g. <code>meta-llama/llama-3.3-70b-instruct:free</code>).
                    Uses the matching API key above. They appear in the model dropdowns; click <strong>Save Settings</strong> to persist.
                  </p>
                </div>

                {nvModels.length > 0 && (
                  <div style={{ display:'flex', flexDirection:'column', gap:'0.5rem' }}>
                    {nvModels.map(m => (
                      <div key={`${m.provider || 'nvidia'}:${m.id}`} style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'0.5rem 0.75rem', background:'var(--surface-2)', borderRadius:'var(--radius-sm)' }}>
                        <div>
                          <div style={{ fontWeight:600, fontSize:'0.85rem' }}>{m.label}</div>
                          <div style={{ fontSize:'0.72rem', color:'var(--fg-muted)' }}>{(m.provider || 'nvidia')}:{m.id}</div>
                        </div>
                        <button className="btn btn-ghost btn-sm" onClick={() => removeNvModel(m.id, m.provider || 'nvidia')}>Remove</button>
                      </div>
                    ))}
                  </div>
                )}

                <div className="form-grid">
                  <div className="form-group">
                    <label className="form-label">Provider</label>
                    <select value={nvProvider} onChange={e => setNvProvider(e.target.value)}>
                      <option value="nvidia">NVIDIA</option>
                      <option value="openrouter">OpenRouter</option>
                    </select>
                  </div>
                  <div className="form-group">
                    <label className="form-label">Search {catalogBusy ? '' : `(${visibleCatalog.length} of ${catalog.length})`}</label>
                    <input value={nvId} onChange={e => setNvId(e.target.value)}
                      placeholder={nvProvider === 'openrouter' ? 'llama, qwen, free…' : 'llama, nemotron…'} />
                  </div>
                </div>

                {catalogErr && <div style={{ fontSize:'0.8rem', color:'var(--danger)' }}>{catalogErr}</div>}

                {/* The list is the picker: every model the key can reach, with the
                    provider's own price, one click to add. A <datalist> hid all of
                    this behind an autocomplete nobody could see. */}
                <div style={{ maxHeight:280, overflowY:'auto', border:'1px solid var(--border)', borderRadius:'var(--radius-sm)' }}>
                  {catalogBusy && <div style={{ padding:'0.75rem', fontSize:'0.8rem', color:'var(--fg-muted)' }}>Loading models…</div>}
                  {!catalogBusy && visibleCatalog.length === 0 && (
                    <div style={{ padding:'0.75rem', fontSize:'0.8rem', color:'var(--fg-muted)' }}>
                      No model matches “{nvId}”.
                    </div>
                  )}
                  {visibleCatalog.map(m => {
                    const added = nvModels.some(x => x.id === m.id && (x.provider || 'nvidia') === nvProvider)
                    return (
                      <div key={m.id} style={{ display:'flex', justifyContent:'space-between', alignItems:'center', gap:'0.75rem', padding:'0.5rem 0.75rem', borderBottom:'1px solid var(--border)' }}>
                        <div style={{ minWidth:0 }}>
                          <div style={{ fontSize:'0.85rem', fontWeight:600, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{m.label}</div>
                          <div style={{ fontSize:'0.72rem', color:'var(--fg-muted)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{m.id}</div>
                        </div>
                        <div style={{ display:'flex', alignItems:'center', gap:'0.75rem', flexShrink:0 }}>
                          <span style={{ fontSize:'0.72rem', color: m.extra === 'free' ? 'var(--success, #3fb950)' : 'var(--fg-muted)', whiteSpace:'nowrap' }}>
                            {m.extra || '—'}
                          </span>
                          <button className="btn btn-outline btn-sm" disabled={added} onClick={() => addModel(m.id, modelLabel(m))}>
                            {added ? 'Added' : 'Add'}
                          </button>
                        </div>
                      </div>
                    )
                  })}
                </div>

                <div style={{ display:'flex', alignItems:'center', gap:'0.75rem', flexWrap:'wrap' }}>
                  <button className="btn btn-ghost btn-sm" onClick={() => loadCatalog(nvProvider, true)} disabled={catalogBusy}>
                    {catalogBusy ? 'Loading…' : 'Refresh list'}
                  </button>
                  {nvId.trim() && !catalog.some(m => m.id === nvId.trim()) && (
                    <button className="btn btn-outline btn-sm" onClick={() => addModel(nvId.trim(), nvId.trim())}>
                      + Add “{nvId.trim()}” as a custom id
                    </button>
                  )}
                  <span style={{ fontSize:'0.75rem', color:'var(--fg-muted)' }}>
                    Free models first, then cheapest. Click <strong>Save Changes</strong> to persist.
                  </span>
                </div>
              </div>
            </div>
          )}

          {activeCategory === 'strategy' && (
            <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
              <Section
                title="Hunting Logic"
                description="Configure what the Job Discovery Agent looks for during its autonomous runs."
                fields={HUNT_FILTERS}
              />
            </div>
          )}

          {activeCategory === 'stealth' && (
            <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
              <Section
                title="Infrastructure"
                description="Avoid bot detection and scale up your automation capability."
                fields={[...STEALTH, ...CAPTCHA]}
              />
              <Section
                title="Portfolio Connections"
                description="Link your GitHub to allow agents to 'read' your code and projects."
                fields={GITHUB}
              />

              <div className="panel" style={{ padding:'1.5rem', display:'flex', flexDirection:'column', gap:'1rem' }}>
                <div>
                  <h3 style={{ fontSize:'1rem' }}>MCP Access Token</h3>
                  <p style={{ fontSize:'0.8rem', color:'var(--fg-subtle)', marginTop:4 }}>
                    For MCP clients that can't open a browser to sign in. Paste it as
                    <code style={{ margin:'0 4px' }}>Authorization: Bearer &lt;token&gt;</code>.
                    Valid for one year. Generating a new one immediately stops the old one working.
                  </p>
                </div>

                {mcpToken && (
                  <div className="alert alert-warning" style={{ padding:'1rem' }}>
                    <p style={{ fontSize:'0.8rem', marginBottom:8 }}>
                      <strong>Copy this now — it is not shown again.</strong>
                    </p>
                    <textarea readOnly value={mcpToken} rows={3} onFocus={e => e.target.select()}
                      style={{ width:'100%', fontFamily:'monospace', fontSize:'0.7rem' }} />
                  </div>
                )}

                <div style={{ display:'flex', gap:'0.75rem', alignItems:'center' }}>
                  <button className="btn btn-outline" onClick={generateMcpToken} disabled={mcpBusy}>
                    {mcpExists ? 'Regenerate Token' : 'Generate Token'}
                  </button>
                  {mcpExists && (
                    <button className="btn btn-ghost btn-sm" onClick={revokeMcpToken} disabled={mcpBusy}>
                      Revoke
                    </button>
                  )}
                  <span style={{ fontSize:'0.75rem', color:'var(--fg-subtle)' }}>
                    {mcpExists ? 'A token is active.' : 'No token issued.'}
                  </span>
                </div>
              </div>
              <div className="alert alert-warning" style={{ padding:'1rem 1.5rem' }}>
                <p style={{ fontSize:'0.8rem' }}>
                  <strong>Security First:</strong> Keys are stored locally in your SQLite database. Never expose port 8000 externally.
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
