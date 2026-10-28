import React, { useState } from 'react'
import '../landing.css'

/* Marketing home — Navy & Cream (DESIGN.md §7): navy hero, overlapping stat
   strip, cream "why" with a ruled feature list, navy pipeline, cream-100 CTA
   band, cream footer. Buttons reuse the app's .btn classes; .on-navy flips
   their tokens for the navy sections. */

const IconArrow = () => (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h14M12 5l7 7-7 7" /></svg>
)
const IconMenu = ({ open }) => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" aria-hidden="true"><path d={open ? 'M18 6L6 18M6 6l12 12' : 'M3 12h18M3 6h18M3 18h18'} /></svg>
)

/* ── Content ──────────────────────────────────────────────────────────────── */
const FEATURES = [
  { title: 'Smart job ingestion',
    body: 'Paste a URL or dump raw JD text. An agentic parser pulls out company, title, salary, location and the full structured description — no forms to fill.' },
  { title: 'A–G fit assessment',
    body: 'A 0–100 match score against your master resume plus a 7-axis breakdown: role archetype, leveling strategy, market demand, interview likelihood, JD red flags.' },
  { title: 'Tailored resume engine',
    body: 'ATS-optimized resumes written per job, run through a two-pass design-rule validator, exported to PDF and DOCX.' },
  { title: 'Cover letters & outreach',
    body: 'Letters drafted from the tailored resume so the narrative stays consistent, plus LinkedIn notes tuned per contact type.' },
  { title: 'Deep company research',
    body: 'A 6-axis investigative sweep: business model, competitors, engineering culture, recent news, value prop, leadership.' },
  { title: 'Multi-model engine',
    body: 'Gemini, Groq, OpenRouter and local Ollama behind one adapter. Fan a prompt out to several models and compare.' },
]
const ROMAN = ['I', 'II', 'III', 'IV', 'V', 'VI']

const STEPS = [
  { n: '01', title: 'Track', body: 'Drop in a link or paste the JD. The parser does the data entry.' },
  { n: '02', title: 'Assess', body: 'Seven axes of gap analysis decide whether the role is worth your time.' },
  { n: '03', title: 'Research', body: 'A background agent builds the company dossier while you keep moving.' },
  { n: '04', title: 'Tailor', body: 'Resume and cover letter generated against that specific description.' },
  { n: '05', title: 'Apply', body: 'Export the documents, or hand the posting to the Playwright bot.' },
  { n: '06', title: 'Follow up', body: 'Every state change lands on an application timeline you can search.' },
]

// Product facts, not outcome claims. Swap in real usage numbers only once they are measured.
const STATS = [
  { v: '7', l: 'Evaluation axes per job' },
  { v: '4', l: 'LLM providers, one switch' },
  { v: '2-pass', l: 'Resume rule validation' },
  { v: '100%', l: 'Your data, your database' },
]

const SOURCES = ['LinkedIn', 'Greenhouse', 'Lever', 'Workday', 'Ashby', 'Wellfound', 'Company sites', 'Raw text']

const NAV = [['why', 'Why HireOS'], ['features', 'Features'], ['how', 'How it works']]

const Eyebrow = ({ children }) => (
  <p className="l-eyebrow"><span className="l-rule" aria-hidden="true" />{children}</p>
)

export default function Landing({ onGetStarted, onSignIn, signedIn = false }) {
  const [menuOpen, setMenuOpen] = useState(false)

  const goTo = (id) => {
    setMenuOpen(false)
    document.getElementById(id)?.scrollIntoView({ block: 'start' })
  }

  return (
    <div className="landing">
      {/* ── Navy hero (with nav) ───────────────────────────────────────── */}
      <header className="l-hero on-navy">
        <nav className="l-nav l-container" aria-label="Main">
          <span className="logo-word">Hire<em>OS</em></span>
          <div className="l-nav-links">
            {NAV.map(([id, label]) => (
              <button key={id} className="l-nav-link" onClick={() => goTo(id)}>{label}</button>
            ))}
          </div>
          <div className="l-nav-actions">
            {!signedIn && <button className="btn btn-ghost btn-sm" onClick={onSignIn}>Sign in</button>}
            <button className="btn btn-accent btn-sm" onClick={onGetStarted}>
              {signedIn ? 'Go to dashboard' : 'Get started'}
            </button>
          </div>
          <button className="btn btn-ghost btn-icon l-menu-btn" aria-expanded={menuOpen} aria-controls="l-menu"
            aria-label={menuOpen ? 'Close menu' : 'Open menu'} onClick={() => setMenuOpen(o => !o)}>
            <IconMenu open={menuOpen} />
          </button>
        </nav>
        {menuOpen && (
          <div id="l-menu" className="l-menu l-container">
            {NAV.map(([id, label]) => (
              <button key={id} className="l-nav-link" onClick={() => goTo(id)}>{label}</button>
            ))}
            {!signedIn && <button className="btn btn-outline" onClick={onSignIn}>Sign in</button>}
            <button className="btn btn-accent" onClick={onGetStarted}>{signedIn ? 'Go to dashboard' : 'Get started'}</button>
          </div>
        )}

        <div className="l-container l-hero-grid">
          <div>
            <Eyebrow>Hiring, considered</Eyebrow>
            <h1 className="l-h1">Stop rewriting <em>your resume.</em></h1>
          </div>
          <div>
            <p className="l-lead">
              HireOS is an agentic job-hunt platform. Paste a posting, get a scored fit
              assessment, a company dossier, and an ATS-tuned resume written for that
              exact description — while the pipeline tracks every application for you.
            </p>
            <div className="l-actions">
              <button className="btn btn-primary" onClick={onGetStarted}>
                {signedIn ? 'Open HireOS' : 'Start free'} <IconArrow />
              </button>
              <button className="btn btn-outline" onClick={() => goTo('how')}>See how it works</button>
            </div>
            <p className="l-note">No credit card · Bring your own API keys · Data stays in your database</p>
          </div>
        </div>
      </header>

      {/* ── Overlapping stat strip ─────────────────────────────────────── */}
      <div className="l-container">
        <section className="l-stats" aria-labelledby="l-stats-h">
          <h2 id="l-stats-h" className="sr-only">Built for people who apply seriously</h2>
          {STATS.map(s => (
            <div key={s.l} className="l-stat">
              <span className="l-stat-label">{s.l}</span>
              <span className="l-stat-num">{s.v}</span>
            </div>
          ))}
        </section>
      </div>

      {/* ── Cream "why": copy left, ruled feature list right ───────────── */}
      <section className="l-section" id="why">
        <div className="l-container l-why">
          <div>
            <Eyebrow>Why HireOS</Eyebrow>
            <h2 className="l-h2">Every step of the hunt, run by an <em>agent</em></h2>
            <p className="l-body">
              Not a spreadsheet with AI bolted on. Each stage is a background worker that
              writes its result back to your timeline.
            </p>
            <p className="l-sources">
              <span className="l-sources-label">Ingests postings from</span>
              {SOURCES.join(' · ')}
            </p>
          </div>
          <ol className="l-features" id="features" aria-label="Features">
            {FEATURES.map((f, i) => (
              <li key={f.title} className="l-feature">
                <span className="l-roman" aria-hidden="true">{ROMAN[i]}.</span>
                <h3>{f.title}</h3>
                <p>{f.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* ── Navy pipeline ──────────────────────────────────────────────── */}
      <section className="l-section on-navy l-how" id="how">
        <div className="l-container">
          <Eyebrow>Pipeline</Eyebrow>
          <h2 className="l-h2">Six stages, <em>one timeline</em></h2>
          <ol className="l-steps">
            {STEPS.map(s => (
              <li key={s.n} className="l-step">
                <span className="l-step-n serif">{s.n}</span>
                <h3>{s.title}</h3>
                <p>{s.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* TODO(testimonial): DESIGN.md §7.4 — add a real user quote here (large serif
          italic on cream, name/role in muted Inter) once one exists. Never invent one. */}

      {/* ── Cream-100 CTA band ─────────────────────────────────────────── */}
      <section className="l-cta">
        <div className="l-container l-cta-inner">
          <div>
            <h2 className="l-h2">Your next application, in <em>minutes</em></h2>
            <p className="l-body">Create an account, paste one job posting, and see the whole pipeline run.</p>
          </div>
          <div className="l-actions">
            <button className="btn btn-primary" onClick={onGetStarted}>
              {signedIn ? 'Back to your pipeline' : 'Create your account'} <IconArrow />
            </button>
            {!signedIn && <button className="btn btn-outline" onClick={onSignIn}>I already have one</button>}
          </div>
        </div>
      </section>

      <footer className="l-footer">
        <div className="l-container l-footer-inner">
          <span className="logo-word">Hire<em>OS</em></span>
          <span>© {new Date().getFullYear()} HireOS — agentic job-hunt platform</span>
        </div>
      </footer>
    </div>
  )
}
