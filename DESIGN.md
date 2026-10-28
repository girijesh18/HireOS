# HireOS Design System — Navy & Cream

> Single source of truth for the HireOS visual redesign. Follow this file exactly.
> Mood: **trustworthy, timeless, considered.** Think a well-made private bank or law firm — not a startup template.

---

## 1. Principles

1. **Navy is the authority, cream is the calm.** Pages alternate navy and cream sections to create rhythm; no other background colours.
2. **Gold is jewellery, not paint.** Gold appears only as thin rules, small eyebrow labels, italic emphasis words and outline buttons **on navy**. Never as a fill, never as body text, never on cream backgrounds (fails contrast).
3. **Serif speaks, sans works.** Libre Baskerville for headlines, big numbers and roman numerals. Inter for everything else (body, UI, buttons, labels).
4. **Square and restrained.** Buttons and cards have 0–2px radius. No gradients, no glassmorphism, no emoji, no drop-shadow stacks. One soft shadow only on overlapping cards.
5. **Generous space.** Sections breathe (≥ 96px vertical padding on desktop). When in doubt, add space, not decoration.

---

## 2. Colour tokens

| Token | Hex | Use |
|---|---|---|
| `--navy-900` | `#14213D` | Primary brand, hero & dark sections, primary text on cream, primary buttons on cream |
| `--navy-800` | `#2A3654` | Borders/dividers on navy |
| `--navy-700` | `#3A4766` | Outline-button borders on navy |
| `--navy-hover` | `#2A3D66` | Link hover on cream |
| `--cream-50` | `#FBF9F4` | Page background, text on navy, primary button on navy |
| `--cream-100` | `#EFEBE1` | Alternate cream section, subtle dividers |
| `--cream-200` | `#E5DFD1` | Card borders, rules on cream |
| `--white` | `#FFFFFF` | Cards that sit on cream |
| `--ink-muted` | `#5A6170` | Secondary text on cream (passes 4.5:1 on cream-50 and cream-100) |
| `--on-navy-muted` | `#C9CFDB` | Secondary text and nav links on navy |
| `--gold` | `#C2A06B` | Thin rules, eyebrow labels, italic accent words, outline button — **on navy only** |
| `--gold-ink` | `#8A6A3A` | Gold-toned small text **on cream** (roman numerals, tiny labels) — passes contrast |

Status colours (use sparingly, always with an icon or label):
`--success #2F6B4F` · `--warning #9A6B1F` · `--danger #A23B3B` · `--info #2A3D66`

### Contrast rules
- Body text on cream: `--navy-900` or `--ink-muted` only.
- Body text on navy: `--cream-50` or `--on-navy-muted` only.
- `--gold` text is allowed only on `--navy-900`, never on cream.
- Never place `--gold` on white/cream for anything smaller than a 1px decorative rule.

---

## 3. Typography

Load from Google Fonts:
```html
<link href="https://fonts.googleapis.com/css2?family=Libre+Baskerville:ital,wght@0,400;0,700;1,400&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
```

| Role | Font | Size (desktop → mobile) | Weight | Line-height | Letter-spacing |
|---|---|---|---|---|---|
| Display / H1 | Libre Baskerville | `clamp(40px, 5.6vw, 68px)` | 400 | 1.12 | -0.01em |
| H2 | Libre Baskerville | `clamp(28px, 3.4vw, 40px)` | 400 | 1.2 | 0 |
| H3 | Inter | 17–20px | 600 | 1.4 | 0 |
| Stat number | Libre Baskerville | 36px | 400 | 1.1 | 0 |
| Body large | Inter | 18px | 400 | 1.7 | 0 |
| Body | Inter | 15–16px | 400 | 1.65 | 0 |
| Eyebrow / label | Inter | 12px, UPPERCASE | 600 | 1.4 | 0.12–0.2em |
| Nav links | Inter | 14px | 500 | — | 0.04em |
| Button | Inter | 15px | 600 | — | 0.02–0.04em |

- **Emphasis word in headings:** Libre Baskerville *italic*, coloured `--gold` on navy, or plain italic `--navy-900` on cream.
- Roman numerals (I., II., III.) for feature lists: Libre Baskerville italic, 14px, `--gold-ink`.

---

## 4. Spacing, layout & shape

- Spacing scale (px): `4, 8, 12, 16, 24, 32, 48, 64, 80, 104, 120`
- Container: `max-width: 1200px; margin: 0 auto; padding-inline: 24px`
- Section padding: 104–120px vertical desktop, 64px mobile
- Radius: `0` for buttons, inputs and cards (max `2px`). Avatars may be circular.
- Borders: 1px solid; `--cream-200` on cream, `--navy-800` on navy.
- Shadow (only for a card overlapping a navy/cream boundary): `0 20px 40px -24px rgba(20,33,61,0.25)`
- Breakpoints: 640 / 960 / 1200. All multi-column layouts collapse to one column under 640px; nav collapses to a menu button.

---

## 5. Signature details

- **Gold rule + eyebrow:** a 40×1px `--gold` line followed by a 12px uppercase, 0.2em-tracked label. Used above hero headlines and section titles.
- **Overlapping stat strip:** a white card with 4 stats (label above, serif number below), separated by 1px `--cream-100` vertical dividers, pulled up `-56px` so it straddles the navy hero and the cream body.
- **Ruled feature list:** features in a 2-column grid with 1px `--cream-200` top/bottom rules (no card boxes), each with a roman numeral, an Inter 600 title and a muted one-line description.

---

## 6. Components

### Buttons (min height 48px, padding 16px 26px, radius 0)
| Variant | On navy | On cream |
|---|---|---|
| Primary | bg `--cream-50`, text `--navy-900` | bg `--navy-900`, text `--cream-50` |
| Secondary | 1px border `--navy-700`, text `--cream-50` | 1px border `--navy-900`, text `--navy-900` |
| Accent (header CTA) | 1px border `--gold`, text `--gold` | — (not allowed) |

Hover: primary darkens/lightens ~6%; secondary fills with 6% tint. Transition `150ms ease`. Focus: `outline: 2px solid --gold` (on navy) / `--navy-900` (on cream), `outline-offset: 3px`.

### Inputs
Height 52px, 1px border `--cream-200`, bg `--white`, radius 0, text `--navy-900`, placeholder `--ink-muted`. Focus: border `--navy-900` + 3px `rgba(20,33,61,0.12)` ring. Always pair with a visible or visually-hidden `<label>`.

### Cards
White on cream, 1px `--cream-200` border, 32px padding, radius 0. No shadow except the overlapping stat strip.

### Navigation (on navy)
Logo in Libre Baskerville 700, 22px, cream. Links Inter 14px `--on-navy-muted`, hover `--cream-50`. Bottom border 1px `--navy-800`. Gold outline CTA on the right.

### Tables (app screens)
Header row: Inter 12px uppercase, 0.12em tracking, `--ink-muted`. Rows: 1px `--cream-200` dividers, 16px vertical padding. No zebra striping.

### Badges / status
Inter 12px 600, padding 4px 10px, radius 0. Default: 1px border `--cream-200`, text `--navy-900`. Status: text in status colour + matching 1px border, never solid fills.

---

## 7. Page structure (marketing home)

1. **Navy hero** — nav; gold rule + eyebrow "Hiring, considered"; serif H1 with one italic gold phrase; right column with body-large muted text + primary (cream) & secondary (outline) buttons.
2. **Overlapping stat strip** — 4 stats.
3. **Cream "why" section** — left: gold rule, H2, short paragraph. Right: ruled 2×2 feature list with roman numerals.
4. **Testimonial** (optional) — large serif italic quote on cream, name/role in muted Inter.
5. **Cream-100 CTA band** — serif H2 with one italic word + navy primary button.
6. **Footer** — serif wordmark left, muted links/copyright right, on cream.

App screens (dashboard, candidate pages): cream-50 background, navy sidebar, white cards, same type and button rules. Gold only in the sidebar (active-item rule, logo accent).

---

## 8. Drop-in code

### CSS variables
```css
:root {
  --navy-900: #14213D; --navy-800: #2A3654; --navy-700: #3A4766; --navy-hover: #2A3D66;
  --cream-50: #FBF9F4; --cream-100: #EFEBE1; --cream-200: #E5DFD1; --white: #FFFFFF;
  --ink-muted: #5A6170; --on-navy-muted: #C9CFDB;
  --gold: #C2A06B; --gold-ink: #8A6A3A;
  --success: #2F6B4F; --warning: #9A6B1F; --danger: #A23B3B; --info: #2A3D66;

  --font-serif: "Libre Baskerville", Georgia, "Times New Roman", serif;
  --font-sans: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;

  --radius: 0px;
  --shadow-overlap: 0 20px 40px -24px rgba(20, 33, 61, 0.25);
  --container: 1200px;
}
body { background: var(--cream-50); color: var(--navy-900); font-family: var(--font-sans); }
h1, h2, .serif { font-family: var(--font-serif); font-weight: 400; }
```

### Tailwind (`tailwind.config.js` → `theme.extend`)
```js
colors: {
  navy:  { 900: '#14213D', 800: '#2A3654', 700: '#3A4766', hover: '#2A3D66' },
  cream: { 50: '#FBF9F4', 100: '#EFEBE1', 200: '#E5DFD1' },
  ink:   { muted: '#5A6170', onNavy: '#C9CFDB' },
  gold:  { DEFAULT: '#C2A06B', ink: '#8A6A3A' },
  success: '#2F6B4F', warning: '#9A6B1F', danger: '#A23B3B', info: '#2A3D66',
},
fontFamily: {
  serif: ['"Libre Baskerville"', 'Georgia', 'serif'],
  sans:  ['Inter', 'system-ui', 'sans-serif'],
},
borderRadius: { none: '0', DEFAULT: '0', sm: '2px', full: '9999px' },
boxShadow: { overlap: '0 20px 40px -24px rgba(20,33,61,0.25)' },
maxWidth: { container: '1200px' },
```

---

## 9. Don'ts

- ❌ Gold text or gold fills on cream/white
- ❌ Rounded "pill" buttons or large radii
- ❌ Gradients, glows, glassmorphism, emoji icons
- ❌ More than two typefaces
- ❌ Coloured card backgrounds other than white, cream-100 or navy
- ❌ Serif for body text, buttons or form labels
- ❌ Pure black `#000` or pure white text on navy (use `--cream-50`)

## 10. Acceptance checklist

- [ ] All text meets WCAG AA (4.5:1 body, 3:1 for ≥24px)
- [ ] Every interactive element has a visible focus state and ≥44px touch target
- [ ] Layout works at 375px wide with no horizontal scroll
- [ ] Only tokens from section 2 are used — no stray hex values
- [ ] Gold appears only on navy surfaces (plus `--gold-ink` for small text on cream)
