# OP_Job_Hunt Platform Features

This document provides a comprehensive, hierarchical breakdown of all features currently implemented in the **OP_Job_Hunt** platform. 

> [!NOTE]
> **To AI Assistants:** This is a living document. Whenever a new feature is added to the OP_Job_Hunt project, you must automatically update this file to reflect the newly integrated capabilities. Keep the hierarchy clean and properly categorized.

---

## 🌟 Major Features

### 1. Agentic Tracking Pipeline
The core mechanic of OP_Job_Hunt is the ability to ingest jobs and track them through an automated background pipeline.
* **Smart Ingestion:** Track jobs simply by pasting a URL or dumping unformatted JD text. 
* **Auto-Extraction:** Agentic parser extracts company name, job title, and full structured job description.
* **Timeline Events:** Every pipeline state change, agent action, and document generation is historically tracked in an application timeline.

### 2. Multi-Model LLM Engine
Switch seamlessly across state-of-the-art AI brains perfectly tailored to your needs.
* **Gemini Selector:** Precision selection across specific Gemini models including Gemini 2.5 Pro (Powerful), Gemini 2.5 Flash (Fast), Gemini 1.5 Pro, and Gemini 1.5 Flash.
* **Provider Fan-Out:** Built-in adapter for Groq (Llama 3 Fast), OpenRouter (Free Tier models), and Ollama (Local/Private).
* **Comparison Engine:** Fire requests to multiple models simultaneously to compare generation reasoning and quality.

### 3. Asynchronous Agent Architecture
Never wait for the AI to finish thinking.
* **Fire-and-Forget Executions:** Agent tasks (like Deep Research) execute asynchronously on backend background threads.
* **Persistent Caching:** Responses (Research Data, Stories, Cover Letters) are permanently stored in a SQLite database and fetched instantly upon navigation.
* **Real-time UI Polling:** The React interface uses a global heartbeat to track executing tasks, dynamically hydrating panels the second a task finishes without manual refreshing.

### 4. A-G Structured Fit Assessment
Strategic gap analysis to determine if a job is worth applying for.
* **Global Match Scoring:** Quantitative out-of-100% score comparing your master resume to the JD.
* **7-Axis Evaluation:** Block-by-block breakdown analyzing role archetype, resume strength, leveling strategy, market demand, structural personalization, interview likelihood, and JD legitimacy/red-flags.

---

## 🚀 Agent-Driven Workflows (Document Generation)

### 1. Tailored Resume Generation
* **Hyper-Targeted Writing:** Ingests the JD and your Master Resume to generate a precise, ATS-optimized markdown resume minimizing capability gaps.
* **Resume Design Rules (`meta/resume_design.md`):** Two-pass enforcement system. Pass 1 injects design rules into the generation prompt. Pass 2 runs a dedicated validation agent that reviews the output against the rules and returns a corrected version. Edit the file to change formatting, structure, tone, or ATS rules — changes take effect on the next generation with no code changes.
* **Version Control:** Each generated document is treated as a unique version linked to the specific job timeline.
* **PDF / DOCX Exporting:** Auto-conversion of Markdown responses to fully styled `.pdf` and `.docx` deliverables.

### 2. Cover Letter Engine
* **Context-Aware Drafting:** Generates a cover letter based directly on the *tailored resume* rather than the unrefined master, ensuring narrative consistency.

### 3. LinkedIn Outreach Architect
* **Strategic Networking:** Generates high-conversion connection requests and follow-up templates exactly for the role.
* **Contact Types:** Adjustable tone and format depending on whether you are messaging the Hiring Manager, an Internal Recruiter, or a Peer.

### 4. Continuous Deep Company Research
* **6-Axis Investigative Agent:** Performs a high-depth sweep covering: 
    - Company Core Business
    - Competitive Landscape
    - Technical Stack / Engineering Culture
    - Recent News & Financial Trajectory
    - Value Proposition
    - Executive/Leadership Context

### 5. Infinite Interview Prep (Story Bank)
* **STAR Formatter:** Cross-references the job description requirements against your master resume to generate behavioral interview stories formatted in STAR+Reflection.
* **Deduplication Validation:** The `StoryBankEntry` cache prevents the agent from rewriting stories that have already been generated in previous preparation runs.

---

## ⚙️ Minor Features & Utilities

### 1. Global Assistant
* **Floating Chatbot:** Always-present UI component for on-the-fly questions, quick ideation, and context injection outside the formal pipeline.

### 2. Job Table Management
* **Kanban State Alignment:** Categorical tracking of stages manually (Found, Applied, Screening, Interview 1, Offer).
* **Job Detail View:** A unified central dashboard consolidating all fetched intelligence, generated documents, and timeline events for a single application.

### 3. Settings Configuration
* **Master Profile:** Ability to load a master resume into the system settings that serves as the baseline ground truth for all downstream AI tasks.
* **LLM API Configuration:** Manage your keys natively for Gemini, Groq, Together, and OpenRouter.
