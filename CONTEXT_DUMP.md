# CONTEXT DUMP - Titan Design Factory
# New Claude: READ THIS ENTIRE FILE FIRST

## PROJECT OVERVIEW
Titan Design Factory is a UI generation pipeline that:
1. Takes a brief (e.g., "martial arts gym landing page")
2. Runs it through a Planner model to extract components
3. Sends to multiple UI Generator models (currently 2 models, 4 variants each = 8 candidates)
4. Patches syntax errors with a Patcher model
5. Screenshots each candidate with Puppeteer
6. Vision Judge (Creative Director) evaluates screenshots
7. Refiner iterates on feedback until "shippable"

## DIRECTORY STRUCTURE
```
/Users/alexburkhart/titan-4-design-factory/
├── src/titan_factory/
│   ├── uigen.py          # UI generation prompts - CRITICAL FILE
│   ├── judge.py          # Creative Director vision prompts - CRITICAL FILE
│   ├── refiner.py        # Refinement loop
│   ├── planner.py        # Brief → components
│   ├── patcher.py        # Syntax fixes
│   └── pipeline.py       # Orchestration
├── config/
│   └── config.yaml       # Model configuration - READ THIS
├── templates/
│   └── nextjs_app_router_tailwind/  # Working Next.js template for previews
├── out/
│   └── run_YYYYMMDD_HHMMSS_hash/    # Output folders
│       ├── manifest.db              # SQLite with candidate code
│       └── gallery/                 # Screenshots
└── tasks/                # Input briefs (YAML files)
```

## MODELS IN USE (from config/config.yaml)
- **Planner**: vertex / google/deepseek-v3.2-maas
- **UI Generator 1**: vertex / moonshotai/kimi-k2-thinking-maas (4 variants)
- **UI Generator 2**: vertex / minimaxai/minimax-m2-maas (4 variants)
- **Patcher**: openrouter / mistralai/devstral-2512:free
- **Vision Judge**: vertex / google/gemini-3-flash-preview

## ACTUAL CODE FROM KEY FILES

### uigen.py (lines 250-325) - UI Generation System Prompt
```python
OUTPUT FORMAT (STRICT):
1) First output a single <think>...</think> block with your reasoning.
2) Immediately after </think>, output ONE JSON object with EXACT keys:
   {"files":[{"path":"app/page.tsx","content":"..."}],"notes":["..."]}

ABSOLUTE RULES (discarded if violated):
- ZERO EMOJI CHARACTERS (🚀❌✅🎯💡⭐ etc.) - AUTOMATIC REJECT. Use inline SVG only.
- No text before <think>.
- No markdown, no ``` fences.
- JSON must be valid and must start with { and end with }.

SELF-QA LOOP (DO THIS INSIDE <think> BEFORE OUTPUT):
1) Hero answers what/who/outcome/next-step fast
2) All required sections exist and feel intentional
3) One primary CTA style, no competing primaries
4) Proof is labeled, no fake logos/metrics
5) A11Y: heading order, labels, focus rings, keyboard-friendly
6) Responsive: no overflow; mobile is polished
7) Visual polish: consistent radius/shadow/border
8) ZERO EMOJIS ANYWHERE - use inline SVG icons ONLY

IMPLEMENTATION RULES:
- Next.js App Router + TypeScript + Tailwind only.
- No UI libraries. No external assets. Use /placeholder.svg or gradients.
- ZERO EMOJI CHARACTERS - these break the premium feel. Use simple inline SVG paths.
- Prefer a single file: path must be "app/page.tsx"
```

User prompt (line 319):
```
- CRITICAL: NO EMOJI CHARACTERS (🚀❌✅⭐💡 etc.) - use inline SVG icons only
```

### judge.py (lines 140-206) - Creative Director Prompt
```python
CREATIVE_DIRECTOR_SYSTEM_PROMPT = """You are a world-class creative director reviewing a website design.

Your job is NOT to give a numeric score. Instead, provide rich qualitative feedback
that helps improve the design while PRESERVING creative emergence and risk-taking.

IMPORTANT PHILOSOPHY:
- "Different" is NOT "broken". Unconventional designs may be intentional.
- Creative risk-taking should be ENCOURAGED, not punished.
- Focus on production readiness, not personal taste.

WHAT MAKES SOMETHING "SHIPPABLE":
- No critical errors (blank page, runtime errors, missing content)
- Core functionality is present (navigation works, CTAs visible)
- Text is readable, layout is coherent
- Responsive across viewports
- NO EMOJI CHARACTERS (🚀❌✅🎯💡 etc.) - emojis look cheap/unprofessional

EMOJI CHECK: If you see ANY emoji characters in the UI (buttons, headings, cards, etc.),
add "Remove emoji characters - use SVG icons instead" to missing_for_production.
Emojis are a hard requirement violation.

WHAT DOES NOT MAKE SOMETHING "NOT SHIPPABLE":
- Unusual color choices (that's creative expression)
- Unconventional layouts (that's experimentation)
- Bold typography (that's design intent)

OUTPUT FORMAT:
{
  "shippable": true,
  "obviously_broken": false,
  "preserve": ["list of creative choices to keep"],
  "missing_for_production": ["only critical issues"],
  "creative_elevations": ["suggestions from a master designer"],
  "appropriate_for_type": true
}"""
```

## EXACT CODE CHANGES MADE (Dec 21, 2025)

### Change 1: uigen.py line ~259
Added to ABSOLUTE RULES:
```
- ZERO EMOJI CHARACTERS (🚀❌✅🎯💡⭐ etc.) - AUTOMATIC REJECT. Use inline SVG only.
```

### Change 2: uigen.py line ~319
Added to user prompt:
```
- CRITICAL: NO EMOJI CHARACTERS (🚀❌✅⭐💡 etc.) - use inline SVG icons only
```

### Change 3: judge.py lines 152-161
Added emoji check to Creative Director.

### Change 4: refiner.py line ~162
Added SVG replacement instruction:
```
- EMOJI → SVG: If told to remove emojis, replace with simple inline SVG icons (24x24 path-based, use currentColor)
```

### Change 5: judge.py line 276
Fixed "Cannot get feedback, not rendered" bug:
```python
# Before:
if candidate.status != CandidateStatus.RENDERED:

# After:
if candidate.status not in [CandidateStatus.RENDERED, CandidateStatus.SCORED]:
```

### Change 6: config/config.yaml
Increased variants from 2 to 4 per model (8 total candidates).

## KNOWN ISSUE: FALSE POSITIVE EMOJI DETECTION
The vision model sometimes flags SVG stars as "emoji" because yellow star SVGs LOOK like ⭐ emoji visually.

We verified via SQL query that ALL 8 candidates have ZERO actual emoji characters:
```bash
sqlite3 out/run_*/manifest.db "SELECT id, code FROM candidates" | grep -P '[\x{1F300}-\x{1F9FF}]'
# Empty output = no emojis (correct)
```

The "stars" are actually inline SVG paths styled with `text-yellow-400`:
```jsx
<svg className="w-5 h-5 text-yellow-400 fill-current" viewBox="0 0 20 20">
  <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292..."/>
</svg>
```

## USER PREFERENCES (CRITICAL)
1. **HATES EMOJIS** - All icons must be inline SVG, never emoji characters
2. **Wants variety** - That's why we increased to 8 candidates
3. **Wants to SEE websites** - Not just screenshots, actual localhost preview
4. **Dislikes verbose explanations** - Be direct, fix things, don't over-explain
5. **Prefers action over discussion** - Just do it

## HOW TO RUN THE FACTORY
```bash
cd /Users/alexburkhart/titan-4-design-factory
source .venv/bin/activate
titan-factory run --max-tasks 1
```

## HOW TO PREVIEW CANDIDATES LOCALLY
```bash
# 1. Find latest run
ls -lt out/ | head -3

# 2. Get run folder name
RUN_DIR="out/run_20251221_183848_4baa94"

# 3. Extract candidate code
sqlite3 $RUN_DIR/manifest.db "SELECT code FROM candidates WHERE id=1" > /tmp/page.tsx

# 4. Copy factory's working template
cp -r templates/nextjs_app_router_tailwind /tmp/titan-preview
cd /tmp/titan-preview

# 5. MUST run npm ci (symlinks break on copy)
npm ci

# 6. Paste candidate code
cp /tmp/page.tsx app/page.tsx

# 7. Run dev server
npm run dev  # localhost:3000
```

## MULTI-PORT PREVIEW (what we were working on)
User wanted all 4 candidates visible at once on ports 3000-3003.

What worked: Server 1 on port 3000 using factory template
What failed: Servers 2-4 failed with "Cannot find module '../server/require-hook'"
Cause: node_modules symlinks break when copying template folder
Fix: Must run `npm ci` in EACH folder individually

```bash
for i in 1 2 3 4; do
  cp -r templates/nextjs_app_router_tailwind /tmp/titan-preview-$i
  cd /tmp/titan-preview-$i
  npm ci  # REQUIRED FOR EACH
  sqlite3 $RUN_DIR/manifest.db "SELECT code FROM candidates WHERE id=$i" > app/page.tsx
  PORT=$((2999 + i)) npm run dev &
done
```

## SQLITE QUERIES FOR manifest.db
```sql
-- List all candidates
SELECT id, status FROM candidates;

-- Get code for specific candidate
SELECT code FROM candidates WHERE id=1;

-- Check for emojis (should return nothing)
SELECT id FROM candidates WHERE code LIKE '%🚀%' OR code LIKE '%⭐%';

-- Get Creative Director feedback
SELECT id, feedback FROM candidates;
```

## CREATIVE DIRECTOR MODE
Changed from numeric scoring (1-10) to qualitative feedback:
- **shippable**: boolean (true/false)
- **verdict**: "SHIP IT" or "NOT YET"
- **strongest_element**: what works best
- **missing_for_production**: array of issues to fix
- **one_line_improvement**: single highest-impact fix

## WHAT STILL NEEDS WORK
1. Multi-port preview - need to fix npm ci for multiple servers
2. Vision model false positives on SVG stars
3. Consider adding post-generation emoji scan in Python before judging

## COMMANDS REFERENCE
```bash
# Activate venv
source .venv/bin/activate

# Run factory
titan-factory run --max-tasks 1

# Check latest output
ls -lt out/ | head -3

# Query candidates
sqlite3 out/run_*/manifest.db "SELECT id, status FROM candidates"

# Extract code
sqlite3 out/run_*/manifest.db "SELECT code FROM candidates WHERE id=1"

# Check for actual emojis
sqlite3 out/run_*/manifest.db "SELECT id, code FROM candidates" | grep -P '[\x{1F300}-\x{1F9FF}]'
```

## SESSION HISTORY (Dec 21, 2025)

### What User Asked For (chronological):
1. "NO emojis but svgs instead" - Stars showing as emoji, wanted SVG
2. "its only one output" - Only 4 candidates, wanted more variety
3. Confirmed all 8 candidates had ZERO emojis after SQL check - vision model false positive
4. "i need to SEE the websites host them locally" - Screenshots not enough
5. "just open all 4" - Wanted 4 localhost servers at once
6. Asked about context usage ("whats eating up 50k tokens")
7. Asked for comprehensive context dump for new Claude

### What We Did:
1. Strengthened no-emoji rules in uigen.py (system prompt + user prompt)
2. Added emoji check to Creative Director in judge.py
3. Added SVG replacement instruction to refiner.py
4. Increased variants from 2 to 4 per model in config.yaml
5. Fixed "not rendered" bug in judge.py line 276
6. Ran factory, got 8 candidates, verified no emojis via SQL
7. Set up local preview on port 3000 using factory template
8. Attempted 4-port preview, 3 failed due to symlink issues

### Previous Session Work (before this):
- Implemented Creative Director mode (qualitative vs numeric scoring)
- Changed judge.py to output shippable/not instead of 1-10 scores
- Refactored refiner.py to use CD feedback instead of scores
- Fixed various pipeline bugs

## PROJECT GOALS (broader context)
User is building a UI generation factory that:
1. Takes simple briefs and outputs multiple production-ready UI candidates
2. Uses multiple models for diversity (kimi-k2 + minimax-m2)
3. Uses vision model (gemini-3-flash) as Creative Director
4. Iterates via refiner until designs are "shippable"
5. Outputs to `out/` folder with SQLite manifest + screenshots

The goal is VARIETY + QUALITY. User wants:
- 8+ distinct candidates per brief
- Each candidate should be production-ready (no placeholders, no emojis)
- Ability to preview all candidates locally
- Creative Director that rewards experimentation, not just safe designs

## TECHNICAL STACK
- Python 3.11+ with async/await
- Vertex AI for model hosting (Google Cloud)
- OpenRouter for fallback models
- Puppeteer for screenshots
- SQLite for manifest storage
- Next.js 14 + TypeScript + Tailwind for output

## ENVIRONMENT
- Working directory: /Users/alexburkhart/titan-4-design-factory
- Virtual env: .venv (activate with `source .venv/bin/activate`)
- Node: v23.11.0
- Factory CLI: `titan-factory` (installed via pip install -e .)

## GOTCHAS
1. node_modules symlinks break when copying template - MUST run `npm ci` after copy
2. Vision model false positives on SVG stars looking like emoji
3. Pipeline can timeout if Vertex AI is slow - check logs in `out/run_*/`
4. Some models output markdown fences despite instructions - patcher fixes this

## END OF CONTEXT DUMP
New Claude: After reading this, read the actual files in this order:
1. config/config.yaml (full file)
2. src/titan_factory/uigen.py (lines 250-320)
3. src/titan_factory/judge.py (lines 130-210, 270-280)
4. src/titan_factory/refiner.py (lines 150-180)

Then ask: "I've read the context dump. Ready to continue - what do you need?"
