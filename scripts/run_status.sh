#!/usr/bin/env bash

set -euo pipefail

RUN_DIR="${1:-}"
if [[ -z "${RUN_DIR}" ]]; then
  echo "Usage: scripts/run_status.sh out/<run_id>"
  exit 2
fi

DB="${RUN_DIR%/}/manifest.db"
if [[ ! -f "${DB}" ]]; then
  echo "manifest.db not found: ${DB}"
  exit 1
fi

echo "Run: ${RUN_DIR%/}"

if [[ -f "${RUN_DIR%/}/run.pid" ]]; then
  RUN_PID="$(cat "${RUN_DIR%/}/run.pid" 2>/dev/null || true)"
  if [[ -n "${RUN_PID}" ]] && kill -0 "${RUN_PID}" 2>/dev/null; then
    echo "Pipeline PID: ${RUN_PID} (running)"
  elif [[ -n "${RUN_PID}" ]]; then
    echo "Pipeline PID: ${RUN_PID} (not running)"
  fi
fi

if [[ -f "${RUN_DIR%/}/preview.pid" ]]; then
  PREVIEW_PID="$(cat "${RUN_DIR%/}/preview.pid" 2>/dev/null || true)"
  if [[ -n "${PREVIEW_PID}" ]] && kill -0 "${PREVIEW_PID}" 2>/dev/null; then
    echo "Preview PID: ${PREVIEW_PID} (running)"
  elif [[ -n "${PREVIEW_PID}" ]]; then
    echo "Preview PID: ${PREVIEW_PID} (not running)"
  fi
fi

echo
echo "Task status counts:"
sqlite3 "${DB}" "SELECT status, COUNT(*) FROM tasks GROUP BY status ORDER BY status;"

echo
echo "Winners:"
sqlite3 "${DB}" "SELECT COUNT(*) AS winners FROM tasks WHERE status='completed';"
sqlite3 "${DB}" "SELECT COUNT(*) AS winners_ge_9 FROM tasks t JOIN candidates c ON c.id=t.selected_candidate_id WHERE t.status='completed' AND c.score>=9.0;"

echo
echo "Most recent tasks:"
sqlite3 "${DB}" "SELECT id, page_type, status, COALESCE(substr(error,1,80),'') FROM tasks ORDER BY updated_at DESC LIMIT 12;"

