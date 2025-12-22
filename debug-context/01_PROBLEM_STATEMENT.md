# TITAN-4-DESIGN DATASET FACTORY - JSON Extraction Bug

## THE PROBLEM
75% of UI generation candidates are being discarded due to JSON extraction failures. Only 1 out of 4 candidates made it through on the last run.

## PIPELINE OVERVIEW
```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   PLANNER   │ ──▶ │  UI GEN x4  │ ──▶ │   PATCHER   │ ──▶ │ VISION JUDGE│
│DeepSeek V3.2│     │Kimi K2 (x2) │     │  Devstral   │     │Gemini 3 Pro │
│             │     │MiniMax (x2) │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

## MODELS INVOLVED
- **Planner**: `deepseek-ai/deepseek-v3.2-maas` (Vertex AI MaaS)
- **UI Generators**:
  - `moonshotai/kimi-k2-thinking-maas` (Vertex AI MaaS) - **THINKING MODEL** - outputs `<think>` blocks
  - `minimaxai/minimax-m2-maas` (Vertex AI MaaS) - Also outputs `<think>` blocks sometimes
- **Patcher**: `mistralai/devstral-2512:free` (OpenRouter)
- **Vision Judge**: `gemini-3-pro-preview` (Gemini API) - **THINKING MODEL**

## THE CRITICAL BUG
The `extract_json()` function in `utils.py` is failing to extract valid JSON from model responses.

### Failure Modes Observed:

1. **Kimi K2 variant 0**: Response was `None` → `'NoneType' object is not subscriptable`
   - Root cause: Model returned empty response or API error not properly handled

2. **Kimi K2 variant 1**: Response was valid JSON but extraction failed
   - Response starts with `{` (no think block visible)
   - JSON appears valid but is **TRUNCATED** mid-code
   - The JSON ends with `onClick` - incomplete button handler

3. **MiniMax M2 variant 0**: Response has `<think>` block followed by JSON
   - `<think>...</think>` block is present and CLOSED properly
   - JSON follows the think block
   - BUT: JSON is wrapped in markdown code block: ` ```json ... ``` `
   - Current extraction should handle this but is FAILING

4. **MiniMax M2 variant 1** (SUCCESS):
   - Has `<think>` block followed by JSON in markdown code block
   - Extracted successfully - WHY DID THIS ONE WORK?

## EXPECTED JSON FORMAT
```json
{
  "files": [
    {"path": "app/page.tsx", "content": "...code..."}
  ],
  "notes": ["note1", "note2"]
}
```

## FILES TO EXAMINE
1. `02_extract_json_function.py` - Current extraction logic
2. `03_uigen_module.py` - Where extraction is called
3. `07-10_*_RESPONSE_*.txt` - Raw model responses (failed and successful)

## YOUR TASK
Analyze WHY extraction fails on 3/4 responses and propose a fix to `extract_json()` that will:
1. Handle `<think>` blocks (closed AND unclosed)
2. Handle markdown code blocks (` ```json ``` `)
3. Handle truncated JSON (maybe attempt repair?)
4. Not break working cases

## KEY INSIGHT
The **successful** response (MiniMax M2 variant 1) has THE SAME FORMAT as the failed MiniMax M2 variant 0:
- Both have `<think>` blocks
- Both have ` ```json ``` ` code blocks
- Both are similar length

So WHY did one work and one fail? Compare files 09 and 10 carefully.
