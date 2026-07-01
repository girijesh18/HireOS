"""
HireOS — all specialized agents.

Each agent is a standalone async class. They share:
- LLMRouter for LLM calls
- Database session (passed in) for persistence
- The output directory for file artifacts
"""
from __future__ import annotations

import json
import os
import re
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger
from database import Job, EvaluationReport, StoryBankEntry, FollowUpLog
from scraper import StealthScraper

from llm_router import LLMRouter


# ── System prompts ─────────────────────────────────────────────────────────────

SYSTEM_JOB_HUNTER = """You are a job discovery research agent for a senior Principal AI Engineer.
Your job is to analyze job descriptions and identify high-quality opportunities.
When given a job URL or raw job description text, extract structured information.
Always respond in valid JSON only."""

SYSTEM_FIT_AGENT = """You are an elite technical recruiter and career strategist specializing in AI/ML engineering roles.
You analyze job descriptions against a candidate's resume and produce a precise, actionable fit assessment.
Always respond in valid JSON only, no markdown fences."""

SYSTEM_RESUME_AGENT = """You are a world-class AI/ML technical resume writer.
You specialize in creating Principal Engineer-level resumes that are ATS-optimized and
deeply tailored to specific job descriptions. You write concisely, powerfully, and with
extremely high technical specificity. Use the candidate's actual accomplishments — never fabricate metrics.
Return the complete resume in the exact markdown format specified in the prompt.
CRITICAL: Experience entries must use `### Company, Location || Dates` format with double pipe for date alignment.
Do NOT wrap output in ``` code fences. Return raw markdown only."""

SYSTEM_COVER_LETTER_AGENT = """You are an elite cover letter writer for Principal AI Engineer roles at top tech companies.
You write compelling, human, concise cover letters (3-4 paragraphs) that:
1. Open with a strong hook connecting the candidate to the company's mission
2. Highlight 2-3 key achievements directly relevant to the JD
3. Close with confident, forward-looking conviction
Never use clichés. Write like a real person, not a bot. Return markdown only."""

SYSTEM_CHAT_DISPATCHER = """You are the AI command center for a job application tracking system called HireOS.
The user gives you natural language instructions. You figure out their intent and respond with a JSON action.

Available actions:
- track_job: { "action": "track_job", "url": "...", "company": "...", "title": "..." }
- analyze_job: { "action": "analyze_job", "job_id": 123 }
- evaluate_job_structured: { "action": "evaluate_job_structured", "job_id": 123 }
- linkedin_outreach: { "action": "linkedin_outreach", "job_id": 123, "contact_type": "recruiter" }
- deep_research: { "action": "deep_research", "job_id": 123 }
- interview_prep: { "action": "interview_prep", "job_id": 123 }
- generate_resume: { "action": "generate_resume", "job_id": 123, "llm": "gemini" }
- generate_cover_letter: { "action": "generate_cover_letter", "job_id": 123, "llm": "gemini" }
- compare_llms: { "action": "compare_llms", "job_id": 123, "task": "resume" }
- list_jobs: { "action": "list_jobs", "filter": "pending" }
- get_stats: { "action": "get_stats" }
- general_reply: { "action": "general_reply", "text": "..." }

If you cannot determine a structured action, return general_reply.
Always respond in valid JSON only."""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _strip_code_fences(text: str) -> str:
    """Remove ```markdown / ``` / ```md wrappers LLMs sometimes add around resume text."""
    text = text.strip()
    for fence in ("```markdown", "```md", "```"):
        if text.startswith(fence):
            text = text[len(fence):]
            break
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _resume_body(text: str, name: str = "") -> str:
    """Return the resume body starting at the first REAL section, dropping any header the LLM wrote.
    Handles the LLM emitting its name as `# Name`, `## NAME` (a section), or plain text — all are
    skipped so the Python-built header isn't duplicated.
    """
    lines = text.split('\n')
    name_u = (name or '').strip().upper()
    section_idxs = [i for i, l in enumerate(lines) if l.strip().startswith('## ')]
    if not section_idxs:
        return text.strip()
    for idx in section_idxs:
        heading = lines[idx].strip().lstrip('#').strip().upper()
        # skip a section heading that is actually the candidate's name
        if name_u and (heading == name_u or heading in name_u or name_u in heading):
            continue
        return '\n'.join(lines[idx:]).strip()
    # every section matched the name (shouldn't happen) — keep from the last
    return '\n'.join(lines[section_idxs[-1]:]).strip()


def _parse_json(text: str) -> Dict:
    """Extract JSON from LLM output, handling markdown fences and malformed output."""
    # Strip markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON by finding the outermost { and matching }
    first_brace = text.find('{')
    if first_brace != -1:
        # Find the last } in the text
        last_brace = text.rfind('}')
        if last_brace > first_brace:
            candidate = text[first_brace:last_brace + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

    # Attempt to repair truncated JSON
    repaired = text
    if not repaired.strip().startswith('{'):
        idx = repaired.find('{')
        if idx != -1:
            repaired = repaired[idx:]

    # Fix unterminated strings by closing them
    in_string = False
    escape_next = False
    for i, ch in enumerate(repaired):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\':
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
    if in_string:
        repaired += '"'

    # Balance braces and brackets
    open_braces = repaired.count('{') - repaired.count('}')
    open_brackets = repaired.count('[') - repaired.count(']')
    repaired += ']' * max(0, open_brackets)
    repaired += '}' * max(0, open_braces)

    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Last resort: truncate after the last complete key-value pair
    last_try = repaired
    last_comma = last_try.rfind(',')
    if last_comma > 0:
        last_try = last_try[:last_comma]
        open_braces = last_try.count('{') - last_try.count('}')
        open_brackets = last_try.count('[') - last_try.count(']')
        last_try += ']' * max(0, open_brackets)
        last_try += '}' * max(0, open_braces)
        try:
            return json.loads(last_try)
        except json.JSONDecodeError:
            pass

    # Final attempt: try with various truncation points
    for i in range(3):
        last_try = repaired
        # Find the nth-from-last comma
        for _ in range(i + 1):
            pos = last_try.rfind(',')
            if pos > 0:
                last_try = last_try[:pos]
        open_b = last_try.count('{') - last_try.count('}')
        open_k = last_try.count('[') - last_try.count(']')
        last_try += ']' * max(0, open_k)
        last_try += '}' * max(0, open_b)
        try:
            return json.loads(last_try)
        except json.JSONDecodeError:
            continue

    raise json.JSONDecodeError(
        f"Could not parse LLM response as JSON. Raw text (first 500 chars): {text[:500]}",
        text, 0
    )


# ── Agent 1: Job Discovery ─────────────────────────────────────────────────────

class JobDiscoveryAgent:
    """
    Extracts structured job info from a URL or raw JD text.
    Optionally fetches the page content from a URL.
    """

    def __init__(self, router: LLMRouter):
        self.router = router

    async def analyze_text(self, jd_text: str, url: str = "", llm: str = "gemini") -> Dict:
        """Extract structured job data from raw JD text (fallback if no url)."""
        # If url is provided, use the CrewAI Tool-calling implementation
        if url:
            return await self.analyze_url(url, llm=llm)

        prompt = f"""Extract structured information from this job posting.

Job Posting:
{jd_text[:6000]}

Return JSON with these exact fields:
{{
  "company": "Company Name",
  "title": "Job Title",
  "location": "City, State or Remote",
  "remote": true or false,
  "salary_min": 180000 or null,
  "salary_max": 250000 or null,
  "platform": "linkedin|greenhouse|lever|workday|ashby|direct|other",
  "listed_at": "YYYY-MM-DD or null",
  "recruiter_name": "Name or null",
  "recruiter_email": "email or null",
  "job_description": "The complete job description re-organized into a highly structured, bullet-point first format. Group information into logical sections (e.g. Core Mission, Responsibilities, Qualifications, Benefits) using ## headers. Use bullet points for virtually everything to maximize readability while ensuring zero context or detail from the original text is lost.",,
  "tech_stack": ["Python", "PyTorch", ...],
  "seniority": "Principal|Staff|Senior|Mid",
  "url": "{url}"
}}"""
        text = await self.router.complete(prompt, llm=llm, system=SYSTEM_JOB_HUNTER, temperature=0.2)
        data = _parse_json(text)
        if url:
            data["url"] = url
        return data

    async def analyze_url(self, url: str, llm: str = "gemini", captcha_key: str = None, proxy_url: str = None) -> Dict:
        """Fetch a job posting URL and extract structured data using CrewAI and Native Tools."""
        url = self._normalize_linkedin_url(url)
        logger.info(f"[JobDiscovery] Analyzing {url} with CrewAI")

        import asyncio
        import threading
        from crewai import Agent, Task, Crew, Process
        from crewai.tools import tool

        # 1. Provide the Scraper as a native tool for the LLM
        @tool("Web Scraper")
        def web_scraper(target_url: str) -> str:
            """Useful for scraping and extracting text from any URL. Input must be a valid http URL."""
            result = ["Error fetching page"]
            def _fetch():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                # Call our robust existing fetch logic
                res = new_loop.run_until_complete(self._fetch_page(target_url, captcha_key, proxy_url))
                result[0] = res[:15000] # Cap to prevent context blowouts
                new_loop.close()
            t = threading.Thread(target=_fetch)
            t.start()
            t.join()
            return result[0]

        # 2. Configure CrewAI
        if self.router.gemini_key:
            os.environ["GEMINI_API_KEY"] = self.router.gemini_key
            
        crew_llm = "gemini/gemini-1.5-pro" if "gemini" in llm else "gpt-4o-mini"

        # 3. Create the Autonomous Agent
        hunter_agent = Agent(
            role="Principal AI Job Discovery Researcher",
            goal="Navigate to the provided URL, read the job description, and extract precisely structured JSON data.",
            backstory="You are an elite web-research agent. You autonomously use the Web Scraper tool to read URLs and then synthesize the raw text into clean, structured data.",
            tools=[web_scraper],
            llm=crew_llm,
            verbose=True,
            allow_delegation=False
        )

        # 4. Define the Task
        extract_task = Task(
            description=f"""Use the Web Scraper tool to fetch the contents of this URL: {url}
            
            Once you have the text, extract the following structured information into a valid JSON object:
            - company
            - title
            - location
            - remote (boolean)
            - salary_min (integer or null)
            - salary_max (integer or null)
            - platform
            - listed_at
            - recruiter_name
            - recruiter_email
            - job_description (The full JD organized with markdown bullet points)
            - tech_stack (list of strings)
            - seniority
            - url (must be exactly: {url})
            
            Return ONLY the raw JSON string without markdown code fences.""",
            expected_output="A valid JSON string containing the extracted job details.",
            agent=hunter_agent
        )

        crew = Crew(
            agents=[hunter_agent],
            tasks=[extract_task],
            process=Process.sequential,
            verbose=False
        )

        # Execute synchronously in an executor since CrewAI kickoff is blocking
        loop = asyncio.get_running_loop()
        def _run_crew():
            return crew.kickoff()
        crew_result = await loop.run_in_executor(None, _run_crew)
        
        output_str = str(crew_result.raw if hasattr(crew_result, 'raw') else crew_result)
        
        data = _parse_json(output_str)
        data["url"] = url
        return data

    async def _fetch_page(self, url: str, captcha_key: str = None, proxy_url: str = None) -> str:
        """Fetch page text, stripping HTML tags. Falls back to StealthScraper for bot-protected sites."""
        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            ) as client:
                resp = await client.get(url)
                text = resp.text

                # Detect login walls or bot challenges
                login_signals = [
                    'authwall', 'login', 'sign-in', 'signin',
                    'session_redirect', 'uas/login', 'checkpoint',
                    'captcha', 'perfdrive', 'stormcaster', 'bot-check'
                ]
                
                needs_stealth = False
                if any(s in str(resp.url).lower() for s in login_signals):
                    needs_stealth = True
                
                if not needs_stealth:
                    # Basic HTML stripping to check content
                    temp_text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL)
                    temp_text = re.sub(r"<style[^>]*>.*?</style>", " ", temp_text, flags=re.DOTALL)
                    temp_text = re.sub(r"<[^>]+>", " ", temp_text)
                    temp_text = re.sub(r"\s+", " ", temp_text).strip()
                    
                    bot_signatures = ["behavior on this site made us think", "solve this CAPTCHA", "Radware Captcha", "unblock to the website"]
                    if any(sig in temp_text for sig in bot_signatures) or len(temp_text) < 500:
                        needs_stealth = True

                if needs_stealth:
                    logger.info(f"[JobDiscovery] Bot wall detected on {url}. Falling back to StealthScraper...")
                    stealth_text = await StealthScraper.fetch(url, captcha_key=captcha_key, proxy_url=proxy_url)
                    
                    # Safety check
                    bot_signals = ["behavior on this site made us think", "solve this CAPTCHA", "unblock to the website", "Anomaly Detected"]
                    if any(sig in stealth_text for sig in bot_signals) or len(stealth_text) < 500:
                        logger.error(f"[JobDiscovery] Stealth bypass failed for {url}")
                        raise ValueError(f"Site is protected by a strong bot-wall. Use 'Paste JD' mode.")
                    
                    return stealth_text[:8000]

                # If no stealth needed, finish processing HTTP response
                text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL)
                text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                return text[:8000]

        except ValueError as ve:
            # Re-raise explicit validation errors
            raise ve
        except Exception as e:
            logger.warning(f"[JobDiscovery] HTTP fetch failed for {url}, trying Stealth fallback: {e}")
            try:
                stealth_text = await StealthScraper.fetch(url, captcha_key=captcha_key, proxy_url=proxy_url)
                # Apply same safety check on fallback
                bot_signals = ["behavior on this site made us think", "solve this CAPTCHA", "unblock to the website", "Anomaly Detected"]
                if any(sig in stealth_text for sig in bot_signals) or len(stealth_text) < 500:
                   raise ValueError("Site is protected by a strong bot-wall. Use 'Paste JD'.")
                return stealth_text[:8000]
            except Exception as se:
                logger.error(f"[JobDiscovery] Both HTTP and Stealth fetch failed: {se}")
                raise ValueError(f"Could not access website: {url}. Error: {se}")


# ── Agent 2: Fit Assessment & Gap Analysis ─────────────────────────────────────

SYSTEM_EVALUATION_AGENT = """You are an elite career strategist and technical recruiter AI.
You perform a comprehensive 7-block (A-G) structured evaluation of job offers.
You analyze job descriptions against a candidate's resume and produce an exhaustive,
actionable assessment.
Always respond in valid JSON only, no markdown fences.
Never fabricate metrics or experience — only use what appears in the resume."""


class FitAssessmentAgent:
    """
    Enhanced Fit Assessment Agent — produces a structured A-G evaluation
    inspired by career-ops' proven 7-block system.

    Blocks:
    - A: Role Summary (archetype, domain, seniority, remote, TL;DR)
    - B: CV Match (requirement-by-requirement, gaps, mitigations)
    - C: Level & Strategy (positioning plan)
    - D: Comp & Demand (salary research, market data)
    - E: Personalization Plan (CV + LinkedIn changes)
    - F: Interview Prep (STAR+Reflection stories)
    - G: Posting Legitimacy (ghost job detection)
    """

    def __init__(self, router: LLMRouter):
        self.router = router

    async def analyze(
        self,
        job_description: str,
        master_resume: str,
        company: str = "",
        title: str = "",
        llm: str = "gemini",
    ) -> Dict:
        prompt = f"""Perform a comprehensive A-G evaluation of this job offer against the candidate's resume.

== JOB: {title} at {company} ==
{job_description[:5000]}

== CANDIDATE RESUME ==
{master_resume[:5000]}

Return a JSON object with this exact structure:
{{
  "block_a_role_summary": {{
    "archetype": "AI Platform/LLMOps | Agentic/Automation | Technical AI PM | AI Solutions Architect | AI Forward Deployed | AI Transformation",
    "domain": "platform | agentic | llmops | ml | enterprise | other",
    "function": "build | consult | manage | deploy",
    "seniority": "Principal | Staff | Senior | Mid | Lead",
    "remote": "full | hybrid | onsite",
    "team_size": "unknown or extracted from JD",
    "tldr": "One-sentence summary of this role"
  }},
  "block_b_cv_match": {{
    "requirements": [
      {{
        "jd_requirement": "Specific requirement from JD",
        "resume_evidence": "Exact quote or reference from resume, or 'NOT FOUND'",
        "match_strength": "strong | partial | missing"
      }}
    ],
    "gaps": [
      {{
        "gap": "What the JD requires that's missing",
        "severity": "hard_blocker | nice_to_have",
        "adjacent_experience": "Related experience the candidate has, or null",
        "mitigation": "Concrete plan to address this gap"
      }}
    ],
    "keywords_matched": ["keyword1", "keyword2"],
    "keywords_missing": ["keyword1", "keyword2"]
  }},
  "block_c_level_strategy": {{
    "jd_level": "Senior | Staff | Principal | Lead",
    "candidate_natural_level": "Based on resume experience",
    "positioning_plan": "Specific phrases and achievements to highlight to sell at the right level",
    "downlevel_contingency": "What to do if they offer a lower level"
  }},
  "block_d_comp_demand": {{
    "estimated_salary_range": "$XXXk-$XXXk or unknown",
    "market_positioning": "Above | At | Below market",
    "demand_trend": "High | Moderate | Low demand for this role type",
    "notes": "Any additional comp intelligence"
  }},
  "block_e_personalization": {{
    "cv_changes": [
      "Top 5 specific changes to make to the resume for this role"
    ],
    "linkedin_changes": [
      "Top 5 specific LinkedIn profile optimizations"
    ]
  }},
  "block_f_interview_prep": {{
    "star_stories": [
      {{
        "jd_requirement": "The requirement this story addresses",
        "title": "Short title for the story",
        "situation": "The context",
        "task": "What needed to be done",
        "action": "What the candidate did",
        "result": "Quantified outcome",
        "reflection": "What was learned or would be done differently"
      }}
    ],
    "red_flag_questions": [
      {{
        "question": "A likely tough question",
        "suggested_response": "How to handle it"
      }}
    ],
    "case_study_recommendation": "Which project from the resume to present and how to frame it"
  }},
  "block_g_legitimacy": {{
    "tier": "High Confidence | Proceed with Caution | Suspicious",
    "signals": [
      {{
        "signal": "What was observed",
        "finding": "The specific finding",
        "weight": "Positive | Neutral | Concerning"
      }}
    ],
    "context_notes": "Any caveats or edge cases"
  }},
  "global_score": 4.2,
  "match_score": 82,
  "summary": "2-3 sentence executive summary of the fit",
  "strengths": ["Strength 1", "Strength 2"],
  "gaps": ["Gap 1", "Gap 2"],
  "action_items": ["Action 1", "Action 2"]
}}

Scoring guide (1-100 global score):
- 90+: Strong match, recommend applying immediately
- 80-89: Good match, worth applying
- 70-79: Decent but not ideal
- Below 70: Recommend against applying

Be specific, technical, and honest. Don't inflate scores. Cite exact resume lines when matching.
Generate 6-10 STAR+Reflection stories. The Reflection column signals seniority."""

        text = await self.router.complete(
            prompt, llm=llm, system=SYSTEM_EVALUATION_AGENT,
            temperature=0.3, max_tokens=12000
        )
        return _parse_json(text)


# ── Agent 3: Resume Tailoring ──────────────────────────────────────────────────

class ResumeTailoringAgent:
    """
    Generates a tailored resume version for a specific job.
    Uses master resume + gap analysis + optional GitHub README context.
    """

    def __init__(self, router: LLMRouter):
        self.router = router

    def enforce_header(self, resume_md: str, contact_facts: dict) -> str:
        """Replace whatever the LLM wrote as the header with Python-extracted verified facts.
        This runs after every LLM call — guarantees no hallucinated contact info."""
        name = (contact_facts or {}).get('name', '').strip()
        if not name:
            return resume_md
        contact_parts = [
            v for k in ('email', 'phone', 'location', 'linkedin', 'github')
            if (v := (contact_facts or {}).get(k, '').strip())
        ]
        correct_header = f"# {name}\n{' | '.join(contact_parts)}"
        body = _resume_body(resume_md, name)
        return correct_header + '\n\n' + body

    async def tailor(
        self,
        job_description: str,
        master_resume: str,
        contact_facts: Optional[Dict] = None,
        company: str = "",
        title: str = "",
        gaps: Optional[List[str]] = None,
        action_items: Optional[List[str]] = None,
        github_context: str = "",
        feedback: str = "",
        design_rules: str = "",
        llm: str = "gemini",
    ) -> str:
        gaps_text    = "\n".join(f"- {g}" for g in (gaps or []))
        actions_text = "\n".join(f"- {a}" for a in (action_items or []))

        extras = ""
        if github_context:
            extras += f"\n\nGITHUB PROJECTS (use for skills/projects sections):\n{github_context[:1500]}"
        if feedback:
            extras += f"\n\nREFINEMENT FEEDBACK (incorporate these changes):\n{feedback}"
        if design_rules:
            extras += f"\n\nUSER STYLE REQUIREMENTS (follow all of these):\n{design_rules}"

        prompt = f"""Tailor this resume for {title} at {company}.

MASTER RESUME — this is the ONLY source of facts. Do not invent anything not here.
{master_resume}

JOB DESCRIPTION:
{job_description[:4000]}

GAPS TO ADDRESS: {gaps_text or "None"}
ACTION ITEMS: {actions_text or "None"}
{extras}

Write the resume body sections. Rules:
1. Every company name, job title, date, metric, skill MUST exist verbatim or as a clear paraphrase in the master resume above
2. Do NOT invent companies, titles, dates, phone numbers, emails, or metrics
3. Tailor bullet points to emphasize what matters for this specific role — reorder, reframe, select — but never fabricate
4. Strong action verbs, specific numbers, ATS keywords from the JD

MARKDOWN SYNTAX — PDF renderer requires this EXACT format:
  Section header:   ## SECTION NAME
  Experience line:  ### Company Name, Location || Month Year – Month Year
  Job title:        **Job Title**   (own line, right after company line)
  Role desc:        *One italic sentence.*   (right after title)
  Bullet:           - **Category:** achievement with metric
CRITICAL formatting rules (breaking these corrupts the PDF):
  - `**text**` IS the bold mechanism — the PDF renderer converts it to real bold. Use `**...**` for every bold element (bullet category labels, metrics, job titles). If any style note says "don't use **", IGNORE it — without `**` nothing is bold.
  - The date after `||` must be PLAIN text. Do NOT wrap it in ** or * (write `|| Jan 2023 – Present`, never `|| **Jan 2023 – Present**`).
  - Job titles must keep the **bold** markers on their own line.
  - No ``` fences. No preamble. Start output with ## (first section)."""

        # max_tokens must cover thinking tokens (Gemini 2.5+/3.x reason ~2-4k) PLUS the full resume output
        raw = await self.router.complete(
            prompt, llm=llm, system=SYSTEM_RESUME_AGENT, temperature=0.3, max_tokens=16000
        )
        # Prepend verified header built from pre-extracted facts; drop whatever header the LLM wrote
        facts = contact_facts or {}
        name = facts.get('name', '')
        body = _resume_body(_strip_code_fences(raw), name)
        contact_parts = [v for k in ('email', 'phone', 'location', 'linkedin', 'github')
                         if (v := facts.get(k, '').strip())]
        header = f"# {name}\n{' | '.join(contact_parts)}" if name else ""
        return (header + '\n\n' + body).strip() if header else body

    async def chat_edit(self, current_resume_md: str, instruction: str, llm: str = "gemini") -> str:
        """Applies a human instruction to an existing generated resume (Live Chat Editor)."""
        prompt = f"""You are an elite executive resume editor. 
The user wants to modify this exact resume based on the following instruction.

INSTRUCTION: {instruction}

CURRENT RESUME:
{current_resume_md}

Apply the requested changes. 
CRITICAL RULES:
1. Keep all markdown formatting EXACTLY as it is (## Headers, ### Company || Date, **Job Title**, *Role*).
2. Do not hallucinate or invent new companies/dates.
3. Return ONLY the updated markdown resume. No preamble, no explanation, no markdown fences. Start immediately with the first # or ##."""
        
        raw = await self.router.complete(
            prompt, llm=llm, system=SYSTEM_RESUME_AGENT, temperature=0.3, max_tokens=16000
        )
        return _strip_code_fences(raw)

    async def validate_design(
        self,
        resume_md: str,
        design_rules: str,
        llm: str = "gemini",
    ) -> str:
        """Apply user style rules to an already-generated resume. Runs only when user has a style guide."""
        if not design_rules.strip():
            return resume_md

        # Separate header from body so LLM only edits the body
        lines = resume_md.split('\n')
        header_end = next((i for i, l in enumerate(lines) if l.strip().startswith('## ')), None)
        if header_end:
            header = '\n'.join(lines[:header_end]).strip()
            body = '\n'.join(lines[header_end:])
        else:
            header, body = '', resume_md

        prompt = f"""Apply the user's style requirements to this resume body.

USER STYLE REQUIREMENTS:
{design_rules}

RESUME BODY TO EDIT:
{body}

Rules:
- Apply all requirements: section order, length, tone, bullet style, keywords
- Keep `### Company, Location || Dates` on experience lines (PDF renderer requires this)
- Do NOT fabricate new facts, companies, dates, or metrics
- Do NOT add or change the name/contact header (it is managed separately)

BOLD MECHANISM (critical — overrides any contradictory user note):
- `**text**` IS how bold is produced. The PDF renderer converts `**text**` into actual bold.
- To make something bold (company names, job titles, dates, metrics, bullet category labels), you MUST wrap it in `**...**`.
- If the user's notes say "don't use **" or "** doesn't become bold", that is OUTDATED — IGNORE it. Without `**` there is NO bold. Use `**` for everything that should be bold.
- Do NOT wrap the date after `||` in `**` (dates render plain).

Return ONLY the edited body starting with ## (first section). No preamble."""

        corrected = await self.router.complete(
            prompt, llm=llm, system=SYSTEM_RESUME_AGENT, temperature=0.2, max_tokens=16000
        )
        corrected = _strip_code_fences(corrected)
        logger.info("[ResumeTailor] Style-guide pass complete.")

        # Re-attach header
        return (header + '\n\n' + corrected).strip() if header else corrected

    async def fetch_github_context(self, github_username: str, github_token: str) -> str:
        """Fetch README.md files from user's pinned/recent repos."""
        if not github_token or not github_username:
            return ""
        try:
            headers = {"Authorization": f"Bearer {github_token}", "Accept": "application/vnd.github.v3+json"}
            async with httpx.AsyncClient(headers=headers, timeout=10) as client:
                # Get top repos
                resp = await client.get(f"https://api.github.com/users/{github_username}/repos?sort=updated&per_page=5")
                repos = resp.json()
                readmes = []
                for repo in repos[:4]:
                    rname = repo.get("name", "")
                    rresp = await client.get(f"https://api.github.com/repos/{github_username}/{rname}/readme")
                    if rresp.status_code == 200:
                        import base64
                        content = rresp.json().get("content", "")
                        decoded = base64.b64decode(content).decode("utf-8", errors="ignore")
                        readmes.append(f"### {rname}\n{decoded[:800]}")
                return "\n\n".join(readmes)
        except Exception as e:
            logger.warning(f"[GitHub] Failed to fetch READMEs: {e}")
            return ""


# ── Agent 4: Cover Letter ──────────────────────────────────────────────────────

class CoverLetterAgent:
    """Generates a compelling cover letter tailored to the job."""

    def __init__(self, router: LLMRouter):
        self.router = router

    async def generate(
        self,
        job_description: str,
        tailored_resume: str,
        company: str = "",
        title: str = "",
        feedback: str = "",
        llm: str = "gemini",
    ) -> str:
        feedback_section = "\n== REFINEMENT FEEDBACK ==\n" + feedback if feedback else ""
        prompt = f"""Write a cover letter for this job application.

== ROLE: {title} at {company} ==
{job_description[:3000]}

== CANDIDATE'S TAILORED RESUME ==
{tailored_resume[:3000]}
{feedback_section}

Write a 3-4 paragraph cover letter that:
- Opens with a compelling hook about {company}'s mission/product
- Highlights 2-3 specific achievements from the resume that directly address the JD's needs
- Demonstrates genuine passion for the problem space
- Closes with confident conviction (no begging, no "I hope to hear from you")
- Reads like a real human wrote it — not a template

Return ONLY the cover letter in markdown. Start with "Dear Hiring Team," or the recruiter's name if known."""
        raw = await self.router.complete(prompt, llm=llm, system=SYSTEM_COVER_LETTER_AGENT, temperature=0.75, max_tokens=2000)
        return _strip_code_fences(raw)


# ── Agent 5: Chat Dispatcher ───────────────────────────────────────────────────

class ChatDispatcherAgent:
    """
    Powers the floating chat assistant.
    Converts natural language → structured action JSON,
    then the backend executes the action.
    """

    def __init__(self, router: LLMRouter):
        self.router = router

    async def dispatch(
        self,
        message: str,
        context: Optional[Dict] = None,
        llm: str = "gemini",
        conversation_history: Optional[List[Dict]] = None,
    ) -> Dict:
        """Parse user intent and return an action dict using Pydantic Self-Healing outputs."""
        from pydantic import BaseModel, Field
        from typing import Optional, Dict, Any

        class DispatchAction(BaseModel):
            action: str = Field(..., description="The intended action mapping (e.g. 'track_job', 'analyze_job', 'general_reply')")
            url: Optional[str] = Field(None, description="The URL if applicable")
            job_id: Optional[int] = Field(None, description="The internal Job ID if mentioned in context")
            text: Optional[str] = Field(None, description="The general text reply to the user if action is 'general_reply'")
            params: Optional[Dict[str, Any]] = Field(None, description="Any additional arbitrary parameters")

        context_str = ""
        if context:
            context_str = f"\nCurrent context: {json.dumps(context)}"

        history_str = ""
        if conversation_history:
            recent = conversation_history[-6:]  # Last 3 exchanges
            history_str = "\nRecent conversation:\n" + "\n".join(
                f"{m['role']}: {m['content']}" for m in recent
            )

        prompt = f"""User message: "{message}"{context_str}{history_str}

Determine the user's intent and return the appropriate action."""

        try:
            # Replaces brittle _parse_json with robust self-healing validation loop
            action_obj = await self.router.structured_complete(
                prompt,
                response_model=DispatchAction,
                llm=llm,
                system=SYSTEM_CHAT_DISPATCHER,
                temperature=0.2
            )
            return action_obj.model_dump()
        except Exception as e:
            logger.warning(f"[ChatDispatcher] Structured parsing failed: {e}")
            return {"action": "general_reply", "text": "I'm not sure what you meant — could you rephrase?"}

    async def generate_reply(self, action_result: Any, original_message: str, llm: str = "gemini") -> str:
        """Turn an action result into a human-readable reply."""
        prompt = f"""The user said: "{original_message}"
The system performed an action and returned: {json.dumps(action_result, default=str)[:1500]}

Write a brief, friendly, natural-language reply (2-3 sentences max) summarizing what happened.
Be specific. Mention key numbers/names from the result if available."""
        try:
            return await self.router.complete(prompt, llm=llm, temperature=0.7, max_tokens=300)
        except Exception:
            return str(action_result)


# ── Agent 6: LinkedIn Outreach ─────────────────────────────────────────────────

SYSTEM_LINKEDIN_AGENT = """You are an expert LinkedIn networking strategist.
You craft concise, compelling connection request messages (under 300 characters)
that make professionals want to respond. Never use corporate-speak, clichés, or
"I'm passionate about..." Never share phone numbers.
Always respond in valid JSON only, no markdown fences."""


class LinkedInOutreachAgent:
    """
    Generates targeted LinkedIn outreach messages with different frameworks
    per contact type (Recruiter, Hiring Manager, Peer, Interviewer).
    Inspired by career-ops' contacto mode.
    """

    def __init__(self, router: LLMRouter):
        self.router = router

    async def generate(
        self,
        job_description: str,
        master_resume: str,
        company: str = "",
        title: str = "",
        contact_type: str = "hiring_manager",
        llm: str = "gemini",
    ) -> Dict:
        prompt = f"""Generate LinkedIn outreach messages for a job application.

== TARGET ROLE: {title} at {company} ==
{job_description[:3000]}

== CANDIDATE RESUME (key highlights) ==
{master_resume[:2000]}

== CONTACT TYPE: {contact_type} ==

Generate messages for each contact type using these frameworks:

**Recruiter**: Frase 1 (Fit): Direct match criteria. Frase 2 (Proof): Screening-shortcut data point. Frase 3 (CTA): "Happy to share my CV if this aligns."
**Hiring Manager**: Frase 1 (Hook): Specific team challenge from JD/news. Frase 2 (Proof): Biggest quantifiable achievement. Frase 3 (CTA): "Would love to hear how your team approaches [challenge]."
**Peer** (referral): Frase 1 (Interest): Reference their work. Frase 2 (Connection): Shared problem space. Frase 3 (CTA): Curiosity-based, NOT job request.
**Interviewer** (pre-interview): Frase 1 (Research): Their specific work/trajectory. Frase 2 (Context): Light connection. Frase 3 (CTA): "Looking forward to our conversation."

Return JSON:
{{
  "primary_target": "{contact_type}",
  "messages": {{
    "recruiter": {{
      "message": "Max 300 chars connection message",
      "search_query": "LinkedIn search query to find this person"
    }},
    "hiring_manager": {{
      "message": "Max 300 chars",
      "search_query": "LinkedIn search query"
    }},
    "peer": {{
      "message": "Max 300 chars",
      "search_query": "LinkedIn search query"
    }},
    "interviewer": {{
      "message": "Max 300 chars",
      "search_query": "LinkedIn search query"
    }}
  }},
  "recommended_target": "Who to contact first and why"
}}

Rules:
- MAXIMUM 300 characters per message (LinkedIn connection request limit)
- NO corporate-speak, NO "I'm passionate about...", NO "proven track record"
- Write something that makes them WANT to respond
- NEVER include phone numbers"""

        text = await self.router.complete(prompt, llm=llm, system=SYSTEM_LINKEDIN_AGENT, temperature=0.7, max_tokens=3000)
        return _parse_json(text)


# ── Agent 7: Deep Research ─────────────────────────────────────────────────────

SYSTEM_DEEP_RESEARCH = """You are a corporate intelligence analyst specializing in tech companies.
You produce comprehensive, actionable research for interview preparation.
Focus on facts, cite sources when possible, and distinguish confirmed facts from inferences.
Always respond in valid JSON only, no markdown fences."""


class DeepResearchAgent:
    """
    6-axis deep company research inspired by career-ops' deep mode.
    Produces structured research for interview preparation.
    """

    def __init__(self, router: LLMRouter):
        self.router = router

    async def research(
        self,
        company: str,
        title: str = "",
        job_description: str = "",
        master_resume: str = "",
        llm: str = "gemini",
    ) -> Dict:
        prompt = f"""Conduct deep research on {company} for a candidate applying to: {title}.

== JOB DESCRIPTION ==
{job_description[:3000]}

== CANDIDATE RESUME ==
{master_resume[:2000]}

Return comprehensive JSON research across 6 axes:
{{
  "company": "{company}",
  "role": "{title}",
  "ai_strategy": {{
    "products_using_ai": ["Product 1 — how it uses AI"],
    "ai_stack": "Known tech stack (models, infra, tools)",
    "engineering_blog": "URL or 'not found'",
    "notable_papers_talks": ["Paper/talk 1"],
    "ai_maturity": "Early | Growing | Mature"
  }},
  "recent_moves": {{
    "key_hires": ["Notable recent hires in AI/ML"],
    "acquisitions_partnerships": ["Recent acquisitions or partnerships"],
    "product_launches": ["Recent product launches or pivots"],
    "funding_leadership": ["Funding rounds or leadership changes"]
  }},
  "engineering_culture": {{
    "deploy_cadence": "How they ship (daily, weekly, etc.)",
    "primary_languages": ["Python", "Go", etc.],
    "remote_policy": "Remote-first | Hybrid | Office-first",
    "glassdoor_sentiment": "Positive | Mixed | Negative — key themes",
    "team_size_estimate": "Estimated eng team size"
  }},
  "likely_challenges": {{
    "scaling_problems": ["Known or inferred scaling challenges"],
    "reliability_cost": ["Reliability, cost, latency issues"],
    "migrations": ["Ongoing infrastructure or model migrations"],
    "pain_points": ["Pain points from reviews or job posts"]
  }},
  "competitive_landscape": {{
    "main_competitors": ["Competitor 1", "Competitor 2"],
    "differentiator": "Their key moat/advantage",
    "market_position": "Leader | Challenger | Niche"
  }},
  "candidate_angle": {{
    "unique_value": "What unique value this candidate brings to this team",
    "most_relevant_projects": ["Project from resume most relevant here"],
    "interview_story": "The story to lead with in the interview",
    "connection_points": ["Shared interests or overlaps between candidate and company"]
  }}
}}

Be thorough and specific. If you don't know something, say 'unknown' rather than making it up."""

        text = await self.router.complete(prompt, llm=llm, system=SYSTEM_DEEP_RESEARCH, temperature=0.4, max_tokens=8000)
        return _parse_json(text)


# ── Agent 8: Interview Prep ────────────────────────────────────────────────────

SYSTEM_INTERVIEW_PREP = """You are an elite interview coach for senior AI/ML engineering roles.
You generate powerful STAR+Reflection stories that demonstrate both competence and
self-awareness. The Reflection component signals seniority — junior candidates describe
what happened, senior candidates extract lessons.
Always respond in valid JSON only, no markdown fences."""


class InterviewPrepAgent:
    """
    Generates STAR+Reflection interview stories and builds a persistent story bank.
    Inspired by career-ops' Block F and story-bank concept.
    """

    def __init__(self, router: LLMRouter):
        self.router = router

    async def generate_stories(
        self,
        job_description: str,
        master_resume: str,
        company: str = "",
        title: str = "",
        existing_stories: Optional[List[Dict]] = None,
        llm: str = "gemini",
    ) -> Dict:
        existing_stories_text = ""
        if existing_stories:
            existing_stories_text = "\n== EXISTING STORY BANK (avoid duplicates) ==\n"
            for s in existing_stories[:10]:
                existing_stories_text += f"- {s.get('title', 'Untitled')}: {s.get('jd_requirement', '')}\n"

        prompt = f"""Generate STAR+Reflection interview stories for this role.

== TARGET ROLE: {title} at {company} ==
{job_description[:3500]}

== CANDIDATE RESUME ==
{master_resume[:3500]}
{existing_stories_text}

Return JSON:
{{
  "stories": [
    {{
      "jd_requirement": "The specific JD requirement this story addresses",
      "title": "Short memorable title for this story",
      "situation": "The context and challenge (2-3 sentences)",
      "task": "What needed to be done and why it was hard (1-2 sentences)",
      "action": "What the candidate specifically did — technical details, decisions made (3-5 sentences)",
      "result": "Quantified outcome with specific metrics (1-2 sentences)",
      "reflection": "What was learned, what would be done differently, or how this shaped future decisions (1-2 sentences)",
      "tags": ["leadership", "technical-depth", "scaling", "cross-functional"]
    }}
  ],
  "red_flag_questions": [
    {{
      "question": "A likely tough question for this role",
      "why_they_ask": "What the interviewer is really probing for",
      "suggested_response": "A thoughtful, honest response framework",
      "pitfall": "What NOT to say"
    }}
  ],
  "case_study": {{
    "project": "Which project from the resume to present",
    "framing": "How to frame it for THIS specific role",
    "key_decisions": ["Decision points to highlight"],
    "metrics_to_cite": ["Specific metrics to mention"]
  }}
}}

Generate 6-10 stories covering different JD requirements. Don't duplicate existing stories.
Every story MUST use real experiences from the resume — never fabricate."""

        text = await self.router.complete(prompt, llm=llm, system=SYSTEM_INTERVIEW_PREP, temperature=0.5, max_tokens=10000)
        return _parse_json(text)


# ── Agent 9: Resume Critic ────────────────────────────────────────────────────

SYSTEM_CRITIC_AGENT = """You are a brutally honest resume critic and career coach.
Your ONLY goal is to get the candidate their dream job. You do NOT sugarcoat.
You point out every weakness, gap, vague claim, weak bullet, and ATS risk with zero mercy.
You are the hard truth the candidate needs to hear, not what they want to hear.
Always respond in valid JSON only, no markdown fences."""


class ResumeCriticAgent:
    """
    Brutally critiques a generated resume against the target JD.
    Runs as Pass 3 after generate + validate_design.
    Stored in ResumeVersion.critic_notes.
    """

    def __init__(self, router: LLMRouter):
        self.router = router

    async def critique(
        self,
        resume_md: str,
        job_description: str,
        company: str = "",
        title: str = "",
        llm: str = "gemini",
    ) -> Dict:
        prompt = f"""Brutally critique this resume for the role below. Your job is to help this candidate get hired — not to make them feel good.

== TARGET ROLE: {title} at {company} ==
{job_description[:4000]}

== GENERATED RESUME ==
{resume_md}

Return JSON with this exact structure:
{{
  "verdict": "One brutal 2-3 sentence summary. No softening. Would YOU shortlist this?",
  "score": 6,
  "score_rationale": "Why this score. Be specific.",
  "fatal_weaknesses": [
    {{
      "issue": "The exact problem",
      "location": "Which section/bullet it's in",
      "why_it_hurts": "How this specifically kills the application",
      "fix": "Exact fix — rewrite the bullet, add this metric, delete this line"
    }}
  ],
  "weak_bullets": [
    {{
      "original": "The exact weak bullet text",
      "problem": "What's wrong with it",
      "rewrite": "A stronger version using real resume data"
    }}
  ],
  "ats_red_flags": [
    {{
      "flag": "The ATS issue",
      "impact": "How this kills the ATS ranking"
    }}
  ],
  "jd_keyword_misses": ["keyword from JD missing in resume"],
  "positioning_verdict": "Is this resume positioned correctly for THIS role/level? What's off?",
  "top_3_actions": [
    "Single most impactful change to make RIGHT NOW",
    "Second most impactful change",
    "Third most impactful change"
  ],
  "competitor_comparison": "If a competing candidate had the same background but a better resume, what would theirs have that this one doesn't?"
}}

Scoring guide (1-10):
- 9-10: Would shortlist immediately. Rare.
- 7-8: Strong, minor tweaks needed.
- 5-6: Gets through ATS maybe, human will pass. Needs real work.
- 3-4: Significant gaps. Not competitive.
- 1-2: Would be rejected in 10 seconds.

Be the mentor who gives the hard truth, not the friend who lies. Cite exact lines. Don't repeat praise from the resume."""

        text = await self.router.complete(
            prompt, llm=llm, system=SYSTEM_CRITIC_AGENT, temperature=0.4, max_tokens=5000
        )
        result = _parse_json(text)
        logger.info(f"[ResumeCritic] Score: {result.get('score', '?')}/10 for {title} at {company}")
        return result


# ── Agent 10: Insights ────────────────────────────────────────────────────────

SYSTEM_INSIGHTS_AGENT = """You are an honest, no-BS career coach analyzing a job seeker's activity log.
Your job is to tell them EXACTLY what they're doing, what's working, what's wasted effort, and the 3 highest-leverage things they should do right now.
Be direct, specific, and reference the actual data. No motivational fluff.
Always respond in valid JSON only, no markdown fences."""


class InsightsAgent:
    """
    Generates a narrative career insight report from activity stats.
    Reads what the user has actually been doing and tells them what it means.
    """

    def __init__(self, router: LLMRouter):
        self.router = router

    async def generate_narrative(
        self,
        stats: Dict,
        recent_samples: List[str],
        llm: str = "gemini",
    ) -> Dict:
        samples_text = "\n".join(f"- {s}" for s in recent_samples[:15])

        prompt = f"""Analyze this job seeker's activity data and generate an honest insight report.

== ACTIVITY STATS (last 90 days) ==
Total interactions: {stats.get('total_interactions', 0)}
By action type: {json.dumps(stats.get('by_action', {}))}
Active days: {stats.get('active_days', 0)}
Top companies researched: {stats.get('top_companies', [])}
Most used LLM: {stats.get('most_used_llm', 'unknown')}

== RECENT ACTIVITY SAMPLES ==
{samples_text or "No recent activity."}

Return JSON:
{{
  "summary": "2-3 sentence honest summary of their job hunt activity pattern",
  "what_youre_doing": "Specific description of their actual behavior — what they spend time on",
  "momentum_score": 6,
  "momentum_rationale": "Why this score. What's driving it up or down.",
  "gaps": "What important activities are MISSING from their data. Be specific.",
  "top_3_recommendations": [
    "Most impactful action to take TODAY",
    "Second most impactful",
    "Third most impactful"
  ],
  "warning": "One hard truth they probably don't want to hear but need to. Or null if no warning."
}}

Momentum score (1-10):
- 8-10: Actively applying, generating documents, researching — high velocity
- 5-7: Moderate activity, some gaps in pipeline
- 2-4: Low activity, mostly researching but not acting
- 1: Essentially idle

Be specific to the DATA. Don't give generic advice."""

        text = await self.router.complete(
            prompt, llm=llm, system=SYSTEM_INSIGHTS_AGENT, temperature=0.4, max_tokens=2000
        )
        return _parse_json(text)


# ── Utility: Follow-Up Cadence Engine ──────────────────────────────────────────

class FollowUpCadenceEngine:
    """
    Calculates follow-up urgency and cadence for active job applications.
    Inspired by career-ops' followup-cadence.mjs.
    """

    # Cadence rules (days)
    CADENCE = {
        "applied_first_followup": 7,       # First follow-up after applying
        "applied_subsequent": 7,            # Subsequent follow-ups
        "applied_max_followups": 2,         # Max follow-ups before going cold
        "responded_initial": 1,             # Respond within 1 day
        "responded_subsequent": 3,          # Follow up every 3 days
        "interview_thank_you": 1,           # Thank you within 24h
    }

    @classmethod
    def compute_urgency(
        cls,
        status: str,
        days_since_applied: int,
        days_since_last_followup: Optional[int],
        followup_count: int,
    ) -> str:
        """Return urgency tier: urgent | overdue | waiting | cold"""
        if status == "applied":
            if followup_count >= cls.CADENCE["applied_max_followups"]:
                return "cold"
            if followup_count == 0 and days_since_applied >= cls.CADENCE["applied_first_followup"]:
                return "overdue"
            if followup_count > 0 and days_since_last_followup is not None:
                if days_since_last_followup >= cls.CADENCE["applied_subsequent"]:
                    return "overdue"
            return "waiting"
        elif status in ("screening", "interview_1", "interview_2"):
            if days_since_applied >= cls.CADENCE["interview_thank_you"]:
                return "overdue"
            return "waiting"
        elif status == "responded":
            if days_since_last_followup is not None and days_since_last_followup >= cls.CADENCE["responded_subsequent"]:
                return "overdue"
            return "waiting"
        return "waiting"

    @classmethod
    def next_followup_date(
        cls,
        status: str,
        applied_date: datetime,
        last_followup_date: Optional[datetime],
        followup_count: int,
    ) -> Optional[datetime]:
        """Calculate the next recommended follow-up date."""
        from datetime import timedelta
        if status == "applied":
            if followup_count >= cls.CADENCE["applied_max_followups"]:
                return None  # Cold — stop following up
            if followup_count == 0:
                return applied_date + timedelta(days=cls.CADENCE["applied_first_followup"])
            if last_followup_date:
                return last_followup_date + timedelta(days=cls.CADENCE["applied_subsequent"])
            return applied_date + timedelta(days=cls.CADENCE["applied_first_followup"])
        elif status in ("screening", "interview_1", "interview_2"):
            return applied_date + timedelta(days=cls.CADENCE["interview_thank_you"])
        elif status == "responded":
            base = last_followup_date or applied_date
            return base + timedelta(days=cls.CADENCE["responded_subsequent"])
        return None


# ── Utility: Pattern Analytics Engine ──────────────────────────────────────────

class PatternAnalyticsEngine:
    """
    Analyzes rejection patterns, conversion funnels, and generates actionable
    recommendations. Inspired by career-ops' analyze-patterns.mjs.
    """

    @staticmethod
    def analyze(jobs: list) -> Dict:
        """Run pattern analysis across all tracked jobs."""
        from collections import Counter

        if not jobs:
            return {"error": "No applications to analyze."}

        total = len(jobs)

        # ── Conversion Funnel ──
        status_counts = Counter(j.status for j in jobs)

        # ── Score by outcome ──
        positive_statuses = {"applied", "screening", "interview_1", "interview_2", "offer"}
        negative_statuses = {"rejected", "withdrawn"}
        positive_scores = [j.match_score for j in jobs if j.status in positive_statuses and j.match_score]
        negative_scores = [j.match_score for j in jobs if j.status in negative_statuses and j.match_score]

        def _stats(scores):
            if not scores:
                return {"avg": 0, "min": 0, "max": 0, "count": 0}
            return {
                "avg": round(sum(scores) / len(scores), 1),
                "min": min(scores),
                "max": max(scores),
                "count": len(scores),
            }

        # ── Archetype breakdown ──
        archetype_counts = Counter()
        archetype_positive = Counter()
        for j in jobs:
            arch = j.archetype or "Unknown"
            archetype_counts[arch] += 1
            if j.status in positive_statuses:
                archetype_positive[arch] += 1
        archetype_breakdown = [
            {
                "archetype": arch,
                "total": archetype_counts[arch],
                "positive": archetype_positive.get(arch, 0),
                "conversion_rate": round(archetype_positive.get(arch, 0) / archetype_counts[arch] * 100) if archetype_counts[arch] > 0 else 0,
            }
            for arch in archetype_counts
        ]
        archetype_breakdown.sort(key=lambda x: x["total"], reverse=True)

        # ── Platform breakdown ──
        platform_counts = Counter(j.platform or "unknown" for j in jobs)
        platform_positive = Counter()
        for j in jobs:
            if j.status in positive_statuses:
                platform_positive[j.platform or "unknown"] += 1
        platform_breakdown = [
            {
                "platform": p,
                "total": platform_counts[p],
                "positive": platform_positive.get(p, 0),
                "conversion_rate": round(platform_positive.get(p, 0) / platform_counts[p] * 100) if platform_counts[p] > 0 else 0,
            }
            for p in platform_counts
        ]

        # ── Common gaps ──
        all_gaps = []
        for j in jobs:
            if j.gaps:
                all_gaps.extend(j.gaps if isinstance(j.gaps, list) else [])
        gap_frequency = Counter(all_gaps).most_common(10)

        # ── Recommendations ──
        recommendations = []

        # Score threshold recommendation
        if positive_scores:
            min_positive = min(positive_scores)
            if min_positive > 50:
                recommendations.append({
                    "action": f"Set minimum score threshold at {int(min_positive)}% before generating resumes",
                    "reasoning": f"No positive outcomes below {int(min_positive)}%. Lower scores are wasted effort.",
                    "impact": "high",
                })

        # Best archetype
        best = sorted(
            [a for a in archetype_breakdown if a["total"] >= 2],
            key=lambda x: x["conversion_rate"], reverse=True
        )
        if best and best[0]["conversion_rate"] > 0:
            recommendations.append({
                "action": f"Double down on '{best[0]['archetype']}' roles ({best[0]['conversion_rate']}% conversion)",
                "reasoning": f"{best[0]['positive']} of {best[0]['total']} applications led to progress.",
                "impact": "medium",
            })

        # Common gap recommendation
        if gap_frequency:
            top_gap = gap_frequency[0]
            recommendations.append({
                "action": f"Address recurring gap: '{top_gap[0]}' (appears in {top_gap[1]} evaluations)",
                "reasoning": "This is your most common weakness — consider upskilling or better positioning.",
                "impact": "medium",
            })

        return {
            "metadata": {
                "total": total,
                "analysis_date": datetime.utcnow().isoformat(),
            },
            "funnel": dict(status_counts),
            "score_comparison": {
                "positive": _stats(positive_scores),
                "negative": _stats(negative_scores),
            },
            "archetype_breakdown": archetype_breakdown,
            "platform_breakdown": platform_breakdown,
            "common_gaps": [{"gap": g, "frequency": c} for g, c in gap_frequency],
            "recommendations": recommendations,
        }
