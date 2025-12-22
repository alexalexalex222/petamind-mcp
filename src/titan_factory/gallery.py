"""Gallery generator - builds a static HTML view of rendered winners."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

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
