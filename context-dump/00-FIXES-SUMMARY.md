# TITAN-4-DESIGN DATASET FACTORY - Bug Fixes Applied

## Summary
All critical issues identified by GPT-5.2 Pro code review have been addressed.

---

## Critical Fixes (Must-Have)

### 1. Determinism + Resume Fixed
**Issue:** Python's `hash()` is randomized per-process, breaking task ID stability.
**Fix:** Created `stable_hash()` using SHA256 for all seed/ID generation.
```python
def stable_hash(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest(), 16) % (2**31)
```

### 2. Exactly 100 Niches Enforced
**Issue:** NICHE_DEFINITIONS had 102 entries, not 100.
**Fix:** Trimmed to 100 + added assertion in `generate_niches()`.

### 3. Edit Tasks Now Use Real Code
**Issue:** Edit tasks had placeholder `"// Code from landing page generation"`.
**Fix:** Edit tasks are now generated dynamically AFTER landing page winners are selected. The orchestrator creates edit tasks using the actual winning code as `code_old`.

### 4. Task Prompts Persisted in DB
**Issue:** Exporter reconstructed prompts instead of using originals.
**Fix:** Added `prompt`, `seed`, `is_edit`, `code_old` columns to tasks table. Exporter now uses stored prompts with fallback reconstruction.

### 5. Patching Loop Reference Bug Fixed
**Issue:** `validate_with_retry()` didn't propagate patched candidate back.
**Fix:** Function now returns `(success, candidate)` tuple and copies patched fields in-place.

### 6. package-lock.json Copy Bug Fixed
**Issue:** Unconditional copy crashed if file missing.
**Fix:** Conditional check before copy.

### 7. Path Traversal Protection Added
**Issue:** Model could write to `../../something`.
**Fix:** Added `ALLOWED_PATH_PATTERNS` allowlist and `validate_file_path()` that:
- Blocks `..` in paths
- Blocks absolute paths
- Only allows: `app/**`, `components/**`, `lib/**`, `public/*`, `styles/*`

---

## Should-Fix Items Addressed

### 8. Viewport Labels in Vision Judge
**Issue:** Images passed without context.
**Fix:** Prompt now includes:
```
SCREENSHOTS PROVIDED (in order):
Image 1: Mobile (375×812)
Image 2: Tablet (768×1024)
Image 3: Desktop (1440×900)
```

### 9. Full Teacher Chain Tracking
**Issue:** `publishable` only checked generator.
**Fix:** Added `TeacherModel` schema with `planner_model` and `patcher_models[]` on Candidate. New `compute_publishable()` method checks entire chain:
```python
publishable = planner.publishable AND generator.publishable AND all(patcher.publishable for patcher)
```

### 10. ResponseCache Documentation
**Issue:** Potential to store raw responses with chain-of-thought.
**Fix:** Renamed column to `extracted_json`, added extensive docstring clarifying only parsed JSON should be cached.

---

### 11. Gemini Native Provider for Vision
**Issue:** Vision judge configured for Vertex provider which uses OpenAI-compatible API. Gemini 3 Pro Preview requires native Gemini API format for multimodal vision.
**Fix:** Created dedicated `GeminiProvider` class:
- Uses Vertex AI Gemini endpoint (not OpenAI-compatible endpoint)
- Supports `inline_data` format for images (Gemini native format)
- Handles ADC token refresh with caching
- Registered with ProviderFactory as "gemini"
- Config updated to use `provider: gemini` for vision_judge

---

## Files Modified
- `src/titan_factory/promptgen.py` - stable_hash, 100 niches, no upfront edit tasks
- `src/titan_factory/orchestrator.py` - DB schema, teacher chain, edit task queue
- `src/titan_factory/validator.py` - path protection, package-lock fix, return tuple
- `src/titan_factory/patcher.py` - records patcher model in chain
- `src/titan_factory/judge.py` - viewport labels in prompt
- `src/titan_factory/exporter.py` - uses stored prompts, full publishable check
- `src/titan_factory/schema.py` - TeacherModel, compute_publishable()
- `src/titan_factory/providers/gemini.py` - **NEW** native Gemini provider for vision
- `src/titan_factory/providers/__init__.py` - registered GeminiProvider
- `config/config.yaml` - vision_judge now uses gemini provider

---

## GO/NO-GO Status: **GO**
All blockers resolved. Pipeline is now production-safe for spending credits.
