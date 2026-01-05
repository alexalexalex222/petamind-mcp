#!/usr/bin/env python3
"""
Summarize multiple Titan Factory runs (temperature sweeps).

Example:
  .venv/bin/python scripts/temp_sweep_summary.py --contains jan3 --prefix temp-
  .venv/bin/python scripts/temp_sweep_summary.py --run-id temp-095-smoke-jan3 --run-id temp-105-smoke-jan3
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunMetrics:
    run_id: str
    db_path: str

    tasks_total: int
    tasks_completed: int
    tasks_no_passing: int

    candidates_total: int
    candidates_build_ok: int
    candidates_render_ok: int
    candidates_scored_ok: int

    winners_total: int
    winners_required_pass: int
    winners_shippable: int

    winners_avg_score: float | None
    winners_avg_creativity: float | None
    winners_avg_lh_accessibility: float | None
    winners_avg_lh_performance: float | None

    winner_models: dict[str, int]

    # temp stats (across all candidates, useful sanity check)
    cand_temp_min: float | None
    cand_temp_avg: float | None
    cand_temp_max: float | None


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


def _list_floats(conn: sqlite3.Connection, sql: str) -> list[float]:
    rows = conn.execute(sql).fetchall()
    out: list[float] = []
    for r in rows:
        v = r[0]
        if v is None:
            continue
        try:
            out.append(float(v))
        except Exception:
            continue
    return out


def _winner_model_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        WITH winners AS (
          SELECT selected_candidate_id AS cid
          FROM tasks
          WHERE status='completed' AND selected_candidate_id IS NOT NULL
        )
        SELECT c.generator_model AS model, COUNT(*) AS n
        FROM winners w
        JOIN candidates c ON c.id = w.cid
        GROUP BY c.generator_model
        ORDER BY n DESC, model ASC
        ;
        """
    ).fetchall()
    return {str(r["model"]): int(r["n"]) for r in rows if r["model"] is not None}


def get_metrics(db_path: Path, *, run_id: str) -> RunMetrics:
    conn = _open_db(db_path)
    try:
        tasks_total = _int(conn, "SELECT COUNT(*) FROM tasks;")
        tasks_completed = _int(conn, "SELECT COUNT(*) FROM tasks WHERE status='completed';")
        tasks_no_passing = _int(
            conn, "SELECT COUNT(*) FROM tasks WHERE status='no_passing_candidates';"
        )

        candidates_total = _int(conn, "SELECT COUNT(*) FROM candidates;")
        candidates_build_ok = _int(
            conn,
            """
            SELECT COUNT(*)
            FROM candidates
            WHERE status IN ('build_passed','rendered','scored','accepted','selected')
            ;
            """,
        )
        candidates_render_ok = _int(
            conn,
            """
            SELECT COUNT(*)
            FROM candidates
            WHERE status IN ('rendered','scored','accepted','selected')
            ;
            """,
        )
        candidates_scored_ok = _int(
            conn,
            """
            SELECT COUNT(*)
            FROM candidates
            WHERE status IN ('scored','accepted','selected')
            ;
            """,
        )

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

        # Shippable composite threshold >= 75 (same as scripts/run_status.py)
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

        winners_avg_score = _float(
            conn,
            """
            WITH winners AS (
              SELECT selected_candidate_id AS cid
              FROM tasks
              WHERE status='completed' AND selected_candidate_id IS NOT NULL
            )
            SELECT AVG(c.score)
            FROM winners w
            JOIN candidates c ON c.id = w.cid
            ;
            """,
        )
        winners_avg_creativity = _float(
            conn,
            """
            WITH winners AS (
              SELECT selected_candidate_id AS cid
              FROM tasks
              WHERE status='completed' AND selected_candidate_id IS NOT NULL
            )
            SELECT AVG(c.section_creativity_avg)
            FROM winners w
            JOIN candidates c ON c.id = w.cid
            ;
            """,
        )

        winners_avg_lh_accessibility = _float(
            conn,
            """
            WITH winners AS (
              SELECT selected_candidate_id AS cid
              FROM tasks
              WHERE status='completed' AND selected_candidate_id IS NOT NULL
            )
            SELECT AVG(COALESCE(json_extract(c.lighthouse_scores, '$.accessibility'), 0.0))
            FROM winners w
            JOIN candidates c ON c.id = w.cid
            ;
            """,
        )
        winners_avg_lh_performance = _float(
            conn,
            """
            WITH winners AS (
              SELECT selected_candidate_id AS cid
              FROM tasks
              WHERE status='completed' AND selected_candidate_id IS NOT NULL
            )
            SELECT AVG(COALESCE(json_extract(c.lighthouse_scores, '$.performance'), 0.0))
            FROM winners w
            JOIN candidates c ON c.id = w.cid
            ;
            """,
        )

        temps = _list_floats(conn, "SELECT generator_temperature FROM candidates;")
        cand_temp_min = min(temps) if temps else None
        cand_temp_max = max(temps) if temps else None
        cand_temp_avg = statistics.mean(temps) if temps else None

        return RunMetrics(
            run_id=run_id,
            db_path=str(db_path),
            tasks_total=tasks_total,
            tasks_completed=tasks_completed,
            tasks_no_passing=tasks_no_passing,
            candidates_total=candidates_total,
            candidates_build_ok=candidates_build_ok,
            candidates_render_ok=candidates_render_ok,
            candidates_scored_ok=candidates_scored_ok,
            winners_total=winners_total,
            winners_required_pass=winners_required_pass,
            winners_shippable=winners_shippable,
            winners_avg_score=winners_avg_score,
            winners_avg_creativity=winners_avg_creativity,
            winners_avg_lh_accessibility=winners_avg_lh_accessibility,
            winners_avg_lh_performance=winners_avg_lh_performance,
            winner_models=_winner_model_counts(conn),
            cand_temp_min=cand_temp_min,
            cand_temp_avg=cand_temp_avg,
            cand_temp_max=cand_temp_max,
        )
    finally:
        conn.close()


def _fmt_pct(num: int, den: int) -> str:
    if den <= 0:
        return "—"
    return f"{(100.0 * num / den):.1f}%"


def _fmt_f(v: float | None, *, digits: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}"


def _discover_run_ids(out_dir: Path, *, prefix: str, contains: str | None) -> list[str]:
    run_ids: list[str] = []
    for p in sorted(out_dir.iterdir()):
        if not p.is_dir():
            continue
        rid = p.name
        if prefix and not rid.startswith(prefix):
            continue
        if contains and contains not in rid:
            continue
        if (p / "manifest.db").exists():
            run_ids.append(rid)
    return run_ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", action="append", default=[], help="Run ID (repeatable)")
    ap.add_argument("--prefix", default="", help="Auto-discover runs starting with prefix")
    ap.add_argument("--contains", default=None, help="Auto-discover runs containing substring")
    ap.add_argument("--out-dir", default="out", help="Out directory (default: out)")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of table")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    run_ids = list(args.run_id)
    if args.prefix or args.contains:
        run_ids.extend(_discover_run_ids(out_dir, prefix=args.prefix, contains=args.contains))
        # de-dupe preserving order
        seen: set[str] = set()
        run_ids = [r for r in run_ids if not (r in seen or seen.add(r))]

    if not run_ids:
        raise SystemExit("No runs selected. Use --run-id or --prefix/--contains.")

    metrics: list[RunMetrics] = []
    for rid in run_ids:
        db_path = out_dir / rid / "manifest.db"
        if not db_path.exists():
            continue
        metrics.append(get_metrics(db_path, run_id=rid))

    if args.json:
        print(json.dumps([asdict(m) for m in metrics], indent=2))
        return 0

    # Markdown-ish table for quick scanning
    headers = [
        "run_id",
        "tasks",
        "completed",
        "winners",
        "req_pass",
        "shippable",
        "avg_score",
        "avg_creativity",
        "build_ok",
        "render_ok",
        "temp_min",
        "temp_avg",
        "temp_max",
        "winner_models",
    ]
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join(["---"] * len(headers)) + "|")

    for m in metrics:
        winner_models = ", ".join([f"{k}:{v}" for k, v in m.winner_models.items()]) or "—"
        print(
            "| "
            + " | ".join(
                [
                    m.run_id,
                    str(m.tasks_total),
                    str(m.tasks_completed),
                    str(m.winners_total),
                    _fmt_pct(m.winners_required_pass, m.winners_total),
                    _fmt_pct(m.winners_shippable, m.winners_total),
                    _fmt_f(m.winners_avg_score, digits=2),
                    _fmt_f(m.winners_avg_creativity, digits=2),
                    _fmt_pct(m.candidates_build_ok, m.candidates_total),
                    _fmt_pct(m.candidates_render_ok, m.candidates_total),
                    _fmt_f(m.cand_temp_min, digits=2),
                    _fmt_f(m.cand_temp_avg, digits=2),
                    _fmt_f(m.cand_temp_max, digits=2),
                    winner_models,
                ]
            )
            + " |"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

