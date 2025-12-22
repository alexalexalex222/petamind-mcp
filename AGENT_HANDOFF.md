# TITAN-4-DESIGN FACTORY - AGENT HANDOFF

## CRITICAL: READ THESE FILES FIRST

```
1. /Users/alexburkhart/titan-4-design-factory/src/titan_factory/utils.py
   - Lines 63-151: extract_json() and extract_json_strict() - JUST FIXED
   - Fix A: Now handles None input safely
   - Fix B: Uses finditer() to try ALL code blocks

2. /Users/alexburkhart/titan-4-design-factory/src/titan_factory/uigen.py
   - Lines 75-201: generate_candidate() - JUST FIXED
   - Fix C: Added truncation detection (finish_reason=="length") + retry loop
   - Retries with 25% more tokens on truncation

3. /Users/alexburkhart/titan-4-design-factory/config/config.yaml
   - Lines 14-27: UI generators config
   - Fix D: max_tokens increased from 8000 to 12000

4. /Users/alexburkhart/titan-4-design-factory/src/titan_factory/cli.py
   - Entry point, requires GOOGLE_CLOUD_PROJECT even when not using Vertex

5. /Users/alexburkhart/titan-4-design-factory/src/titan_factory/orchestrator.py
   - Main pipeline orchestration

6. /Users/alexburkhart/titan-4-design-factory/src/titan_factory/providers/vertex.py
   - Vertex AI MaaS provider (Kimi K2, MiniMax M2, DeepSeek)

7. /Users/alexburkhart/titan-4-design-factory/src/titan_factory/providers/openrouter.py
   - OpenRouter provider

8. /Users/alexburkhart/titan-4-design-factory/src/titan_factory/providers/gemini.py
   - Gemini API provider (for vision judge)
```

## WHAT WAS ACCOMPLISHED

GPT-5.2 Pro analyzed the 75% candidate failure rate and identified 3 root causes:

### Fix A ✅ DONE - None Safety
`extract_json_strict()` was crashing with `'NoneType' is not subscriptable` when trying `text[:500]` on None input.

**Fixed in**: `utils.py:146-150`
```python
preview = "<None>" if text is None else text[:500]
raise ValueError(f"Failed to extract JSON from response: {preview}...")
```

### Fix B ✅ DONE - Multiple Code Blocks
`extract_json()` only tried the FIRST markdown code block. Now uses `finditer()` to try ALL blocks.

**Fixed in**: `utils.py:102-109`
```python
for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE):
    block = match.group(1).strip()
    try:
        return json.loads(block)
    except json.JSONDecodeError:
        continue
```

### Fix C ✅ DONE - Truncation Detection + Retry
Added retry loop that detects `finish_reason=="length"` and retries with 25% more tokens.

**Fixed in**: `uigen.py:132-194`
- `max_retries = 2`
- On truncation: `max_tokens = int(max_tokens * 1.25)`
- Adds retry hint message asking for clean JSON output

### Fix D ✅ DONE - Token Budget Increase
Increased `max_tokens` from 8000 to 12000 for UI generators.

**Fixed in**: `config/config.yaml:20,26`

## CURRENT BLOCKERS

### 1. Vertex AI Billing
Project `gen-lang-client-0623615200` returns 403:
```
"This API method requires billing to be enabled"
```

The PREVIOUS RUN (run_20251213_045924_40c76e) WORKED with Vertex. Either:
- Billing was enabled then and got disabled
- A different project was used (couldn't find it)
- Trial credits expired

**Service account files found**:
- `/Users/alexburkhart/Downloads/gen-lang-client-0623615200-0dbd1f72ab38.json`
- `/Users/alexburkhart/Downloads/gen-lang-client-0623615200-103a4c99bc74.json`

### 2. OpenRouter Credits
Returns 402: `"Insufficient credits"`

Free models (devstral:free) return 429 rate limit.

### 3. Environment Variables
These are SET:
- `GEMINI_API_KEY` ✅ (starts with AIzaSyC8ns...)
- `OPENROUTER_API_KEY` ✅ (starts with sk-or-v1-653c46...)

These need to be SET:
- `GOOGLE_CLOUD_PROJECT` - needs a project with billing enabled

## WHAT NEEDS TO BE DONE

1. **Find or create a GCP project with billing enabled**
   - Check if user has other GCP projects
   - Or enable billing on gen-lang-client-0623615200
   - Or use a different auth method

2. **Run the pipeline to test the fixes**
   ```bash
   source .venv/bin/activate
   GOOGLE_CLOUD_PROJECT=<project-with-billing> python -m titan_factory.cli run --max-tasks 1
   ```

3. **Verify the 4 fixes work**
   - Check that truncated responses trigger retry
   - Check that None responses don't crash
   - Check that code blocks are properly extracted
   - Check that 12000 tokens is sufficient

## PREVIOUS SUCCESSFUL RUN

**Run ID**: `run_20251213_045924_40c76e`
**Location**: `/Users/alexburkhart/titan-4-design-factory/out/run_20251213_045924_40c76e/`

Results:
- 4 candidates generated (2 Kimi K2, 2 MiniMax M2)
- 3 failed JSON extraction (the bug we fixed)
- 1 winner: MiniMax M2 variant 1, score 9.2/10

Check DB:
```bash
sqlite3 out/run_20251213_045924_40c76e/manifest.db "SELECT id, generator_model, status, score FROM candidates"
```

## DEBUG CONTEXT FILES

Created for GPT-5.2 Pro analysis:
```
/Users/alexburkhart/titan-4-design-factory/debug-context/
├── 01_PROBLEM_STATEMENT.md      # Overview of the bug
├── 02_extract_json_function.py  # The function that was failing
├── 03_uigen_module.py           # Where extraction is called
├── 04_schema_definitions.py     # Expected JSON structure
├── 05_config_yaml.yaml          # Model config
├── 06_providers_vertex.py       # Vertex API calls
├── 07_FAILED_RESPONSE_kimi_k2_variant0.txt   # Empty response
├── 08_FAILED_RESPONSE_kimi_k2_variant1.txt   # Truncated JSON
├── 09_FAILED_RESPONSE_minimax_m2_variant0.txt # Think block + truncated
├── 10_SUCCESS_RESPONSE_minimax_m2_variant1.txt # Working response
```

## PROJECT STRUCTURE

```
/Users/alexburkhart/titan-4-design-factory/
├── src/titan_factory/
│   ├── cli.py          # Entry point
│   ├── orchestrator.py # Pipeline runner
│   ├── planner.py      # Generates UI_SPEC
│   ├── uigen.py        # Generates code (FIXED)
│   ├── renderer.py     # Screenshots
│   ├── judge.py        # Vision scoring
│   ├── patcher.py      # Build error fixer
│   ├── exporter.py     # Training data export
│   ├── utils.py        # JSON extraction (FIXED)
│   ├── schema.py       # Pydantic models
│   ├── config.py       # Config loader
│   └── providers/
│       ├── vertex.py   # Vertex AI MaaS
│       ├── openrouter.py
│       └── gemini.py
├── config/
│   ├── config.yaml     # Main config (FIXED max_tokens)
│   ├── config-openrouter.yaml  # OpenRouter alternative
│   └── config-free.yaml        # Free models (rate limited)
├── templates/
│   └── nextjs_app_router_tailwind/  # Project template
├── out/                # Run outputs
└── .venv/              # Python virtual environment
```

## RUNNING THE PIPELINE

```bash
cd /Users/alexburkhart/titan-4-design-factory
source .venv/bin/activate

# With Vertex (needs billing)
GOOGLE_CLOUD_PROJECT=<your-project> python -m titan_factory.cli run --max-tasks 1

# Check results
sqlite3 out/run_*/manifest.db "SELECT id, status, score, error FROM candidates"
```

## THE USER'S GOAL

Generate synthetic training data for fine-tuning small models on UI design.
Pipeline: Planner → UI Generators (2 models × 2 variants) → Builder → Renderer → Vision Judge → Export

**CRITICAL REQUIREMENT**: Preserve `<think>` reasoning blocks in training data so student models learn to reason.

The raw_generator_response field stores the FULL response including thinking.
The extraction strips thinking for JSON parsing but keeps it for training.

## NOTES

- The fixes are DONE. The code is correct now.
- The blocker is purely billing/auth on GCP.
- Previous run worked, so there's a way - just need to find the right project or enable billing.
- gcloud CLI is installed at `~/google-cloud-sdk/bin/gcloud` but not logged in.
