// Centralised API client -- all backend calls go through here
const BASE = '/api'

async function req(method, path, body, isFormData = false) {
  const headers = isFormData ? {} : { 'Content-Type': 'application/json' }
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: isFormData ? body : (body !== undefined ? JSON.stringify(body) : undefined),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
    throw new Error(err.detail || `API error ${res.status}`)
  }
  return res.json()
}

export const api = {
  // Health
  health: () => req('GET', '/'),

  // Stats
  getStats: () => req('GET', '/stats'),

  // Jobs CRUD
  listJobs: (params = {}) => {
    const qs = new URLSearchParams(Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null)
    )).toString()
    return req('GET', `/jobs${qs ? `?${qs}` : ''}`)
  },
  createJob: (data) => req('POST', '/jobs', data),
  getJob: (id) => req('GET', `/jobs/${id}`),
  updateJob: (id, data) => req('PATCH', `/jobs/${id}`, data),
  deleteJob: (id) => req('DELETE', `/jobs/${id}`),
  getTasks: (id) => req('GET', `/jobs/${id}/tasks`),

  // Events (Timeline)
  getEvents: (jobId) => req('GET', `/jobs/${jobId}/events`),
  addEvent: (jobId, data) => req('POST', `/jobs/${jobId}/events`, data),

  // Resumes / Cover Letters
  getResumes: (jobId) => req('GET', `/jobs/${jobId}/resumes`),
  getCoverLetters: (jobId) => req('GET', `/jobs/${jobId}/cover-letters`),

  // Settings
  getSettings: () => req('GET', '/settings'),
  saveSettings: (settings) => req('POST', '/settings', { settings }),

  // LLM Providers
  getProviders: () => req('GET', '/llm/providers'),

  // Agent: Track a job from a URL (AI auto-extracts)
  trackUrl: (url, llm = 'gemini') => req('POST', '/agent/track-url', { url, llm }),

  // Agent: Track from pasted JD text (works for LinkedIn and login-walled sites)
  trackJdText: (text, llm = 'gemini') => req('POST', '/agent/track-jd', { text, llm }),

  // Agent: Run fit assessment / gap analysis
  analyzeJob: (jobId, llm = 'gemini') => req('POST', `/agent/analyze/${jobId}`, { llm }),

  // Agent: Generate a tailored resume
  generateResume: (jobId, { llm = 'gemini-2.5-flash', feedback = '' } = {}) =>
    req('POST', `/agent/resume/${jobId}`, { llm, feedback }),

  // Agent: Generate a cover letter
  generateCoverLetter: (jobId, { llm = 'gemini-2.5-flash', feedback = '', resumeVersion = null } = {}) =>
    req('POST', `/agent/cover-letter/${jobId}`, { llm, feedback, resume_version: resumeVersion }),

  // Agent: Fan-out LLM comparison
  compareLLMs: (jobId, task = 'resume', providers = ['gemini', 'groq']) =>
    req('POST', `/agent/compare/${jobId}`, { task, providers }),

  // Search: full-text job search
  searchJobs: (q, params = {}) => {
    const all = { ...(q ? { q } : {}), ...params }
    const qs = new URLSearchParams(
      Object.fromEntries(Object.entries(all).filter(([, v]) => v !== undefined && v !== null))
    ).toString()
    return req('GET', `/search${qs ? `?${qs}` : ''}`)
  },

  // Search: autocomplete keyword suggestions
  suggest: (q, limit = 10) =>
    req('GET', `/search/suggest?q=${encodeURIComponent(q)}&limit=${limit}`),

  // Search: trending keywords from your pipeline
  getTrending: () => req('GET', '/search/trending'),

  // Chat (floating assistant)
  chat: (message, llm = 'gemini', context = null) =>
    req('POST', '/chat', { message, llm, context }),

  // Export
  exportCSV: () => window.open(`${BASE}/export/csv`, '_blank'),

  // File Download
  downloadFile: (jobId, filename) =>
    window.open(`${BASE}/download/${jobId}/${filename}`, '_blank'),

  // ── Career-Ops Enhanced Agent Endpoints ──────────────────────────────────

  // Full A-G Structured Evaluation
  evaluateJob: (jobId, llm = 'gemini-2.5-flash') =>
    req('POST', `/agent/evaluate/${jobId}?llm=${llm}`),

  // Get evaluation reports for a job
  getEvaluations: (jobId) =>
    req('GET', `/evaluations/${jobId}`),

  // LinkedIn outreach generator
  generateLinkedIn: (jobId, contactType = 'hiring_manager', llm = 'gemini-2.5-flash') =>
    req('POST', `/agent/linkedin/${jobId}`, { contact_type: contactType, llm }),
  getLinkedIn: (jobId) =>
    req('GET', `/agent/linkedin/${jobId}`),

  // Deep company research
  deepResearch: (jobId, llm = 'gemini-2.5-flash') =>
    req('POST', `/agent/deep-research/${jobId}`, { llm }),
  getDeepResearch: (jobId) =>
    req('GET', `/agent/deep-research/${jobId}`),

  // Interview prep (STAR stories)
  interviewPrep: (jobId, llm = 'gemini-2.5-flash') =>
    req('POST', `/agent/interview-prep/${jobId}`, { llm }),
  getInterviewPrep: (jobId) =>
    req('GET', `/agent/interview-prep/${jobId}`),

  // Story bank
  getStoryBank: (tag) =>
    req('GET', `/story-bank${tag ? `?tag=${encodeURIComponent(tag)}` : ''}`),
  addStory: (story, jobId) =>
    req('POST', `/story-bank${jobId ? `?job_id=${jobId}` : ''}`, story),

  // Follow-up cadence
  getFollowupCadence: () =>
    req('GET', '/analytics/followups'),
  logFollowup: (jobId, data) =>
    req('POST', `/followups/${jobId}`, data),
  getFollowups: (jobId) =>
    req('GET', `/followups/${jobId}`),

  // Pattern analytics
  getPatternAnalytics: () =>
    req('GET', '/analytics/patterns'),

  // Master Resume Components
  getResumeComponents: () => req('GET', '/settings/resume-components'),
  addTextResumeComponent: (data) => req('POST', '/settings/resume-components/text', data),
  uploadResumeFile: (name, file) => {
    const formData = new FormData()
    formData.append('file', file)
    return req('POST', `/settings/resume-components/file?name=${encodeURIComponent(name)}`, formData, true)
  },
  updateResumeComponent: (id, data) => req('PATCH', `/settings/resume-components/${id}`, data),
  deleteResumeComponent: (id) => req('DELETE', `/settings/resume-components/${id}`),
}
