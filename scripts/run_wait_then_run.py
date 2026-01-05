#!/usr/bin/env python3
"""
Wait for a run to complete, then kick off another run.

This is used to avoid render-port collisions across separate pipeline processes.

Example (wait for full 200, then start raw 200):
  .venv/bin/python scripts/run_wait_then_run.py \
    --wait-run-id full-refine-200c \
    --wait-target-tasks 200 \
    --then-cmd ".venv/bin/python -m titan_factory.cli run --max-tasks 200 --run-id raw-baseline-200 -c config/config-raw-baseline-200-dec31.yaml"
"""

from __future__ import annotations

import argparse
import shlex
import sqlite3
import subprocess
import time
from pathlib import Path


def _get_counts(db_path: Path) -> tuple[int, int]:
    conn = sqlite3.connect(str(db_path))
    try:
        total = conn.execute("SELECT COUNT(*) FROM tasks;").fetchone()[0]
        completed = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='completed';").fetchone()[0]
        return int(total), int(completed)
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="out", help="Out directory (default: out)")
    parser.add_argument("--wait-run-id", required=True, help="Run ID to wait for")
    parser.add_argument("--wait-target-tasks", type=int, required=True, help="Target completed tasks to consider done")
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=60,
        help="Poll interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--then-cmd",
        required=True,
        help="Command to run after wait completes (shell string)",
    )

    args = parser.parse_args()
    out_dir = Path(args.out_dir).resolve()
    db_path = out_dir / args.wait_run_id / "manifest.db"

    print(f"Waiting for run '{args.wait_run_id}' to complete {args.wait_target_tasks} tasks...")
    print(f"DB: {db_path}")

    while True:
        if not db_path.exists():
            print("DB not created yet; sleeping...")
            time.sleep(args.poll_seconds)
            continue

        total, completed = _get_counts(db_path)
        print(f"progress: completed={completed} total={total} target={args.wait_target_tasks}")

        if completed >= args.wait_target_tasks:
            break

        time.sleep(args.poll_seconds)

    print("Wait complete. Launching next command:")
    print(args.then_cmd)

    cmd_parts = shlex.split(args.then_cmd)
    proc = subprocess.run(cmd_parts, check=False)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
