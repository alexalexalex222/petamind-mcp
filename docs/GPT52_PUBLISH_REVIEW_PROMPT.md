# GPT‑5.2 Pro — Petamind MCP “Pre‑Publish Gap‑Filler” Review Prompt

You are **GPT‑5.2 Pro** acting as a **release engineer + OSS maintainer + security reviewer**.
Your job is to find what we missed and help us ship an OSS repo that is **easy for Claude Code users** to adopt.

This is NOT a theoretical review: **use tool calls**, run the commands, read the files, and use **web search** to verify
anything that might have changed (Claude Code MCP config, MCP spec, Vertex endpoints, etc.).

## 0) Repo context

Repo name (current): **Petamind MCP**

Goal: ship a Claude Code MCP server that runs a **multi‑candidate agentic coding loop**:
reasoner plan → generate patches → deterministic gates → **mandatory vision loop** → pick best winner.

Key idea: Claude Code can orchestrate many subagents; the MCP should be usable in two modes:

1) **Evaluator / “primitive” mode (recommended, low friction)**
   - Claude/subagents generate patch bundles
   - MCP applies patch in an isolated git worktree, runs gates, captures screenshots, and returns:
     - JSON summary + artifacts path
     - screenshots as MCP ImageContent (so Claude can judge with built‑in vision)
   - Default vision provider is **client** (no cloud creds required).

2) **Full internal loop**
   - MCP itself calls text models to plan/generate/fix (Vertex MaaS defaults)
   - Optional: automated vision scoring via Gemini/Vertex or Claude-on-Vertex

Repo path on disk:
`<REPO_ROOT>` (example: the folder where you cloned the repo)

## 1) What I need from you (deliverables)

Produce a release-readiness report with:

1. **Verdict**: “ready to open source” vs “NO‑GO”, with concrete reasons
2. **Top risks** (ranked): severity + exact fix (file + code change)
3. **User friction audit**: list the steps a new Claude Code user must do; propose ways to remove steps
4. **Docs correctness audit**: especially Claude Code MCP config locations + CLI commands
5. **Naming audit (web research required)**:
   - Check whether “Petamind” and “Poetiq” are already established brands/projects/trademarks
   - Recommend safe naming options (we can still say “Poetiq‑style” descriptively, but avoid confusion)
6. **Security audit**:
   - No keys/tokens printed by tools
   - No secrets in repo
   - No absolute user-home paths (macOS/Linux/Windows) in tracked content
7. **CI / release hygiene**: recommend a minimal CI workflow for PRs (tests + smoke)
8. **Patch set**:
   - Provide a minimal set of patches (git diff or apply_patch style) for the top issues you found

## 2) Mandatory tool usage (run these)

### A) Local verification (must run)

```bash
cd <REPO_ROOT>
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile src/petamind_mcp/mcp_server.py src/titan_factory/mcp_code_server.py
.venv/bin/python scripts/smoke_mcp_stdio.py
```

### B) Hygiene scans (must run)

```bash
cd <REPO_ROOT>
rg -n '/[U]sers/|/[h]ome/|C:[\\]' -S .
rg -n "BEGIN PRIVATE KEY|AIza|Authorization: Bearer|GOOGLE_APPLICATION_CREDENTIALS|private_key_id|client_email" -S .
rg -n "sk-[A-Za-z0-9]{20,}" -S .
```

### C) Stdio safety check (must reason about)

MCP stdio breaks if anything writes to stdout besides protocol messages.
Verify the server forces logs to **stderr** when used over stdio.

## 3) Files you must read (minimum set)

### MCP server and tools

- `src/petamind_mcp/mcp_server.py`
- `src/titan_factory/mcp_code_server.py`
- `src/titan_factory/utils.py`

### Docs

- `README.md`
- `docs/MCP_PETAMIND_MCP.md`
- `docs/VERTEX_SETUP.md`
- `docs/TROUBLESHOOTING.md`
- `THIRD_PARTY_NOTICES.md`
- `SECURITY.md`
- `CONTRIBUTING.md`

### Packaging

- `pyproject.toml`

### Smoke + setup scripts

- `scripts/setup.sh`
- `scripts/smoke_mcp_stdio.py`
- `scripts/smoke_claude_cli.sh`
- `scripts/preflight_check.py` (ensure it never prints secrets/tokens)

## 4) Web research requirements (must do)

Use web search to verify:

1) Claude Code MCP setup:
   - Where config lives (`~/.claude.json` vs project `.mcp.json`)
   - Current `claude mcp add ...` / `claude mcp add-json ...` syntax and scope options

2) Model Context Protocol tools/transport:
   - Any constraints on tool naming or content blocks (TextContent, ImageContent)

3) Naming/trademark risk:
   - Whether “Petamind” or “Poetiq” are already used by notable products/companies/projects
   - If yes, propose safer alternatives and how to phrase “Poetiq‑style” as a descriptive term without implying affiliation

Include links for your most important claims.

## 5) What “easy for Claude Code users” means (acceptance criteria)

We want a new user to be able to:

1) `git clone …`
2) `./scripts/setup.sh`
3) Add MCP via either:
   - a copy/paste `.mcp.json`, OR
   - a single `claude mcp add-json --scope user ...` command
4) In Claude Code, ask: “Use Petamind MCP to evaluate this patch.”

No cloud account should be required for the default evaluator workflow.

## 6) Output format

Return:
- A concise report (sections: Verdict, Top Risks, Fixes, Docs, Naming, Security, CI)
- Then patches (only for the top issues)
