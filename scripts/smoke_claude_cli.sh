#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v claude >/dev/null 2>&1; then
  echo "ERROR: claude CLI not found. Install Claude Code first." >&2
  exit 1
fi

TMP_REPO="$(mktemp -d -t petamind-mcp-claude-smoke.XXXXXX)"
echo "hello" > "$TMP_REPO/hello.txt"
(cd "$TMP_REPO" && git init -q && git add hello.txt && git -c user.email=test@example.com -c user.name=test commit -qm init)

TMP_CFG="$(mktemp -t petamind-mcp.XXXXXX.json)"
cat > "$TMP_CFG" <<JSON
{
  "mcpServers": {
    "petamind-mcp": {
      "command": "$REPO_ROOT/.venv/bin/python",
      "args": ["-m", "petamind_mcp.mcp_server"],
      "env": {
        "TITAN_CONFIG_PATH": "$REPO_ROOT/config/config.yaml"
      }
    }
  }
}
JSON

PATCH='@@ -1,1 +1,1 @@\n-hello\n+hello world\n'

echo "Running Claude Code MCP smoke…"
echo "  repo: $TMP_REPO"
echo "  cfg:  $TMP_CFG"
echo

set +e
PROMPT="$(cat <<EOF
Use the MCP tool petamind_eval_patch NOW.
Do not ask questions. Do not request clarification.

Args (exact JSON):
{
  "repo_path": "$TMP_REPO",
  "patches": [
    { "path": "hello.txt", "patch": "$PATCH" }
  ],
  "test_command": "true",
  "vision_mode": "auto",
  "vision_provider": "client"
}

After the tool runs, reply with exactly: OK
EOF
)"

OUTPUT="$(claude --print --output-format stream-json --mcp-config "$TMP_CFG" --mcp-debug --allowedTools "mcp__petamind-mcp__petamind_eval_patch" -- "$PROMPT" 2>&1)"
EC=$?
set -e

echo "$OUTPUT"
echo

if [ "$EC" -ne 0 ]; then
  echo "NOTE: If you see a model 404, set ANTHROPIC_MODEL to a model you have access to, e.g.:" >&2
  echo "  ANTHROPIC_MODEL=claude-opus-4-5-20251101 $REPO_ROOT/scripts/smoke_claude_cli.sh" >&2
  exit "$EC"
fi

if echo "$OUTPUT" | grep -q "API Error:"; then
  echo "ERROR: Claude CLI returned an API Error in the output (even though exit code was 0)." >&2
  echo "Try setting a model you have access to, e.g.:" >&2
  echo "  ANTHROPIC_MODEL=claude-opus-4-5-20251101 $REPO_ROOT/scripts/smoke_claude_cli.sh" >&2
  exit 2
fi

if ! echo "$OUTPUT" | grep -q '"tool_use"'; then
  echo "ERROR: Claude did not appear to call the MCP tool (no tool_use blocks in output)." >&2
  echo "Make sure you approved tools (or pass --allowedTools) and that MCP server starts cleanly." >&2
  exit 3
fi

if ! echo "$OUTPUT" | grep -q '"text": "OK"'; then
  echo "ERROR: Claude did not return the expected OK sentinel." >&2
  exit 4
fi

echo "OK: Claude Code successfully used Petamind MCP"
