# Troubleshooting — Petamind MCP

This doc is intentionally blunt and copy/paste friendly.

## 0) First: verify you installed Playwright *browsers*

Installing the Python package is not enough — you also need browser binaries.

```bash
source .venv/bin/activate
playwright install chromium
```

If you skip this, vision mode will fail with “Executable doesn't exist … chromium …”.

---

## 1) MCP server doesn’t show up in Claude Code

### Check project config file location

Claude Code supports:

- Project scope: `.mcp.json` in the repo root you open in Claude Code
- User scope: `~/.claude.json`

### Quick sanity test (outside Claude Code)

This starts the server over stdio and lists tools:

```bash
source .venv/bin/activate
python - <<'PY'
import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

async def main():
    params = StdioServerParameters(
        command='.venv/bin/python',
        args=['-m', 'petamind_mcp.mcp_server'],
        cwd='.',
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print([t.name for t in tools.tools])

anyio.run(main)
PY
```

Expected output:
`['titan_code_solve', 'titan_code_eval_patch', 'petamind_solve', 'petamind_eval_patch', 'terra_mind_solve', 'terra_mind_eval_patch']`

---

## 2) Vertex returns 401 / 403

This only applies if you are using cloud providers (Vertex / Gemini / Anthropic-on-Vertex).

If you are using `vision_provider=client` (default), you do **not** need Vertex credentials.

When it does apply, it’s almost always **credentials** or **permissions**.

### If you are using ADC

```bash
gcloud auth application-default login
gcloud config set project YOUR_GCP_PROJECT
```

### If you are using a service account JSON

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/absolute/path/to/key.json"
```

Then retry.

If you still get 403:
- Your identity likely lacks Vertex permissions in the project.

See `docs/VERTEX_SETUP.md`.

---

## 3) Vertex returns 404 (model not found / wrong location)

Common causes:

- `GOOGLE_CLOUD_REGION` is wrong (try `global`)
- The model isn’t enabled/entitled for your project
- Your org policy restricts publisher models

Try:

```bash
export GOOGLE_CLOUD_REGION="global"
```

If your org requires a region, confirm the model supports it.

---

## 4) Vision fails but tests pass

This is expected sometimes: the deterministic gates only prove the repo builds/tests,
while vision is meant to catch “looks wrong / broken UI / bad UX”.

Common causes:

- Preview server didn’t start (wrong `preview_command`)
- Wrong `preview_url`
- Port collision when running multiple candidates concurrently
- Playwright can’t launch Chromium (missing install)

### Port collision fix

When using vision with `candidate_concurrency > 1`, configure:

- `TITAN_MCP_PORT_START` (default 3000)
- `TITAN_MCP_PORT_STRIDE` (default 25)

Example:

```bash
export TITAN_MCP_PORT_START=3200
export TITAN_MCP_PORT_STRIDE=50
```

---

## 5) “Everything is slow”

The default workflow is intentionally heavyweight:

- multiple candidates
- multiple gates
- Playwright screenshots
- vision scoring

Speed levers:

- Set `max_candidates=1` while iterating
- Keep `candidate_concurrency=1` until stable
- Provide a fast `test_command` (or split tests into quick vs full)

---

## 6) “It wrote artifacts with diffs/screenshots”

That’s expected: runs are designed to be debuggable.

By default, artifacts go to:

`out/mcp-code-runs/`

Change it with:

```bash
export TITAN_MCP_OUT_DIR="$HOME/petamind-mcp-runs"
```

If you’re reporting bugs publicly, **do not paste run artifacts** without reviewing them:
they may contain repository diffs and screenshots.
