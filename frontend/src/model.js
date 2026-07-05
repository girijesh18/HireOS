// Single source of truth for the globally-selected model. Stored in
// localStorage; a custom event keeps every mounted component in sync so the
// top-bar selector drives the whole platform.
import { useState, useEffect } from 'react'
import { getLlmOptions } from './llmOptions'

const KEY = 'preferredLlm'
const EVENT = 'preferred-llm-change'
const DEFAULT = 'gemini-2.5-flash'

export function getPreferredLlm() {
  return localStorage.getItem(KEY) || DEFAULT
}

export function setPreferredLlm(v) {
  localStorage.setItem(KEY, v)
  window.dispatchEvent(new CustomEvent(EVENT, { detail: v }))
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

export { getLlmOptions }
