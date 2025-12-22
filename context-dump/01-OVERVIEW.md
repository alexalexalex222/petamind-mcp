# TITAN-4-DESIGN DATASET FACTORY v1

## Project Overview

A production-grade synthetic dataset generator for fine-tuning small student models on UI/UX design tasks using Next.js App Router + TypeScript + Tailwind CSS.

## Repository Structure

```
titan-4-design-factory/
├── README.md
├── .env.example
├── .gitignore
├── pyproject.toml
├── Makefile
├── config/
│   └── config.yaml                 # Model configs, thresholds, budgets
├── templates/
│   └── nextjs_app_router_tailwind/ # Next.js base template
│       ├── package.json
│       ├── tsconfig.json
│       ├── tailwind.config.ts
│       ├── postcss.config.mjs
│       ├── next.config.ts
│       └── app/
│           ├── layout.tsx
│           ├── page.tsx
│           └── globals.css
├── src/
│   └── titan_factory/
│       ├── __init__.py
│       ├── cli.py                  # CLI interface (typer)
│       ├── config.py               # Configuration loader
│       ├── schema.py               # Pydantic models & validation
│       ├── utils.py                # Utilities (JSON extraction, hashing)
│       ├── promptgen.py            # 100 niches + task generation
│       ├── planner.py              # UI_SPEC generation (DeepSeek)
│       ├── uigen.py                # Code generation (Kimi, MiniMax)
│       ├── patcher.py              # Build error fixing (Devstral)
│       ├── validator.py            # Next.js build validation
│       ├── renderer.py             # Playwright screenshots
│       ├── judge.py                # Vision scoring + heuristic fallback
│       ├── orchestrator.py         # Pipeline coordinator
│       ├── exporter.py             # Training data export
│       └── providers/
│           ├── __init__.py
│           ├── base.py             # Provider interface
│           ├── vertex.py           # Google Vertex AI
│           └── openrouter.py       # OpenRouter
└── tests/
    ├── test_schema.py
    ├── test_utils.py
    └── test_promptgen.py
```

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         TITAN FACTORY PIPELINE                          │
├─────────────────────────────────────────────────────────────────────────┤
│  1. PROMPTGEN      → Generate 100 niches × 7 tasks = 700 tasks         │
│  2. PLANNER        → DeepSeek generates UI_SPEC JSON                    │
│  3. UIGEN          → Kimi + MiniMax generate code candidates (2 each)   │
│  4. VALIDATOR      → Next.js build with Devstral patcher loop           │
│  5. RENDERER       → Playwright screenshots (3 viewports)               │
│  6. JUDGE          → Vision model scores OR heuristic fallback          │
│  7. SELECTOR       → Best candidate per task (score ≥ 8.0)              │
│  8. EXPORTER       → train.jsonl + valid.jsonl (public/private tracks)  │
└─────────────────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

1. **No chain-of-thought storage** — Only structured UI_SPEC + final code files stored
2. **Deterministic task IDs** — `hash(niche_id + page_type + seed)` enables resume
3. **Two-track export** — `public/` (publishable models) vs `private/` (all models)
4. **Quality gating** — Only winners (build ✓ + score ≥ 8.0) reach training data
5. **Cached node_modules** — Symlinked to avoid 60s install per candidate
6. **Heuristic fallback** — Pipeline runs without vision model (warns loudly)

## Models Used

| Stage | Provider | Model | Purpose |
|-------|----------|-------|---------|
| Planner | Vertex AI | deepseek-ai/deepseek-v3.2-maas | Generate UI_SPEC |
| UI Gen | Vertex AI | moonshotai/kimi-k2-thinking-maas | Generate code (2 variants) |
| UI Gen | Vertex AI | minimaxai/minimax-m2-maas | Generate code (2 variants) |
| Patcher | OpenRouter | mistralai/devstral-2512:free | Fix build errors |
| Judge | OpenRouter | (configurable) | Score screenshots |

## Training Data Format

Each line in `train.jsonl`:

```json
{
  "messages": [
    {"role": "system", "content": "You are Titan 4 Design. Produce JSON only..."},
    {"role": "user", "content": "Create a dark themed landing page for..."},
    {"role": "assistant", "content": "{\"ui_spec\": {...}, \"files\": [...]}"}
  ]
}
```

## Quick Start

```bash
cd titan-4-design-factory
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium

cp .env.example .env
# Set: GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_REGION, OPENROUTER_API_KEY

gcloud auth application-default login

make smoke  # Run 3 tasks end-to-end
make run_public  # Full run with publishable models
```
