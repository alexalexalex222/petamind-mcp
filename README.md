# TITAN-4-DESIGN DATASET FACTORY v1

A production-grade synthetic dataset generator for fine-tuning small student models on UI/UX design tasks (landing pages, directories, dashboards) using Next.js App Router + TypeScript + Tailwind.

## Features

- **Multi-model pipeline**: Uses Vertex AI (DeepSeek, Kimi, MiniMax) and OpenRouter (Devstral, vision models)
- **Quality gating**: Only winners pass through to training data (build success + vision score threshold)
- **Resumable**: SQLite caching for model responses, task state persistence
- **Two output tracks**: `public/` (publishable models only) and `private/` (all models)
- **No contamination**: Chain-of-thought/thinking never stored; only structured specs + code

## Quick Start

### 1. Environment Setup

```bash
# Clone and enter
cd titan-4-design-factory

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .

# Or with uv (faster)
uv pip install -e .

# Install Playwright browsers
playwright install chromium
```

### 2. Configure Environment Variables

```bash
cp .env.example .env
# Edit .env with your credentials
```

Required:
- `GOOGLE_CLOUD_PROJECT`: Your GCP project ID
- `GOOGLE_CLOUD_REGION`: Region for Vertex AI (e.g., `us-central1`)
- `OPENROUTER_API_KEY`: Your OpenRouter API key

Optional:
- `GCS_BUCKET`: For cloud backup of outputs

### 3. Authenticate with Google Cloud

```bash
gcloud auth application-default login
```

### 4. Run

```bash
# Smoke test (3 tasks end-to-end)
make smoke

# Full run (public models only)
make run_public

# Full run (all models including private)
make run_private

# Resume a previous run
titan-factory run --resume <run_id>

# Export training data
make export RUN_ID=<run_id>
```

## Configuration

Edit `config/config.yaml` to customize:

```yaml
models:
  planner:
    provider: vertex
    model: deepseek-ai/deepseek-v3.2-maas
    publishable: true

  ui_generators:
    - provider: vertex
      model: moonshotai/kimi-k2-thinking-maas
      publishable: true
      variants: 2
    - provider: vertex
      model: minimaxai/minimax-m2-maas
      publishable: true
      variants: 2

  patcher:
    provider: openrouter
    model: mistralai/devstral-2512:free
    publishable: true

  vision_judge:
    provider: openrouter
    model: null  # Falls back to heuristic scorer
    publishable: false

pipeline:
  vision_score_threshold: 8.0
  max_fix_rounds: 2
  polish_loop_enabled: true
  tasks_per_niche: 7

budget:
  concurrency_vertex: 5
  concurrency_openrouter: 10
  requests_per_min_vertex: 60
  requests_per_min_openrouter: 100
  max_total_tasks: null  # Run all
  stop_after_usd: null   # No limit

export:
  holdout_niches: 12
  validation_split: 0.08
```

## Pipeline Stages

1. **Niche/Task Generation**: Creates 100 niches × 7 tasks = 700+ tasks
2. **Planning**: DeepSeek generates UI_SPEC JSON for each task
3. **UI Generation**: Kimi + MiniMax generate code candidates (2 variants each)
4. **Validation**: Next.js build with Devstral-powered fix loops
5. **Rendering**: Playwright captures screenshots at 3 viewport sizes
6. **Scoring**: Vision judge (or heuristic fallback) scores candidates
7. **Selection**: Best candidate per task selected for training
8. **Export**: Winners exported to train.jsonl / valid.jsonl

## Output Structure

```
out/<run_id>/
├── cache.db                 # SQLite response cache
├── manifest.db              # Task state tracking
├── prompts/
│   ├── niches.json
│   └── tasks.jsonl
├── renders/
│   └── <task_id>/
│       └── <candidate_id>/
│           ├── 375x812.png
│           ├── 768x1024.png
│           └── 1440x900.png
├── rich_records.jsonl       # All candidates (for audit)
├── selected_records.jsonl   # Winners only
├── public/
│   ├── train.jsonl
│   └── valid.jsonl
└── private/
    ├── train.jsonl
    └── valid.jsonl
```

## Training Data Format

Each line in train.jsonl:

```json
{
  "messages": [
    {"role": "system", "content": "You are Titan 4 Design..."},
    {"role": "user", "content": "<task prompt>"},
    {"role": "assistant", "content": "{\"ui_spec\": ..., \"files\": [...]}"}
  ]
}
```

## Page Types Covered

- `landing`: Marketing landing pages
- `directory_home`: Directory homepage with search
- `city_index`: City-specific listing pages
- `category_index`: Category-specific listing pages
- `listing_profile`: Individual listing detail pages
- `admin_dashboard`: Admin/analytics dashboards
- `edit`: Refactor/edit tasks (20% of dataset)

## Development

```bash
# Run tests
pytest tests/

# Type checking
mypy src/

# Format
ruff format src/ tests/
ruff check src/ tests/
```

## Architecture Notes

- **Provider abstraction**: Clean interface for Vertex AI and OpenRouter
- **Deterministic IDs**: Tasks have stable IDs from hash(niche_id + page_type + seed)
- **JSON strictness**: Safe extraction with fallback parsing
- **Async throughout**: Uses asyncio for concurrent model calls
- **No thinking storage**: Only structured UI_SPEC and final code stored

## License

MIT
