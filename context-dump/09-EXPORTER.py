"""Exporter module - exports training data to JSONL format."""

import hashlib
import json
import random
from pathlib import Path

import aiosqlite

from titan_factory.config import Config
from titan_factory.schema import Candidate, GeneratedFile, JudgeScore, TeacherModel, UISpec
from titan_factory.utils import ensure_dir, log_info, log_success

# === Chat Template ===
SYSTEM_MESSAGE = """You are Titan 4 Design. Produce JSON only: {"ui_spec":..., "files":[...]}.
Use Next.js App Router + TypeScript + Tailwind. No UI libraries. No markdown."""


def format_training_example(
    task_prompt: str,
    ui_spec: UISpec,
    files: list[GeneratedFile],
    is_edit: bool = False,
    code_old: str | None = None,
) -> dict:
    """Format a training example in chat format.

    Args:
        task_prompt: Original task prompt
        ui_spec: UI specification
        files: Generated files
        is_edit: Whether this is an edit task
        code_old: Original code for edit tasks

    Returns:
        Training example dict
    """
    # Build user message
    if is_edit and code_old:
        user_content = f"{task_prompt}\n\n<CODE_OLD>\n{code_old}\n</CODE_OLD>"
    else:
        user_content = task_prompt

    # Build assistant response (JSON only)
    assistant_response = {
        "ui_spec": ui_spec.model_dump(),
        "files": [f.model_dump() for f in files],
    }

    return {
        "messages": [
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": json.dumps(assistant_response)},
        ]
    }


def _compute_publishable_from_db(
    publishable_flag: bool,
    planner_model_json: str | None,
    patcher_models_json: str | None,
) -> bool:
    """Compute publishable status from DB fields.

    Uses the full teacher chain to determine if candidate is publishable.
    A candidate is only publishable if ALL models in the chain are publishable.

    Args:
        publishable_flag: The generator's publishable flag
        planner_model_json: JSON string of planner TeacherModel
        patcher_models_json: JSON string of patcher TeacherModel list

    Returns:
        True if all models in chain are publishable
    """
    if not publishable_flag:
        return False

    # Check planner
    if planner_model_json:
        try:
            planner_data = json.loads(planner_model_json)
            if not planner_data.get("publishable", True):
                return False
        except (json.JSONDecodeError, KeyError):
            pass

    # Check all patchers
    if patcher_models_json:
        try:
            patchers = json.loads(patcher_models_json)
            for patcher in patchers:
                if not patcher.get("publishable", True):
                    return False
        except (json.JSONDecodeError, KeyError):
            pass

    return True


async def load_candidates_from_manifest(manifest_path: Path) -> list[dict]:
    """Load selected candidates from manifest database.

    Args:
        manifest_path: Path to manifest.db

    Returns:
        List of candidate records with task info including original prompt
    """
    async with aiosqlite.connect(manifest_path) as conn:
        # Get tasks with selected candidates, including original prompt and teacher chain
        cursor = await conn.execute("""
            SELECT
                t.id as task_id,
                t.niche_id,
                t.page_type,
                t.prompt,
                t.is_edit,
                t.code_old,
                t.ui_spec,
                c.id as candidate_id,
                c.files,
                c.score,
                c.publishable,
                c.planner_model,
                c.patcher_models
            FROM tasks t
            JOIN candidates c ON t.selected_candidate_id = c.id
            WHERE t.status = 'completed'
        """)

        records = []
        async for row in cursor:
            # Compute publishable from full teacher chain
            publishable = _compute_publishable_from_db(
                publishable_flag=bool(row[10]),
                planner_model_json=row[11],
                patcher_models_json=row[12],
            )

            records.append({
                "task_id": row[0],
                "niche_id": row[1],
                "page_type": row[2],
                "prompt": row[3],           # Original prompt stored in DB
                "is_edit": bool(row[4]),    # Stored is_edit flag
                "code_old": row[5],         # Stored code_old for edits
                "ui_spec": json.loads(row[6]) if row[6] else None,
                "candidate_id": row[7],
                "files": json.loads(row[8]) if row[8] else [],
                "score": row[9],
                "publishable": publishable,  # Computed from full teacher chain
            })

        return records


def split_records(
    records: list[dict],
    config: Config,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split records into train, validation, and holdout sets.

    Args:
        records: All records
        config: Configuration with split settings

    Returns:
        Tuple of (train, validation, holdout) record lists
    """
    # Determine holdout niches
    all_niches = list(set(r["niche_id"] for r in records))
    random.shuffle(all_niches)

    if config.export.holdout_niche_ids:
        holdout_niches = set(config.export.holdout_niche_ids)
    else:
        holdout_count = config.export.holdout_niches
        holdout_niches = set(all_niches[:holdout_count])

    log_info(f"Holding out {len(holdout_niches)} niches for generalization testing")

    # Split by niche
    holdout = [r for r in records if r["niche_id"] in holdout_niches]
    remaining = [r for r in records if r["niche_id"] not in holdout_niches]

    # Random validation split within remaining
    random.shuffle(remaining)
    val_count = int(len(remaining) * config.export.validation_split)
    validation = remaining[:val_count]
    train = remaining[val_count:]

    log_info(f"Split: {len(train)} train, {len(validation)} validation, {len(holdout)} holdout")

    return train, validation, holdout


async def export_run(
    run_dir: Path,
    config: Config,
) -> None:
    """Export a run's results to training format.

    Creates both public/ and private/ exports.

    Args:
        run_dir: Run output directory
        config: Configuration
    """
    manifest_path = run_dir / "manifest.db"

    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    # Load all selected candidates
    records = await load_candidates_from_manifest(manifest_path)
    log_info(f"Loaded {len(records)} selected candidates")

    if not records:
        log_info("No records to export")
        return

    # Write rich records (all candidates for audit)
    rich_path = run_dir / "rich_records.jsonl"
    with open(rich_path, "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    log_info(f"Wrote {len(records)} rich records to {rich_path}")

    # Export public (publishable only)
    public_records = [r for r in records if r["publishable"]]
    if public_records:
        await _export_track(
            public_records,
            run_dir / "public",
            config,
            "public",
        )

    # Export private (all)
    await _export_track(
        records,
        run_dir / "private",
        config,
        "private",
    )


async def _export_track(
    records: list[dict],
    output_dir: Path,
    config: Config,
    track_name: str,
) -> None:
    """Export a single track (public or private).

    Args:
        records: Records to export
        output_dir: Output directory
        config: Configuration
        track_name: Track name for logging
    """
    ensure_dir(output_dir)

    # Split into train/val/holdout
    train, validation, holdout = split_records(records, config)

    # Write train.jsonl
    train_path = output_dir / "train.jsonl"
    _write_training_jsonl(train, train_path)

    # Write valid.jsonl
    valid_path = output_dir / "valid.jsonl"
    _write_training_jsonl(validation, valid_path)

    # Write holdout.jsonl (for later evaluation)
    holdout_path = output_dir / "holdout.jsonl"
    _write_training_jsonl(holdout, holdout_path)

    log_success(
        f"Exported {track_name}: "
        f"{len(train)} train, {len(validation)} valid, {len(holdout)} holdout"
    )


def _write_training_jsonl(records: list[dict], path: Path) -> None:
    """Write records to training JSONL format.

    Uses the original stored prompt rather than reconstructing it.
    This ensures training data matches what was actually generated.

    Args:
        records: Records to write
        path: Output path
    """
    with open(path, "w") as f:
        for record in records:
            if not record["ui_spec"] or not record["files"]:
                continue

            # Reconstruct objects
            ui_spec = UISpec.model_validate(record["ui_spec"])
            files = [GeneratedFile.model_validate(f) for f in record["files"]]

            # Use stored prompt and edit info (not reconstructed!)
            task_prompt = record.get("prompt")
            is_edit = record.get("is_edit", False)
            code_old = record.get("code_old")

            # Fallback to reconstruction only if prompt wasn't stored
            # (for backwards compatibility with older manifests)
            if not task_prompt:
                task_prompt = _reconstruct_prompt(record, ui_spec)

            # Format training example
            example = format_training_example(
                task_prompt=task_prompt,
                ui_spec=ui_spec,
                files=files,
                is_edit=is_edit,
                code_old=code_old if is_edit else None,
            )

            f.write(json.dumps(example) + "\n")


def _reconstruct_prompt(record: dict, ui_spec: UISpec) -> str:
    """Reconstruct a task prompt from the UI spec.

    Args:
        record: Record dict
        ui_spec: UI specification

    Returns:
        Reconstructed prompt
    """
    # Build a prompt that matches the style of the original
    page_type = record["page_type"]
    brand = ui_spec.brand
    content = ui_spec.content

    if page_type == "landing":
        return f"""Create a {brand.mood} themed landing page for {content.business_name}, a {ui_spec.niche.vertical.replace("_", " ")} in {content.city}.

Style: {", ".join(brand.style_keywords)}, {brand.accent} accent
CTA: "{ui_spec.cta.primary}"

{content.offer}. Target audience: {content.audience}.

Make it premium with: {", ".join(content.highlights[:3])}. No UI libraries."""

    elif page_type == "directory_home":
        return f"""Create a {brand.mood} directory homepage for finding {ui_spec.niche.vertical.replace("_", " ")} services.

Style: {", ".join(brand.style_keywords)}, {brand.accent} accent

Include search, filters, and listing grid. No UI libraries."""

    elif page_type in ("city_index", "category_index"):
        return f"""Create a {brand.mood} {page_type.replace("_", " ")} page for {ui_spec.niche.vertical.replace("_", " ")} in {content.city}.

Style: {", ".join(brand.style_keywords)}, {brand.accent} accent

Include filterable listings grid and local info. No UI libraries."""

    elif page_type == "listing_profile":
        return f"""Create a {brand.mood} listing detail page for {content.business_name}.

Style: {", ".join(brand.style_keywords)}, {brand.accent} accent
CTA: "{ui_spec.cta.primary}"

Include gallery, info, services, reviews, and contact. No UI libraries."""

    elif page_type == "admin_dashboard":
        return f"""Create a {brand.mood} admin dashboard for managing a {ui_spec.niche.vertical.replace("_", " ")} business.

Style: {", ".join(brand.style_keywords)}, {brand.accent} accent

Include stats, activity table, and charts. No UI libraries."""

    else:
        return f"""Create a {page_type} page for {content.business_name}.

Style: {brand.mood}, {", ".join(brand.style_keywords)}, {brand.accent} accent

No UI libraries."""


async def export_stats(run_dir: Path) -> dict:
    """Get statistics for a run.

    Args:
        run_dir: Run directory

    Returns:
        Stats dictionary
    """
    manifest_path = run_dir / "manifest.db"

    if not manifest_path.exists():
        return {"error": "Manifest not found"}

    async with aiosqlite.connect(manifest_path) as conn:
        # Task counts by status
        cursor = await conn.execute(
            "SELECT status, COUNT(*) FROM tasks GROUP BY status"
        )
        task_stats = dict(await cursor.fetchall())

        # Candidate counts by status
        cursor = await conn.execute(
            "SELECT status, COUNT(*) FROM candidates GROUP BY status"
        )
        candidate_stats = dict(await cursor.fetchall())

        # Average score of selected
        cursor = await conn.execute(
            "SELECT AVG(score) FROM candidates WHERE status = 'selected'"
        )
        avg_score = (await cursor.fetchone())[0]

        # Winners by model
        cursor = await conn.execute(
            "SELECT generator_model, COUNT(*) FROM candidates WHERE status = 'selected' GROUP BY generator_model"
        )
        winners_by_model = dict(await cursor.fetchall())

    return {
        "tasks": task_stats,
        "candidates": candidate_stats,
        "avg_winner_score": round(avg_score, 2) if avg_score else 0,
        "winners_by_model": winners_by_model,
    }
