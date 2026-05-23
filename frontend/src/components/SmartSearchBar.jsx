import React, { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../api/client'

// Category colors and icons
const CATEGORY_META = {
  roles:      { color: 'var(--primary)',  bg: 'var(--primary-subtle)',  icon: '💼' },
  tech:       { color: 'var(--info)',     bg: 'var(--info-subtle)',     icon: '⚙️' },
  seniority:  { color: 'var(--purple)',   bg: 'var(--purple-subtle)',   icon: '🏅' },
  companies:  { color: 'var(--warning)',  bg: 'var(--warning-subtle)',  icon: '🏢' },
  work_style: { color: 'var(--success)',  bg: 'var(--success-subtle)',  icon: '🌐' },
  salary:     { color: 'var(--success)',  bg: 'var(--success-subtle)',  icon: '💰' },
  your_jobs:  { color: 'var(--fg)',       bg: 'var(--surface-2)',       icon: '📁' },
}

function debounce(fn, ms) {
  let timer
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms) }
}

/**
 * SmartSearchBar — autocomplete search with keyword suggestions.
 *
 * Props:
 *   onSearch(query: string)  — called when user confirms a search
 *   onClear()                — called when search is cleared
 *   placeholder              — input placeholder text
 *   autoFocus                — focus on mount
 */
export default function SmartSearchBar({ onSearch, onClear, placeholder = 'Search jobs, roles, tech…', autoFocus = false }) {
  const [query, setQuery] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [trending, setTrending] = useState([])
  const [open, setOpen] = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const [loading, setLoading] = useState(false)
  const [activeTags, setActiveTags] = useState([])  // applied search tags
  const inputRef = useRef(null)
  const dropRef = useRef(null)

  // Load trending keywords on mount
  useEffect(() => {
    api.getTrending().then(setTrending).catch(() => {})
    if (autoFocus) inputRef.current?.focus()
  }, [autoFocus])

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e) => {
      if (!dropRef.current?.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const fetchSuggestions = useCallback(
    debounce(async (q) => {
      if (!q.trim()) { setSuggestions([]); return }
      setLoading(true)
      try {
        const results = await api.suggest(q)
        setSuggestions(results)
      } catch { setSuggestions([]) }
      setLoading(false)
    }, 220),
    []
  )

  const handleChange = (e) => {
    const val = e.target.value
    setQuery(val)
    setActiveIdx(-1)
    if (val.trim()) {
      setOpen(true)
      fetchSuggestions(val)
    } else {
      setSuggestions([])
      // Show trending when empty
      setOpen(true)
    }
  }

  const applyKeyword = (keyword) => {
    setQuery('')
    setSuggestions([])
    setOpen(false)
    setActiveIdx(-1)
    // Add to active tags if not already present
    if (!activeTags.includes(keyword)) {
      const newTags = [...activeTags, keyword]
      setActiveTags(newTags)
      onSearch(newTags.join(' '))
    }
  }

  const removeTag = (tag) => {
    const newTags = activeTags.filter(t => t !== tag)
    setActiveTags(newTags)
    if (newTags.length === 0) {
      onClear?.()
    } else {
      onSearch(newTags.join(' '))
    }
  }

  const handleKeyDown = (e) => {
    const items = suggestions.length ? suggestions : trending
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIdx(i => Math.min(i + 1, items.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIdx(i => Math.max(i - 1, -1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (activeIdx >= 0 && items[activeIdx]) {
        applyKeyword(items[activeIdx].keyword)
      } else if (query.trim()) {
        // Free-text search
        const newTags = [...activeTags, query.trim()]
        setActiveTags(newTags)
        onSearch(newTags.join(' '))
        setQuery('')
        setOpen(false)
      }
    } else if (e.key === 'Escape') {
      setOpen(false)
      setActiveIdx(-1)
    } else if (e.key === 'Backspace' && !query && activeTags.length > 0) {
      // Remove last tag on backspace when input is empty
      removeTag(activeTags[activeTags.length - 1])
    }
  }

  const displayItems = query.trim() ? suggestions : trending
  const showDropdown = open && (displayItems.length > 0 || loading)

  return (
    <div className="search-bar-wrap" ref={dropRef}>
      {/* Input row */}
      <div className={`search-bar-input-row ${open && showDropdown ? 'open' : ''}`}>
        <span className="search-icon">
          {loading
            ? <span className="search-spinner" />
            : <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          }
        </span>

        {/* Active tag chips inside input */}
        {activeTags.map(tag => {
          const cat = CATEGORY_META[Object.keys(CATEGORY_META).find(c => 
            tag.startsWith('$') ? c === 'salary' : true
          )] || CATEGORY_META.roles
          return (
            <span key={tag} className="search-tag" style={{ background: cat.bg, color: cat.color }}>
              {tag}
              <button className="search-tag-x" onClick={() => removeTag(tag)}>×</button>
            </span>
          )
        })}

        <input
          ref={inputRef}
          value={query}
          onChange={handleChange}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
          placeholder={activeTags.length ? 'Add more keywords…' : placeholder}
          className="search-input"
          autoComplete="off"
          spellCheck={false}
        />

        {(activeTags.length > 0 || query) && (
          <button className="search-clear" onClick={() => {
            setQuery(''); setActiveTags([]); setSuggestions([]); setOpen(false); onClear?.()
          }} title="Clear search">
            <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        )}

        <button className="btn btn-primary btn-sm" style={{ flexShrink:0, borderRadius:'8px' }}
          onClick={() => {
            if (query.trim()) applyKeyword(query.trim())
            else if (activeTags.length) onSearch(activeTags.join(' '))
          }}>
          Search
        </button>
      </div>

      {/* Dropdown */}
      {showDropdown && (
        <div className="search-dropdown">
          <div className="search-dropdown-header">
            {query.trim() ? `Suggestions for "${query}"` : '🔥 Trending Keywords'}
          </div>

          {loading && (
            <div className="search-dropdown-loading">Searching suggestions…</div>
          )}

          {!loading && displayItems.map((item, idx) => {
            const meta = CATEGORY_META[item.category] || CATEGORY_META.tech
            const isActive = idx === activeIdx
            return (
              <div
                key={item.keyword + idx}
                className={`search-suggestion-item ${isActive ? 'active' : ''}`}
                onMouseDown={() => applyKeyword(item.keyword)}
                onMouseEnter={() => setActiveIdx(idx)}
              >
                <span className="search-suggestion-icon" style={{ background: meta.bg, color: meta.color }}>
                  {meta.icon}
                </span>
                <span className="search-suggestion-text">
                  {highlightMatch(item.keyword, query)}
                </span>
                <span className="search-suggestion-cat" style={{ color: meta.color }}>
                  {item.category?.replace('_', ' ')}
                  {item.source === 'your_jobs' && ' · yours'}
                </span>
                {item.count > 0 && (
                  <span className="search-suggestion-count">{item.count}</span>
                )}
              </div>
            )
          })}

          {/* Quick filters row */}
          {!query.trim() && (
            <div className="search-quick-filters">
              <span className="search-quick-label">Quick:</span>
              {['Remote', '$200K+', 'Principal', 'LLMs', 'MLOps'].map(kw => (
                <button key={kw} className="search-quick-chip" onMouseDown={() => applyKeyword(kw)}>
                  {kw}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function highlightMatch(text, query) {
  if (!query.trim()) return text
  const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi')
  const parts = text.split(regex)
  return parts.map((part, i) =>
    regex.test(part)
      ? <mark key={i} style={{ background: 'var(--primary-subtle)', color: 'var(--primary)', borderRadius:3, padding:'0 2px' }}>{part}</mark>
      : part
  )
}
