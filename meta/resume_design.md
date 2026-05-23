# Resume Design Rules
# These rules are automatically injected before generation (Pass 1) and enforced via validation (Pass 2).
# Edit this file freely — changes take effect on the next resume generation.

---

## Structure & Length

- Maximum 2 pages worth of content (roughly 700-900 words of body text)
- Sections order: Contact/Header → Summary → Skills → Experience → Projects → Education
- Each section uses `##` headers, bullet points for everything below headers
- No tables. No columns. ATS-safe linear structure only.

## Summary Block

- 3-4 sentences max
- Lead with years of experience + primary domain (e.g., "8+ years building production AI/ML systems...")
- Mention 1-2 hard numbers (e.g., "reduced latency by 40%", "led team of 12")
- Do NOT use first person ("I built..." → "Built...")
- No fluff phrases: "passionate about", "results-driven", "proven track record"

## Skills Block

- Group into categories (e.g., Languages, Frameworks, Infrastructure, LLMs, Methods)
- Each category is one comma-separated line — no bullet points inside skills
- Only include skills that appear in the Experience or Projects section — no padding

## Experience Block

- Format: `**Company** — *Title* | Location | Start – End`
- Max 4 bullets per role, 3 is ideal
- Every bullet must start with a strong past-tense action verb (Built, Designed, Led, Reduced, Shipped...)
- At least 2 out of 4 bullets must contain a quantified metric (%, $, x, ms, users, etc.)
- No soft skill bullets ("collaborated with cross-functional teams") unless tied to a hard outcome
- Most recent/relevant role gets 4 bullets; older/less relevant roles get 2-3

## Projects Block

- Only include if directly relevant to the target JD's tech stack or domain
- Format: `**Project Name** — [one-line tech stack]`
- Max 2 bullets per project: what it does + one impact/scale metric
- GitHub links optional but welcome if public

## Formatting Rules

- Use `**bold**` only for company names and key tech terms in bullets — sparingly
- No italics in body text (only for job title in header line)
- Consistent date format: `Jan 2021 – Mar 2023` (abbreviated month, no full year repetition)
- Oxford comma in all lists
- Em dashes for header separators (—), not hyphens (-)

## ATS Rules

- Use exact keywords from the JD — verbatim, not paraphrased
- Put the most critical JD keywords in the first bullet of the most recent role
- Avoid special characters, icons, or unicode symbols in bullet points
- Contact section: email | LinkedIn URL | GitHub URL — plain text, no hyperlink markdown

## Tone & Voice

- Confident, technical, first-person implied (no "I")
- Avoid: "responsible for", "helped with", "assisted in", "worked on"
- Prefer: "Built", "Architected", "Shipped", "Drove", "Reduced", "Scaled", "Designed"
- Every bullet answers: *what was built/done, how, and what was the impact*
