"""Gallery generator - builds static HTML views of generated sites."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from titan_factory.utils import ensure_dir


@dataclass(frozen=True)
class GalleryItem:
    """A single gallery entry for an accepted/selected candidate."""

    task_id: str
    niche_id: str
    page_type: str
    is_edit: bool
    candidate_id: str
    generator_model: str
    uigen_prompt_id: str
    score: float | None
    screenshots: dict[str, str]


@dataclass(frozen=True)
class PortalItem:
    """A single portal entry for a task and its selected (winner) candidate."""

    task_id: str
    niche_id: str
    page_type: str
    is_edit: bool
    task_status: str
    task_error: str
    style_family: str
    style_persona: str
    theme_mood: str
    theme_accent: str

    candidate_id: str
    candidate_status: str
    generator_model: str
    uigen_prompt_id: str
    score: float | None
    creativity_all: float | None
    creativity_core: float | None
    creativity_key: float | None
    creativity_high_count: int | None
    section_creativity: list[dict[str, Any]]

    screenshots: dict[str, str]
    deterministic_passed: bool | None
    deterministic_failures: list[str]
    axe_violations: list[dict[str, Any]]
    lighthouse_scores: dict[str, float]
    style_gate_passed: bool | None
    style_gate_failures: list[str]
    style_gate_warnings: list[str]

    # Composite shippable scoring (Phase 0)
    shippable_score: int
    shippable: bool
    required_pass: bool
    failure_reasons: list[str]


def _relative_to_run_dir(path_str: str, run_dir: Path) -> str:
    """Convert an absolute screenshot path to a run_dir-relative path when possible."""
    if not path_str:
        return ""

    try:
        p = Path(path_str).resolve()
        rel = p.relative_to(run_dir.resolve())
        return str(rel).replace("\\", "/")
    except Exception:
        return path_str.replace("\\", "/")


def _load_gallery_items(
    manifest_path: Path,
    run_dir: Path,
    *,
    min_score: float | None = None,
) -> list[GalleryItem]:
    conn = sqlite3.connect(manifest_path)
    try:
        # Backwards compatible: older runs may not have candidates.uigen_prompt_id.
        try:
            cursor = conn.execute(
                """
                SELECT
                    t.id,
                    t.niche_id,
                    t.page_type,
                    t.is_edit,
                    c.id,
                    c.generator_model,
                    c.uigen_prompt_id,
                    c.score,
                    c.screenshot_paths
                FROM candidates c
                JOIN tasks t ON c.task_id = t.id
                WHERE c.status IN ('selected', 'accepted')
                ORDER BY t.created_at ASC, c.created_at ASC
                """
            )
            has_prompt_id = True
        except sqlite3.OperationalError as e:
            if "uigen_prompt_id" not in str(e):
                raise
            cursor = conn.execute(
                """
                SELECT
                    t.id,
                    t.niche_id,
                    t.page_type,
                    t.is_edit,
                    c.id,
                    c.generator_model,
                    c.score,
                    c.screenshot_paths
                FROM candidates c
                JOIN tasks t ON c.task_id = t.id
                WHERE c.status IN ('selected', 'accepted')
                ORDER BY t.created_at ASC, c.created_at ASC
                """
            )
            has_prompt_id = False

        items: list[GalleryItem] = []
        for row in cursor.fetchall():
            score_idx = 7 if has_prompt_id else 6
            shots_idx = 8 if has_prompt_id else 7

            score_value = row[score_idx]
            if min_score is not None:
                if score_value is None or float(score_value) < float(min_score):
                    continue

            screenshots_raw = row[shots_idx] or "{}"
            try:
                screenshots = json.loads(screenshots_raw)
                if not isinstance(screenshots, dict):
                    screenshots = {}
            except json.JSONDecodeError:
                screenshots = {}

            # Convert to relative paths for portability
            screenshots_rel = {
                k: _relative_to_run_dir(v, run_dir) for k, v in screenshots.items()
            }

            items.append(
                GalleryItem(
                    task_id=row[0],
                    niche_id=row[1],
                    page_type=row[2],
                    is_edit=bool(row[3]),
                    candidate_id=row[4],
                    generator_model=row[5],
                    uigen_prompt_id=(row[6] if has_prompt_id else "default") or "default",
                    score=score_value,
                    screenshots=screenshots_rel,
                )
            )

        return items
    finally:
        conn.close()


def build_gallery(run_dir: Path, *, min_score: float | None = None) -> Path:
    """Build a static HTML gallery for a run.

    The gallery displays the selected winner screenshots for each completed task.

    Args:
        run_dir: Path to the run directory (out/<run_id>)
        min_score: Optional minimum score threshold for included items

    Returns:
        Path to the generated index.html
    """
    manifest_path = run_dir / "manifest.db"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    items = _load_gallery_items(manifest_path, run_dir, min_score=min_score)

    gallery_dir = ensure_dir(run_dir / "gallery")
    index_path = gallery_dir / "index.html"

    def esc(s: str) -> str:
        return (
            (s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    cards_html: list[str] = []
    for i, item in enumerate(items, 1):
        score = f"{item.score:.1f}" if item.score is not None else "?"
        mobile = item.screenshots.get("mobile", "")
        tablet = item.screenshots.get("tablet", "")
        desktop = item.screenshots.get("desktop", "")

        cards_html.append(
            f"""
            <section class="card" id="task-{esc(item.task_id)}">
              <div class="meta">
                <div class="title">
                  <span class="idx">#{i}</span>
                  <span class="type">{esc(item.page_type)}{' (edit)' if item.is_edit else ''}</span>
                  <span class="score">score {esc(score)}</span>
                </div>
                <div class="sub">
                  <span class="pill">task {esc(item.task_id)}</span>
                  <span class="pill">cand {esc(item.candidate_id)}</span>
                  <span class="pill">{esc(item.generator_model)}</span>
                  <span class="pill">prompt {esc(item.uigen_prompt_id)}</span>
                  <span class="pill">niche {esc(item.niche_id)}</span>
                </div>
              </div>

              <div class="shots">
                <div class="shot">
                  <div class="shot-label">Desktop</div>
                  {f'<a href=\"../{esc(desktop)}\" target=\"_blank\"><img src=\"../{esc(desktop)}\" alt=\"desktop\" /></a>' if desktop else '<div class=\"missing\">missing</div>'}
                </div>
                <div class="shot-row">
                  <div class="shot small">
                    <div class="shot-label">Tablet</div>
                    {f'<a href=\"../{esc(tablet)}\" target=\"_blank\"><img src=\"../{esc(tablet)}\" alt=\"tablet\" /></a>' if tablet else '<div class=\"missing\">missing</div>'}
                  </div>
                  <div class="shot small">
                    <div class="shot-label">Mobile</div>
                    {f'<a href=\"../{esc(mobile)}\" target=\"_blank\"><img src=\"../{esc(mobile)}\" alt=\"mobile\" /></a>' if mobile else '<div class=\"missing\">missing</div>'}
                  </div>
                </div>
              </div>
            </section>
            """
        )

    subtitle = ""
    if min_score is not None:
        subtitle = f" (min score {min_score:.1f})"

    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>TITAN Factory Gallery{subtitle}</title>
    <style>
      :root {{
        --bg: #0b0d12;
        --panel: #121623;
        --muted: rgba(255,255,255,0.72);
        --text: rgba(255,255,255,0.92);
        --border: rgba(255,255,255,0.10);
        --shadow: 0 18px 50px rgba(0,0,0,0.45);
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
        background: radial-gradient(1200px 800px at 30% -20%, rgba(56,189,248,0.18), transparent 60%),
                    radial-gradient(900px 700px at 90% 10%, rgba(168,85,247,0.16), transparent 65%),
                    var(--bg);
        color: var(--text);
      }}
      header {{
        padding: 28px 22px 10px;
        max-width: 1200px;
        margin: 0 auto;
      }}
      h1 {{
        margin: 0 0 6px;
        font-size: 22px;
        letter-spacing: -0.02em;
      }}
      .hint {{
        margin: 0;
        color: var(--muted);
        font-size: 14px;
        line-height: 1.45;
      }}
      .grid {{
        max-width: 1200px;
        margin: 0 auto;
        padding: 18px 22px 60px;
        display: grid;
        gap: 18px;
      }}
      .card {{
        background: linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.03));
        border: 1px solid var(--border);
        border-radius: 16px;
        overflow: hidden;
        box-shadow: var(--shadow);
      }}
      .meta {{
        padding: 14px 14px 10px;
        border-bottom: 1px solid var(--border);
        background: rgba(10,12,18,0.45);
        backdrop-filter: blur(10px);
      }}
      .title {{
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
      }}
      .idx {{
        font-weight: 700;
        color: rgba(255,255,255,0.9);
      }}
      .type {{
        font-weight: 650;
        letter-spacing: -0.01em;
      }}
      .score {{
        margin-left: auto;
        color: rgba(255,255,255,0.78);
        font-variant-numeric: tabular-nums;
      }}
      .sub {{
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        margin-top: 10px;
      }}
      .pill {{
        font-size: 12px;
        color: rgba(255,255,255,0.74);
        border: 1px solid rgba(255,255,255,0.12);
        padding: 6px 10px;
        border-radius: 999px;
        background: rgba(255,255,255,0.04);
      }}
      .shots {{
        padding: 14px;
        display: grid;
        gap: 12px;
      }}
      .shot {{
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 14px;
        background: rgba(0,0,0,0.25);
        overflow: hidden;
      }}
      .shot-label {{
        font-size: 12px;
        color: rgba(255,255,255,0.7);
        padding: 10px 12px;
        border-bottom: 1px solid rgba(255,255,255,0.10);
      }}
      img {{
        width: 100%;
        height: auto;
        display: block;
      }}
      .shot-row {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
      }}
      .shot.small img {{
        max-height: 520px;
        object-fit: cover;
        object-position: top;
      }}
      .missing {{
        padding: 18px 12px;
        color: rgba(255,255,255,0.55);
        font-size: 13px;
      }}
      a {{
        color: inherit;
        text-decoration: none;
      }}
      @media (max-width: 860px) {{
        .shot-row {{ grid-template-columns: 1fr; }}
        .score {{ margin-left: 0; }}
      }}
    </style>
  </head>
  <body>
    <header>
      <h1>TITAN Factory Gallery{subtitle}</h1>
      <p class="hint">
        Selected winners only. Click an image to open it in a new tab.
        Gallery path: <code>{esc(str(index_path))}</code>
      </p>
    </header>
    <main class="grid">
      {''.join(cards_html) if cards_html else '<p class=\"hint\">No completed tasks found.</p>'}
    </main>
  </body>
</html>
"""

    index_path.write_text(html, encoding="utf-8")
    return index_path


def _parse_json(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _axe_impact_counts(axe_violations: list[dict[str, Any]]) -> tuple[int, int]:
    critical = 0
    serious = 0
    for v in axe_violations or []:
        if not isinstance(v, dict):
            continue
        impact = str(v.get("impact") or "").strip().lower()
        if impact == "critical":
            critical += 1
        elif impact == "serious":
            serious += 1
    return critical, serious


def _composite_score(
    *,
    rendered_ok: bool,
    axe_critical: int,
    axe_serious: int,
    lighthouse_accessibility: float,
    lighthouse_performance: float,
) -> tuple[int, bool, bool, list[str]]:
    """Compute the Phase 0 composite score and failure reasons.

    Mirrors the shippable_composite definition:
      required:
        rendered: true
        axe_critical: 0
        lighthouse_accessibility: >= 0.80
      scoring:
        +25 required pass
        +25 lighthouse_accessibility >= 0.90
        +25 lighthouse_performance >= 0.70
        +25 axe_serious == 0
      shippable_threshold: >= 75
    """
    failure_reasons: list[str] = []
    if not rendered_ok:
        failure_reasons.append("missing_render")
    if axe_critical > 0:
        failure_reasons.append(f"axe_critical={axe_critical}")
    if lighthouse_accessibility < 0.80:
        failure_reasons.append(f"lh_accessibility={lighthouse_accessibility:.2f}<0.80")

    required_pass = (
        rendered_ok
        and axe_critical == 0
        and lighthouse_accessibility >= 0.80
    )

    score = 0
    if required_pass:
        score += 25
    if lighthouse_accessibility >= 0.90:
        score += 25
    if lighthouse_performance >= 0.70:
        score += 25
    if axe_serious == 0:
        score += 25

    shippable = bool(required_pass and score >= 75)
    return score, shippable, required_pass, failure_reasons


def build_portal(run_dir: Path, *, include_edits: bool = False) -> Path:
    """Build a portal HTML page that lists all tasks and separates failures.

    This differs from `build_gallery()`:
    - Includes every task (even failed tasks)
    - Uses the selected candidate per task when available
    - Separates "Shippable" vs "Failed" sections using the Phase 0 composite metric
    """
    manifest_path = run_dir / "manifest.db"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    conn = sqlite3.connect(manifest_path)
    try:
        # Column presence checks for backwards compatibility.
        cand_cols = {row[1] for row in conn.execute("PRAGMA table_info(candidates);").fetchall()}
        task_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks);").fetchall()}
        has_prompt_id = "uigen_prompt_id" in cand_cols

        select_cols = [
            "t.id",
            "t.niche_id",
            "t.page_type",
            "t.is_edit",
            "t.status",
            ("COALESCE(t.error,'')" if "error" in task_cols else "''"),
            "t.selected_candidate_id",
            "c.id",
            "COALESCE(c.status,'')",
            "COALESCE(c.generator_model,'')",
            ("COALESCE(c.uigen_prompt_id,'default')" if has_prompt_id else "'default'"),
            "c.score",
            ("c.section_creativity_avg" if "section_creativity_avg" in cand_cols else "NULL"),
            ("c.section_creativity_core_avg" if "section_creativity_core_avg" in cand_cols else "NULL"),
            ("c.section_creativity_key_avg" if "section_creativity_key_avg" in cand_cols else "NULL"),
            ("c.section_creativity_high_count" if "section_creativity_high_count" in cand_cols else "NULL"),
            (
                "COALESCE(c.section_creativity,'[]')"
                if "section_creativity" in cand_cols
                else "'[]'"
            ),
            ("COALESCE(c.screenshot_paths,'{}')" if "screenshot_paths" in cand_cols else "'{}'"),
            ("c.deterministic_passed" if "deterministic_passed" in cand_cols else "NULL"),
            (
                "COALESCE(c.deterministic_failures,'[]')"
                if "deterministic_failures" in cand_cols
                else "'[]'"
            ),
            ("COALESCE(c.axe_violations,'[]')" if "axe_violations" in cand_cols else "'[]'"),
            ("COALESCE(c.lighthouse_scores,'{}')" if "lighthouse_scores" in cand_cols else "'{}'"),
            ("COALESCE(c.error,'')" if "error" in cand_cols else "''"),
            # Task style routing (optional)
            ("COALESCE(t.style_family,'')" if "style_family" in task_cols else "''"),
            ("COALESCE(t.style_persona,'')" if "style_persona" in task_cols else "''"),
            ("COALESCE(t.theme_mood,'')" if "theme_mood" in task_cols else "''"),
            ("COALESCE(t.theme_accent,'')" if "theme_accent" in task_cols else "''"),
            # Candidate style gates (optional)
            ("c.style_gate_passed" if "style_gate_passed" in cand_cols else "NULL"),
            (
                "COALESCE(c.style_gate_failures,'[]')"
                if "style_gate_failures" in cand_cols
                else "'[]'"
            ),
            (
                "COALESCE(c.style_gate_warnings,'[]')"
                if "style_gate_warnings" in cand_cols
                else "'[]'"
            ),
        ]

        where = ""
        if not include_edits:
            where = "WHERE t.is_edit = 0"

        cursor = conn.execute(
            f"""
            SELECT {", ".join(select_cols)}
            FROM tasks t
            LEFT JOIN candidates c ON c.id = t.selected_candidate_id
            {where}
            ORDER BY t.created_at ASC
            """
        )

        items: list[PortalItem] = []
        for row in cursor.fetchall():
            (
                task_id,
                niche_id,
                page_type,
                is_edit,
                task_status,
                task_error,
                selected_candidate_id,
                candidate_id,
                candidate_status,
                generator_model,
                uigen_prompt_id,
                score_value,
                creativity_all_raw,
                creativity_core_raw,
                creativity_key_raw,
                creativity_high_raw,
                section_creativity_raw,
                screenshot_paths_raw,
                deterministic_passed_raw,
                deterministic_failures_raw,
                axe_violations_raw,
                lighthouse_scores_raw,
                candidate_error,
                style_family,
                style_persona,
                theme_mood,
                theme_accent,
                style_gate_passed_raw,
                style_gate_failures_raw,
                style_gate_warnings_raw,
            ) = row

            # Screenshots
            screenshots = _parse_json(screenshot_paths_raw, {})
            if not isinstance(screenshots, dict):
                screenshots = {}
            screenshots_rel = {k: _relative_to_run_dir(v, run_dir) for k, v in screenshots.items()}

            # Section creativity (optional)
            section_creativity = _parse_json(section_creativity_raw, [])
            if not isinstance(section_creativity, list):
                section_creativity = []
            section_creativity = [s for s in section_creativity if isinstance(s, dict)]

            def _as_float(v: object) -> float | None:
                if v is None:
                    return None
                try:
                    return float(v)
                except Exception:
                    return None

            creativity_all = _as_float(creativity_all_raw)
            creativity_core = _as_float(creativity_core_raw)
            creativity_key = _as_float(creativity_key_raw)
            creativity_high = None
            if creativity_high_raw is not None:
                try:
                    creativity_high = int(creativity_high_raw)
                except Exception:
                    creativity_high = None

            # Gates
            deterministic_failures = _parse_json(deterministic_failures_raw, [])
            if not isinstance(deterministic_failures, list):
                deterministic_failures = []
            deterministic_failures = [str(x) for x in deterministic_failures if str(x).strip()]

            axe_violations = _parse_json(axe_violations_raw, [])
            if not isinstance(axe_violations, list):
                axe_violations = []
            axe_violations = [v for v in axe_violations if isinstance(v, dict)]

            lighthouse_scores = _parse_json(lighthouse_scores_raw, {})
            if not isinstance(lighthouse_scores, dict):
                lighthouse_scores = {}

            # Normalize Lighthouse scores to floats.
            lh_accessibility = float(lighthouse_scores.get("accessibility") or 0.0)
            lh_performance = float(lighthouse_scores.get("performance") or 0.0)

            axe_critical, axe_serious = _axe_impact_counts(axe_violations)

            rendered_ok = bool(screenshots_rel)
            shippable_score, shippable, required_pass, failure_reasons = _composite_score(
                rendered_ok=rendered_ok,
                axe_critical=axe_critical,
                axe_serious=axe_serious,
                lighthouse_accessibility=lh_accessibility,
                lighthouse_performance=lh_performance,
            )

            # Task-level failure reasons (prefer deterministic_failures when present)
            if str(task_status or "").lower() != "completed":
                failure_reasons = [f"task_status={task_status or 'unknown'}"]
                if task_error and str(task_error).strip():
                    failure_reasons.append(f"task_error={str(task_error).strip()[:180]}")
            elif (selected_candidate_id is None) or (not str(selected_candidate_id).strip()):
                failure_reasons = ["no_selected_candidate"]
            else:
                # If deterministic gates ran, these are the most actionable.
                if deterministic_failures:
                    failure_reasons = [str(x) for x in deterministic_failures[:8]]
                elif failure_reasons:
                    # Keep composite reasons if deterministic failures are absent.
                    failure_reasons = list(failure_reasons)

                if candidate_error and str(candidate_error).strip():
                    failure_reasons.append(f"candidate_error={str(candidate_error).strip()[:180]}")

            det_pass = None
            if deterministic_passed_raw is not None:
                try:
                    det_pass = bool(int(deterministic_passed_raw))
                except Exception:
                    det_pass = bool(deterministic_passed_raw)

            sg_pass = None
            if style_gate_passed_raw is not None:
                try:
                    sg_pass = bool(int(style_gate_passed_raw))
                except Exception:
                    sg_pass = bool(style_gate_passed_raw)

            sg_failures = _parse_json(style_gate_failures_raw, [])
            if not isinstance(sg_failures, list):
                sg_failures = []
            sg_failures = [str(x) for x in sg_failures if str(x).strip()]

            sg_warnings = _parse_json(style_gate_warnings_raw, [])
            if not isinstance(sg_warnings, list):
                sg_warnings = []
            sg_warnings = [str(x) for x in sg_warnings if str(x).strip()]

            items.append(
                PortalItem(
                    task_id=str(task_id),
                    niche_id=str(niche_id),
                    page_type=str(page_type),
                    is_edit=bool(is_edit),
                    task_status=str(task_status),
                    task_error=str(task_error or ""),
                    style_family=str(style_family or ""),
                    style_persona=str(style_persona or ""),
                    theme_mood=str(theme_mood or ""),
                    theme_accent=str(theme_accent or ""),
                    candidate_id=str(candidate_id or ""),
                    candidate_status=str(candidate_status or ""),
                    generator_model=str(generator_model or ""),
                    uigen_prompt_id=str(uigen_prompt_id or "default"),
                    score=score_value,
                    creativity_all=creativity_all,
                    creativity_core=creativity_core,
                    creativity_key=creativity_key,
                    creativity_high_count=creativity_high,
                    section_creativity=section_creativity,
                    screenshots=screenshots_rel,
                    deterministic_passed=det_pass,
                    deterministic_failures=deterministic_failures,
                    axe_violations=axe_violations,
                    lighthouse_scores={
                        "accessibility": lh_accessibility,
                        "performance": lh_performance,
                        "best_practices": float(lighthouse_scores.get("best_practices") or 0.0),
                        "seo": float(lighthouse_scores.get("seo") or 0.0),
                    },
                    style_gate_passed=sg_pass,
                    style_gate_failures=sg_failures,
                    style_gate_warnings=sg_warnings,
                    shippable_score=int(shippable_score),
                    shippable=bool(shippable) if str(task_status).lower() == "completed" else False,
                    required_pass=bool(required_pass) if str(task_status).lower() == "completed" else False,
                    failure_reasons=failure_reasons,
                )
            )

        def _portal_creativity_metric(item: PortalItem) -> float | None:
            if item.creativity_key is not None:
                return float(item.creativity_key)
            if item.creativity_core is not None:
                return float(item.creativity_core)
            if item.creativity_all is not None:
                return float(item.creativity_all)
            return None

        creativity_gate_min = 0.70
        creativity_gate_min_high_sections = 2

        def _portal_creativity_gate_pass(item: PortalItem) -> bool:
            # Prefer "high section count" (hero + at least one mid-page moment) when available.
            if item.creativity_high_count is not None:
                try:
                    return int(item.creativity_high_count) >= creativity_gate_min_high_sections
                except Exception:
                    return False

            # Backwards-compatible fallback for older runs without per-section aggregates.
            metric = _portal_creativity_metric(item)
            return metric is not None and float(metric) >= creativity_gate_min

        creative_shippable_items = [
            i
            for i in items
            if i.task_status.lower() == "completed"
            and i.shippable
            and _portal_creativity_gate_pass(i)
        ]
        shippable_but_generic_items = [
            i
            for i in items
            if i.task_status.lower() == "completed"
            and i.shippable
            and (not _portal_creativity_gate_pass(i))
        ]
        failed_items = [
            i for i in items if not (i.task_status.lower() == "completed" and i.shippable)
        ]

        portal_dir = ensure_dir(run_dir / "portal")
        index_path = portal_dir / "index.html"

        def esc(s: str) -> str:
            return (
                (s or "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )

        def fmt_pct(val: float) -> str:
            try:
                return f"{float(val) * 100.0:.0f}%"
            except Exception:
                return "?"

        def _card(item: PortalItem, idx: int) -> str:
            desktop = item.screenshots.get("desktop", "")
            tablet = item.screenshots.get("tablet", "")
            mobile = item.screenshots.get("mobile", "")

            score = f"{item.score:.1f}" if item.score is not None else "?"
            # Creativity is the north star. Treat "shippable but generic" as a distinct bucket.
            creativity_metric = (
                item.creativity_key
                if item.creativity_key is not None
                else (item.creativity_core if item.creativity_core is not None else item.creativity_all)
            )
            creativity_gate_min = 0.70
            creativity_gate_min_high_sections = 2
            creativity_gate_pass = _portal_creativity_gate_pass(item)

            if item.shippable and creativity_gate_pass:
                pill_status = "pass"
                status_label = "CREATIVE + SHIPPABLE"
            elif item.shippable and not creativity_gate_pass:
                pill_status = "warn"
                status_label = "SHIPPABLE (NOT CREATIVE)"
            else:
                pill_status = "fail"
                status_label = "FAILED"

            axe_critical, axe_serious = _axe_impact_counts(item.axe_violations)
            lh_a11y = float(item.lighthouse_scores.get("accessibility") or 0.0)
            lh_perf = float(item.lighthouse_scores.get("performance") or 0.0)

            failure_bits: list[str] = []
            if item.shippable and not creativity_gate_pass:
                cm = "?" if creativity_metric is None else f"{float(creativity_metric):.2f}"
                hs = (
                    "?"
                    if item.creativity_high_count is None
                    else str(int(item.creativity_high_count))
                )
                failure_bits.append(
                    "creativity gate failed "
                    f"(avg<{creativity_gate_min:.2f} and/or high_sections<{creativity_gate_min_high_sections}; "
                    f"got avg={cm}, high_sections={hs})"
                )
            if (not item.shippable) and item.failure_reasons:
                failure_bits.extend(item.failure_reasons[:8])

            failure_line = ""
            if failure_bits:
                failure_line = (
                    "<div class=\"fail\">"
                    + esc(" • ".join([str(x) for x in failure_bits if str(x).strip()][:10]))
                    + "</div>"
                )

            # Provide a compact, explicit breakdown for passes and failures.
            def _axe_list(impact: str) -> list[dict[str, Any]]:
                out: list[dict[str, Any]] = []
                for v in item.axe_violations or []:
                    if not isinstance(v, dict):
                        continue
                    if str(v.get("impact") or "").strip().lower() == impact:
                        out.append(v)
                return out

            axe_crit_list = _axe_list("critical")
            axe_ser_list = _axe_list("serious")

            def _axe_line(vs: list[dict[str, Any]]) -> str:
                parts: list[str] = []
                for v in vs[:6]:
                    vid = str(v.get("id") or "").strip() or "(unknown)"
                    help_url = str(v.get("helpUrl") or "").strip()
                    if help_url:
                        parts.append(
                            f"<a href=\"{esc(help_url)}\" target=\"_blank\">{esc(vid)}</a>"
                        )
                    else:
                        parts.append(esc(vid))
                return ", ".join(parts) if parts else "(none)"

            det_fail = (
                ", ".join(esc(x) for x in (item.deterministic_failures or [])[:8]) or "(none)"
            )

            sg_fail = ", ".join(esc(x) for x in (item.style_gate_failures or [])[:6]) or "(none)"
            sg_warn = ", ".join(esc(x) for x in (item.style_gate_warnings or [])[:6]) or "(none)"

            def yn(v: bool) -> str:
                return "yes" if v else "no"

            details_title = (
                "Why passed (details)"
                if (item.shippable and creativity_gate_pass)
                else "Why failed (details)"
            )
            details_html = f"""
              <details class=\"why\">
                <summary>{details_title}</summary>
                <div class=\"why-body\">
                  <div class=\"why-row\"><span class=\"why-k\">Style</span><span class=\"why-v\">family {esc(item.style_family or '(none)')} • persona {esc(item.style_persona or '(none)')} • theme {esc(item.theme_mood or '?')}/{esc(item.theme_accent or '?')}</span></div>
                  <div class=\"why-row\"><span class=\"why-k\">Creativity</span><span class=\"why-v\">key {esc(f'{(item.creativity_key or 0.0):.2f}' if item.creativity_key is not None else '(n/a)')} • core {esc(f'{(item.creativity_core or 0.0):.2f}' if item.creativity_core is not None else '(n/a)')} • all {esc(f'{(item.creativity_all or 0.0):.2f}' if item.creativity_all is not None else '(n/a)')} • high_sections {esc(str(item.creativity_high_count) if item.creativity_high_count is not None else '(n/a)')}</span></div>
                  <div class=\"why-row\"><span class=\"why-k\">Composite</span><span class=\"why-v\">required {yn(bool(item.required_pass))} • a11y≥90% {yn(lh_a11y >= 0.90)} • perf≥70% {yn(lh_perf >= 0.70)} • serious=0 {yn(axe_serious == 0)}</span></div>
                  <div class=\"why-row\"><span class=\"why-k\">Deterministic</span><span class=\"why-v\">{det_fail}</span></div>
                  <div class=\"why-row\"><span class=\"why-k\">Lighthouse</span><span class=\"why-v\">a11y {fmt_pct(item.lighthouse_scores.get('accessibility', 0.0))} • perf {fmt_pct(item.lighthouse_scores.get('performance', 0.0))}</span></div>
                  <div class=\"why-row\"><span class=\"why-k\">Axe critical</span><span class=\"why-v\">{_axe_line(axe_crit_list)}</span></div>
                  <div class=\"why-row\"><span class=\"why-k\">Axe serious</span><span class=\"why-v\">{_axe_line(axe_ser_list)}</span></div>
                  <div class=\"why-row\"><span class=\"why-k\">Style gate</span><span class=\"why-v\">passed {yn(bool(item.style_gate_passed))} • failures {sg_fail} • warnings {sg_warn}</span></div>
                </div>
              </details>
            """

            return f"""
            <section class="card {pill_status}" id="task-{esc(item.task_id)}">
              <div class="meta">
                <div class="title">
                  <span class="idx">#{idx}</span>
                  <span class="badge {pill_status}">{esc(status_label)}</span>
                  <span class="type">{esc(item.page_type)}{' (edit)' if item.is_edit else ''}</span>
                  <span class="score">score {esc(score)} • composite {item.shippable_score}</span>
                </div>
                <div class="sub">
                  <span class="pill">task {esc(item.task_id)}</span>
                  <span class="pill">status {esc(item.task_status)}</span>
                  {f'<span class=\"pill\">cand {esc(item.candidate_id)}</span>' if item.candidate_id else '<span class=\"pill\">cand (none)</span>'}
                  {f'<span class=\"pill\">{esc(item.generator_model)}</span>' if item.generator_model else ''}
                  <span class="pill">prompt {esc(item.uigen_prompt_id)}</span>
                  <span class="pill">a11y {esc(fmt_pct(lh_a11y))}</span>
                  <span class="pill">perf {esc(fmt_pct(lh_perf))}</span>
                  <span class="pill">axe crit {axe_critical}</span>
                  <span class="pill">axe serious {axe_serious}</span>
                  <span class="pill">creativity {esc('?' if creativity_metric is None else f'{float(creativity_metric):.2f}')}</span>
                  <span class="pill">niche {esc(item.niche_id)}</span>
                  {f'<span class=\"pill\">style {esc(item.style_family)}</span>' if item.style_family else ''}
                  {f'<span class=\"pill\">theme {esc(item.theme_mood)}/{esc(item.theme_accent)}</span>' if item.theme_mood or item.theme_accent else ''}
                </div>
                {failure_line}
                {details_html}
              </div>

              <div class="shots">
                <div class="shot">
                  <div class="shot-label">Desktop</div>
                  {f'<a href=\"../{esc(desktop)}\" target=\"_blank\"><img loading=\"lazy\" src=\"../{esc(desktop)}\" alt=\"desktop\" /></a>' if desktop else '<div class=\"missing\">missing</div>'}
                </div>
                <div class="shot-row">
                  <div class="shot small">
                    <div class="shot-label">Tablet</div>
                    {f'<a href=\"../{esc(tablet)}\" target=\"_blank\"><img loading=\"lazy\" src=\"../{esc(tablet)}\" alt=\"tablet\" /></a>' if tablet else '<div class=\"missing\">missing</div>'}
                  </div>
                  <div class="shot small">
                    <div class="shot-label">Mobile</div>
                    {f'<a href=\"../{esc(mobile)}\" target=\"_blank\"><img loading=\"lazy\" src=\"../{esc(mobile)}\" alt=\"mobile\" /></a>' if mobile else '<div class=\"missing\">missing</div>'}
                  </div>
                </div>
              </div>
            </section>
            """

        def _section(title: str, subtitle: str, section_id: str, section_items: list[PortalItem]) -> str:
            cards = []
            for i, item in enumerate(section_items, 1):
                cards.append(_card(item, i))

            empty = '<p class="hint">No items.</p>'
            return f"""
            <section class="section" id="{esc(section_id)}">
              <div class="section-head">
                <h2>{esc(title)}</h2>
                <p class="hint">{esc(subtitle)}</p>
              </div>
              <div class="grid">
                {''.join(cards) if cards else empty}
              </div>
            </section>
            """

        total = len(items)
        creative_n = len(creative_shippable_items)
        ship_only_n = len(shippable_but_generic_items)
        failed_n = len(failed_items)
        shippable_n = creative_n + ship_only_n
        shippable_pct = (100.0 * shippable_n / total) if total else 0.0
        creative_pct = (100.0 * creative_n / total) if total else 0.0

        html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>TITAN Factory Portal</title>
    <style>
      :root {{
        --bg: #0b0d12;
        --panel: #121623;
        --muted: rgba(255,255,255,0.72);
        --text: rgba(255,255,255,0.92);
        --border: rgba(255,255,255,0.10);
        --shadow: 0 18px 50px rgba(0,0,0,0.45);
        --good: rgba(16,185,129,0.95);
        --warn: rgba(251,191,36,0.95);
        --bad: rgba(248,113,113,0.95);
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
        background: radial-gradient(1200px 800px at 30% -20%, rgba(56,189,248,0.18), transparent 60%),
                    radial-gradient(900px 700px at 90% 10%, rgba(168,85,247,0.16), transparent 65%),
                    var(--bg);
        color: var(--text);
      }}
      header {{
        padding: 28px 22px 16px;
        max-width: 1200px;
        margin: 0 auto;
      }}
      h1 {{
        margin: 0 0 10px;
        font-size: 22px;
        letter-spacing: -0.02em;
      }}
      .hint {{
        margin: 0;
        color: var(--muted);
        font-size: 14px;
        line-height: 1.45;
      }}
      .nav {{
        margin-top: 14px;
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
      }}
      .nav a {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        font-size: 13px;
        color: rgba(255,255,255,0.86);
        border: 1px solid rgba(255,255,255,0.12);
        padding: 8px 12px;
        border-radius: 999px;
        background: rgba(255,255,255,0.04);
        text-decoration: none;
      }}
      .section {{
        max-width: 1200px;
        margin: 0 auto;
        padding: 0 22px 54px;
      }}
      .section-head {{
        padding: 8px 0 14px;
      }}
      h2 {{
        margin: 0 0 6px;
        font-size: 18px;
        letter-spacing: -0.02em;
      }}
      .grid {{
        display: grid;
        gap: 18px;
      }}
      .card {{
        background: linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.03));
        border: 1px solid var(--border);
        border-radius: 16px;
        overflow: hidden;
        box-shadow: var(--shadow);
      }}
      .card.pass {{
        border-color: rgba(16,185,129,0.25);
      }}
      .card.warn {{
        border-color: rgba(251,191,36,0.28);
      }}
      .card.fail {{
        border-color: rgba(248,113,113,0.25);
      }}
      .meta {{
        padding: 14px 14px 10px;
        border-bottom: 1px solid var(--border);
        background: rgba(10,12,18,0.45);
        backdrop-filter: blur(10px);
      }}
      .title {{
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
      }}
      .idx {{
        font-weight: 700;
        color: rgba(255,255,255,0.9);
      }}
      .badge {{
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.06em;
        padding: 6px 10px;
        border-radius: 999px;
        border: 1px solid rgba(255,255,255,0.16);
      }}
      .badge.pass {{
        color: var(--good);
        border-color: rgba(16,185,129,0.35);
        background: rgba(16,185,129,0.10);
      }}
      .badge.warn {{
        color: var(--warn);
        border-color: rgba(251,191,36,0.35);
        background: rgba(251,191,36,0.10);
      }}
      .badge.fail {{
        color: var(--bad);
        border-color: rgba(248,113,113,0.35);
        background: rgba(248,113,113,0.10);
      }}
      .type {{
        font-weight: 650;
        letter-spacing: -0.01em;
      }}
      .score {{
        margin-left: auto;
        color: rgba(255,255,255,0.78);
        font-variant-numeric: tabular-nums;
      }}
      .sub {{
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        margin-top: 10px;
      }}
      .pill {{
        font-size: 12px;
        color: rgba(255,255,255,0.74);
        border: 1px solid rgba(255,255,255,0.12);
        padding: 6px 10px;
        border-radius: 999px;
        background: rgba(255,255,255,0.04);
      }}
      .fail {{
        margin-top: 10px;
        color: rgba(248,113,113,0.92);
        font-size: 12px;
      }}
      details.why {{
        margin-top: 10px;
        border: 1px solid rgba(255,255,255,0.12);
        border-radius: 12px;
        background: rgba(255,255,255,0.03);
        overflow: hidden;
      }}
      details.why summary {{
        cursor: pointer;
        padding: 10px 12px;
        color: rgba(255,255,255,0.86);
        font-size: 12px;
        list-style: none;
      }}
      details.why summary::-webkit-details-marker {{
        display: none;
      }}
      .why-body {{
        padding: 10px 12px 12px;
        border-top: 1px solid rgba(255,255,255,0.10);
        display: grid;
        gap: 8px;
      }}
      .why-row {{
        display: grid;
        grid-template-columns: 130px 1fr;
        gap: 10px;
        font-size: 12px;
        color: rgba(255,255,255,0.78);
        line-height: 1.35;
      }}
      .why-k {{
        color: rgba(255,255,255,0.88);
        font-weight: 650;
      }}
      .why-v a {{
        color: rgba(96,165,250,0.95);
        text-decoration: none;
      }}
      .why-v a:hover {{
        text-decoration: underline;
      }}
      .shots {{
        padding: 14px;
        display: grid;
        gap: 12px;
      }}
      .shot {{
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 14px;
        background: rgba(0,0,0,0.25);
        overflow: hidden;
      }}
      .shot-label {{
        font-size: 12px;
        color: rgba(255,255,255,0.7);
        padding: 10px 12px;
        border-bottom: 1px solid rgba(255,255,255,0.10);
      }}
      img {{
        width: 100%;
        height: auto;
        display: block;
      }}
      .shot-row {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
      }}
      .shot.small img {{
        max-height: 520px;
        object-fit: cover;
        object-position: top;
      }}
      .missing {{
        padding: 18px 12px;
        color: rgba(255,255,255,0.55);
        font-size: 13px;
      }}
      @media (max-width: 860px) {{
        .shot-row {{ grid-template-columns: 1fr; }}
        .score {{ margin-left: 0; }}
      }}
    </style>
  </head>
  <body>
    <header>
      <h1>TITAN Factory Portal</h1>
      <p class="hint">
        Run: <code>{esc(str(run_dir))}</code> •
        Total tasks: <strong>{total}</strong> •
        Creative+Shippable: <strong>{creative_n}</strong> •
        Shippable (not creative): <strong>{ship_only_n}</strong> •
        Failed: <strong>{failed_n}</strong> •
        Shippable rate: <strong>{shippable_pct:.1f}%</strong> •
        Creative rate: <strong>{creative_pct:.1f}%</strong>
      </p>
      <nav class="nav">
        <a href="#creative">Creative+Shippable ({creative_n})</a>
        <a href="#ship-only">Shippable (not creative) ({ship_only_n})</a>
        <a href="#failed">Failed ({failed_n})</a>
        <a href="logs.html">Logs</a>
      </nav>
      <p class="hint" style="margin-top:10px;">
        Click an image to open it in a new tab. Images are loaded lazily.
        Portal path: <code>{esc(str(index_path))}</code>
      </p>
    </header>

    {_section("Creative + Shippable Websites", "Meets Phase 0 shippable composite AND creativity gate (>=2 high sections, or legacy avg >= 0.70).", "creative", creative_shippable_items)}
    {_section("Shippable (Not Creative)", "Meets Phase 0 shippable composite but fails creativity gate (still useful for debugging).", "ship-only", shippable_but_generic_items)}
    {_section("Failed Websites", "Failed generation/build/render or did not meet required deterministic gates.", "failed", failed_items)}
  </body>
</html>
"""

        index_path.write_text(html, encoding="utf-8")

        # Optional logs page: fetches run.log (if present) from the run root.
        logs_path = portal_dir / "logs.html"
        logs_html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>TITAN Factory Portal - Logs</title>
    <style>
      :root {
        --bg: #0b0d12;
        --muted: rgba(255,255,255,0.72);
        --text: rgba(255,255,255,0.92);
        --border: rgba(255,255,255,0.10);
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
        background: var(--bg);
        color: var(--text);
      }
      header, main {
        max-width: 1200px;
        margin: 0 auto;
      }
      header {
        padding: 18px 22px 12px;
      }
      main {
        padding: 0 22px 26px;
      }
      a {
        color: rgba(96,165,250,0.95);
        text-decoration: none;
      }
      a:hover { text-decoration: underline; }
      h1 {
        margin: 10px 0 8px;
        font-size: 18px;
        letter-spacing: -0.02em;
      }
      .hint {
        margin: 0;
        color: var(--muted);
        font-size: 13px;
        line-height: 1.45;
      }
      .actions {
        margin-top: 10px;
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
      }
      .btn {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        font-size: 13px;
        color: rgba(255,255,255,0.86);
        border: 1px solid rgba(255,255,255,0.12);
        padding: 8px 12px;
        border-radius: 999px;
        background: rgba(255,255,255,0.04);
      }
      pre {
        margin: 14px 0 0;
        padding: 14px;
        border: 1px solid rgba(255,255,255,0.12);
        border-radius: 14px;
        background: rgba(255,255,255,0.03);
        overflow: auto;
        white-space: pre-wrap;
        word-break: break-word;
        font-size: 12px;
        line-height: 1.35;
        color: rgba(255,255,255,0.86);
      }
      code {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
        font-size: 12px;
        color: rgba(255,255,255,0.85);
      }
    </style>
  </head>
  <body>
    <header>
      <a class="btn" href="index.html">← Back to portal</a>
      <h1>Run logs</h1>
      <p class="hint">This page loads <code>../run.log</code> (and optional <code>../smoke_summary.md</code>) from the run directory.</p>
      <div class="actions">
        <a class="btn" href="../run.log" target="_blank" rel="noopener">Open run.log</a>
        <a class="btn" href="../run.log" download>Download run.log</a>
        <a class="btn" href="../smoke_summary.md" target="_blank" rel="noopener">Open smoke_summary.md</a>
        <a class="btn" href="../manifest.db" download>Download manifest.db</a>
      </div>
    </header>
    <main>
      <pre id="log">Loading run.log…</pre>
    </main>
    <script>
      const el = document.getElementById('log');
      fetch('../run.log')
        .then(r => {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.text();
        })
        .then(t => { el.textContent = t; })
        .catch(err => { el.textContent = 'Could not load ../run.log: ' + err; });
    </script>
  </body>
</html>
"""
        logs_path.write_text(logs_html, encoding="utf-8")

        return index_path
    finally:
        conn.close()
