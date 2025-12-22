"""Command-line interface for TITAN Factory."""

import asyncio
import json
import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from titan_factory.config import load_config
from titan_factory.exporter import export_run, export_stats
from titan_factory.gallery import build_gallery
from titan_factory.orchestrator import backfill_no_winner, run_pipeline
from titan_factory.promptgen import generate_niches, generate_tasks, save_niches, save_tasks
from titan_factory.utils import log_error, log_info, log_success

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
            has_gemini_key = bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
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
