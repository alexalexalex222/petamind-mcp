"""Orchestrator module - coordinates the full pipeline."""

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

import aiosqlite

from titan_factory.config import Config
from titan_factory.judge import score_all_candidates
from titan_factory.patcher import patch_candidate
from titan_factory.planner import generate_ui_spec
from titan_factory.promptgen import (
    generate_task_prompt,
    load_tasks,
    save_niches,
    save_tasks,
    stable_hash,
)
from titan_factory.renderer import render_all_candidates
from titan_factory.schema import Candidate, CandidateStatus, NicheDefinition, PageType, Task, TeacherModel
from titan_factory.uigen import generate_all_candidates
from titan_factory.utils import ensure_dir, log_error, log_info, log_success, log_warning
from titan_factory.validator import validate_with_retry


class TaskManifest:
    """Tracks task and candidate state in SQLite."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Initialize database."""
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                niche_id TEXT,
                page_type TEXT,
                prompt TEXT,
                seed INTEGER,
                is_edit INTEGER DEFAULT 0,
                code_old TEXT,
                status TEXT DEFAULT 'pending',
                ui_spec TEXT,
                selected_candidate_id TEXT,
                error TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS candidates (
                id TEXT PRIMARY KEY,
                task_id TEXT,
                generator_model TEXT,
                variant_index INTEGER,
                status TEXT,
                files TEXT,
                build_logs TEXT,
                fix_rounds INTEGER DEFAULT 0,
                screenshot_paths TEXT,
                score REAL,
                score_details TEXT,
                publishable INTEGER DEFAULT 1,
                error TEXT,
                planner_model TEXT,
                patcher_models TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        await self._conn.commit()

    async def close(self) -> None:
        """Close database connection."""
        if self._conn:
            await self._conn.close()

    async def get_task_status(self, task_id: str) -> str | None:
        """Get task status."""
        if not self._conn:
            return None
        cursor = await self._conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (task_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def save_task(self, task: Task, status: str = "pending", ui_spec: str = "") -> None:
        """Save task state including the original prompt for export."""
        if not self._conn:
            return
        now = datetime.utcnow().isoformat()
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO tasks
            (id, niche_id, page_type, prompt, seed, is_edit, code_old, status, ui_spec, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.niche_id,
                task.page_type.value,
                task.prompt,
                task.seed,
                1 if task.is_edit else 0,
                task.code_old,
                status,
                ui_spec,
                now,
                now,
            ),
        )
        await self._conn.commit()

    async def update_task(self, task_id: str, **kwargs) -> None:
        """Update task fields."""
        if not self._conn:
            return
        kwargs["updated_at"] = datetime.utcnow().isoformat()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        await self._conn.execute(
            f"UPDATE tasks SET {sets} WHERE id = ?",
            (*kwargs.values(), task_id),
        )
        await self._conn.commit()

    async def save_candidate(self, candidate: Candidate) -> None:
        """Save candidate state including teacher chain for publishable tracking."""
        if not self._conn:
            return
        now = datetime.utcnow().isoformat()
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO candidates
            (id, task_id, generator_model, variant_index, status, files, build_logs,
             fix_rounds, screenshot_paths, score, score_details, publishable, error,
             planner_model, patcher_models, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.id,
                candidate.task_id,
                candidate.generator_model,
                candidate.variant_index,
                candidate.status.value,
                json.dumps([f.model_dump() for f in candidate.files]),
                candidate.build_logs,
                candidate.fix_rounds,
                json.dumps(candidate.screenshot_paths),
                candidate.score,
                candidate.score_details.model_dump_json() if candidate.score_details else None,
                1 if candidate.publishable else 0,
                candidate.error,
                candidate.planner_model.model_dump_json() if candidate.planner_model else None,
                json.dumps([p.model_dump() for p in candidate.patcher_models]),
                now,
                now,
            ),
        )
        await self._conn.commit()

    async def get_completed_task_ids(self) -> set[str]:
        """Get IDs of completed tasks."""
        if not self._conn:
            return set()
        cursor = await self._conn.execute(
            "SELECT id FROM tasks WHERE status = 'completed'"
        )
        rows = await cursor.fetchall()
        return {row[0] for row in rows}


class ResponseCache:
    """Caches EXTRACTED JSON outputs in SQLite.

    CRITICAL: This cache stores only the extracted/parsed JSON output,
    NOT the raw model response. This is important because:

    1. "Thinking" models (like Kimi) include chain-of-thought reasoning
       in their raw responses that we MUST NOT store for training data
    2. Raw responses may be very large; extracted JSON is compact
    3. We only need the structured output for resumability

    The providers extract JSON immediately after receiving a response,
    and only that extracted JSON should ever reach this cache.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Initialize database."""
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS responses (
                hash TEXT PRIMARY KEY,
                provider TEXT,
                model TEXT,
                extracted_json TEXT,
                created_at TEXT
            )
        """)
        await self._conn.commit()

    async def close(self) -> None:
        """Close database connection."""
        if self._conn:
            await self._conn.close()

    async def get(self, hash_key: str) -> str | None:
        """Get cached extracted JSON output."""
        if not self._conn:
            return None
        cursor = await self._conn.execute(
            "SELECT extracted_json FROM responses WHERE hash = ?", (hash_key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def set(
        self,
        hash_key: str,
        provider: str,
        model: str,
        extracted_json: str,
    ) -> None:
        """Cache extracted JSON output.

        Args:
            hash_key: Cache key (hash of prompt)
            provider: Provider name
            model: Model name
            extracted_json: The EXTRACTED JSON output only,
                           never the raw model response
        """
        if not self._conn:
            return
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO responses
            (hash, provider, model, extracted_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (hash_key, provider, model, extracted_json, datetime.utcnow().isoformat()),
        )
        await self._conn.commit()


class PipelineOrchestrator:
    """Orchestrates the full data generation pipeline."""

    def __init__(
        self,
        config: Config,
        run_id: str | None = None,
        public_only: bool = False,
        max_tasks: int | None = None,
        resume: bool = False,
    ) -> None:
        """Initialize orchestrator.

        Args:
            config: Application configuration
            run_id: Optional run ID (generated if not provided)
            public_only: Only use publishable models
            max_tasks: Maximum tasks to process
            resume: Whether to resume from previous state
        """
        self.config = config
        self.run_id = run_id or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.public_only = public_only
        self.max_tasks = max_tasks or config.budget.max_total_tasks
        self.resume = resume

        # Set up directories
        self.run_dir = config.out_path / self.run_id
        ensure_dir(self.run_dir)

        # Initialize state managers
        self.manifest = TaskManifest(self.run_dir / "manifest.db")
        self.cache = ResponseCache(self.run_dir / "cache.db")

        # Tracking
        self.tasks_processed = 0
        self.candidates_generated = 0
        self.winners_selected = 0

        # Queue for edit tasks (populated dynamically from successful landing pages)
        self._edit_task_queue: list[Task] = []

    async def run(self) -> None:
        """Run the full pipeline."""
        log_info(f"Starting pipeline run: {self.run_id}")
        log_info(f"Output directory: {self.run_dir}")

        try:
            # Initialize databases
            await self.manifest.init()
            await self.cache.init()

            # Generate prompts
            log_info("Generating niches and tasks...")
            save_niches(self.config)
            save_tasks(self.config)

            # Load tasks
            tasks = load_tasks(self.config)
            log_info(f"Loaded {len(tasks)} tasks")

            # Filter if resuming
            if self.resume:
                completed = await self.manifest.get_completed_task_ids()
                tasks = [t for t in tasks if t.id not in completed]
                log_info(f"Resuming with {len(tasks)} remaining tasks")

            # Apply max_tasks limit
            if self.max_tasks:
                tasks = tasks[: self.max_tasks]
                log_info(f"Limited to {len(tasks)} tasks")

            # Process tasks
            for task in tasks:
                try:
                    await self._process_task(task)
                except Exception as e:
                    log_error(f"Task {task.id} failed: {e}")
                    await self.manifest.update_task(task.id, status="failed", error=str(e))

            # Process edit tasks (generated dynamically from successful landing pages)
            if self._edit_task_queue:
                log_info(f"Processing {len(self._edit_task_queue)} edit tasks...")
                for edit_task in self._edit_task_queue:
                    try:
                        await self._process_task(edit_task)
                    except Exception as e:
                        log_error(f"Edit task {edit_task.id} failed: {e}")
                        await self.manifest.update_task(
                            edit_task.id, status="failed", error=str(e)
                        )

            # Export results
            log_info("Exporting results...")
            from titan_factory.exporter import export_run

            await export_run(self.run_dir, self.config)

            log_success(f"Pipeline complete! Processed {self.tasks_processed} tasks")
            log_success(f"Winners selected: {self.winners_selected}")

        finally:
            await self.manifest.close()
            await self.cache.close()

    async def _process_task(self, task: Task) -> None:
        """Process a single task through the pipeline.

        Args:
            task: Task to process
        """
        log_info(f"Processing task {task.id} ({task.page_type.value})")

        # Save initial state
        await self.manifest.save_task(task, status="planning")

        # Stage 1: Generate UI_SPEC
        try:
            ui_spec = await generate_ui_spec(task, self.config)
            await self.manifest.update_task(
                task.id,
                status="generating",
                ui_spec=ui_spec.model_dump_json(),
            )
        except Exception as e:
            log_error(f"Task {task.id}: Planning failed - {e}")
            await self.manifest.update_task(task.id, status="failed", error=str(e))
            return

        # Stage 2: Generate candidates
        # Create planner model info for teacher chain tracking
        planner_model = TeacherModel(
            provider=self.config.planner.provider,
            model=self.config.planner.model or "unknown",
            publishable=self.config.planner.publishable,
        )

        candidates: list[Candidate] = []
        async for candidate in generate_all_candidates(
            task, ui_spec, self.config, self.public_only
        ):
            # Attach planner model info to candidate for publishable tracking
            candidate.planner_model = planner_model
            candidates.append(candidate)
            await self.manifest.save_candidate(candidate)
            self.candidates_generated += 1

        if not candidates:
            log_error(f"Task {task.id}: No candidates generated")
            await self.manifest.update_task(task.id, status="failed", error="No candidates")
            return

        # Stage 3: Validate (build) candidates
        log_info(f"Task {task.id}: Validating {len(candidates)} candidates...")

        for i, candidate in enumerate(candidates):
            if candidate.status == CandidateStatus.DISCARDED:
                continue

            success, updated_candidate = await validate_with_retry(
                candidate,
                self.config,
                patcher_fn=patch_candidate,
            )

            # Replace in list to ensure we have the patched version
            candidates[i] = updated_candidate
            await self.manifest.save_candidate(updated_candidate)

        # Stage 4: Render passing candidates
        passing = [c for c in candidates if c.status == CandidateStatus.BUILD_PASSED]

        if not passing:
            log_warning(f"Task {task.id}: No candidates passed build")
            await self.manifest.update_task(task.id, status="no_passing_candidates")
            self.tasks_processed += 1
            return

        candidates = await render_all_candidates(candidates, self.run_dir, self.config)

        for candidate in candidates:
            await self.manifest.save_candidate(candidate)

        # Stage 5: Score candidates
        candidates = await score_all_candidates(candidates, self.config)

        for candidate in candidates:
            await self.manifest.save_candidate(candidate)

        # Stage 6: Select winner
        winner = self._select_winner(candidates)

        if winner:
            winner.status = CandidateStatus.SELECTED
            await self.manifest.save_candidate(winner)
            await self.manifest.update_task(
                task.id,
                status="completed",
                selected_candidate_id=winner.id,
            )
            self.winners_selected += 1
            log_success(
                f"Task {task.id}: Selected winner {winner.id} "
                f"(score: {winner.score:.1f})"
            )

            # Create and queue an edit task if this was a landing page
            edit_task = self._create_edit_task_from_winner(task, winner)
            if edit_task:
                # Check if edit task already completed (for resume)
                existing_status = await self.manifest.get_task_status(edit_task.id)
                if existing_status != "completed":
                    log_info(f"Queueing edit task {edit_task.id} using winner's code")
                    self._edit_task_queue.append(edit_task)
        else:
            await self.manifest.update_task(task.id, status="no_winner")
            log_warning(f"Task {task.id}: No winner selected")

        self.tasks_processed += 1

    def _create_edit_task_from_winner(
        self,
        original_task: Task,
        winner: Candidate,
    ) -> Task | None:
        """Create an edit task using code from a successful landing page.

        This ensures edit tasks have real code_old instead of placeholders,
        which produces higher quality training data for refactoring tasks.

        Args:
            original_task: The landing page task that produced the winner
            winner: The winning candidate with generated code

        Returns:
            New edit task, or None if winner has no usable code
        """
        # Only create edit tasks from landing pages
        if original_task.page_type != PageType.LANDING:
            return None

        # Get the main page code
        code_old = None
        for f in winner.files:
            if f.path == "app/page.tsx":
                code_old = f.content
                break

        if not code_old:
            log_warning(f"Task {original_task.id}: No app/page.tsx found in winner")
            return None

        # Create a deterministic edit task ID
        edit_seed = stable_hash(f"{original_task.niche_id}:edit:{original_task.id}")
        task_id = hashlib.sha256(
            f"{original_task.niche_id}:edit:{edit_seed}".encode()
        ).hexdigest()[:16]

        # Build niche definition for prompt generation
        niche = NicheDefinition(
            id=original_task.niche_id,
            vertical=original_task.niche_id.rsplit("_", 1)[0],
            pattern=original_task.niche_id.rsplit("_", 1)[-1],
            description="",  # Not needed for edit prompt
        )

        # Generate edit prompt with real code
        edit_prompt = generate_task_prompt(
            niche=niche,
            page_type=PageType.EDIT,
            seed=edit_seed,
            is_edit=True,
            code_old=code_old,
        )

        return Task(
            id=task_id,
            niche_id=original_task.niche_id,
            page_type=PageType.EDIT,
            seed=edit_seed,
            prompt=edit_prompt,
            is_edit=True,
            code_old=code_old,
        )

    def _select_winner(self, candidates: list[Candidate]) -> Candidate | None:
        """Select the best candidate.

        Selection criteria:
        1. Must have passed build
        2. Must meet score threshold
        3. Highest score wins
        4. Tie-break: fewer files, smaller code, fewer fix rounds

        Args:
            candidates: List of candidates

        Returns:
            Best candidate, or None
        """
        threshold = self.config.pipeline.vision_score_threshold

        # Filter to scored candidates above threshold
        eligible = [
            c for c in candidates
            if c.status == CandidateStatus.SCORED
            and c.score is not None
            and c.score >= threshold
        ]

        if not eligible:
            # Check if any passed at all
            scored = [c for c in candidates if c.status == CandidateStatus.SCORED]
            if scored:
                log_warning(
                    f"No candidates met threshold {threshold}. "
                    f"Best score: {max(c.score or 0 for c in scored):.1f}"
                )
            return None

        # Sort by score (desc), then by file count (asc), code size (asc), fix rounds (asc)
        def sort_key(c: Candidate) -> tuple:
            total_size = sum(len(f.content) for f in c.files)
            return (
                -(c.score or 0),  # Higher score better
                len(c.files),  # Fewer files better
                total_size,  # Smaller code better
                c.fix_rounds,  # Fewer fixes better
            )

        eligible.sort(key=sort_key)
        return eligible[0]


async def run_pipeline(
    config: Config,
    run_id: str | None = None,
    public_only: bool = False,
    max_tasks: int | None = None,
    resume_run_id: str | None = None,
) -> str:
    """Run the pipeline.

    Args:
        config: Application configuration
        run_id: Optional run ID
        public_only: Only use publishable models
        max_tasks: Maximum tasks
        resume_run_id: Run ID to resume

    Returns:
        Run ID
    """
    if resume_run_id:
        run_id = resume_run_id
        resume = True
    else:
        resume = False

    orchestrator = PipelineOrchestrator(
        config=config,
        run_id=run_id,
        public_only=public_only,
        max_tasks=max_tasks,
        resume=resume,
    )

    await orchestrator.run()
    return orchestrator.run_id
