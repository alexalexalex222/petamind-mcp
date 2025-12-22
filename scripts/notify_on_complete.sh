#!/usr/bin/env bash

set -euo pipefail

RUN_DIR="${1:-}"
MIN_SCORE="${2:-9.0}"

if [[ -z "${RUN_DIR}" ]]; then
  echo "Usage: $0 <run_dir> [min_score]" >&2
  exit 2
fi

if [[ ! -d "${RUN_DIR}" ]]; then
  echo "Run directory not found: ${RUN_DIR}" >&2
  exit 2
fi

PID_FILE="${RUN_DIR}/run.pid"
if [[ ! -f "${PID_FILE}" ]]; then
  echo "Missing pid file: ${PID_FILE}" >&2
  exit 2
fi

PID="$(cat "${PID_FILE}" | tr -d '[:space:]')"
RUN_ID="$(basename "${RUN_DIR}")"

echo "[notify] watching ${RUN_ID} (pid ${PID})..."

while kill -0 "${PID}" >/dev/null 2>&1; do
  sleep 30
done

echo "[notify] run finished: ${RUN_ID}"

# Regenerate the gallery once at completion (best-effort).
if [[ -x "./.venv/bin/titan-factory" ]]; then
  ./.venv/bin/titan-factory gallery --run-id "${RUN_ID}" --min-score "${MIN_SCORE}" --config config/config-vertex-oss-train.yaml >/dev/null 2>&1 || true
  ./.venv/bin/titan-factory export --run-id "${RUN_ID}" --min-score "${MIN_SCORE}" --config config/config-vertex-oss-train.yaml >/dev/null 2>&1 || true
fi

TITLE="TITAN Factory"
MESSAGE="Run ${RUN_ID} finished. Preview: http://localhost:3001/ (min score ${MIN_SCORE})"

if command -v osascript >/dev/null 2>&1; then
  osascript -e "display notification \"${MESSAGE}\" with title \"${TITLE}\"" >/dev/null 2>&1 || true
fi

echo "[notify] done"
