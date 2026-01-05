#!/usr/bin/env python3
"""
Quick status helper for an in-progress Titan Factory run.

Usage:
  .venv/bin/python scripts/run_status.py --run-id full-refine-25
  .venv/bin/python scripts/run_status.py --db out/full-refine-25/manifest.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunStatus:
    run_id: str
    db_path: str
    tasks_total: int
    tasks_completed: int
    tasks_no_passing: int
    tasks_other: int
    winners_total: int
    winners_required_pass: int
    winners_shippable: int
    required_pass_rate_pct: float | None
    shippable_rate_pct: float | None


def _open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _int(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    if row is None:
        return 0
    v = row[0]
    return int(v or 0)


def _float(conn: sqlite3.Connection, sql: str) -> float | None:
    row = conn.execute(sql).fetchone()
    if row is None:
        return None
    v = row[0]
    return None if v is None else float(v)


def get_status(db_path: Path, *, run_id: str) -> RunStatus:
    conn = _open_db(db_path)
    try:
        tasks_total = _int(conn, "SELECT COUNT(*) FROM tasks;")
        tasks_completed = _int(conn, "SELECT COUNT(*) FROM tasks WHERE status='completed';")
        tasks_no_passing = _int(
            conn, "SELECT COUNT(*) FROM tasks WHERE status='no_passing_candidates';"
        )
        tasks_other = tasks_total - tasks_completed - tasks_no_passing

        # Winners are completed tasks with a selected_candidate_id
        winners_total = _int(
            conn,
            "SELECT COUNT(*) FROM tasks WHERE status='completed' AND selected_candidate_id IS NOT NULL;",
        )

        # Required pass = rendered_ok + axe_critical==0 + lh_accessibility>=0.80
        winners_required_pass = _int(
            conn,
            """
            WITH winners AS (
              SELECT selected_candidate_id AS cid
              FROM tasks
              WHERE status='completed' AND selected_candidate_id IS NOT NULL
            )
            SELECT COUNT(*)
            FROM winners w
            JOIN candidates c ON c.id = w.cid
            WHERE
              (SELECT COUNT(1) FROM json_each(COALESCE(c.screenshot_paths, '{}'))) > 0
              AND c.status IN ('rendered','scored','accepted','selected')
              AND (
                SELECT COUNT(1)
                FROM json_each(COALESCE(c.axe_violations, '[]')) v
                WHERE lower(json_extract(v.value, '$.impact')) = 'critical'
              ) = 0
              AND COALESCE(json_extract(c.lighthouse_scores, '$.accessibility'), 0.0) >= 0.80
            ;
            """,
        )

        # Shippable composite as defined in out/nightshift-dec28/shippable_composite.sql
        winners_shippable = _int(
            conn,
            """
            WITH winners AS (
              SELECT selected_candidate_id AS cid
              FROM tasks
              WHERE status='completed' AND selected_candidate_id IS NOT NULL
            ),
            base AS (
              SELECT
                c.*,
                COALESCE(json_extract(c.lighthouse_scores, '$.accessibility'), 0.0) AS lh_accessibility,
                COALESCE(json_extract(c.lighthouse_scores, '$.performance'), 0.0) AS lh_performance,
                (
                  SELECT COUNT(1)
                  FROM json_each(COALESCE(c.axe_violations, '[]')) v
                  WHERE lower(json_extract(v.value, '$.impact')) = 'critical'
                ) AS axe_critical,
                (
                  SELECT COUNT(1)
                  FROM json_each(COALESCE(c.axe_violations, '[]')) v
                  WHERE lower(json_extract(v.value, '$.impact')) = 'serious'
                ) AS axe_serious,
                (
                  SELECT COUNT(1)
                  FROM json_each(COALESCE(c.screenshot_paths, '{}'))
                ) AS screenshot_count
              FROM winners w
              JOIN candidates c ON c.id = w.cid
            ),
            scored AS (
              SELECT
                *,
                (
                  25 * CASE WHEN screenshot_count > 0
                              AND status IN ('rendered','scored','accepted','selected')
                              AND axe_critical = 0
                              AND lh_accessibility >= 0.80
                            THEN 1 ELSE 0 END
                  + 25 * CASE WHEN lh_accessibility >= 0.90 THEN 1 ELSE 0 END
                  + 25 * CASE WHEN lh_performance >= 0.70 THEN 1 ELSE 0 END
                  + 25 * CASE WHEN axe_serious = 0 THEN 1 ELSE 0 END
                ) AS shippable_score,
                CASE
                  WHEN (
                    25 * CASE WHEN screenshot_count > 0
                                AND status IN ('rendered','scored','accepted','selected')
                                AND axe_critical = 0
                                AND lh_accessibility >= 0.80
                              THEN 1 ELSE 0 END
                    + 25 * CASE WHEN lh_accessibility >= 0.90 THEN 1 ELSE 0 END
                    + 25 * CASE WHEN lh_performance >= 0.70 THEN 1 ELSE 0 END
                    + 25 * CASE WHEN axe_serious = 0 THEN 1 ELSE 0 END
                  ) >= 75
                  AND screenshot_count > 0
                  AND status IN ('rendered','scored','accepted','selected')
                  AND axe_critical = 0
                  AND lh_accessibility >= 0.80
                  THEN 1
                  ELSE 0
                END AS shippable
              FROM base
            )
            SELECT COALESCE(SUM(shippable), 0) FROM scored;
            """,
        )

        required_pass_rate_pct: float | None
        shippable_rate_pct: float | None
        if winners_total > 0:
            required_pass_rate_pct = round(100.0 * winners_required_pass / winners_total, 1)
            shippable_rate_pct = round(100.0 * winners_shippable / winners_total, 1)
        else:
            required_pass_rate_pct = None
            shippable_rate_pct = None

        return RunStatus(
            run_id=run_id,
            db_path=str(db_path),
            tasks_total=tasks_total,
            tasks_completed=tasks_completed,
            tasks_no_passing=tasks_no_passing,
            tasks_other=tasks_other,
            winners_total=winners_total,
            winners_required_pass=winners_required_pass,
            winners_shippable=winners_shippable,
            required_pass_rate_pct=required_pass_rate_pct,
            shippable_rate_pct=shippable_rate_pct,
        )
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", help="Run ID (expects out/<run-id>/manifest.db)")
    ap.add_argument("--db", help="Path to manifest.db (overrides --run-id)")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = ap.parse_args()

    if not args.db and not args.run_id:
        raise SystemExit("Provide --run-id or --db")

    if args.db:
        db_path = Path(args.db)
        run_id = args.run_id or db_path.parent.name
    else:
        run_id = str(args.run_id)
        db_path = Path("out") / run_id / "manifest.db"

    status = get_status(db_path, run_id=run_id)

    if args.json:
        print(json.dumps(asdict(status), indent=2))
    else:
        print(f"run_id: {status.run_id}")
        print(f"db: {status.db_path}")
        print(f"tasks_total: {status.tasks_total}")
        print(f"tasks_completed: {status.tasks_completed}")
        print(f"tasks_no_passing: {status.tasks_no_passing}")
        print(f"tasks_other: {status.tasks_other}")
        print(f"winners_total: {status.winners_total}")
        print(f"winners_required_pass: {status.winners_required_pass}")
        print(f"winners_shippable: {status.winners_shippable}")
        print(f"required_pass_rate_pct: {status.required_pass_rate_pct}")
        print(f"shippable_rate_pct: {status.shippable_rate_pct}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
