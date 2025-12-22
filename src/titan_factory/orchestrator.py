"""Orchestrator module - coordinates the full pipeline."""

import asyncio
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

import aiosqlite

from titan_factory.config import Config
from titan_factory.judge import (
    assess_premium_candidates,
    filter_broken_candidates,
    get_creative_director_feedback,
    score_all_candidates,
)
from titan_factory.patcher import patch_candidate, polish_candidate
from titan_factory.planner import generate_ui_spec
from titan_factory.refiner import refine_candidate, refine_candidate_creative_director
from titan_factory.promptgen import (
    generate_task_prompt,
    load_tasks,
    save_niches,
    save_tasks,
    stable_hash,
)
from titan_factory.renderer import render_all_candidates, render_candidate
from titan_factory.schema import (
    Candidate,
    CandidateStatus,
    JudgeScore,
    NicheDefinition,
    PageType,
    Task,
    TeacherModel,
)
from titan_factory.uigen import generate_all_candidates
from titan_factory.utils import ensure_dir, log_error, log_info, log_success, log_warning
from titan_factory.validator import validate_with_retry


class TaskManifest:
    """Tracks task and candidate state in SQLite."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        # Serialize SQLite operations to avoid "database is locked" under task concurrency.
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        """Initialize database."""
        async with self._lock:
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
                    uigen_prompt_id TEXT,
                    variant_index INTEGER,
                    status TEXT,
                    files TEXT,
                    build_logs TEXT,
                    fix_rounds INTEGER DEFAULT 0,
                    polish_rounds INTEGER DEFAULT 0,
                    screenshot_paths TEXT,
                    score REAL,
                    score_details TEXT,
                    premium_gate TEXT,
                    publishable INTEGER DEFAULT 1,
                    error TEXT,
                    planner_model TEXT,
                    patcher_models TEXT,
                    raw_generator_response TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            await self._conn.commit()
            await self._migrate()

    async def _migrate(self) -> None:
        """Perform lightweight schema migrations for existing runs."""
        if not self._conn:
            return

        # Add candidates.uigen_prompt_id if missing (older runs)
        cursor = await self._conn.execute("PRAGMA table_info(candidates)")
        cols = [row[1] for row in await cursor.fetchall()]
        if "uigen_prompt_id" not in cols:
            await self._conn.execute("ALTER TABLE candidates ADD COLUMN uigen_prompt_id TEXT")
            await self._conn.commit()
        if "polish_rounds" not in cols:
            await self._conn.execute(
                "ALTER TABLE candidates ADD COLUMN polish_rounds INTEGER DEFAULT 0"
            )
            await self._conn.commit()
        if "premium_gate" not in cols:
            await self._conn.execute("ALTER TABLE candidates ADD COLUMN premium_gate TEXT")
            await self._conn.commit()

        # === REFINEMENT LOOP COLUMNS (ported from titan-ui-synth-pipeline) ===
        if "refine_passes" not in cols:
            await self._conn.execute(
                "ALTER TABLE candidates ADD COLUMN refine_passes INTEGER DEFAULT 0"
            )
            await self._conn.commit()
        if "pass_scores" not in cols:
            await self._conn.execute("ALTER TABLE candidates ADD COLUMN pass_scores TEXT")
            await self._conn.commit()
        if "pass_feedback" not in cols:
            await self._conn.execute("ALTER TABLE candidates ADD COLUMN pass_feedback TEXT")
            await self._conn.commit()
        if "refine_models" not in cols:
            await self._conn.execute("ALTER TABLE candidates ADD COLUMN refine_models TEXT")
            await self._conn.commit()

    async def close(self) -> None:
        """Close database connection."""
        async with self._lock:
            if self._conn:
                await self._conn.close()
                self._conn = None

    async def get_task_status(self, task_id: str) -> str | None:
        """Get task status."""
        async with self._lock:
            if not self._conn:
                return None
            cursor = await self._conn.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def save_task(self, task: Task, status: str = "pending", ui_spec: str = "") -> None:
        """Save task state including the original prompt for export."""
        async with self._lock:
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
        async with self._lock:
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
        """Save candidate state including teacher chain and raw response for training."""
        async with self._lock:
            if not self._conn:
                return
            now = datetime.utcnow().isoformat()

            # Serialize pass_feedback (list of JudgeScore or None)
            pass_feedback_json = None
            if hasattr(candidate, "pass_feedback") and candidate.pass_feedback:
                pass_feedback_json = json.dumps([
                    fb.model_dump() if fb else None
                    for fb in candidate.pass_feedback
                ])

            await self._conn.execute(
                """
                INSERT OR REPLACE INTO candidates
                (id, task_id, generator_model, uigen_prompt_id, variant_index, status, files, build_logs,
                 fix_rounds, polish_rounds, screenshot_paths, score, score_details, premium_gate, publishable, error,
                 planner_model, patcher_models, raw_generator_response,
                 refine_passes, pass_scores, pass_feedback, refine_models,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.id,
                    candidate.task_id,
                    candidate.generator_model,
                    getattr(candidate, "uigen_prompt_id", "default"),
                    candidate.variant_index,
                    candidate.status.value,
                    json.dumps([f.model_dump() for f in candidate.files]),
                    candidate.build_logs,
                    candidate.fix_rounds,
                    getattr(candidate, "polish_rounds", 0),
                    json.dumps(candidate.screenshot_paths),
                    candidate.score,
                    candidate.score_details.model_dump_json() if candidate.score_details else None,
                    candidate.premium_gate.model_dump_json() if getattr(candidate, "premium_gate", None) else None,
                    1 if candidate.publishable else 0,
                    candidate.error,
                    candidate.planner_model.model_dump_json() if candidate.planner_model else None,
                    json.dumps([p.model_dump() for p in candidate.patcher_models]),
                    candidate.raw_generator_response,
                    # Refinement loop fields
                    getattr(candidate, "refine_passes", 0),
                    json.dumps(getattr(candidate, "pass_scores", [])),
                    pass_feedback_json,
                    json.dumps([m.model_dump() for m in getattr(candidate, "refine_models", [])]),
                    now,
                    now,
                ),
            )
            await self._conn.commit()

    async def get_completed_task_ids(self) -> set[str]:
        """Get IDs of completed tasks."""
        async with self._lock:
            if not self._conn:
                return set()
            cursor = await self._conn.execute(
                "SELECT id FROM tasks WHERE status = 'completed'"
            )
            rows = await cursor.fetchall()
            return {row[0] for row in rows}

    async def get_completed_landing_winners(self) -> list[tuple[str, str, str]]:
        """Get completed landing tasks with selected candidate files.

        Returns:
            List of tuples: (task_id, niche_id, candidate_files_json)
        """
        async with self._lock:
            if not self._conn:
                return []

            cursor = await self._conn.execute(
                """
                SELECT
                    t.id,
                    t.niche_id,
                    c.files
                FROM tasks t
                JOIN candidates c ON t.selected_candidate_id = c.id
                WHERE
                    t.status = 'completed'
                    AND t.page_type = ?
                """,
                (PageType.LANDING.value,),
            )
            rows = await cursor.fetchall()
            return [(row[0], row[1], row[2]) for row in rows]

    async def load_pending_edit_tasks(self) -> list[Task]:
        """Load edit tasks that are queued or were interrupted mid-run.

        We avoid rerunning tasks marked failed or completed.
        """
        async with self._lock:
            if not self._conn:
                return []

            cursor = await self._conn.execute(
                """
                SELECT id, niche_id, page_type, prompt, seed, is_edit, code_old
                FROM tasks
                WHERE
                    is_edit = 1
                    AND status IN ('queued', 'planning', 'generating')
                ORDER BY created_at ASC
                """
            )
            rows = await cursor.fetchall()
        tasks: list[Task] = []
        for row in rows:
            try:
                page_type = PageType(row[2])
            except Exception:
                page_type = PageType.EDIT

            tasks.append(
                Task(
                    id=row[0],
                    niche_id=row[1],
                    page_type=page_type,
                    prompt=row[3] or "",
                    seed=int(row[4] or 0),
                    is_edit=bool(row[5]),
                    code_old=row[6],
                )
            )
        return tasks

    async def get_task_ids_by_status(self, status: str) -> list[str]:
        """Get task IDs by status."""
        async with self._lock:
            if not self._conn:
                return []
            cursor = await self._conn.execute(
                "SELECT id FROM tasks WHERE status = ?",
                (status,),
            )
            rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def load_candidates_for_task(self, task_id: str) -> list[Candidate]:
        """Load all candidates for a task from the manifest DB."""
        async with self._lock:
            if not self._conn:
                return []
            cursor = await self._conn.execute(
                """
                SELECT
                    id, task_id, generator_model, uigen_prompt_id, variant_index, status, files,
                    build_logs, fix_rounds, screenshot_paths, score, score_details,
                    publishable, error, planner_model, patcher_models,
                    raw_generator_response
                FROM candidates
                WHERE task_id = ?
                """,
                (task_id,),
            )
            rows = await cursor.fetchall()

        candidates: list[Candidate] = []
        for row in rows:
            try:
                status_val = CandidateStatus(row[5]) if row[5] else CandidateStatus.PENDING
            except Exception:
                status_val = CandidateStatus.PENDING

            files = []
            try:
                files_raw = json.loads(row[6] or "[]")
                if isinstance(files_raw, list):
                    files = files_raw
            except json.JSONDecodeError:
                files = []

            screenshot_paths = {}
            try:
                screenshot_paths = json.loads(row[9] or "{}")
            except json.JSONDecodeError:
                screenshot_paths = {}

            score_details = None
            if row[11]:
                try:
                    score_details = JudgeScore.model_validate_json(row[11])
                except Exception:
                    score_details = None

            planner_model = None
            if row[14]:
                try:
                    planner_model = TeacherModel.model_validate_json(row[14])
                except Exception:
                    planner_model = None

            patcher_models = []
            if row[15]:
                try:
                    patcher_models_raw = json.loads(row[15])
                    if isinstance(patcher_models_raw, list):
                        patcher_models = [
                            TeacherModel.model_validate(p) for p in patcher_models_raw
                        ]
                except Exception:
                    patcher_models = []

            # Build a minimal Candidate instance
            try:
                candidate = Candidate(
                    id=row[0],
                    task_id=row[1],
                    generator_model=row[2],
                    uigen_prompt_id=row[3] or "default",
                    variant_index=int(row[4] or 0),
                    status=status_val,
                    files=[f for f in files if isinstance(f, dict)],
                    build_logs=row[7] or "",
                    fix_rounds=int(row[8] or 0),
                    screenshot_paths=screenshot_paths if isinstance(screenshot_paths, dict) else {},
                    score=row[10],
                    score_details=score_details,
                    publishable=bool(row[12]),
                    error=row[13],
                    raw_generator_response=row[16],
                    planner_model=planner_model,
                    patcher_models=patcher_models,
                )
            except Exception:
                # If parsing fails, skip this candidate
                continue

            candidates.append(candidate)

        return candidates


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
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        """Initialize database."""
        async with self._lock:
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
        async with self._lock:
            if self._conn:
                await self._conn.close()
                self._conn = None

    async def get(self, hash_key: str) -> str | None:
        """Get cached extracted JSON output."""
        async with self._lock:
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
        async with self._lock:
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
        self.accepted_count = 0

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

            # Apply max_tasks limit
            if self.max_tasks:
                tasks = tasks[: self.max_tasks]
                log_info(f"Limited to {len(tasks)} tasks")

            # Filter if resuming (within the same limited task set)
            if self.resume:
                completed = await self.manifest.get_completed_task_ids()
                tasks = [t for t in tasks if t.id not in completed]
                log_info(f"Resuming with {len(tasks)} remaining tasks")

            # Ensure any edit tasks created from already-completed landing pages are queued.
            await self._ensure_edit_tasks_for_completed_landings()

            # Process tasks concurrently (bounded)
            task_sem = asyncio.Semaphore(max(1, int(self.config.budget.task_concurrency)))

            async def _run_one(t: Task) -> None:
                async with task_sem:
                    try:
                        await self._process_task(t)
                    except Exception as e:
                        log_error(f"Task {t.id} failed: {e}")
                        await self.manifest.update_task(t.id, status="failed", error=str(e))

            await asyncio.gather(*[asyncio.create_task(_run_one(t)) for t in tasks])

            # Process edit tasks (persisted in manifest so resume doesn't lose them)
            if self.config.pipeline.generate_edit_tasks:
                await self._ensure_edit_tasks_for_completed_landings()
                edit_tasks = await self.manifest.load_pending_edit_tasks()

                if edit_tasks:
                    log_info(f"Processing {len(edit_tasks)} edit tasks...")

                    async def _run_edit(t: Task) -> None:
                        async with task_sem:
                            try:
                                await self._process_task(t)
                            except Exception as e:
                                log_error(f"Edit task {t.id} failed: {e}")
                                await self.manifest.update_task(
                                    t.id, status="failed", error=str(e)
                                )

                    await asyncio.gather(
                        *[asyncio.create_task(_run_edit(t)) for t in edit_tasks]
                    )

            # Backfill winners if judge was skipped (legacy salvage for earlier runs).
            if self.config.pipeline.skip_judge:
                await self._backfill_no_winner_tasks()

            # Export results
            log_info("Exporting results...")
            from titan_factory.exporter import export_run

            await export_run(self.run_dir, self.config)

            log_success(f"Pipeline complete! Processed {self.tasks_processed} tasks")
            if self.config.pipeline.skip_judge:
                log_success(f"Accepted candidates: {self.accepted_count}")
            else:
                log_success(f"Winners selected: {self.winners_selected}")

        finally:
            await self.manifest.close()
            await self.cache.close()

    async def _ensure_edit_tasks_for_completed_landings(self) -> None:
        """Queue missing edit tasks for any completed landing winners.

        We create deterministic edit task IDs derived from the landing task,
        and persist them in the tasks table with status="queued" so they survive resume.
        """
        rows = await self.manifest.get_completed_landing_winners()
        if not rows:
            return

        created = 0
        for landing_task_id, niche_id, files_json in rows:
            code_old = self._extract_app_page_from_files_json(files_json)
            if not code_old:
                continue

            edit_seed = stable_hash(f"{niche_id}:edit:{landing_task_id}")
            edit_task_id = hashlib.sha256(f"{niche_id}:edit:{edit_seed}".encode()).hexdigest()[:16]

            existing_status = await self.manifest.get_task_status(edit_task_id)
            if existing_status is not None:
                continue

            niche = NicheDefinition(
                id=niche_id,
                vertical=niche_id.rsplit("_", 1)[0],
                pattern=niche_id.rsplit("_", 1)[-1],
                description="",
            )

            edit_prompt = generate_task_prompt(
                niche=niche,
                page_type=PageType.EDIT,
                seed=edit_seed,
                is_edit=True,
                code_old=None,  # injected separately via Task.code_old / uigen prompt
            )

            edit_task = Task(
                id=edit_task_id,
                niche_id=niche_id,
                page_type=PageType.EDIT,
                seed=edit_seed,
                prompt=edit_prompt,
                is_edit=True,
                code_old=code_old,
            )

            await self.manifest.save_task(edit_task, status="queued")
            created += 1

        if created:
            log_info(f"Queued {created} edit task(s) from completed landing winners")

    def _extract_app_page_from_files_json(self, files_json: str) -> str | None:
        """Extract app/page.tsx content from a candidate files JSON blob."""
        try:
            files = json.loads(files_json or "[]")
        except json.JSONDecodeError:
            return None

        if not isinstance(files, list):
            return None

        for f in files:
            try:
                if f.get("path") == "app/page.tsx" and f.get("content"):
                    return str(f.get("content"))
            except AttributeError:
                continue

        return None

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

        # Stage 5: Score candidates (or skip judge if configured)
        if self.config.pipeline.skip_judge:
            # Optional: run a conservative vision gate that ONLY discards clearly broken renders.
            if getattr(self.config.pipeline, "broken_vision_gate_enabled", False):
                candidates = await filter_broken_candidates(candidates, self.config)
                for candidate in candidates:
                    await self.manifest.save_candidate(candidate)

            # Optional: label premium/ship-ready (boolean) for audit + polishing decisions.
            if getattr(self.config.pipeline, "premium_vision_gate_enabled", False):
                candidates = await assess_premium_candidates(candidates, self.config)
                for candidate in candidates:
                    await self.manifest.save_candidate(candidate)

            # Optional: automatic polish loop (quality improvement) WITHOUT selecting winners.
            # Policy: never discard for aesthetics; only attempt to upgrade some candidates,
            # and revert if polish makes it worse/broken.
            if getattr(self.config.pipeline, "polish_loop_enabled", False) and getattr(
                self.config.pipeline, "premium_vision_gate_enabled", False
            ):
                max_targets = max(
                    0, int(getattr(self.config.pipeline, "polish_max_candidates_per_task", 1) or 1)
                )
                max_rounds = max(0, int(getattr(self.config.pipeline, "polish_max_rounds", 1) or 1))
                min_conf = float(
                    getattr(self.config.pipeline, "premium_vision_gate_min_confidence", 0.75) or 0.75
                )

                targets: list[tuple[int, Candidate, float]] = []
                for idx, c in enumerate(candidates):
                    if c.status != CandidateStatus.RENDERED:
                        continue
                    pg = getattr(c, "premium_gate", None)
                    if not pg or pg.premium:
                        continue
                    if float(getattr(pg, "confidence", 0.0) or 0.0) < min_conf:
                        continue
                    if int(getattr(c, "polish_rounds", 0) or 0) >= max_rounds:
                        continue
                    targets.append((idx, c, float(getattr(pg, "confidence", 0.0) or 0.0)))

                targets.sort(key=lambda t: t[2], reverse=True)
                if max_targets and targets:
                    targets = targets[:max_targets]

                for idx, candidate, _conf in targets:
                    original = candidate.model_copy(deep=True)
                    pg = getattr(candidate, "premium_gate", None)
                    issues = list(getattr(pg, "issues", []) or []) if pg else []
                    fixes = list(getattr(pg, "fix_suggestions", []) or []) if pg else []

                    candidate = await polish_candidate(
                        candidate,
                        self.config,
                        quality_issues=issues,
                        quality_fixes=fixes,
                    )
                    # Re-validate build after polish (and allow build-fix patching if needed)
                    candidate.status = CandidateStatus.GENERATED
                    candidate.error = None
                    candidate.build_logs = ""

                    success, updated_candidate = await validate_with_retry(
                        candidate,
                        self.config,
                        patcher_fn=patch_candidate,
                    )

                    if not success or updated_candidate.status != CandidateStatus.BUILD_PASSED:
                        log_warning(
                            f"Task {task.id}: Polish made candidate {candidate.id} fail build; reverting"
                        )
                        candidates[idx] = original
                        await self.manifest.save_candidate(original)
                        continue

                    # Re-render polished candidate
                    await self.manifest.save_candidate(updated_candidate)
                    await render_candidate(updated_candidate, self.run_dir, self.config)
                    await self.manifest.save_candidate(updated_candidate)

                    # Re-run broken gate for the polished result (revert if it becomes broken)
                    if getattr(self.config.pipeline, "broken_vision_gate_enabled", False):
                        checked = await filter_broken_candidates([updated_candidate], self.config)
                        updated_candidate = checked[0]
                        await self.manifest.save_candidate(updated_candidate)
                        if updated_candidate.status == CandidateStatus.DISCARDED:
                            log_warning(
                                f"Task {task.id}: Polished candidate {candidate.id} flagged broken; reverting"
                            )
                            candidates[idx] = original
                            await self.manifest.save_candidate(original)
                            continue

                    # Refresh premium gate label after polish (best-effort)
                    updated_list = await assess_premium_candidates([updated_candidate], self.config)
                    updated_candidate = updated_list[0]
                    candidates[idx] = updated_candidate
                    await self.manifest.save_candidate(updated_candidate)

            log_warning(
                f"Task {task.id}: Skipping vision judge; accepting rendered candidates"
            )

            accepted = [c for c in candidates if c.status == CandidateStatus.RENDERED]

            if not accepted:
                await self.manifest.update_task(task.id, status="no_accepted_candidates")
                log_warning(f"Task {task.id}: No rendered candidates to accept")
                self.tasks_processed += 1
                return

            # Mark all rendered candidates as accepted (no winners).
            for candidate in accepted:
                candidate.score = candidate.score or 0.0
                if candidate.score_details is None:
                    candidate.score_details = JudgeScore(
                        score=0.0,
                        passing=True,
                        issues=[],
                        highlights=[],
                        fix_suggestions=[],
                    )
                candidate.status = CandidateStatus.ACCEPTED
                await self.manifest.save_candidate(candidate)

            # Pick a representative candidate only for edit-task seeding.
            def sort_key(c: Candidate) -> tuple:
                total_size = sum(len(f.content) for f in c.files)
                return (len(c.files), total_size, c.fix_rounds)

            representative = sorted(accepted, key=sort_key)[0]

            await self.manifest.update_task(
                task.id,
                status="completed",
                selected_candidate_id=representative.id,
            )
            self.accepted_count += len(accepted)
            log_success(
                f"Task {task.id}: Accepted {len(accepted)} candidates "
                f"(representative {representative.id})"
            )

            # Create and queue an edit task if this was a landing page
            if self.config.pipeline.generate_edit_tasks:
                edit_task = self._create_edit_task_from_winner(task, representative)
                if edit_task:
                    existing_status = await self.manifest.get_task_status(edit_task.id)
                    if existing_status != "completed":
                        log_info(f"Queueing edit task {edit_task.id} using accepted code")
                        await self.manifest.save_task(edit_task, status="queued")
        else:
            candidates = await score_all_candidates(candidates, self.config)

            for candidate in candidates:
                await self.manifest.save_candidate(candidate)

            # === REFINEMENT LOOP (ported from titan-ui-synth-pipeline) ===
            # If refinement is enabled and candidate score < threshold, refine and re-score
            if self.config.pipeline.refinement_enabled:
                candidates = await self._refine_candidates_loop(task, candidates)

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
                if self.config.pipeline.generate_edit_tasks:
                    edit_task = self._create_edit_task_from_winner(task, winner)
                    if edit_task:
                        existing_status = await self.manifest.get_task_status(edit_task.id)
                        if existing_status != "completed":
                            log_info(f"Queueing edit task {edit_task.id} using winner's code")
                            await self.manifest.save_task(edit_task, status="queued")
            else:
                await self.manifest.update_task(task.id, status="no_winner")
                log_warning(f"Task {task.id}: No winner selected")

        self.tasks_processed += 1

    async def _refine_candidates_loop(
        self,
        task: Task,
        candidates: list[Candidate],
    ) -> list[Candidate]:
        """Apply iterative refinement to candidates.

        Supports two modes:
        1. Score-based (traditional): Uses numeric thresholds
        2. Creative Director mode: Uses qualitative feedback

        Args:
            task: Parent task
            candidates: Scored/rendered candidates

        Returns:
            Candidates with refinement applied where needed
        """
        if self.config.pipeline.creative_director_mode:
            return await self._refine_candidates_creative_director(task, candidates)
        else:
            return await self._refine_candidates_score_based(task, candidates)

    async def _refine_candidates_score_based(
        self,
        task: Task,
        candidates: list[Candidate],
    ) -> list[Candidate]:
        """Apply score-based iterative refinement to candidates.

        Ported from titan-ui-synth-pipeline's 3-pass refinement architecture.
        For each scored candidate:
        1. If score < pass2_threshold, refine → validate → render → score
        2. If score still < pass3_threshold, refine again → validate → render → score
        3. Track all pass scores for training data

        Args:
            task: Parent task
            candidates: Scored candidates

        Returns:
            Candidates with refinement applied where needed
        """
        pass2_threshold = self.config.pipeline.refine_pass2_threshold
        pass3_threshold = self.config.pipeline.refine_pass3_threshold
        max_passes = self.config.pipeline.max_refine_passes

        for i, candidate in enumerate(candidates):
            # Skip candidates that aren't scored or already meet threshold
            if candidate.status != CandidateStatus.SCORED:
                continue
            if candidate.score is None:
                continue

            # Record initial pass 1 score
            initial_score = candidate.score
            candidate.pass_scores = [initial_score]
            candidate.pass_feedback = [candidate.score_details]

            current = candidate
            current_score = initial_score

            # Refinement passes
            for pass_num in range(2, max_passes + 2):  # pass 2, pass 3, etc.
                # Determine threshold for this pass
                if pass_num == 2:
                    threshold = pass2_threshold
                else:
                    threshold = pass3_threshold

                # Check if refinement is needed
                if current_score >= threshold:
                    log_info(
                        f"Task {task.id} candidate {current.id}: "
                        f"Score {current_score:.1f} >= {threshold}, skipping pass {pass_num}"
                    )
                    break

                if current.refine_passes >= max_passes:
                    log_warning(
                        f"Task {task.id} candidate {current.id}: "
                        f"Max refine passes ({max_passes}) reached"
                    )
                    break

                # Get feedback from last score
                feedback = current.score_details
                if not feedback:
                    log_warning(
                        f"Task {task.id} candidate {current.id}: "
                        f"No feedback available for refinement"
                    )
                    break

                log_info(
                    f"Task {task.id} candidate {current.id}: "
                    f"Score {current_score:.1f} < {threshold}, starting pass {pass_num} refinement"
                )

                # Step 1: Refine
                refined = await refine_candidate(current, feedback, self.config)
                if not refined:
                    log_warning(
                        f"Task {task.id} candidate {current.id}: "
                        f"Refinement pass {pass_num} failed"
                    )
                    break

                # Step 2: Validate (build)
                success, validated = await validate_with_retry(
                    refined,
                    self.config,
                    patcher_fn=patch_candidate,
                )
                await self.manifest.save_candidate(validated)

                if not success or validated.status != CandidateStatus.BUILD_PASSED:
                    log_warning(
                        f"Task {task.id} candidate {current.id}: "
                        f"Refinement pass {pass_num} failed build validation"
                    )
                    # Revert to previous version
                    break

                # Step 3: Render
                await render_candidate(validated, self.run_dir, self.config)
                await self.manifest.save_candidate(validated)

                if validated.status != CandidateStatus.RENDERED:
                    log_warning(
                        f"Task {task.id} candidate {current.id}: "
                        f"Refinement pass {pass_num} failed rendering"
                    )
                    break

                # Step 4: Score
                scored_list = await score_all_candidates([validated], self.config)
                if not scored_list:
                    log_warning(
                        f"Task {task.id} candidate {current.id}: "
                        f"Refinement pass {pass_num} failed scoring"
                    )
                    break

                validated = scored_list[0]
                await self.manifest.save_candidate(validated)

                # Record this pass's score
                new_score = validated.score or 0.0
                validated.pass_scores = list(current.pass_scores) + [new_score]
                validated.pass_feedback = list(current.pass_feedback) + [validated.score_details]

                log_success(
                    f"Task {task.id} candidate {current.id}: "
                    f"Pass {pass_num} complete, score: {current_score:.1f} → {new_score:.1f}"
                )

                # Update for next iteration
                current = validated
                current_score = new_score
                candidates[i] = current
                await self.manifest.save_candidate(current)

        return candidates

    async def _refine_candidates_creative_director(
        self,
        task: Task,
        candidates: list[Candidate],
    ) -> list[Candidate]:
        """Apply creative director refinement to candidates.

        Uses qualitative feedback instead of numeric scores:
        1. Get creative director feedback (is it shippable? what's missing?)
        2. If not shippable and has missing_for_production items, refine
        3. Repeat until shippable or max passes reached
        4. Preserves creative choices throughout

        Args:
            task: Parent task
            candidates: Rendered candidates

        Returns:
            Candidates with refinement applied where needed
        """
        max_passes = self.config.pipeline.max_refine_passes

        for i, candidate in enumerate(candidates):
            # Skip candidates that aren't rendered
            if candidate.status not in (CandidateStatus.RENDERED, CandidateStatus.SCORED):
                continue

            current = candidate

            # Refinement passes
            for pass_num in range(1, max_passes + 1):
                # Get creative director feedback
                feedback = await get_creative_director_feedback(current, self.config)

                if not feedback:
                    log_warning(
                        f"Task {task.id} candidate {current.id}: "
                        f"Creative director feedback failed"
                    )
                    break

                # Store feedback on candidate
                current.creative_director_feedback = feedback
                await self.manifest.save_candidate(current)

                # Check if already shippable
                if feedback.shippable and not feedback.obviously_broken:
                    log_success(
                        f"Task {task.id} candidate {current.id}: "
                        f"Creative director says SHIPPABLE - no refinement needed"
                    )
                    break

                # Check if obviously broken (should have been caught by build)
                if feedback.obviously_broken:
                    log_warning(
                        f"Task {task.id} candidate {current.id}: "
                        f"Creative director says BROKEN - skipping"
                    )
                    break

                # Check if there are production issues to fix
                if not feedback.missing_for_production:
                    log_info(
                        f"Task {task.id} candidate {current.id}: "
                        f"No production issues to fix - accepting as-is"
                    )
                    break

                # Check max passes
                if current.refine_passes >= max_passes:
                    log_warning(
                        f"Task {task.id} candidate {current.id}: "
                        f"Max refine passes ({max_passes}) reached"
                    )
                    break

                log_info(
                    f"Task {task.id} candidate {current.id}: "
                    f"Creative director pass {pass_num}: {len(feedback.missing_for_production)} production issues"
                )
                log_info(f"  Missing: {feedback.missing_for_production[:3]}")
                if feedback.preserve:
                    log_info(f"  Preserving: {feedback.preserve[:2]}")

                # Step 1: Refine using creative director feedback
                refined = await refine_candidate_creative_director(current, feedback, self.config)
                if not refined:
                    log_warning(
                        f"Task {task.id} candidate {current.id}: "
                        f"Creative director refinement pass {pass_num} failed"
                    )
                    break

                # Step 2: Validate (build)
                success, validated = await validate_with_retry(
                    refined,
                    self.config,
                    patcher_fn=patch_candidate,
                )
                await self.manifest.save_candidate(validated)

                if not success or validated.status != CandidateStatus.BUILD_PASSED:
                    log_warning(
                        f"Task {task.id} candidate {current.id}: "
                        f"Creative director refinement pass {pass_num} failed build"
                    )
                    break

                # Step 3: Render
                await render_candidate(validated, self.run_dir, self.config)
                await self.manifest.save_candidate(validated)

                if validated.status != CandidateStatus.RENDERED:
                    log_warning(
                        f"Task {task.id} candidate {current.id}: "
                        f"Creative director refinement pass {pass_num} failed rendering"
                    )
                    break

                log_success(
                    f"Task {task.id} candidate {current.id}: "
                    f"Creative director pass {pass_num} complete"
                )

                # Update for next iteration
                current = validated
                candidates[i] = current
                await self.manifest.save_candidate(current)

        return candidates

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
            code_old=None,  # injected separately via Task.code_old / uigen prompt
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
        if self.config.pipeline.skip_judge:
            eligible = [
                c for c in candidates
                if c.status in (CandidateStatus.SCORED, CandidateStatus.RENDERED, CandidateStatus.BUILD_PASSED)
            ]
            if not eligible:
                return None
        else:
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
            if self.config.pipeline.skip_judge:
                return (
                    len(c.files),
                    total_size,
                    c.fix_rounds,
                )
            return (
                -(c.score or 0),  # Higher score better
                len(c.files),  # Fewer files better
                total_size,  # Smaller code better
                c.fix_rounds,  # Fewer fixes better
            )

        eligible.sort(key=sort_key)
        return eligible[0]

    async def _backfill_no_winner_tasks(self) -> None:
        """Select winners for previously processed tasks with no_winner status.

        This salvages candidates when the judge is disabled or unavailable.
        """
        task_ids = await self.manifest.get_task_ids_by_status("no_winner")
        if not task_ids:
            return

        recovered = 0
        for task_id in task_ids:
            candidates = await self.manifest.load_candidates_for_task(task_id)
            if not candidates:
                continue

            # Ensure any rendered candidates are treated as scored for selection.
            for candidate in candidates:
                if candidate.status == CandidateStatus.RENDERED:
                    candidate.score = candidate.score or 0.0
                    if candidate.score_details is None:
                        candidate.score_details = JudgeScore(
                            score=0.0,
                            passing=True,
                            issues=[],
                            highlights=[],
                            fix_suggestions=[],
                        )
                    candidate.status = CandidateStatus.SCORED

            winner = self._select_winner(candidates)
            if not winner:
                continue

            winner.status = CandidateStatus.SELECTED
            await self.manifest.save_candidate(winner)
            await self.manifest.update_task(
                task_id,
                status="completed",
                selected_candidate_id=winner.id,
            )
            recovered += 1

        if recovered:
            log_info(f"Backfilled winners for {recovered} no_winner tasks")


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


async def backfill_no_winner(
    config: Config,
    run_id: str,
) -> None:
    """Backfill winners for tasks marked no_winner.

    This is useful when the vision judge was skipped or unavailable.
    """
    orchestrator = PipelineOrchestrator(
        config=config,
        run_id=run_id,
        public_only=False,
        max_tasks=None,
        resume=True,
    )

    await orchestrator.manifest.init()
    try:
        await orchestrator._backfill_no_winner_tasks()
    finally:
        await orchestrator.manifest.close()
