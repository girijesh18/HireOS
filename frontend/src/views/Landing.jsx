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
  { title: 'Discovery that runs itself',
    body: 'Twice every weekday it checks the Greenhouse, Lever, Ashby and Workday boards of the companies you pick, LinkedIn\'s public job search and your job-alert emails, then scores every new posting against your resume.' },
  { title: 'A resume for every job',
    body: 'Each match gets its own ATS-friendly resume, as PDF and DOCX, and a cover letter when the form asks for one. Everything is written from your master resume, never made up.' },
  { title: 'Review from your phone',
    body: 'Resume cards land in Telegram or Discord with the PDF and a list of what changed. Approve, skip or ask for edits in plain words, or just ask "what came in today?"' },
  { title: 'An agent that fills the form',
    body: 'A real browser fills Workday, Greenhouse, Lever and Ashby applications, reads back every value it entered and sends you the review page. Open the live screen to watch it work.' },
  { title: 'Answers you can trust',
    body: 'Facts come only from your profile and resume. A question it can\'t answer from them comes to you once, and your reply is saved for the next form.' },
  { title: 'Status that updates itself',
    body: 'Read-only Gmail scanning moves each job to received, rejected, interview or offer, and you get a message whenever something needs you.' },
]
const ROMAN = ['I', 'II', 'III', 'IV', 'V', 'VI']

const STEPS = [
  { n: '01', title: 'Discover', body: 'Scheduled runs pull fresh postings from the boards and searches you choose.' },
  { n: '02', title: 'Score', body: 'Each job gets a fit score and gap analysis against your resume; weak matches drop out.' },
  { n: '03', title: 'Tailor', body: 'A resume, and a cover letter when needed, written for that exact posting.' },
  { n: '04', title: 'Approve', body: 'One tap in Telegram, Discord or the web, or let auto mode approve your best matches.' },
  { n: '05', title: 'Apply', body: 'The agent fills and checks the form. Review mode waits for your Submit; auto mode sends clean forms itself. CAPTCHAs and email codes always come to you.' },
  { n: '06', title: 'Track', body: 'Email updates, daily caps, company cooldowns and one timeline per job.' },
]

// Product facts, not outcome claims. Swap in real usage numbers only once they are measured.
const STATS = [
  { v: '4', l: 'Application systems it fills' },
  { v: '2×', l: 'Discovery runs every weekday' },
  { v: '2', l: 'Modes: review or auto' },
  { v: '0', l: 'Answers invented' },
]

const SOURCES = ['Greenhouse', 'Lever', 'Ashby', 'Workday', 'LinkedIn search', 'Job-alert emails', 'Pasted links & text']

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
            <h1 className="l-h1">Find it. Tailor it. <em>Apply.</em></h1>
          </div>
          <div>
            <p className="l-lead">
              HireOS finds jobs that fit you, writes a resume for each one, and fills the
              application in a real browser. Review each one from Telegram or Discord, or
              let auto mode send the ones it could fill entirely from your resume.
            </p>
            <div className="l-actions">
              <button className="btn btn-primary" onClick={onGetStarted}>
                {signedIn ? 'Open HireOS' : 'Start free'} <IconArrow />
              </button>
              <button className="btn btn-outline" onClick={() => goTo('how')}>See how it works</button>
            </div>
            <p className="l-note">Free to start · Answers only from your resume · Bot checks always stay with you</p>
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
            <h2 className="l-h2">The whole hunt, run by an <em>agent</em></h2>
            <p className="l-body">
              Set your targets once. HireOS searches, scores, tailors and applies in the
              background, and asks you only when a decision is yours to make.
            </p>
            <p className="l-sources">
              <span className="l-sources-label">Finds postings on</span>
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
          <h2 className="l-h2">From posting to <em>applied</em></h2>
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
            <h2 className="l-h2">Set your targets <em>once</em></h2>
            <p className="l-body">Create an account, add your resume and the companies you want. The next discovery run does the rest.</p>
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
          <span>© {new Date().getFullYear()} HireOS — your job hunt, on autopilot with you at the wheel</span>
        </div>
      </footer>
    </div>
  )
}
