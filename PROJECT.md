---
id: op-job-hunt
name: OP Job Hunt
status: active
priority: P0
type: internal_tool
last_updated: 2026-05-09
---

## Description
Full-stack agentic job hunt portal. Ingests job postings (URL or raw JD), runs structured fit assessment (A-G, 7-axis, 0-100 score), generates tailored ATS-optimized resumes, writes cover letters, tracks application pipeline, and automates applications via Playwright bot. Multi-model LLM engine with provider fan-out.

## Tech Stack
- Frontend: React
- Backend: FastAPI, SQLite
- Automation: Playwright
- LLMs: Gemini (primary), Groq (Llama 3), OpenRouter, Ollama
- Infrastructure: Docker, docker-compose

## Surface Areas

| Surface | Enabled | Detail |
|---|---|---|
| Resume | false | Internal tool |
| Portfolio | false | Private — not for public showcase |
| GitHub | false | Private repo |
| Live | true | local (localhost) |
| Job Hunt | false | This IS the job hunt system |

## Skills Demonstrated
(Internal use — not showcased externally)

## Key Features
- Smart job ingestion (URL or raw text)
- A-G structured fit assessment (7-axis evaluation)
- Async agent architecture (fire-and-forget, real-time polling)
- Multi-model LLM with comparison engine
- Hyper-targeted resume generation
- Application timeline tracking
- Playwright automation bot

## Current Status
Active and in use for current job hunt. Add features as needed via `features.md`.

## Changelog
- 2026-05-09: Initial PROJECT.md entry
