# Titan Design Factory - UI Generation Pipeline

## What This Project Does
Generates multiple UI design candidates from a single brief using multiple AI models, then uses a Creative Director (vision model) to judge which are "shippable" vs need work.

## Quick Start
```bash
cd /Users/alexburkhart/titan-4-design-factory
source .venv/bin/activate
titan-factory run --max-tasks 1
```

## Key Files (READ THESE FIRST)
| File | Lines | What It Does |
|------|-------|--------------|
| `src/titan_factory/uigen.py` | 250-320 | UI generation system prompt |
| `src/titan_factory/judge.py` | 130-200 | Creative Director prompt |
| `src/titan_factory/refiner.py` | 150-180 | Refinement instructions |
| `config/config.yaml` | all | Model config, variants |

## Architecture
```
Brief → Planner (deepseek-v3.2) → UI Generators (kimi-k2 + minimax-m2, 4 variants each)
     → Patcher (devstral) → Vision Judge (gemini-3-flash) → Refiner loop → Final outputs
```

## Current Model Config (config/config.yaml)
```yaml
planner:
  provider: vertex
  model: google/deepseek-v3.2-maas

ui_generators:
  - provider: vertex
    model: moonshotai/kimi-k2-thinking-maas
    variants: 4  # CHANGED from 2
  - provider: vertex
    model: minimaxai/minimax-m2-maas
    variants: 4  # CHANGED from 2

patcher:
  provider: openrouter
  model: mistralai/devstral-2512:free

vision_judge:
  provider: vertex
  model: google/gemini-3-flash-preview
```

## HARD RULES (User is strict about these)
1. **NO EMOJI CHARACTERS EVER** - Stars must be inline SVG `<svg>` paths, not ⭐
2. All icons = inline SVG (24x24, path-based, use currentColor)
3. Tailwind CSS only, no external deps
4. Single-file components (page.tsx exports everything)

## Code Changes Made (Dec 21, 2025)

### 1. uigen.py - No-Emoji in System Prompt
Location: `src/titan_factory/uigen.py` around line 259
```python
ABSOLUTE RULES (discarded if violated):
- ZERO EMOJI CHARACTERS (🚀❌✅🎯💡⭐ etc.) - AUTOMATIC REJECT. Use inline SVG only.
```

Also added to user prompt around line 319:
```python
- CRITICAL: NO EMOJI CHARACTERS (🚀❌✅⭐💡 etc.) - use inline SVG icons only
```

### 2. judge.py - Creative Director Emoji Check
Location: `src/titan_factory/judge.py` lines 152-161
```python
WHAT MAKES SOMETHING "SHIPPABLE":
...
- NO EMOJI CHARACTERS (🚀❌✅🎯💡 etc.) - emojis look cheap/unprofessional

EMOJI CHECK: If you see ANY emoji characters in the UI (buttons, headings, cards, etc.),
add "Remove emoji characters - use SVG icons instead" to missing_for_production.
Emojis are a hard requirement violation.
```

### 3. refiner.py - SVG Replacement Instruction
Location: `src/titan_factory/refiner.py` line 162
```python
- EMOJI → SVG: If told to remove emojis, replace with simple inline SVG icons (24x24 path-based, use currentColor)
```

### 4. judge.py - Fixed "not rendered" bug
Location: `src/titan_factory/judge.py` line 276
Changed to accept SCORED status in addition to RENDERED:
```python
if candidate.status not in [CandidateStatus.RENDERED, CandidateStatus.SCORED]:
```

### 5. config.yaml - Increased variants
Changed from 2 to 4 per model (8 total candidates)

## Known Issue: False Positive Emoji Detection
The vision model (gemini-3-flash) sometimes flags SVG stars as "emoji" because they LOOK similar visually. We verified via SQL that ALL candidates have ZERO actual emoji characters - the stars are SVG paths with `text-yellow-400` Tailwind class.

To verify no emojis:
```bash
sqlite3 out/run_*/manifest.db "SELECT id, code FROM candidates" | grep -P '[\x{1F300}-\x{1F9FF}]'
# Empty output = no emojis (correct)
```

## Output Location
Runs save to: `out/run_YYYYMMDD_HHMMSS_hash/`
- `manifest.db` - SQLite with all candidate code
- `gallery/` - Screenshots of each candidate

## How to Preview Candidates Locally
```bash
# 1. Find latest run
ls -lt out/ | head -3

# 2. Extract candidate code
sqlite3 out/run_XXXXX/manifest.db "SELECT code FROM candidates WHERE id=1" > /tmp/page.tsx

# 3. Copy factory's working template
cp -r templates/nextjs_app_router_tailwind /tmp/titan-preview
cd /tmp/titan-preview

# 4. Install deps (MUST run npm ci, symlinks break on copy)
npm ci

# 5. Paste candidate code
cp /tmp/page.tsx app/page.tsx

# 6. Run
npm run dev  # localhost:3000
```

## User Preferences
- HATES emojis - SVG icons only
- Wants VARIETY in outputs (that's why 8 candidates now)
- Wants to SEE websites locally, not just screenshots
- Dislikes verbose explanations - be direct
- Prefers fixing things over explaining why they broke

## Current State (as of Dec 21 evening)
- Factory runs and produces 8 candidates
- Creative Director mode working (qualitative feedback)
- Local preview works on port 3000 using factory template
- Vision model has false positives on SVG stars (cosmetic, not a real bug)

## What Was Being Worked On
User wanted all 4 candidates visible simultaneously on ports 3000-3003. Server 1 worked, servers 2-4 failed due to node_modules symlink issues. Fix is to run `npm ci` in each folder individually.
