import React, { useEffect, useRef } from 'react'
import '../landing.css'

/* ── Icons (SVG, stroked, inherit currentColor) ───────────────────────────── */
const Ico = ({ d, size = 20 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d={d} />
  </svg>
)
const IconInbox = () => <Ico d="M22 12h-6l-2 3h-4l-2-3H2M5.45 5.11L2 12v6a2 2 0 002 2h16a2 2 0 002-2v-6l-3.45-6.89A2 2 0 0016.76 4H7.24a2 2 0 00-1.79 1.11z" />
const IconTarget = () => <Ico d="M12 21a9 9 0 100-18 9 9 0 000 18zM12 17a5 5 0 100-10 5 5 0 000 10zM12 13a1 1 0 100-2 1 1 0 000 2z" />
const IconDoc = () => <Ico d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8" />
const IconSend = () => <Ico d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
const IconSearch = () => <Ico d="M11 19a8 8 0 100-16 8 8 0 000 16zM21 21l-4.35-4.35" />
const IconLayers = () => <Ico d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
const IconArrow = () => <Ico d="M5 12h14M12 5l7 7-7 7" size={17} />
const IconBolt = () => <Ico d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" size={16} />
const IconCheck = () => <Ico d="M20 6L9 17l-5-5" size={16} />

/* ── Content ──────────────────────────────────────────────────────────────── */
const FEATURES = [
  { icon: <IconInbox />, tint: 'var(--info)', span: 'l-card-wide',
    title: 'Smart job ingestion',
    body: 'Paste a URL or dump raw JD text. An agentic parser pulls out company, title, salary, location and the full structured description — no forms to fill.' },
  { icon: <IconTarget />, tint: 'var(--purple)', span: 'l-card-wide',
    title: 'A–G fit assessment',
    body: 'A 0–100 match score against your master resume plus a 7-axis breakdown: role archetype, leveling strategy, market demand, interview likelihood, JD red flags.' },
  { icon: <IconDoc />, tint: 'var(--primary)',
    title: 'Tailored resume engine',
    body: 'ATS-optimized resumes written per job, run through a two-pass design-rule validator, exported to PDF and DOCX.' },
  { icon: <IconSend />, tint: 'var(--cyan)',
    title: 'Cover letters & outreach',
    body: 'Letters drafted from the tailored resume so the narrative stays consistent, plus LinkedIn notes tuned per contact type.' },
  { icon: <IconSearch />, tint: 'var(--success)',
    title: 'Deep company research',
    body: 'A 6-axis investigative sweep: business model, competitors, engineering culture, recent news, value prop, leadership.' },
  { icon: <IconLayers />, tint: 'var(--warning)', span: 'l-card-full',
    title: 'Multi-model engine',
    body: 'Gemini, Groq, OpenRouter and local Ollama behind one adapter. Fan a prompt out to several models and compare.' },
]

const STEPS = [
  { n: '01', title: 'Track', body: 'Drop in a link or paste the JD. The parser does the data entry.' },
  { n: '02', title: 'Assess', body: 'Seven axes of gap analysis decide whether the role is worth your time.' },
  { n: '03', title: 'Research', body: 'A background agent builds the company dossier while you keep moving.' },
  { n: '04', title: 'Tailor', body: 'Resume and cover letter generated against that specific description.' },
  { n: '05', title: 'Apply', body: 'Export the documents, or hand the posting to the Playwright bot.' },
  { n: '06', title: 'Follow up', body: 'Every state change lands on an application timeline you can search.' },
]

const STATS = [
  { v: '7', l: 'evaluation axes per job' },
  { v: '4', l: 'LLM providers, one switch' },
  { v: '2-pass', l: 'resume rule validation' },
  { v: '100%', l: 'your data, your database' },
]

const SOURCES = ['LinkedIn', 'Greenhouse', 'Lever', 'Workday', 'Ashby', 'Wellfound', 'Company sites', 'Raw text']

const HEADLINE = ['Stop', 'rewriting', 'your', 'resume.']

/* ── Hero mock rows ───────────────────────────────────────────────────────── */
const MOCK_ROWS = [
  { title: 'Principal AI Engineer', co: 'Anthropic', score: '92', state: 'Approved', tint: 'var(--primary)' },
  { title: 'Staff Data Scientist', co: 'Stripe', score: '87', state: 'Interview', tint: 'var(--purple)' },
  { title: 'ML Platform Lead', co: 'Ramp', score: '78', state: 'Applied', tint: 'var(--success)' },
]

export default function Landing({ onGetStarted, onSignIn }) {
  const scrollRef = useRef(null)
  const navRef = useRef(null)
  const deckRef = useRef(null)

  /* Scroll-reveal. One observer for every .reveal in the page. */
  useEffect(() => {
    const nodes = scrollRef.current.querySelectorAll('.reveal')
    const io = new IntersectionObserver(entries => {
      for (const e of entries) {
        if (!e.isIntersecting) continue
        e.target.classList.add('in')
        io.unobserve(e.target)
      }
    }, { threshold: 0.15, rootMargin: '0px 0px -8% 0px' })
    nodes.forEach(n => io.observe(n))
    return () => io.disconnect()
  }, [])

  /* Sticky nav gets a border once the page has moved. */
  useEffect(() => {
    const el = scrollRef.current
    const onScroll = () => navRef.current?.classList.toggle('stuck', el.scrollTop > 12)
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  /* Pointer-driven 3D tilt on the hero deck. Writes CSS custom properties
     instead of re-rendering — React never sees a mousemove. */
  const onStageMove = (e) => {
    const deck = deckRef.current
    if (!deck) return
    const r = deck.getBoundingClientRect()
    const x = (e.clientX - r.left) / r.width - 0.5   // -0.5 .. 0.5
    const y = (e.clientY - r.top) / r.height - 0.5
    deck.classList.add('tracking')
    deck.style.setProperty('--tilt-y', `${x * 26 - 4}deg`)
    deck.style.setProperty('--tilt-x', `${-y * 18 + 4}deg`)
    deck.style.setProperty('--mx', x * 2)
    deck.style.setProperty('--my', y * 2)
  }
  const onStageLeave = () => {
    const deck = deckRef.current
    if (!deck) return
    deck.classList.remove('tracking')
    deck.style.removeProperty('--tilt-x')
    deck.style.removeProperty('--tilt-y')
    deck.style.setProperty('--mx', 0)
    deck.style.setProperty('--my', 0)
  }

  /* Spotlight inside whichever bento card the pointer is over. Delegated so
     six cards cost one listener. */
  const onBentoMove = (e) => {
    const card = e.target.closest('.l-card')
    if (!card) return
    const r = card.getBoundingClientRect()
    card.style.setProperty('--cx', `${e.clientX - r.left}px`)
    card.style.setProperty('--cy', `${e.clientY - r.top}px`)
  }

  const goTo = (id) => {
    scrollRef.current.querySelector(`#${id}`)?.scrollIntoView({ block: 'start' })
  }

  return (
    <div className="landing" ref={scrollRef}>
      <div className="l-aurora" aria-hidden="true"><span /><span /><span /></div>

      {/* ── Nav ─────────────────────────────────────────────────────────── */}
      <nav className="l-nav" ref={navRef}>
        <div className="l-nav-inner">
          <span className="logo-word" style={{ fontSize: '1.25rem' }}>Hire<em>OS</em></span>
          <div className="l-nav-links">
            <button className="l-nav-link" onClick={() => goTo('features')}>Features</button>
            <button className="l-nav-link" onClick={() => goTo('how')}>How it works</button>
            <button className="l-nav-link" onClick={() => goTo('why')}>Why</button>
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <button className="l-btn l-btn-ghost" style={{ padding: '0.5rem 0.95rem', fontSize: '0.85rem' }}
              onClick={onSignIn}>Sign in</button>
            <button className="l-btn l-btn-primary" style={{ padding: '0.5rem 1rem', fontSize: '0.85rem' }}
              onClick={onGetStarted}>Get started</button>
          </div>
        </div>
      </nav>

      {/* ── Hero ────────────────────────────────────────────────────────── */}
      <header className="l-hero">
        <div className="l-grid-floor" aria-hidden="true" />

        <div className="l-hero-copy">
          <div className="l-badge">
            <b>New</b> Multi-model engine — Gemini, Groq, Ollama
          </div>

          <h1 className="l-h1">
            {HEADLINE.map((w, i) => (
              <span key={w} className="l-word" style={{ '--i': i }}>
                {i === HEADLINE.length - 1 ? <span className="l-gradient">{w}</span> : w}{' '}
              </span>
            ))}
          </h1>

          <p className="l-hero-sub">
            HireOS is an agentic job-hunt platform. Paste a posting, get a scored fit
            assessment, a company dossier, and an ATS-tuned resume written for that
            exact description — while the pipeline tracks every application for you.
          </p>

          <div className="l-hero-cta">
            <button className="l-btn l-btn-primary" onClick={onGetStarted}>
              Start free <IconArrow />
            </button>
            <button className="l-btn l-btn-ghost" onClick={() => goTo('how')}>
              See how it works
            </button>
          </div>

          <p className="l-hero-note">
            <IconCheck /> No credit card &nbsp;·&nbsp; Bring your own API keys &nbsp;·&nbsp; Data stays in your database
          </p>
        </div>

        {/* 3D stage */}
        <div className="l-stage" onMouseMove={onStageMove} onMouseLeave={onStageLeave}>
          <div className="l-deck" ref={deckRef}>
            <div className="l-window">
              <div className="l-window-bar">
                <i className="l-dot" /><i className="l-dot" /><i className="l-dot" />
                <span className="l-window-title">HireOS — Pipeline</span>
              </div>
              <div className="l-window-body">
                <div className="l-mini-stats">
                  <div className="l-mini-stat"><span>Tracked</span><b>34</b></div>
                  <div className="l-mini-stat"><span>Avg fit</span><b style={{ color: 'var(--success)' }}>81</b></div>
                  <div className="l-mini-stat"><span>Interviews</span><b style={{ color: 'var(--purple)' }}>6</b></div>
                </div>
                {MOCK_ROWS.map((r, i) => (
                  <div key={r.title} className="l-mini-row" style={{ '--i': i }}>
                    <div>
                      <div className="l-mini-title">{r.title}</div>
                      <div className="l-mini-co">{r.co}</div>
                    </div>
                    <span className="l-chip" style={{ background: `color-mix(in srgb, ${r.tint} 16%, transparent)`, color: r.tint }}>
                      {r.state}
                    </span>
                    <span className="l-mini-score">{r.score}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="l-sat l-sat-1">
              <span className="l-sat-icon" style={{ background: 'var(--success-subtle)', color: 'var(--success)' }}><IconTarget /></span>
              <div><b>92 / 100</b><span>Fit score</span></div>
            </div>
            <div className="l-sat l-sat-2">
              <span className="l-sat-icon" style={{ background: 'var(--primary-subtle)', color: 'var(--primary)' }}><IconDoc /></span>
              <div><b>Resume ready</b><span>PDF · DOCX</span></div>
            </div>
            <div className="l-sat l-sat-3">
              <span className="l-sat-icon" style={{ background: 'var(--purple-subtle)', color: 'var(--purple)' }}><IconBolt /></span>
              <div><b>Agent running</b><span>Deep research</span></div>
            </div>
          </div>
        </div>
      </header>

      {/* ── Sources marquee ─────────────────────────────────────────────── */}
      <section className="l-marquee">
        <p className="l-marquee-label">Ingests postings from</p>
        <div className="l-marquee-track">
          {[...SOURCES, ...SOURCES].map((s, i) => (
            <span className="l-marquee-item" key={i} aria-hidden={i >= SOURCES.length}>{s}</span>
          ))}
        </div>
      </section>

      {/* ── Features ────────────────────────────────────────────────────── */}
      <section className="l-section" id="features">
        <div className="l-center reveal">
          <p className="l-eyebrow">Capabilities</p>
          <h2 className="l-h2">Every step of the hunt, run by an agent</h2>
          <p className="l-sub">
            Not a spreadsheet with AI bolted on. Each stage is a background worker that
            writes its result back to your timeline.
          </p>
        </div>

        <div className="l-bento" onMouseMove={onBentoMove}>
          {FEATURES.map((f, i) => (
            <article key={f.title} className={`l-card reveal ${f.span || ''}`} style={{ '--d': `${i * 70}ms` }}>
              <div className="l-card-icon" style={{ background: `color-mix(in srgb, ${f.tint} 15%, transparent)`, color: f.tint }}>
                {f.icon}
              </div>
              <h3>{f.title}</h3>
              <p>{f.body}</p>
            </article>
          ))}
        </div>
      </section>

      {/* ── How it works: 3D carousel ───────────────────────────────────── */}
      <section className="l-section" id="how">
        <div className="l-center reveal">
          <p className="l-eyebrow">Pipeline</p>
          <h2 className="l-h2">Six stages, one timeline</h2>
          <p className="l-sub">Hover to hold the carousel still.</p>
        </div>

        <div className="l-ring-stage reveal">
          <div className="l-ring">
            {STEPS.map((s, i) => (
              <div key={s.n} className="l-ring-face" style={{ '--a': `${i * 60}deg`, '--r': '320px' }}>
                <div className="l-ring-step">STEP {s.n}</div>
                <h4>{s.title}</h4>
                <p>{s.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Stats ───────────────────────────────────────────────────────── */}
      <section className="l-section" id="why">
        <div className="l-center reveal" style={{ marginBottom: '2.5rem' }}>
          <p className="l-eyebrow">Why HireOS</p>
          <h2 className="l-h2">Built for people who apply seriously</h2>
        </div>
        <div className="l-stats">
          {STATS.map((s, i) => (
            <div key={s.l} className="l-stat reveal" style={{ '--d': `${i * 80}ms` }}>
              <b>{s.v}</b><span>{s.l}</span>
            </div>
          ))}
        </div>
      </section>

      {/* ── Final CTA ───────────────────────────────────────────────────── */}
      <section className="l-cta reveal">
        <h2 className="l-h2">Your next application, in minutes</h2>
        <p className="l-sub l-center" style={{ marginBottom: '2rem' }}>
          Create an account, paste one job posting, and see the whole pipeline run.
        </p>
        <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'center', flexWrap: 'wrap' }}>
          <button className="l-btn l-btn-primary" onClick={onGetStarted}>
            Create your account <IconArrow />
          </button>
          <button className="l-btn l-btn-ghost" onClick={onSignIn}>I already have one</button>
        </div>
      </section>

      <footer className="l-footer">
        <span className="logo-word" style={{ fontSize: '1rem' }}>Hire<em>OS</em></span>
        <span>© {new Date().getFullYear()} HireOS — agentic job-hunt platform</span>
      </footer>
    </div>
  )
}
