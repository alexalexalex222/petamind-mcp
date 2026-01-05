# Petamind MCP (Claude Code)

This repo includes a lightweight **MCP server** that exposes a multi-candidate agentic coding loop to Claude Code.

You can use:
- `titan_code_solve` (the full internal loop), or
- `titan_code_eval_patch` (a “primitive” evaluator for agent-orchestrated workflows).

Aliases (same behavior):
- `petamind_solve`
- `petamind_eval_patch`

Legacy aliases (backwards compatibility):
- `terra_mind_solve`
- `terra_mind_eval_patch`

## Naming note (“Poetiq-style”)

You may see “Poetiq-style” used in this repo *descriptively* to refer to iterative refinement loops
(generate → critique → refine → verify). This project is **not affiliated** with Poetiq.

## What it does (workflow)

1. **Reasoner plan** (optional): `single` or `megamind` (bold/minimal/safe → synthesizer)
2. **Generate patch candidates**: multiple temps for diversity
3. **Deterministic gates**: run your `test_command` (and optional `lint_command`)
4. **Vision loop** (mandatory): screenshots are always captured
   - If you provide `preview_command` + `preview_url`, it screenshots the running app (UI view).
- Otherwise it screenshots the git diff (diff view).
- **Default (easiest, evaluator tool):** `vision_provider=client` → Claude judges from returned screenshots (no extra cloud creds).
   - **Automated scoring (optional):** set `vision_provider=gemini` or `vision_provider=anthropic_vertex` so the MCP server
     scores screenshots itself (requires cloud credentials/quota).
5. **Winner selection**: prefer candidates that pass all gates, then higher vision score, then smaller diff

## Install

### Option A (recommended): install via `pipx` (PyPI)

```bash
pipx install petamind-mcp
petamind-setup
```

This installs the `petamind-mcp` command and downloads Playwright Chromium (required for screenshots).

### Option B: install from a git clone

From this repo root:

```bash
./scripts/setup.sh
```

Optional: run a local stdio smoke test (no Claude required):

```bash
.venv/bin/python scripts/smoke_mcp_stdio.py
```

If you want the MCP server to call cloud models itself (e.g. `titan_code_solve`, or automated vision scoring),
configure Vertex:

- Vertex setup: `docs/VERTEX_SETUP.md`
- Troubleshooting: `docs/TROUBLESHOOTING.md`

## Run (manual)

```bash
petamind-mcp
```

(`terra-mind-mcp` also works as a legacy alias.)

This runs over **stdio** (the MCP transport Claude Code expects).

## Configure Claude Code

Claude Code supports **project-scoped** and **user-scoped** MCP server configs:

- **User scope (recommended):** add servers to `~/.claude.json` so Petamind MCP is available in every project.
- **Project scope:** create a `.mcp.json` file in the *target repo* you open in Claude Code.

You can also add servers via CLI (optional): `claude mcp add ...`

## Permissions (important)

Claude Code requires you to approve MCP tools before use.

- Interactive sessions: Claude will ask the first time; approve `petamind_eval_patch`.
- Non-interactive `--print` runs: pass `--allowedTools "mcp__petamind-mcp__petamind_eval_patch"` (or the wildcard
  `mcp__petamind-mcp__*`).

### User scope: `~/.claude.json` (recommended)

If installed via `pipx`, the simplest config is:

```json
{
  "mcpServers": {
    "petamind-mcp": {
      "command": "petamind-mcp",
      "args": []
    }
  }
}
```

If installed from a git clone, add a server entry like this (replace `<PETAMIND_MCP_REPO>` with the path where you cloned this repo):

```json
{
  "mcpServers": {
    "petamind-mcp": {
      "command": "<PETAMIND_MCP_REPO>/.venv/bin/python",
      "args": ["-m", "petamind_mcp.mcp_server"],
      "env": {
        "TITAN_CONFIG_PATH": "<PETAMIND_MCP_REPO>/config/config.yaml"
      }
    }
  }
}
```

### Project scope: `.mcp.json`

Create a `.mcp.json` file in the repo root you open in Claude Code:

```json
{
  "mcpServers": {
    "petamind-mcp": {
      "command": "<PETAMIND_MCP_REPO>/.venv/bin/python",
      "args": ["-m", "petamind_mcp.mcp_server"],
      "env": {
        "TITAN_CONFIG_PATH": "<PETAMIND_MCP_REPO>/config/config.yaml"
      }
    }
  }
}
```

Notes:
- Avoid committing secrets into `.mcp.json` if you plan to share the repo.
- If you installed `petamind-mcp` globally, you can set `"command": "petamind-mcp"` and omit the python module args.
- If you want the MCP server to call Vertex/Gemini models (automated scoring or `titan_code_solve` defaults), set:
  - `GOOGLE_CLOUD_PROJECT`
  - `GOOGLE_CLOUD_REGION` (often `global` for MaaS / Gemini 3)
  - See `docs/VERTEX_SETUP.md`.

### Auth / “permanent key” notes

This matters only when you use cloud providers (Vertex/Gemini).

- Tokens *expire hourly* (normal), but `google-auth` refreshes them automatically.
- The “permanent” solution is using either:
  - **ADC** (recommended): run `gcloud auth application-default login` once, or
  - a **service account JSON** with `GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json`.

### Optional env vars

- `TITAN_MCP_OUT_DIR`: where to write run artifacts (defaults to `out/mcp-code-runs/` inside this repo).
- `TITAN_MCP_KEEP_WORKTREES=1`: keep candidate worktrees on disk for debugging.
- `TITAN_MCP_PORT_START`: base port used for preview servers in vision mode (default `3000`).
- `TITAN_MCP_PORT_STRIDE`: per-candidate port-range spacing when `candidate_concurrency > 1` (default `25`).

## Tool: `titan_code_solve`

Signature (high level):

`titan_code_solve(repo_path, goal, ...)`

Key args:

- `repo_path` (string): path inside the target git repo
- `goal` (string): what to change
- `context_files` (list[string]): relative paths to include as context (you pick these)
- `auto_context_mode`: `off | goal | queries` (default `off`)
- `auto_context_queries`: list of strings used when `auto_context_mode=queries`
- `auto_context_max_files`: max files added by auto context (default `12`)
- `context_max_chars`: total character budget for all included context (default `150000`)
- `context_max_file_chars`: per-file character cap for context (default `12000`)
- `planning_mode`: `off | single | megamind`
- `provider` / `model`: which text model to use (default: Vertex MaaS DeepSeek)
- `max_candidates`: number of candidates (default 4)
- `candidate_concurrency`: how many candidates run in parallel (default 1)
- `temperature_schedule`: per-candidate temps (default `[0.2, 0.5, 0.85, 1.0]`)
- `worktree_reuse_dirs`: directories to symlink from repo root into each candidate worktree (default `["node_modules"]`)
- `test_command`: deterministic gate
  - If omitted, the tool tries to infer a sane default based on repo files (Node/Python/Go/Rust).
  - If it can’t infer (or the runner isn’t on `PATH`), it falls back to `true` (no deterministic validation).
- `lint_command`: optional second deterministic gate
- `allow_nonpassing_winner`: if true, returns the best-effort candidate even if it fails some gates (default false)
- `apply_to_repo`: if true, attempts to apply the winner patch to the original repo (default false)

Winner behavior:

- By default, if **no candidate passes all enabled gates**, `winner` will be `null`.
- Set `allow_nonpassing_winner=true` to return the best-effort candidate anyway (still labeled with gate results).
- `apply_to_repo=true` only applies when the chosen winner **passes all enabled gates**; best-effort winners must be applied manually.

Vision args (always-on):

- `vision_mode`: `auto | on`
  - `auto`: vision always runs; uses UI screenshots if `preview_command`+`preview_url` are provided, otherwise uses diff screenshots
  - `on`: requires `preview_command` + `preview_url` (UI screenshots)
- `preview_command`: command to start the preview server (supports `{port}` placeholder)
- `preview_url`: URL to open (supports `{port}` placeholder), e.g. `http://127.0.0.1:{port}/`
- `vision_provider`:
  - For `titan_code_eval_patch` (recommended): default `client` (Claude judges from returned screenshots; easiest / no cloud creds)
  - For `titan_code_solve` (full internal loop): defaults to an automated provider (currently `anthropic_vertex`) so the server can score and rank candidates
- `vision_model`: used only for automated scoring providers (`gemini`, `anthropic_vertex`, etc); ignored in `client` mode
- `vision_score_threshold`: default `8.0`
- `max_vision_fix_rounds`: default `1`

Mixed-quality / creativity refinement (optional):

- `section_creativity_mode`: `off | auto | on`
  - `auto` runs only when UI screenshots are enabled (uses the same screenshots)
- `section_creativity_model`: defaults to `vision_model`
- `section_creativity_min_score`: default `0.7`
- `section_creativity_min_confidence`: default `0.6`
- `max_creativity_fix_rounds`: default `1`

Behavior:

- Runs a section creativity scorer on the latest **desktop** UI screenshot.
- Only runs once the main vision gate is already passing (so it focuses on “mixed quality”, not broken pages).
- If the page is “mixed quality” (some sections clearly strong and some clearly weak), runs a targeted refiner
  pass that tries to improve **only the weak sections**.

Output includes:

- `winner.patch` (as a unified git diff string) in the JSON response
- `test_command`, `test_command_inferred`, `test_command_inferred_reason` so you can see what gate ran
- `applied_to_repo`, `apply_error`, `apply_skipped_reason` when `apply_to_repo=true`
- `run_dir` on disk with per-candidate artifacts:
  - `llm_response.json`
  - `git_diff.patch`
  - `candidate_summary.json` (machine-readable outcome per candidate)
  - `auto_context.json` (if auto context enabled)
  - `preview_logs/` (stdout/stderr per vision iteration)
  - `screens/` (if vision enabled)
  - `vision_report*.json`
  - `section_creativity_report_*.json` (if section creativity enabled)
  - `section_creativity_summary_*.json` (strong/weak labels + avg/min)
  - `llm_creativity_fix_response_*.json` (if a targeted creativity pass ran)
  - `run_summary.json` (machine-readable summary of the whole run)

## Tool: `titan_code_eval_patch` (agent-orchestrated evaluation)

This is the “primitive” evaluation tool for workflows where Claude Code spawns many subagents to
propose patches, and uses the MCP only to validate/score those patches in isolated worktrees.

High-level signature:

`titan_code_eval_patch(repo_path, patches, ...)`

Inputs:

- `repo_path`: path inside the target git repo
- `goal`: optional short description of what the patch is trying to do (improves vision scoring)
- `patches`: list of `{ "path": "...", "patch": "...unified diff hunks..." }`
- `test_command` / `lint_command`: deterministic gates (inferred like `titan_code_solve` if omitted)
- Vision args: `vision_mode`, `vision_provider`, `vision_model`, and optionally `preview_command`+`preview_url`
- MCP output toggles: `include_images` (default true), `include_vision_instructions` (default true)

Outputs:

- Tool result content includes:
  - A **JSON summary** (TextContent)
  - If `vision_provider=client` and `include_vision_instructions=true`, a short **vision judging rubric** (TextContent)
  - If `include_images=true` (default), the captured screenshots as **ImageContent** so Claude can judge with built-in vision
    - Tip: in `claude --print` mode, this will include base64 image payloads in the raw output; set `include_images=false`
      if you prefer to keep output small and open screenshots manually from `screenshot_files`.

The JSON summary includes:
- `passes_all_gates`
- `passes_all_gates_includes_vision` (true only for automated scoring providers)
- `vision_scored`, `vision_kind`, `vision_ok`, `vision_score`
- `run_dir` / `candidate_dir` with `candidate_summary.json` + `run_summary.json`

## Example: UI repo with vision in the loop

```json
{
  "repo_path": "/path/to/your/webapp",
  "goal": "Fix the broken layout on mobile and make the hero CTA more obvious.",
  "context_files": ["app/page.tsx", "app/layout.tsx", "tailwind.config.ts"],
  "test_command": "npm test",
  "vision_mode": "on",
  "vision_provider": "client",
  "preview_command": "npm run dev -- --port {port}",
  "preview_url": "http://127.0.0.1:{port}/"
}
```
