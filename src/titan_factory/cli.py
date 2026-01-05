"""Command-line interface for TITAN Factory."""

import asyncio
import json
import os
import socketserver
from pathlib import Path
from typing import Optional

from functools import partial
from http.server import SimpleHTTPRequestHandler
from datetime import datetime

import typer
from rich.console import Console
from rich.table import Table

from titan_factory.config import load_config
from titan_factory.exporter import export_run, export_stats
from titan_factory.gallery import build_gallery, build_portal
from titan_factory.orchestrator import backfill_no_winner, run_pipeline
from titan_factory.promptgen import generate_niches, generate_tasks, save_niches, save_tasks
from titan_factory.utils import log_error, log_info, log_success, log_warning

app = typer.Typer(
    name="titan-factory",
    help="TITAN-4-DESIGN Dataset Factory - Generate synthetic UI training data",
)
console = Console()


@app.command()
def run(
    public_only: bool = typer.Option(
        False,
        "--public-only",
        help="Only use publishable models",
    ),
    max_tasks: Optional[int] = typer.Option(
        None,
        "--max-tasks",
        help="Maximum number of tasks to process",
    ),
    run_id: Optional[str] = typer.Option(
        None,
        "--run-id",
        help="Custom run ID",
    ),
    resume: Optional[str] = typer.Option(
        None,
        "--resume",
        help="Resume from a previous run ID",
    ),
    config_path: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Run the data generation pipeline."""
    try:
        config = load_config(config_path)

        # Validate configuration (only require env vars for providers in use)
        providers_used: set[str] = set()
        if config.planner.model:
            providers_used.add(config.planner.provider)
        for gen in config.ui_generators:
            if gen.model:
                providers_used.add(gen.provider)
        if config.patcher.model:
            providers_used.add(config.patcher.provider)
        if config.vision_judge.model:
            providers_used.add(config.vision_judge.provider)

        if {"vertex", "anthropic_vertex"} & providers_used and not config.google_project:
            log_error(
                "GOOGLE_CLOUD_PROJECT not set but a Vertex provider is configured. "
                "Set GOOGLE_CLOUD_PROJECT (and optionally GOOGLE_CLOUD_REGION)."
            )
            raise typer.Exit(1)

        if "openrouter" in providers_used and not config.openrouter_api_key:
            log_error(
                "OPENROUTER_API_KEY not set but an OpenRouter provider is configured. "
                "Set OPENROUTER_API_KEY."
            )
            raise typer.Exit(1)

        if "gemini" in providers_used:
            # GeminiProvider supports API-key auth (GOOGLE_API_KEY/GEMINI_API_KEY) or ADC (requires GOOGLE_CLOUD_PROJECT).
            force_adc = bool(getattr(config.vision_judge, "force_adc", False))
            has_gemini_key = bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))

            if force_adc:
                if not config.google_project:
                    log_error(
                        "Gemini provider configured with force_adc=true but GOOGLE_CLOUD_PROJECT is missing. "
                        "Set GOOGLE_CLOUD_PROJECT (and optionally GOOGLE_CLOUD_REGION)."
                    )
                    raise typer.Exit(1)
            else:
                if not has_gemini_key and not config.google_project:
                    log_error(
                        "Gemini provider configured but no GOOGLE_API_KEY/GEMINI_API_KEY set and "
                        "GOOGLE_CLOUD_PROJECT is missing for ADC auth."
                    )
                    raise typer.Exit(1)

        console.print("\n[bold blue]TITAN-4-DESIGN Dataset Factory[/bold blue]")
        console.print(f"Mode: {'Public only' if public_only else 'All models'}")
        if max_tasks:
            console.print(f"Max tasks: {max_tasks}")
        console.print(
            "Concurrency: "
            f"tasks={config.budget.task_concurrency} • "
            f"vertex={config.budget.concurrency_vertex} • "
            f"build={config.budget.concurrency_build} • "
            f"render={config.budget.concurrency_render}"
        )
        if int(getattr(config.budget, 'task_concurrency', 1) or 1) <= 1:
            log_warning(
                "budget.task_concurrency is 1 → tasks will run one-at-a-time. "
                "Set it to 2–5 for throughput."
            )
        console.print()

        # Run pipeline
        result_run_id = asyncio.run(
            run_pipeline(
                config=config,
                run_id=run_id,
                public_only=public_only,
                max_tasks=max_tasks,
                resume_run_id=resume,
            )
        )

        # Generate a human-readable portal (shippable vs failed) for local review.
        run_dir = config.out_path / result_run_id
        try:
            index_path = build_portal(run_dir, include_edits=False)
            log_success(f"Portal created: {index_path}")
            console.print(f"Open (file): {index_path}")
            console.print(f"Tip: serve it with `titan-factory serve --run-id {result_run_id}`\n")
        except Exception as e:
            log_warning(f"Portal generation skipped: {e}")

        console.print()
        log_success(f"Pipeline complete! Run ID: {result_run_id}")
        console.print(f"Output: {config.out_path / result_run_id}")

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        raise typer.Exit(130)
    except Exception as e:
        log_error(str(e))
        raise typer.Exit(1)


@app.command()
def serve(
    run_id: str = typer.Option(
        ...,
        "--run-id",
        help="Run ID to serve locally (out/<run_id>)",
    ),
    port: int = typer.Option(
        8002,
        "--port",
        help="Port to bind the local server on",
    ),
    bind: str = typer.Option(
        "127.0.0.1",
        "--bind",
        help="Bind address (default: 127.0.0.1)",
    ),
    include_edits: bool = typer.Option(
        False,
        "--include-edits",
        help="Include edit tasks in the portal",
    ),
    config_path: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file (used to locate out/ directory)",
    ),
) -> None:
    """Serve a run directory locally (portal + logs + artifacts).

    This makes the portal viewable in the browser (required for logs.html fetch).
    """
    try:
        config = load_config(config_path)
        run_dir = config.out_path / run_id

        if not run_dir.exists():
            log_error(f"Run directory not found: {run_dir}")
            raise typer.Exit(1)

        # Always (re)build portal so it's never stale.
        try:
            build_portal(run_dir, include_edits=include_edits)
        except Exception as e:
            log_warning(f"Portal generation skipped: {e}")

        handler = partial(SimpleHTTPRequestHandler, directory=str(run_dir))

        class _ReusableTCPServer(socketserver.ThreadingTCPServer):
            allow_reuse_address = True

        url_base = f"http://{bind}:{port}"
        console.print("\n[bold]Serving run artifacts[/bold]")
        console.print(f"Dir: {run_dir}")
        console.print(f"Portal: {url_base}/portal/index.html")
        console.print(f"Logs:   {url_base}/portal/logs.html")
        console.print(f"DB:     {url_base}/manifest.db\n")
        console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

        with _ReusableTCPServer((bind, port), handler) as httpd:
            httpd.serve_forever()

    except KeyboardInterrupt:
        console.print("\n[yellow]Server stopped[/yellow]")
        raise typer.Exit(130)
    except OSError as e:
        log_error(f"Failed to start server on {bind}:{port}: {e}")
        console.print(f"Try a different port, e.g. `--port {port + 1}`")
        raise typer.Exit(1)
    except Exception as e:
        log_error(str(e))
        raise typer.Exit(1)


@app.command()
def serve_out(
    port: int = typer.Option(
        8003,
        "--port",
        help="Port to bind the local server on",
    ),
    bind: str = typer.Option(
        "127.0.0.1",
        "--bind",
        help="Bind address (default: 127.0.0.1)",
    ),
    config_path: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file (used to locate out/ directory)",
    ),
) -> None:
    """Serve the entire out/ directory with a run index.

    This gives you one stable port to browse:
    - multiple run portals (shippable vs failed)
    - logs and artifacts for any run
    """
    try:
        config = load_config(config_path)
        out_dir = config.out_path

        if not out_dir.exists():
            log_error(f"out/ directory not found: {out_dir}")
            raise typer.Exit(1)

        def _run_dirs() -> list[Path]:
            runs: list[Path] = []
            for p in out_dir.iterdir():
                if p.is_dir() and (p / "manifest.db").exists():
                    runs.append(p)
            runs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
            return runs

        def esc(s: str) -> str:
            return (
                (s or "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )

        class OutIndexHandler(SimpleHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                if self.path in ("/", "/index.html"):
                    runs = _run_dirs()
                    cards: list[str] = []

                    for i, d in enumerate(runs, 1):
                        run_id = d.name
                        mtime = datetime.fromtimestamp(d.stat().st_mtime).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                        has_portal = (d / "portal" / "index.html").exists()
                        portal_href = (
                            f"/{esc(run_id)}/portal/index.html" if has_portal else f"/{esc(run_id)}/"
                        )

                        cards.append(
                            f"""
                            <div class="card">
                              <div class="top">
                                <div class="title">#{i} <code>{esc(run_id)}</code></div>
                                <div class="meta">updated {esc(mtime)}</div>
                              </div>
                              <div class="links">
                                <a class="btn" href="{portal_href}">Open portal</a>
                                <a class="btn" href="/{esc(run_id)}/run.log" target="_blank" rel="noopener">run.log</a>
                                <a class="btn" href="/{esc(run_id)}/manifest.db" target="_blank" rel="noopener">manifest.db</a>
                              </div>
                            </div>
                            """
                        )

                    cards_html = "".join(cards) if cards else '<div class="card">No runs found.</div>'

                    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>TITAN Factory Runs</title>
    <style>
      :root {{
        --bg: #0b0d12;
        --border: rgba(255,255,255,0.12);
        --text: rgba(255,255,255,0.92);
        --muted: rgba(255,255,255,0.72);
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
      header {{ max-width: 1100px; margin: 0 auto; padding: 26px 18px 12px; }}
      h1 {{ margin: 0 0 8px; font-size: 20px; letter-spacing: -0.02em; }}
      p {{ margin: 0; color: var(--muted); font-size: 13px; line-height: 1.45; }}
      main {{ max-width: 1100px; margin: 0 auto; padding: 0 18px 40px; }}
      .grid {{ display: grid; gap: 12px; }}
      .card {{
        border: 1px solid var(--border);
        background: rgba(255,255,255,0.04);
        border-radius: 14px;
        padding: 12px 12px;
      }}
      .top {{ display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; }}
      .title {{ font-weight: 700; }}
      .meta {{ color: var(--muted); font-size: 12px; }}
      code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }}
      .links {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 10px; }}
      .btn {{
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
      .btn:hover {{ border-color: rgba(255,255,255,0.18); background: rgba(255,255,255,0.06); }}
    </style>
  </head>
  <body>
    <header>
      <h1>TITAN Factory Runs</h1>
      <p>Browsing <code>{esc(str(out_dir))}</code>. Click a run to open its portal.</p>
    </header>
    <main>
      <div class="grid">{cards_html}</div>
    </main>
  </body>
</html>
"""

                    body = html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                return super().do_GET()

            def log_message(self, format: str, *args) -> None:  # noqa: A003 - stdlib signature
                return

        handler = partial(OutIndexHandler, directory=str(out_dir))

        class _ReusableTCPServer(socketserver.ThreadingTCPServer):
            allow_reuse_address = True

        url_base = f"http://{bind}:{port}"
        console.print("\n[bold]Serving all TITAN runs[/bold]")
        console.print(f"Dir:   {out_dir}")
        console.print(f"Index: {url_base}/\n")
        console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

        with _ReusableTCPServer((bind, port), handler) as httpd:
            httpd.serve_forever()

    except KeyboardInterrupt:
        console.print("\n[yellow]Server stopped[/yellow]")
        raise typer.Exit(130)
    except OSError as e:
        log_error(f"Failed to start server on {bind}:{port}: {e}")
        console.print(f"Try a different port, e.g. `--port {port + 1}`")
        raise typer.Exit(1)
    except Exception as e:
        log_error(str(e))
        raise typer.Exit(1)


@app.command()
def export(
    run_id: str = typer.Option(
        ...,
        "--run-id",
        help="Run ID to export",
    ),
    min_score: Optional[float] = typer.Option(
        None,
        "--min-score",
        help="Only export winners with score >= this value (e.g. 9.0)",
    ),
    config_path: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Export training data from a completed run."""
    try:
        config = load_config(config_path)
        run_dir = config.out_path / run_id

        if not run_dir.exists():
            log_error(f"Run directory not found: {run_dir}")
            raise typer.Exit(1)

        asyncio.run(export_run(run_dir, config, min_score=min_score))
        log_success(f"Export complete! Check {run_dir}")

    except Exception as e:
        log_error(str(e))
        raise typer.Exit(1)


@app.command()
def backfill(
    run_id: str = typer.Option(
        ...,
        "--run-id",
        help="Run ID to backfill no_winner tasks",
    ),
    config_path: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Backfill winners for tasks marked no_winner (skip-judge salvage)."""
    try:
        config = load_config(config_path)
        asyncio.run(backfill_no_winner(config=config, run_id=run_id))
        log_success(f"Backfill complete for run: {run_id}")
    except Exception as e:
        log_error(str(e))
        raise typer.Exit(1)


@app.command()
def gallery(
    run_id: str = typer.Option(
        ...,
        "--run-id",
        help="Run ID to generate a screenshot gallery for",
    ),
    min_score: Optional[float] = typer.Option(
        None,
        "--min-score",
        help="Minimum winner score to include in the gallery (e.g. 9.0)",
    ),
    config_path: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file (used to locate out/ directory)",
    ),
) -> None:
    """Generate a static HTML gallery of selected winners for a run."""
    try:
        config = load_config(config_path)
        run_dir = config.out_path / run_id

        if not run_dir.exists():
            log_error(f"Run directory not found: {run_dir}")
            raise typer.Exit(1)

        index_path = build_gallery(run_dir, min_score=min_score)
        log_success(f"Gallery created: {index_path}")
        console.print(f"Open: {index_path}")

    except Exception as e:
        log_error(str(e))
        raise typer.Exit(1)


@app.command()
def portal(
    run_id: str = typer.Option(
        ...,
        "--run-id",
        help="Run ID to generate a portal for",
    ),
    include_edits: bool = typer.Option(
        False,
        "--include-edits",
        help="Include edit tasks in the portal",
    ),
    config_path: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file (used to locate out/ directory)",
    ),
) -> None:
    """Generate a static HTML portal with separate shippable/failed sections."""
    try:
        config = load_config(config_path)
        run_dir = config.out_path / run_id

        if not run_dir.exists():
            log_error(f"Run directory not found: {run_dir}")
            raise typer.Exit(1)

        index_path = build_portal(run_dir, include_edits=include_edits)
        log_success(f"Portal created: {index_path}")
        console.print(f"Open: {index_path}")

    except Exception as e:
        log_error(str(e))
        raise typer.Exit(1)


@app.command()
def stats(
    run_id: str = typer.Option(
        ...,
        "--run-id",
        help="Run ID to get stats for",
    ),
    config_path: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Show statistics for a run."""
    try:
        config = load_config(config_path)
        run_dir = config.out_path / run_id

        if not run_dir.exists():
            log_error(f"Run directory not found: {run_dir}")
            raise typer.Exit(1)

        result = asyncio.run(export_stats(run_dir))

        console.print(f"\n[bold]Stats for run: {run_id}[/bold]\n")

        # Task stats table
        if "tasks" in result:
            table = Table(title="Tasks by Status")
            table.add_column("Status", style="cyan")
            table.add_column("Count", justify="right")
            for status, count in result["tasks"].items():
                table.add_row(status, str(count))
            console.print(table)
            console.print()

        # Candidate stats
        if "candidates" in result:
            table = Table(title="Candidates by Status")
            table.add_column("Status", style="cyan")
            table.add_column("Count", justify="right")
            for status, count in result["candidates"].items():
                table.add_row(status, str(count))
            console.print(table)
            console.print()

        # Summary
        if "avg_winner_score" in result:
            console.print(f"[green]Average winner score:[/green] {result['avg_winner_score']}")

        if "winners_by_model" in result:
            console.print("\n[bold]Winners by model:[/bold]")
            for model, count in result["winners_by_model"].items():
                console.print(f"  {model}: {count}")

    except Exception as e:
        log_error(str(e))
        raise typer.Exit(1)


@app.command("generate-prompts")
def generate_prompts(
    config_path: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Generate niches and tasks (without running pipeline)."""
    try:
        config = load_config(config_path)

        # Generate niches
        niches = generate_niches()
        console.print(f"Generated {len(niches)} niches")

        # Save
        save_niches(config)
        path, count = save_tasks(config)

        console.print(f"Generated {count} tasks")
        console.print(f"Output: {config.prompts_path}")

    except Exception as e:
        log_error(str(e))
        raise typer.Exit(1)


@app.command("list-niches")
def list_niches() -> None:
    """List all available niches."""
    niches = generate_niches()

    table = Table(title=f"Available Niches ({len(niches)})")
    table.add_column("ID", style="cyan")
    table.add_column("Vertical", style="green")
    table.add_column("Pattern", style="yellow")
    table.add_column("Description")

    for niche in niches[:20]:  # Show first 20
        table.add_row(niche.id, niche.vertical, niche.pattern, niche.description)

    console.print(table)

    if len(niches) > 20:
        console.print(f"... and {len(niches) - 20} more")


@app.command("validate-config")
def validate_config(
    config_path: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Validate configuration."""
    try:
        config = load_config(config_path)

        console.print("[bold]Configuration Summary[/bold]\n")

        # Check environment for providers actually configured in this config
        providers_used: set[str] = set()
        if config.planner.model:
            providers_used.add(config.planner.provider)
        for gen in config.ui_generators:
            if gen.model:
                providers_used.add(gen.provider)
        if config.patcher.model:
            providers_used.add(config.patcher.provider)
        if config.vision_judge.model:
            providers_used.add(config.vision_judge.provider)

        checks: list[tuple[str, bool]] = []
        if "vertex" in providers_used:
            checks.append(("GOOGLE_CLOUD_PROJECT", bool(config.google_project)))
            checks.append(("GOOGLE_CLOUD_REGION", bool(config.google_region)))

        if "openrouter" in providers_used:
            checks.append(("OPENROUTER_API_KEY", bool(config.openrouter_api_key)))

        if "gemini" in providers_used:
            has_gemini_key = bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
            # Gemini can use API key mode without ADC; otherwise it requires ADC+project.
            checks.append(("GEMINI_API_KEY/GOOGLE_API_KEY", has_gemini_key or bool(config.google_project)))

        table = Table(title="Environment")
        table.add_column("Variable")
        table.add_column("Status")

        for name, ok in checks:
            status = "[green]OK[/green]" if ok else "[red]MISSING[/red]"
            table.add_row(name, status)

        console.print(table)
        console.print()

        # Models
        console.print("[bold]Models[/bold]")
        console.print(f"  Planner: {config.planner.model} ({config.planner.provider})")
        console.print(f"  UI Generators: {len(config.ui_generators)}")
        for gen in config.ui_generators:
            pub = "[green]pub[/green]" if gen.publishable else "[yellow]priv[/yellow]"
            console.print(f"    - {gen.model} ({gen.provider}) {pub}")
        console.print(f"  Patcher: {config.patcher.model} ({config.patcher.provider})")
        console.print(f"  Vision Judge: {config.vision_judge.model or '[yellow]heuristic fallback[/yellow]'}")
        console.print()

        # Pipeline settings
        console.print("[bold]Pipeline[/bold]")
        console.print(f"  Score threshold: {config.pipeline.vision_score_threshold}")
        console.print(f"  Max fix rounds: {config.pipeline.max_fix_rounds}")
        console.print(f"  Tasks per niche: {config.pipeline.tasks_per_niche}")
        console.print(f"  Total niches: {config.pipeline.total_niches}")

        all_ok = all(ok for _, ok in checks)
        if all_ok:
            console.print("\n[green]Configuration valid![/green]")
        else:
            console.print("\n[red]Configuration incomplete. Set missing environment variables.[/red]")
            raise typer.Exit(1)

    except Exception as e:
        log_error(str(e))
        raise typer.Exit(1)


def main() -> None:
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
