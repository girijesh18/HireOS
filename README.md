# HireOS

**Agentic job application OS.** Multi-LLM pipeline for resume generation, evaluation, interview prep, and full pipeline tracking — built to land the job, not just track it.

---

## What it does

- **Track jobs** — paste a URL or JD text, AI auto-extracts company/title/salary/stack
- **Generate tailored resumes** — 3-pass pipeline: tailor → validate design rules → brutal critic review
- **Evaluate fit** — 7-block structured evaluation (role match, CV gaps, comp, legitimacy, interview angles)
- **Interview prep** — STAR story generation keyed to the JD requirements
- **LinkedIn outreach** — AI-drafted recruiter/HM messages
- **Deep company research** — funding, culture, red flags, talking points
- **Pipeline tracking** — Kanban-style stage management (Found → Applied → Interview → Offer)
- **Insights dashboard** — momentum score, AI narrative on your activity, interaction history with semantic search
- **Multi-LLM** — Gemini, Groq, Claude, OpenAI switchable per task

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI, SQLite, SQLAlchemy |
| AI / Agents | Gemini 2.5, Claude Sonnet, Groq, OpenAI |
| Vector store | ChromaDB (embedded, local) |
| Frontend | React + Vite |
| PDF generation | Playwright (Chromium) |
| Web scraping | Playwright Stealth |

## Project structure

```
HireOS/
├── backend/
│   ├── main.py          # FastAPI app, all endpoints
│   ├── agents.py        # All AI agents (resume, critic, eval, research, insights…)
│   ├── llm_router.py    # LLM abstraction (Gemini / Claude / Groq / OpenAI)
│   ├── database.py      # SQLAlchemy models
│   ├── chat_store.py    # ChromaDB interaction history
│   └── schemas.py       # Pydantic schemas
├── frontend/
│   └── src/
│       ├── views/       # Dashboard, JobList, JobDetail, Insights, StoryBank, Settings
│       └── api/client.js
└── meta/
    └── resume_design.md # Resume formatting rules (auto-injected into every generation)
```

## Setup

```bash
# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
uvicorn main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

**Env vars** (`.env` in `backend/`):
```
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=...
GROQ_API_KEY=...
OPENAI_API_KEY=...
```

App runs at `http://localhost:5173`, API at `http://localhost:8000`.

## Resume pipeline

1. **Pass 1** — LLM tailors resume to JD using master resume components + design rules
2. **Pass 2** — LLM validates against `meta/resume_design.md` rules and fixes violations
3. **Pass 3** — `CriticAgent` (separate LLM, default Claude) delivers brutal structured critique: fatal weaknesses, weak bullets with rewrites, ATS red flags, keyword gaps, competitor comparison

---

Built by [Girijesh Singh](https://linkedin.com/in/girijesh-singh)
