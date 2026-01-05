#!/usr/bin/env python3
"""
Generate a static HTML gallery to review output diversity across runs.

Designed for local browsing via:
  python -m titan_factory.cli serve-out --port 8003

Example:
  .venv/bin/python scripts/generate_diversity_gallery.py \
    --full-run full-refine-25 \
    --raw-run raw-baseline-25 \
    --output-subdir compare-dec31
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WinnerRow:
    run_id: str
    task_id: str
    page_type: str
    niche_id: str
    candidate_id: str
    generator_model: str
    candidate_status: str
    deterministic_passed: bool | None
    deterministic_failures: list[str]
    axe_critical: int
    axe_serious: int
    lh_accessibility: float
    lh_performance: float
    lh_best_practices: float
    judge_score: float | None
    creativity_avg: float | None


def _count_axe_impacts(axe_violations: Any) -> tuple[int, int]:
    critical = 0
    serious = 0
    if not isinstance(axe_violations, list):
        return critical, serious
    for v in axe_violations:
        impact = (v.get("impact") or "").lower() if isinstance(v, dict) else ""
        if impact == "critical":
            critical += 1
        elif impact == "serious":
            serious += 1
    return critical, serious


def _parse_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _load_winners(out_dir: Path, run_id: str) -> dict[str, WinnerRow]:
    db_path = out_dir / run_id / "manifest.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Missing manifest.db for run '{run_id}': {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
              t.id AS task_id,
              t.page_type AS page_type,
              t.niche_id AS niche_id,
              t.selected_candidate_id AS candidate_id,
              c.generator_model AS generator_model,
              c.status AS candidate_status,
              c.deterministic_passed AS deterministic_passed,
              c.deterministic_failures AS deterministic_failures,
              c.axe_violations AS axe_violations,
              c.lighthouse_scores AS lighthouse_scores,
              c.score AS judge_score,
              c.section_creativity_avg AS creativity_avg
            FROM tasks t
            JOIN candidates c ON c.id = t.selected_candidate_id
            WHERE t.status = 'completed'
              AND t.selected_candidate_id IS NOT NULL
            ORDER BY t.created_at ASC, t.id ASC
            """
        ).fetchall()
    finally:
        conn.close()

    winners: dict[str, WinnerRow] = {}
    for r in rows:
        failures = _parse_json(r["deterministic_failures"], [])
        axe = _parse_json(r["axe_violations"], [])
        lh = _parse_json(r["lighthouse_scores"], {})
        axe_critical, axe_serious = _count_axe_impacts(axe)
        winners[r["task_id"]] = WinnerRow(
            run_id=run_id,
            task_id=r["task_id"],
            page_type=r["page_type"] or "",
            niche_id=r["niche_id"] or "",
            candidate_id=r["candidate_id"] or "",
            generator_model=r["generator_model"] or "",
            candidate_status=r["candidate_status"] or "",
            deterministic_passed=(
                None if r["deterministic_passed"] is None else bool(r["deterministic_passed"])
            ),
            deterministic_failures=failures if isinstance(failures, list) else [],
            axe_critical=axe_critical,
            axe_serious=axe_serious,
            lh_accessibility=float(lh.get("accessibility") or 0.0),
            lh_performance=float(lh.get("performance") or 0.0),
            lh_best_practices=float(lh.get("best_practices") or 0.0),
            judge_score=None if r["judge_score"] is None else float(r["judge_score"]),
            creativity_avg=None if r["creativity_avg"] is None else float(r["creativity_avg"]),
        )
    return winners


def _img_rel_path(run_id: str, task_id: str, candidate_id: str, viewport: str) -> str:
    # Served from out/ root via `titan_factory.cli serve-out`.
    return f"{run_id}/renders/{task_id}/{candidate_id}/{viewport}.png"


def _fmt_pct(x01: float) -> str:
    return f"{round(x01 * 100.0, 1)}%"


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _render_card(w: WinnerRow, mode: str) -> str:
    # mode: "full" | "raw"
    desktop = _img_rel_path(w.run_id, w.task_id, w.candidate_id, "desktop")
    meta = [
        f"<div><span class='k'>task</span> <code>{_escape(w.task_id)}</code></div>",
        f"<div><span class='k'>page_type</span> <code>{_escape(w.page_type)}</code></div>",
        f"<div><span class='k'>model</span> <code>{_escape(w.generator_model)}</code></div>",
        f"<div><span class='k'>deterministic</span> <code>{'PASS' if w.deterministic_passed else 'FAIL'}</code></div>",
        f"<div><span class='k'>axe</span> <code>crit={w.axe_critical} serious={w.axe_serious}</code></div>",
        f"<div><span class='k'>LH</span> <code>a11y={_fmt_pct(w.lh_accessibility)} perf={_fmt_pct(w.lh_performance)}</code></div>",
    ]
    if w.deterministic_failures:
        meta.append(
            "<div class='failures'><span class='k'>failures</span> "
            + "<code>"
            + _escape(", ".join(w.deterministic_failures))
            + "</code></div>"
        )

    if mode == "full":
        if w.judge_score is not None:
            meta.append(f"<div><span class='k'>judge</span> <code>{w.judge_score:.2f}</code></div>")
        if w.creativity_avg is not None:
            meta.append(
                f"<div><span class='k'>creativity</span> <code>{w.creativity_avg:.2f}</code></div>"
            )

    return f"""
      <article class="card">
        <a class="imgwrap" href="{desktop}" target="_blank" rel="noreferrer">
          <img loading="lazy" src="{desktop}" alt="{_escape(w.page_type)} desktop screenshot" />
        </a>
        <div class="meta">
          {''.join(meta)}
        </div>
      </article>
    """


def _render_compare_row(task_id: str, full: WinnerRow | None, raw: WinnerRow | None) -> str:
    def column(label: str, w: WinnerRow | None) -> str:
        if w is None:
            return f"<div class='col'><div class='colhdr'>{_escape(label)}</div><div class='missing'>missing</div></div>"
        desktop = _img_rel_path(w.run_id, w.task_id, w.candidate_id, "desktop")
        failures = ", ".join(w.deterministic_failures) if w.deterministic_failures else ""
        return f"""
          <div class="col">
            <div class="colhdr">{_escape(label)}</div>
            <a class="imgwrap" href="{desktop}" target="_blank" rel="noreferrer">
              <img loading="lazy" src="{desktop}" alt="{_escape(label)} desktop screenshot" />
            </a>
            <div class="meta small">
              <div><span class='k'>model</span> <code>{_escape(w.generator_model)}</code></div>
              <div><span class='k'>deterministic</span> <code>{'PASS' if w.deterministic_passed else 'FAIL'}</code></div>
              <div><span class='k'>axe</span> <code>crit={w.axe_critical} serious={w.axe_serious}</code></div>
              <div><span class='k'>LH</span> <code>a11y={_fmt_pct(w.lh_accessibility)} perf={_fmt_pct(w.lh_performance)}</code></div>
              {f"<div class='failures'><span class='k'>failures</span> <code>{_escape(failures)}</code></div>" if failures else ""}
            </div>
          </div>
        """

    page_type = full.page_type if full else (raw.page_type if raw else "")
    return f"""
      <section class="compareRow">
        <div class="compareHdr">
          <div class="title">
            <code>{_escape(task_id)}</code>
            <span class="pill">{_escape(page_type)}</span>
          </div>
        </div>
        <div class="compareGrid">
          {column("Full pipeline (refinement loop)", full)}
          {column("Raw baseline", raw)}
        </div>
      </section>
    """


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-run", required=True, help="Run ID for full pipeline (e.g. full-refine-25)")
    parser.add_argument("--raw-run", required=True, help="Run ID for raw baseline (e.g. raw-baseline-25)")
    parser.add_argument(
        "--out-dir",
        default="out",
        help="Path to out/ directory (default: out)",
    )
    parser.add_argument(
        "--output-subdir",
        default="compare",
        help="Subdir under out/ to write HTML into (default: compare)",
    )

    args = parser.parse_args()
    out_dir = Path(args.out_dir).resolve()

    full = _load_winners(out_dir, args.full_run)
    raw = _load_winners(out_dir, args.raw_run)

    full_tasks = list(full.keys())
    raw_tasks = list(raw.keys())
    shared = sorted(set(full_tasks) & set(raw_tasks))
    only_full = sorted(set(full_tasks) - set(raw_tasks))
    only_raw = sorted(set(raw_tasks) - set(full_tasks))

    out_subdir = out_dir / args.output_subdir
    out_subdir.mkdir(parents=True, exist_ok=True)
    out_path = out_subdir / "index.html"

    full_cards = "\n".join(_render_card(full[t], mode="full") for t in full_tasks)
    raw_cards = "\n".join(_render_card(raw[t], mode="raw") for t in raw_tasks)
    compare_rows = "\n".join(_render_compare_row(t, full.get(t), raw.get(t)) for t in shared)

    html_out = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>TITAN Diversity Gallery</title>
    <style>
      :root {{
        --bg: #0b0d12;
        --card: rgba(255,255,255,0.06);
        --border: rgba(255,255,255,0.12);
        --text: rgba(255,255,255,0.92);
        --muted: rgba(255,255,255,0.70);
        --pill: rgba(255,255,255,0.10);
        --shadow: 0 12px 40px rgba(0,0,0,0.45);
      }}
      body {{
        margin: 0;
        font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji",
          "Segoe UI Emoji";
        background: radial-gradient(1200px 600px at 20% -10%, rgba(99,102,241,0.25), transparent 60%),
          radial-gradient(1200px 600px at 80% -10%, rgba(16,185,129,0.22), transparent 55%),
          var(--bg);
        color: var(--text);
      }}
      a {{ color: inherit; }}
      header {{
        position: sticky;
        top: 0;
        backdrop-filter: blur(10px);
        background: rgba(11,13,18,0.72);
        border-bottom: 1px solid var(--border);
        z-index: 20;
      }}
      .wrap {{ max-width: 1400px; margin: 0 auto; padding: 18px 18px; }}
      h1 {{ font-size: 18px; margin: 0; letter-spacing: 0.2px; }}
      .sub {{
        color: var(--muted);
        font-size: 13px;
        margin-top: 6px;
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
      }}
      nav {{
        margin-top: 10px;
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
      }}
      .btn {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 8px 10px;
        border: 1px solid var(--border);
        border-radius: 12px;
        background: rgba(255,255,255,0.03);
        text-decoration: none;
        font-size: 13px;
        color: var(--muted);
      }}
      .btn:hover {{ color: var(--text); border-color: rgba(255,255,255,0.25); }}
      main {{ padding: 18px; }}
      section {{ max-width: 1400px; margin: 0 auto 28px; }}
      .sectionTitle {{
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 10px;
        margin: 12px 0;
      }}
      .sectionTitle h2 {{ margin: 0; font-size: 16px; }}
      .sectionTitle .meta {{
        margin: 0;
        color: var(--muted);
        font-size: 13px;
      }}
      .grid {{
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
        gap: 14px;
      }}
      .card {{
        border: 1px solid var(--border);
        background: var(--card);
        border-radius: 16px;
        overflow: hidden;
        box-shadow: var(--shadow);
      }}
      .imgwrap {{
        display: block;
        background: rgba(255,255,255,0.03);
      }}
      img {{
        width: 100%;
        height: auto;
        display: block;
      }}
      .meta {{
        padding: 10px 12px 12px;
        display: grid;
        gap: 6px;
        font-size: 12px;
        color: var(--muted);
      }}
      .meta code {{
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
        font-size: 11px;
        color: rgba(255,255,255,0.85);
        background: rgba(0,0,0,0.18);
        padding: 2px 6px;
        border-radius: 8px;
        border: 1px solid rgba(255,255,255,0.08);
      }}
      .k {{
        display: inline-block;
        min-width: 88px;
        color: rgba(255,255,255,0.55);
      }}
      .failures code {{
        color: rgba(255,200,200,0.95);
        border-color: rgba(255,100,100,0.25);
        background: rgba(255,50,50,0.08);
      }}
      .compareRow {{
        border: 1px solid var(--border);
        border-radius: 16px;
        background: rgba(255,255,255,0.04);
        overflow: hidden;
        margin-bottom: 14px;
      }}
      .compareHdr {{
        padding: 10px 12px;
        border-bottom: 1px solid rgba(255,255,255,0.10);
        display: flex;
        justify-content: space-between;
        gap: 10px;
      }}
      .title {{
        display: inline-flex;
        align-items: center;
        gap: 10px;
      }}
      .pill {{
        font-size: 12px;
        color: rgba(255,255,255,0.75);
        background: var(--pill);
        border: 1px solid rgba(255,255,255,0.12);
        padding: 4px 8px;
        border-radius: 999px;
      }}
      .compareGrid {{
        display: grid;
        grid-template-columns: 1fr 1fr;
      }}
      @media (max-width: 980px) {{
        .compareGrid {{ grid-template-columns: 1fr; }}
      }}
      .col {{
        border-right: 1px solid rgba(255,255,255,0.10);
      }}
      .col:last-child {{ border-right: none; }}
      .colhdr {{
        padding: 10px 12px;
        color: rgba(255,255,255,0.80);
        font-size: 13px;
      }}
      .small {{ font-size: 12px; }}
      .missing {{
        padding: 12px;
        color: rgba(255,255,255,0.55);
        font-size: 13px;
      }}
      footer {{
        max-width: 1400px;
        margin: 30px auto 60px;
        padding: 0 18px;
        color: rgba(255,255,255,0.60);
        font-size: 12px;
      }}
    </style>
  </head>
  <body>
    <header>
      <div class="wrap">
        <h1>TITAN Diversity Gallery</h1>
        <div class="sub">
          <span>full: <code>{_escape(args.full_run)}</code> ({len(full_tasks)} winners)</span>
          <span>raw: <code>{_escape(args.raw_run)}</code> ({len(raw_tasks)} winners)</span>
          <span>shared tasks: <code>{len(shared)}</code></span>
          <span>only full: <code>{len(only_full)}</code></span>
          <span>only raw: <code>{len(only_raw)}</code></span>
        </div>
        <nav>
          <a class="btn" href="../index.html">← out/ index</a>
          <a class="btn" href="#full">Full winners</a>
          <a class="btn" href="#raw">Raw winners</a>
          <a class="btn" href="#compare">Side-by-side compare</a>
        </nav>
      </div>
    </header>
    <main>
      <section id="full">
        <div class="sectionTitle">
          <h2>Full pipeline winners</h2>
          <div class="meta">Click an image to open the desktop screenshot full-size.</div>
        </div>
        <div class="grid">
          {full_cards}
        </div>
      </section>

      <section id="raw">
        <div class="sectionTitle">
          <h2>Raw baseline winners</h2>
          <div class="meta">Raw mode skips planner/judge/refinement. Gates still run.</div>
        </div>
        <div class="grid">
          {raw_cards}
        </div>
      </section>

      <section id="compare">
        <div class="sectionTitle">
          <h2>Side-by-side comparison (same task ID)</h2>
          <div class="meta">This is the easiest way to visually confirm diversity.</div>
        </div>
        {compare_rows}
      </section>
    </main>
    <footer>
      Tip: Ensure you are serving <code>out/</code> (not the repo root) via
      <code>python -m titan_factory.cli serve-out --port 8003</code>.
    </footer>
  </body>
</html>
"""

    out_path.write_text(html_out, encoding="utf-8")
    print(f"OK Wrote gallery: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
