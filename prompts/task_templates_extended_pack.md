# Extended Task Prompt Templates (SaaS / E‑commerce / App Shells / OS Demos)

These templates extend the prompt library beyond niche local-business pages into:
- general SaaS/home/product pages
- e‑commerce home + PDP
- general app shells + dashboards
- a few “OS / desktop / browser‑OS” UIs (UI demos)

All prompts assume TITAN constraints are in effect (Next.js App Router + TS + Tailwind, no UI libs, no emojis, inline SVG only, proof labeling, accessibility, etc.).

---

## General pages prompt pack (landing / home / product)

### Prompt G1

category: home_page  
intent: generate  
quality traits: copy-first, constraints-heavy, style-pack

Build a premium SaaS homepage.

Brand: [NAME]  
Product: [WHAT IT DOES IN 1 LINE]  
Audience: [PRIMARY PERSONA]  
Primary CTA: [CTA]  
Secondary CTA: link-only

Required sections:
- Header with story-driven logo mark (inline SVG, not a triangle)
- Hero: H1 + subhead + primary CTA + 3 trust chips + key info digest (3 bullets)
- “How it works” (3 steps)
- Value props (5 bullets, outcome-led)
- Proof wall (3 items, labeled provided/qualified/illustrative)
- Pricing summary (2 tiers)
- FAQ (>=6; include skeptical objections)
- Final CTA with “what happens next” + privacy line

Rules:
- No emojis. Inline SVG icons <= 6 total; icons must be custom and consistent.
- No UI libraries. Tailwind only.
- No fake logos/metrics/reviews. Qualify claims.

Output JSON only with app/page.tsx and any components.

Why it’s good: Forces real homepage IA + trust policy + premium motif + compile-safe code.

### Prompt G2

category: landing  
intent: generate  
quality traits: constraints-heavy, a11y-first

Create a landing page for a single feature (not a full product).

Feature: [FEATURE NAME]  
Who it’s for: [PERSONA]  
Job-to-be-done: [JTBD]  
Primary CTA: [CTA]

Required:
- Hero with 3 outcomes (not features) + 3 trust chips + digest(3)
- Before/after section (calm, factual)
- “How it works” steps (3)
- Screenshot/mock area (no external images; use lightweight mock blocks)
- Proof policy section (how you verify / what’s illustrative)
- FAQ (>=6)

No emojis. SVG icons <= 4. Logo mark must hint at the feature’s core mechanism.  
Output JSON only.

Why it’s good: Prevents “marketing soup” by anchoring to one feature + mechanism.

### Prompt G3

category: product_page  
intent: generate  
quality traits: component-first, copy-first

Build a “Product Overview” page (marketing site).

Brand: [NAME]  
Audience: [PERSONA]  
Positioning: [ONE SENTENCE]  
Primary CTA: [CTA]

Must include:
- Hero + digest(3) + trust chips(3)
- 3 “use-case” cards (each with VoC phrasing)
- Feature → Outcome mapping (table or grid)
- Integrations row (NO real brand logos unless provided; use generic placeholders labeled)
- Security/Privacy/Control panel (trust-first)
- FAQ (>=6)
- Final CTA + what happens next

No emojis. Inline SVG icons <= 6 (custom, consistent).  
Output JSON only.

Why it’s good: Adds trust/controls + integration handling without faking logos.

### Prompt G4

category: pricing_page  
intent: generate  
quality traits: conversion-focused, constraints-heavy

Build a premium pricing page.

Brand: [NAME]  
Plans: 2–3 tiers  
Primary CTA: [CTA]  
Secondary CTA: link-only (“Contact sales” or “Compare plans”)

Required:
- Pricing cards (2–3) with “best for” tags + included items + exclusions
- “How billing works” and “What happens after you start” blocks
- FAQ (>=8; include 4 skeptical objections: refunds, cancellation, data, limits)
- Proof labeling: never invent metrics; qualify anything not provided

No emojis. SVG icons <= 4.  
Output JSON only.

Why it’s good: Pricing pages die on missing clarity—this forces it.

### Prompt G5

category: comparison_page  
intent: generate  
quality traits: data-driven, clarity-first

Create a competitor comparison page.

Brand: [NAME]  
Competitors: [A], [B] (do NOT claim “best” unless backed)  
Goal: help a buyer choose honestly

Must include:
- Clear comparison table (feature/outcome rows)
- “Where we’re not a fit” section (2–3 bullets)
- Proof policy (what’s verified vs illustrative)
- FAQ (>=6)

No emojis. No fake badges. No fabricated reviews.  
Output JSON only.

Why it’s good: Adds “where not a fit” (rare, premium trust signal).

### Prompt G6

category: docs_home  
intent: generate  
quality traits: information-architecture, a11y-first

Build a docs homepage (clean, scannable).

Brand: [NAME]  
Sections: Getting Started, Guides, API, Examples, Troubleshooting

Required:
- Search bar (client component isolated)
- Card grid for major sections
- “Quickstart” code block area (as <pre><code>)
- “Common tasks” list (5)
- Footer with version + links

No emojis. SVG icons <= 3.  
Output JSON only.

Why it’s good: Tests IA + code formatting + search UI.

### Prompt G7

category: home_page  
intent: generate  
quality traits: editorial, content-first

Build a premium editorial homepage for a product blog.

Brand: [NAME]  
Content: featured post, latest posts, categories, newsletter

Rules:
- Strong typography hierarchy
- No clickbait copy
- Use tags, read-time chips, and a clean grid
- Newsletter CTA must include what happens next + privacy line

No emojis. SVG icons <= 2.  
Output JSON only.

Why it’s good: Forces calm, premium editorial layout (not generic SaaS).

### Prompt G8

category: waitlist_page  
intent: generate  
quality traits: conversion, microcopy

Create a waitlist page.

Brand: [NAME]  
What it is: [1 line]  
Who it’s for: [persona]  
Primary CTA: “Join the waitlist”

Required:
- Hero + digest(3) + trust chips(3)
- Form with: label, helper text, error text, aria-describedby
- “What you’ll get” (3 bullets)
- “What happens next” transparency block
- FAQ (>=6)

No emojis. SVG icons <= 3.  
Output JSON only.

Why it’s good: Great training data for forms + transparency + a11y.

---

## E‑commerce prompts (home + product detail)

### Prompt E1

category: ecom_home  
intent: generate  
quality traits: component-first, conversion

Build an e-commerce homepage.

Brand: [NAME]  
Category: [e.g., supplements / apparel / gadgets]  
Primary CTA: “Shop bestsellers”  
Secondary CTA: link-only

Must include:
- Hero with category promise + digest(3) + trust chips(3)
- Bestseller grid (8 items, placeholder data objects)
- Value props row (5)
- Returns/shipping policy preview (trust)
- FAQ (>=6)

No emojis. No fake reviews.  
If showing ratings, label as “example UI” and avoid numbers unless provided.  
SVG icons <= 4.  
Output JSON only.

Why it’s good: Handles the “ratings trap” safely.

### Prompt E2

category: ecom_product_page  
intent: generate  
quality traits: a11y-first, data-driven

Build an e-commerce product detail page (PDP).

Product: [NAME]  
Price: [IF PROVIDED]  
Variants: [size/color] (if none, omit)  
Primary CTA: “Add to cart”

Required:
- Gallery area (mock blocks)
- Product title + price + key benefits (3)
- Variant selectors (accessible)
- Shipping/returns block
- Reviews section (only if provided; otherwise “Illustrative example” label and no stars)
- FAQ (>=6)

No emojis. SVG icons <= 3.  
Output JSON only.

Why it’s good: PDP is hard—this enforces accessible selectors + policies.

---

## General dashboards / app shells (beyond “directory admin”)

### Prompt D1

category: dashboard  
intent: generate  
quality traits: data-driven, states-required

Build an analytics dashboard.

KPIs: 3 or 5 (use placeholder numbers)  
Main table: list of items with status + owner + last updated  
Include: date range control, filters row, empty/loading/error states, primary action.

No chart libraries. If trend needed: tiny inline SVG sparkline only.  
No emojis. SVG icons <= 4.  
Output JSON only.

Why it’s good: Enforces real-world dashboard constraints (states + tables + filters).

### Prompt D2

category: dashboard  
intent: generate  
quality traits: workflow UI

Build a lightweight CRM pipeline dashboard.

Include:
- Pipeline stages (kanban-like, but keep it simple)
- Lead list table fallback for mobile
- Search + filters
- Empty state for “no leads”
- Primary action: “Add lead”

No heavy drag/drop libraries. Minimal client component allowed.  
No emojis. SVG icons <= 4.  
Output JSON only.

Why it’s good: Tests “complex UI, minimal JS” skill.

### Prompt D3

category: dashboard  
intent: generate  
quality traits: trust-first

Build an account/billing dashboard.

Include:
- Plan summary card
- Payment method panel (no real card data)
- Invoices table
- Usage panel (bars made with divs)
- FAQ (>=6) for billing issues

No emojis. SVG icons <= 2.  
Output JSON only.

Why it’s good: Produces high-value SaaS UI with trust microcopy.

### Prompt D4

category: app_shell  
intent: generate  
quality traits: navigation, layout discipline

Build a product “app shell” (logged-in experience).

Include:
- Top bar (brand + global search)
- Left nav (5 items)
- Main content area with example dashboard/table
- Right panel (help/insights)
- Empty/loading/error states

No UI libs. No emojis. SVG icons <= 6.  
Output JSON only.

Why it’s good: App shells train consistent spacing + navigation patterns.

---

## OS / Desktop UI prompts (UI demos only)

### Prompt OS1 — Desktop OS shell

category: os_desktop  
intent: generate  
quality traits: novel, component-first, restraint

Create a “desktop OS” interface as a web UI demo.

Name: OrchidOS  
Goal: feel premium and original (not a macOS clone), but familiar.

Required layout:
- Menu bar (left: logo + app name; right: time + system controls)
- Dock (6 apps max; app icons must be simple lettermarks in rounded squares — NOT emojis)
- Desktop area with 2 windows open by default:
  1) “Finder-like” file browser window
  2) “Notes” window
- Window controls: close/minimize/maximize (simple buttons, accessible labels)
- Optional: command palette (Ctrl+K) as a small client component

Rules:
- No UI libraries. No emojis.
- Inline SVG icons <= 6 total (use them sparingly; prefer lettermarks for app icons).
- Must include reduced-motion handling.

Output JSON only.

Why it’s good: Forces premium OS layout primitives without icon spam.

### Prompt OS2 — Browser OS / workspace UI

category: os_browser  
intent: generate  
quality traits: workflow UI, navigation

Build a “browser OS” workspace UI demo.

Name: Nova Workspace  
Required:
- Left sidebar with spaces/projects
- Top tab strip (3 tabs) with clean active state
- Main content: a “web page” panel + optional split view
- Bottom status bar with network/sync indicators (no emojis)
- A settings drawer panel (client component, isolated)

No UI libs. Tailwind only.  
Inline SVG icons <= 6 total, custom and consistent.  
Output JSON only.

Why it’s good: Trains complex layout (sidebar + tabs + split panes) with restraint.

### Prompt OS3 — Settings app

category: os_settings  
intent: generate  
quality traits: a11y-first, forms

Create an OS “Settings” app UI.

Sections:
- Appearance (theme, accent)
- Privacy (permissions toggles)
- Notifications (per-app settings)
- Keyboard (shortcuts)
- About (system info)

Rules:
- Use accessible toggles/controls (buttons + aria, no UI libs)
- Include empty state for “no apps installed” in notifications
- No emojis. SVG icons <= 4.

Output JSON only.

Why it’s good: Settings UI is basically form UX + a11y—great training data.

---

## Extension note (append to your system/meta prompt)

EXTENSION NOTE:
Keep ALL existing TITAN constraints, proof/claims policy, a11y/perf rules, and JSON-only output.
In addition to landing/directory/dashboard, also support:
- home_page (marketing homepage)
- product_page (overview/features/integrations/security)
- pricing_page (plans + billing clarity)
- docs_home (search + IA)
- ecom_home and ecom_product_page (safe reviews/ratings handling)
- app_shell (logged-in navigation layout)
- os_desktop / os_browser / os_settings (UI demos only; minimal interactivity, no drag/drop required; prefer lettermark app icons over adding many SVGs)
If any page type conflicts with rules, preserve compile-safety + a11y + clarity first.

