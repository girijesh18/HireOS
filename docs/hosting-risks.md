# Hosting risks (multi-user HireOS without OpenClaw)

Written 2026-10-27 while moving discovery, alerts and applying out of OpenClaw.
To revisit before HireOS is opened to other users. None of these block local use.

| Risk | Detail | Level | Direction |
|---|---|---|---|
| LLM | Discovery, prepare, form answers and edits default to the local llama.cpp model (`llm: "local"`). Hosted has no GPU. | Med | OpenRouter (or another hosted model) as the hosted default; re-check prompt quality on it; tell users resume data goes to that provider. |
| Two Telegram pollers on one token | The bot long-polls `getUpdates` inside the API process. Fly and a local instance with the same `TELEGRAM_BOT_TOKEN` get 409 Conflict and steal each other's updates. | High at deploy | Webhook on the hosted instance, or a separate bot token per environment. Never the same token in two running instances. |
| Apply worker host | Fly VM is 1 GB shared CPU (`iad`). Chromium + the API won't fit; datacenter IPs likely see more bot walls / CAPTCHAs on Workday and others. | High | Separate worker machine(s), one browser at a time per machine; measure bot-wall rate before scaling; consider a region near the users. |
| ATS account vault | Workday sign-in is a plaintext JSON file for one user (`ats-account.json`). | High | Per-user, encrypted at rest (key outside the DB), never returned by the API, only decrypted inside the worker. |
| SQLite | One file shared by the API, Telegram poller, background loops and remote workers. | Med | Fine for a handful of users; Postgres before real multi-user load. |
| Workday site list provenance | The 45 Workday career sites in the discovery targets came from ApplyPilot's `employers.yaml` (AGPL). | Med (legal) | Don't ship that list in the public repo as-is; let users add companies, and grow the list from find-ats hits. |
| Single-user assumptions | Prompts, skills and discovery targets were written for one person (their name, city, searches). | Med | Every target, rule and prompt input comes from the user's own settings. |
| LinkedIn guest endpoints at scale | `linkedin-search` and find-ats read LinkedIn guest pages. One user from a home IP is fine; many users from datacenter IPs means rate limits or blocks, and a ToS grey area. | Med-High at scale | Per-user rate limits, caching, back-off on 429; prefer email alerts and ATS feeds; never log in to LinkedIn. |
| Gmail access | Outcome scanning reads the user's mailbox over IMAP (app password). | Med | Per-user OAuth (read-only scope) before hosting; never store app passwords in plaintext. |
| "HireOS is down" alerts | HireOS can't report its own outage. | Low | External uptime check on `/api/health` (Fly checks + a pinger) that messages the operator. |
