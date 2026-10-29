// The one model HireOS uses. The server holds it (setting "default_llm": it also drives the
// Telegram/Discord bots, discovery, the apply agent and Gmail reading); this browser keeps a
// copy in localStorage so pages read it without a fetch, and a custom event keeps every
// mounted picker (top bar, Settings › AI Model) in sync.
import { useState, useEffect } from 'react'
import { api } from './api/client'
import { getLlmOptions, setCustomNvidiaModels } from './llmOptions'

const KEY = 'preferredLlm'
const EVENT = 'preferred-llm-change'
const DEFAULT = ''   // until the server's choice loads; '' lets the server pick the user's model
const PROVIDER_NAMES = { openrouter: 'OpenRouter', nvidia: 'NVIDIA', gemini: 'Gemini', groq: 'Groq',
  together: 'Together', claude: 'Claude', ollama: 'Ollama', local: 'Local' }

export function getPreferredLlm() {
  return localStorage.getItem(KEY) || DEFAULT
}

// save=false only when copying the server's value in; every user choice is saved.
export function setPreferredLlm(v, { save = true } = {}) {
  localStorage.setItem(KEY, v)
  window.dispatchEvent(new CustomEvent(EVENT, { detail: v }))
  if (save) api.saveSettings([{ key: 'default_llm', value: v }]).catch(() => {})
}

// Fetch the server's choice, the providers with a key and the added models.
export async function loadModelState() {
  const r = await api.getProviders()
  if (r.model) setPreferredLlm(r.model, { save: false })
  if (Array.isArray(r.custom)) setCustomNvidiaModels(r.custom)
  return r
}

// React state that tracks the selected model, updating on change (this tab via
// the custom event, other tabs via the storage event).
export function usePreferredLlm() {
  const [llm, setLlm] = useState(getPreferredLlm())
  useEffect(() => {
    const onChange = () => setLlm(getPreferredLlm())
    window.addEventListener(EVENT, onChange)
    window.addEventListener('storage', onChange)
    return () => {
      window.removeEventListener(EVENT, onChange)
      window.removeEventListener('storage', onChange)
    }
  }, [])
  return llm
}

export function modelLabel(v) {
  const known = getLlmOptions().find(o => o.value === v)
  if (known) return known.label
  const i = v.indexOf(':')
  if (i < 0) return PROVIDER_NAMES[v] ? `${PROVIDER_NAMES[v]} (its default model)` : v
  return `${v.slice(i + 1)} · ${PROVIDER_NAMES[v.slice(0, i)] || v.slice(0, i)}`
}

// A picker's options: models whose provider has a key (all of them until that is known),
// and always the current model, even when it isn't in the list.
export function modelOptions(available, current) {
  const has = v => available.some(p => v === p || v.startsWith(p + '-') || v.startsWith(p + ':'))
  const all = getLlmOptions()
  const opts = available.length ? all.filter(o => has(o.value)) : all
  if (current && !opts.some(o => o.value === current)) opts.unshift({ value: current, label: modelLabel(current) })
  return opts
}

export { getLlmOptions }
