# Petamind MCP (Claude Code) — Skill

This document is meant to be dropped into a repo and used as **agent instructions** for how to
drive the `petamind-mcp` server from Claude Code.

(Server script name: `petamind-mcp`; legacy alias: `terra-mind-mcp`.)

It’s intentionally “workflow first” and assumes Claude Code can spawn parallel subagents.

## Naming note (“Poetiq-style”)

If you see “Poetiq-style” referenced, it is used *descriptively* to mean an iterative refinement loop
(generate → critique → refine → verify). This project is **not affiliated** with Poetiq.

## What you get

The MCP server exposes two tools:

- `titan_code_solve`
- `titan_code_eval_patch`

Aliases (same behavior):
- `petamind_solve`
- `petamind_eval_patch`

Legacy aliases (backwards compatibility):
- `terra_mind_solve`
- `terra_mind_eval_patch`

It can run a full Titan-style loop internally (plan → generate → gate → fix → pick winner),
or you can do the orchestration in Claude Code using subagents.

## Two recommended operating modes

### Mode A — MCP does the loop (simple)

Use when you want the MCP to do everything inside one call.

Suggested args:

- `planning_mode="megamind"` (default)
- `max_candidates=4`
- `candidate_concurrency=1` (keep stable; Claude Code can parallelize elsewhere)
- `test_command` explicitly set for the repo (recommended)
- `lint_command` optionally set for the repo
- `apply_to_repo=false` (default)

Vision is always in the loop.

- If you want **UI screenshots** (recommended for web/UI work), set:
  - `vision_mode="on"`
  - `preview_command` and `preview_url` (supports `{port}` placeholder)
- Otherwise leave `vision_mode="auto"` (default) and the MCP will score a **diff screenshot**.

### Mode B — Claude Code orchestrates (recommended for heavy parallelism)

Use when you want Claude Code to spawn many subagents, each producing an independent candidate.

Pattern:

1. The main agent selects:
   - `repo_path`, `goal`, `test_command`, `lint_command`
   - a temperature schedule (e.g. `[0.2, 0.5, 0.85, 1.0]`)
2. Spawn N subagents, one per candidate:
   - Each subagent either:
     - generates a patch itself, then calls `titan_code_eval_patch`, or
     - calls `titan_code_solve` with `max_candidates=1` (convenience mode)
   - If using `titan_code_eval_patch`, each subagent passes:
     - `patches=[{"path": "...", "patch": "..."}]`
     - `goal` (optional but recommended; helps vision scoring)
     - `test_command` (recommended)
     - optional preview args (for UI screenshots)
   - If using `titan_code_solve`, each subagent calls it with:
     - `max_candidates=1`
     - `candidate_concurrency=1`
     - `temperature_schedule=[<one temp>]`
     - optionally different `planner_*_model` or different `model` values
3. Each subagent returns:
   - `run_dir`
   - winner patch + candidate summary info
4. Main agent ranks candidates using:
   - `winner.passes_all_gates`
   - `winner.vision_score` (always-on vision gate)
   - `adds + deletes` (smaller diffs preferred)
   - `run_dir/run_summary.json` and `candidates/*/candidate_summary.json`
5. Apply the winner manually (preferred), or re-run a second round focused only on the best 1–2.

Why this is better:

- Claude Code can run 10–100 subagents in parallel without forcing the MCP server to be a giant orchestrator.
- Each run writes clean on-disk artifacts (`run_summary.json`, `candidate_summary.json`) for selection.
- Failures stay isolated and debuggable.

## Practical defaults

- Always set `test_command` if you can.
  - If omitted, `titan_code_solve` will infer (Node/Python/Go/Rust) or fall back to `true`.
- Keep `apply_to_repo=false` unless you *really* want auto-application.
  - The MCP refuses to apply a best-effort winner that didn’t pass all enabled gates.
- For vision mode:
  - Ensure Playwright browser binaries exist: `playwright install chromium`
  - If using parallel candidates with vision:
    - use `TITAN_MCP_PORT_START` + `TITAN_MCP_PORT_STRIDE` to avoid port collisions

## Minimal call template (paste into a subagent)

```json
{
  "repo_path": "/path/to/target/repo",
  "goal": "Describe the change you want.",
  "planning_mode": "off",
  "max_candidates": 1,
  "candidate_concurrency": 1,
  "temperature_schedule": [0.85],
  "test_command": "npm test",
  "lint_command": null,
  "vision_mode": "auto",
  "vision_provider": "anthropic_vertex",
  "vision_model": "claude-opus-4-5@20251101",
  "section_creativity_mode": "off",
  "apply_to_repo": false,
  "allow_nonpassing_winner": false
}
```
