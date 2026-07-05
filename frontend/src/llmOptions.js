// Model options for the LLM selectors. Base list is static; custom NVIDIA
// models (any id from build.nvidia.com) are user-managed in Settings and
// mirrored to localStorage so both selectors pick them up without a fetch.

export const BASE_LLM_OPTIONS = [
  { value:'gemini-3.5-flash', label:'Gemini 3.5 Flash (Fastest · New)' },
  { value:'gemini-3.1-pro-preview', label:'Gemini 3.1 Pro Preview (Most Capable)' },
  { value:'gemini-3-flash-preview', label:'Gemini 3 Flash Preview' },
  { value:'gemini-3.1-flash-lite', label:'Gemini 3.1 Flash Lite' },
  { value:'gemini-2.5-flash', label:'Gemini 2.5 Flash (Recommended)' },
  { value:'gemini-2.5-flash-lite', label:'Gemini 2.5 Flash Lite' },
  { value:'gemini-2.5-pro', label:'Gemini 2.5 Pro' },
  { value:'gemini-2.0-flash', label:'Gemini 2.0 Flash' },
  { value:'gemini-2.0-flash-lite', label:'Gemini 2.0 Flash Lite' },
  { value:'groq', label:'Groq (Llama 3 · Fast)' },
  { value:'openrouter', label:'OpenRouter (Free)' },
  { value:'nvidia', label:'NVIDIA (MiniMax-M3)' },
  { value:'claude', label:'Claude Sonnet (Anthropic)' },
  { value:'ollama', label:'Ollama (Local)' },
]

export function getCustomNvidiaModels() {
  try { return JSON.parse(localStorage.getItem('customNvidiaModels') || '[]') }
  catch { return [] }
}

export function setCustomNvidiaModels(models) {
  localStorage.setItem('customNvidiaModels', JSON.stringify(models || []))
}

// Full option list = base + custom NVIDIA models (value "nvidia:<model-id>").
export function getLlmOptions() {
  const custom = getCustomNvidiaModels()
    .filter(m => m && m.id)
    .map(m => ({ value: `nvidia:${m.id}`, label: `⚡ ${m.label || m.id}` }))
  return [...BASE_LLM_OPTIONS, ...custom]
}
