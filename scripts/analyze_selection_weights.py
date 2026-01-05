#!/usr/bin/env python3
"""
Analyze winner selection differences between:
- control weights (judge=1.0, creativity=0.0)
- weighted (judge=0.6, creativity=0.4)

This uses the SAME candidate pool from a single run (manifest.db),
so differences isolate selection logic rather than generator randomness.

Usage:
  .venv/bin/python scripts/analyze_selection_weights.py out/<run_id>/manifest.db
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Cand:
    id: str
    task_id: str
    variant_index: int
    deterministic_passed: bool
    score: float
    creativity_avg: float
    file_count: int
    total_size: int
    fix_rounds: int
    refine_passes: int


def _parse_bool_int(v) -> bool:
    try:
        if v is None:
            return False
        return bool(int(v))
    except Exception:
        return False


def _parse_float(v, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _parse_int(v, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def _total_size_from_files(files_json: str) -> tuple[int, int]:
    try:
        files = json.loads(files_json or "[]")
    except Exception:
        return 0, 0
    if not isinstance(files, list):
        return 0, 0
    total = 0
    for f in files:
        if isinstance(f, dict):
            total += len(str(f.get("content") or ""))
    return len(files), total


def select_winner(cands: list[Cand], judge_weight: float, creativity_weight: float) -> Cand | None:
    if not cands:
        return None

    def key(c: Cand):
        weighted = c.score * judge_weight + c.creativity_avg * creativity_weight
        # Mirrors orchestrator.py sort key for non-skip_judge mode
        return (
            0 if c.deterministic_passed else 1,
            -weighted,
            c.variant_index,
            c.file_count,
            c.total_size,
            c.fix_rounds,
            c.refine_passes,
        )

    return sorted(cands, key=key)[0]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip())
        return 2

    db_path = Path(sys.argv[1])
    if not db_path.exists():
        print(f"manifest.db not found: {db_path}")
        return 2

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    tasks = conn.execute(
        """
        SELECT id, niche_id, status, selected_candidate_id
        FROM tasks
        WHERE status = 'completed'
        ORDER BY created_at ASC
        """
    ).fetchall()

    if not tasks:
        print("No completed tasks found.")
        return 0

    diffs = []
    close_cases = 0

    for t in tasks:
        task_id = t["id"]
        selected = t["selected_candidate_id"]

        rows = conn.execute(
            """
            SELECT
              id,
              task_id,
              variant_index,
              deterministic_passed,
              score,
              section_creativity_avg,
              files,
              fix_rounds,
              refine_passes,
              status
            FROM candidates
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchall()

        cands: list[Cand] = []
        for r in rows:
            # Winners are persisted as status=selected after selection, but were eligible
            # at selection-time as scored. Include both for post-hoc analysis.
            if (r["status"] or "") not in ("scored", "selected"):
                continue
            score = _parse_float(r["score"], default=0.0)
            creativity_avg = _parse_float(r["section_creativity_avg"], default=0.0)
            file_count, total_size = _total_size_from_files(r["files"])
            cands.append(
                Cand(
                    id=str(r["id"]),
                    task_id=str(r["task_id"]),
                    variant_index=_parse_int(r["variant_index"], 0),
                    deterministic_passed=_parse_bool_int(r["deterministic_passed"]),
                    score=score,
                    creativity_avg=creativity_avg,
                    file_count=file_count,
                    total_size=total_size,
                    fix_rounds=_parse_int(r["fix_rounds"], 0),
                    refine_passes=_parse_int(r["refine_passes"], 0),
                )
            )

        if not cands:
            continue

        # Identify "close score" tasks: top-2 by judge score within 0.6
        by_score = sorted(cands, key=lambda c: c.score, reverse=True)
        if len(by_score) >= 2 and abs(by_score[0].score - by_score[1].score) <= 0.6:
            close_cases += 1

        w_control = select_winner(cands, judge_weight=1.0, creativity_weight=0.0)
        w_weighted = select_winner(cands, judge_weight=0.6, creativity_weight=0.4)

        if not w_control or not w_weighted:
            continue

        if w_control.id != w_weighted.id:
            # Gather a short comparison
            diffs.append(
                {
                    "task_id": task_id,
                    "niche_id": t["niche_id"],
                    "selected_by_run": selected,
                    "control_winner": {
                        "id": w_control.id,
                        "score": w_control.score,
                        "creativity": w_control.creativity_avg,
                    },
                    "weighted_winner": {
                        "id": w_weighted.id,
                        "score": w_weighted.score,
                        "creativity": w_weighted.creativity_avg,
                    },
                }
            )

    print(f"Completed tasks: {len(tasks)}")
    print(f"Close-score tasks (top2 Δ<=0.6): {close_cases}")
    print(f"Winner changed by adding creativity weight: {len(diffs)}")

    if diffs:
        print("\nExamples (first 8):")
        for d in diffs[:8]:
            cw = d["control_winner"]
            ww = d["weighted_winner"]
            print(
                f"- {d['task_id']} ({d['niche_id']}): "
                f"control={cw['id']} score={cw['score']:.2f} cr={cw['creativity']:.2f} | "
                f"weighted={ww['id']} score={ww['score']:.2f} cr={ww['creativity']:.2f} | "
                f"selected_by_run={d['selected_by_run']}"
            )

    # Also write a machine-readable artifact next to the DB for later inspection.
    out_path = db_path.parent / "selection_weight_diff.json"
    try:
        out_path.write_text(json.dumps(diffs, indent=2), encoding="utf-8")
        print(f"\nWrote diff JSON: {out_path}")
    except Exception as e:
        print(f"\nFailed to write diff JSON: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
