# Session Log - Dec 21, 2025

## User Messages (Summarized)
1. "NO emojis but svgs instead" - wanted stars as SVG, not emoji
2. "its only one output" - wanted more variety (was getting 4, wanted 8)
3. "i need to SEE the websites host them locally" - screenshots not enough
4. "just open all 4" - wanted multiple candidates visible at once
5. "whats eating up 50k tokens on start up" - concerned about context usage

## What We Changed

### 1. No-Emoji Rule Strengthened
**File: `src/titan_factory/uigen.py`**
- Line ~259: Added to ABSOLUTE RULES section
- Line ~319: Added to user prompt
```python
ABSOLUTE RULES (discarded if violated):
- ZERO EMOJI CHARACTERS - AUTOMATIC REJECT. Use inline SVG only.
```

### 2. Increased Variants
**File: `config/config.yaml`**
```yaml
ui_generators:
  - model: kimi-k2-thinking-maas
    variants: 4  # was 2
  - model: minimax-m2-maas
    variants: 4  # was 2
```

### 3. Creative Director Emoji Check
**File: `src/titan_factory/judge.py`** (lines 152-161)
```python
EMOJI CHECK: If you see ANY emoji characters in the UI,
add "Remove emoji characters - use SVG icons instead" to missing_for_production.
```

### 4. Refiner SVG Replacement
**File: `src/titan_factory/refiner.py`** (line 162)
```python
- EMOJI → SVG: If told to remove emojis, replace with simple inline SVG icons
```

## Key Discovery
After all changes, ran factory and got 8 candidates. Creative Director flagged some for "emoji" but SQL query proved ALL 8 candidates had ZERO actual emoji characters. The SVG stars styled with `text-yellow-400` just LOOKED like emoji to the vision model. False positive.

## Local Preview Attempt
- Tried hosting 4 candidates on ports 3000-3003
- Server 1 (port 3000) worked via factory template
- Servers 2-4 failed: broken node_modules symlinks
- Fix: Need `npm ci` in each folder, not just copy

## Files Modified This Session
1. `/src/titan_factory/uigen.py` - no-emoji rules
2. `/config/config.yaml` - variants 2→4
3. `/src/titan_factory/judge.py` - CD emoji check
4. `/src/titan_factory/refiner.py` - SVG replacement instruction
