"""Orchestrator module - coordinates the full pipeline."""

import asyncio
import hashlib
import json
import traceback
import uuid
from datetime import datetime
from pathlib import Path

import aiosqlite

from titan_factory.config import Config
from titan_factory.judge import (
    assess_premium_candidates,
    assess_section_creativity,
    filter_broken_candidates,
    get_creative_director_feedback,
    score_all_candidates,
)
from titan_factory.patcher import (
    patch_candidate,
    polish_candidate,
    refine_candidate_section_creativity,
)
from titan_factory.planner import generate_ui_spec
from titan_factory.promptgen import (
    generate_task_prompt,
    load_tasks,
    save_niches,
    save_tasks,
    stable_hash,
)
from titan_factory.refiner import refine_candidate, refine_candidate_creative_director
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
from titan_factory.style_gates import evaluate_style_gates
from titan_factory.uigen import generate_all_candidates, generate_all_candidates_raw
from titan_factory.utils import (
    ensure_dir,
    log_error,
    log_info,
    log_success,
    log_warning,
    set_run_log_file,
)
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
                    style_family TEXT,
                    style_persona TEXT,
                    style_keywords_mandatory TEXT,
                    style_avoid TEXT,
                    style_density TEXT,
                    style_imagery_style TEXT,
                    style_layout_motif TEXT,
                    theme_mood TEXT,
                    theme_accent TEXT,
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
                    generator_temperature REAL,
                    status TEXT,
                    files TEXT,
                    build_logs TEXT,
                    fix_rounds INTEGER DEFAULT 0,
                    polish_rounds INTEGER DEFAULT 0,
                    screenshot_paths TEXT,
                    deterministic_passed INTEGER,
                    deterministic_failures TEXT,
                    axe_violations TEXT,
                    lighthouse_scores TEXT,
                    lighthouse_report_path TEXT,
                    style_gate_passed INTEGER,
                    style_gate_failures TEXT,
                    style_gate_warnings TEXT,
                    style_gate_details TEXT,
                    score REAL,
                    score_details TEXT,
                    premium_gate TEXT,
                    section_creativity TEXT,
                    section_creativity_avg REAL,
                    section_creativity_core_avg REAL,
                    section_creativity_key_avg REAL,
                    section_creativity_high_count INTEGER,
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

        # Tasks table migrations
        cursor = await self._conn.execute("PRAGMA table_info(tasks)")
        task_cols = [row[1] for row in await cursor.fetchall()]
        for col_name, col_type in (
            ("style_family", "TEXT"),
            ("style_persona", "TEXT"),
            ("style_keywords_mandatory", "TEXT"),
            ("style_avoid", "TEXT"),
            ("style_density", "TEXT"),
            ("style_imagery_style", "TEXT"),
            ("style_layout_motif", "TEXT"),
            ("theme_mood", "TEXT"),
            ("theme_accent", "TEXT"),
        ):
            if col_name not in task_cols:
                await self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {col_name} {col_type}")
                await self._conn.commit()

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
        if "generator_temperature" not in cols:
            await self._conn.execute(
                "ALTER TABLE candidates ADD COLUMN generator_temperature REAL"
            )
            await self._conn.commit()

        if "premium_gate" not in cols:
            await self._conn.execute("ALTER TABLE candidates ADD COLUMN premium_gate TEXT")
            await self._conn.commit()
        if "section_creativity" not in cols:
            await self._conn.execute("ALTER TABLE candidates ADD COLUMN section_creativity TEXT")
            await self._conn.commit()
        if "section_creativity_avg" not in cols:
            await self._conn.execute("ALTER TABLE candidates ADD COLUMN section_creativity_avg REAL")
            await self._conn.commit()
        if "section_creativity_core_avg" not in cols:
            await self._conn.execute(
                "ALTER TABLE candidates ADD COLUMN section_creativity_core_avg REAL"
            )
            await self._conn.commit()
        if "section_creativity_key_avg" not in cols:
            await self._conn.execute(
                "ALTER TABLE candidates ADD COLUMN section_creativity_key_avg REAL"
            )
            await self._conn.commit()
        if "section_creativity_high_count" not in cols:
            await self._conn.execute(
                "ALTER TABLE candidates ADD COLUMN section_creativity_high_count INTEGER"
            )
            await self._conn.commit()

        # === DETERMINISTIC QUALITY GATES (axe + Lighthouse) ===
        if "deterministic_passed" not in cols:
            await self._conn.execute(
                "ALTER TABLE candidates ADD COLUMN deterministic_passed INTEGER"
            )
            await self._conn.commit()
        if "deterministic_failures" not in cols:
            await self._conn.execute(
                "ALTER TABLE candidates ADD COLUMN deterministic_failures TEXT"
            )
            await self._conn.commit()
        if "axe_violations" not in cols:
            await self._conn.execute("ALTER TABLE candidates ADD COLUMN axe_violations TEXT")
            await self._conn.commit()
        if "lighthouse_scores" not in cols:
            await self._conn.execute("ALTER TABLE candidates ADD COLUMN lighthouse_scores TEXT")
            await self._conn.commit()
        if "lighthouse_report_path" not in cols:
            await self._conn.execute(
                "ALTER TABLE candidates ADD COLUMN lighthouse_report_path TEXT"
            )
            await self._conn.commit()

        # === STYLE GATES ===
        if "style_gate_passed" not in cols:
            await self._conn.execute("ALTER TABLE candidates ADD COLUMN style_gate_passed INTEGER")
            await self._conn.commit()
        if "style_gate_failures" not in cols:
            await self._conn.execute("ALTER TABLE candidates ADD COLUMN style_gate_failures TEXT")
            await self._conn.commit()
        if "style_gate_warnings" not in cols:
            await self._conn.execute("ALTER TABLE candidates ADD COLUMN style_gate_warnings TEXT")
            await self._conn.commit()
        if "style_gate_details" not in cols:
            await self._conn.execute("ALTER TABLE candidates ADD COLUMN style_gate_details TEXT")
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
                (id, niche_id, page_type, prompt, seed, is_edit, code_old,
                 style_family, style_persona, style_keywords_mandatory, style_avoid,
                 style_density, style_imagery_style, style_layout_motif,
                 theme_mood, theme_accent,
                 status, ui_spec, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.niche_id,
                    task.page_type.value,
                    task.prompt,
                    task.seed,
                    1 if task.is_edit else 0,
                    task.code_old,
                    getattr(task, "style_family", None),
                    getattr(task, "style_persona", None),
                    json.dumps(getattr(task, "style_keywords_mandatory", []) or []),
                    json.dumps(getattr(task, "style_avoid", []) or []),
                    getattr(task, "style_density", None),
                    getattr(task, "style_imagery_style", None),
                    getattr(task, "style_layout_motif", None),
                    getattr(task, "theme_mood", None),
                    getattr(task, "theme_accent", None),
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
                (id, task_id, generator_model, uigen_prompt_id, variant_index, generator_temperature,
                 status, files, build_logs,
                 fix_rounds, polish_rounds, screenshot_paths,
                 deterministic_passed, deterministic_failures, axe_violations, lighthouse_scores, lighthouse_report_path,
                 style_gate_passed, style_gate_failures, style_gate_warnings, style_gate_details,
                 score, score_details, premium_gate, section_creativity, section_creativity_avg,
                 section_creativity_core_avg, section_creativity_key_avg, section_creativity_high_count,
                 publishable, error,
                 planner_model, patcher_models, raw_generator_response,
                 refine_passes, pass_scores, pass_feedback, refine_models,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.id,
                    candidate.task_id,
                    candidate.generator_model,
                    getattr(candidate, "uigen_prompt_id", "default"),
                    candidate.variant_index,
                    getattr(candidate, "generator_temperature", None),
                    candidate.status.value,
                    json.dumps([f.model_dump() for f in candidate.files]),
                    candidate.build_logs,
                    candidate.fix_rounds,
                    getattr(candidate, "polish_rounds", 0),
                    json.dumps(candidate.screenshot_paths),
                    None
                    if getattr(candidate, "deterministic_passed", None) is None
                    else (1 if bool(getattr(candidate, "deterministic_passed", False)) else 0),
                    json.dumps(getattr(candidate, "deterministic_failures", []) or []),
                    json.dumps(getattr(candidate, "axe_violations", []) or []),
                    json.dumps(getattr(candidate, "lighthouse_scores", {}) or {}),
                    getattr(candidate, "lighthouse_report_path", None),
                    None
                    if getattr(candidate, "style_gate_passed", None) is None
                    else (1 if bool(getattr(candidate, "style_gate_passed", False)) else 0),
                    json.dumps(getattr(candidate, "style_gate_failures", []) or []),
                    json.dumps(getattr(candidate, "style_gate_warnings", []) or []),
                    json.dumps(getattr(candidate, "style_gate_details", {}) or {}),
                    candidate.score,
                    candidate.score_details.model_dump_json() if candidate.score_details else None,
                    candidate.premium_gate.model_dump_json() if getattr(candidate, "premium_gate", None) else None,
                    json.dumps(getattr(candidate, "section_creativity", []) or []),
                    getattr(candidate, "section_creativity_avg", None),
                    getattr(candidate, "section_creativity_core_avg", None),
                    getattr(candidate, "section_creativity_key_avg", None),
                    getattr(candidate, "section_creativity_high_count", None),
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
                    build_logs, fix_rounds, screenshot_paths,
                    deterministic_passed, deterministic_failures, axe_violations, lighthouse_scores, lighthouse_report_path,
                    score, score_details, section_creativity, section_creativity_avg,
                    section_creativity_core_avg, section_creativity_key_avg, section_creativity_high_count,
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

            deterministic_passed = None
            try:
                if row[10] is not None:
                    deterministic_passed = bool(int(row[10]))
            except Exception:
                deterministic_passed = None

            deterministic_failures = []
            try:
                deterministic_failures_raw = json.loads(row[11] or "[]")
                if isinstance(deterministic_failures_raw, list):
                    deterministic_failures = [str(x) for x in deterministic_failures_raw if str(x).strip()]
            except Exception:
                deterministic_failures = []

            axe_violations = []
            try:
                axe_raw = json.loads(row[12] or "[]")
                if isinstance(axe_raw, list):
                    axe_violations = [v for v in axe_raw if isinstance(v, dict)]
            except Exception:
                axe_violations = []

            lighthouse_scores = {}
            try:
                lh_raw = json.loads(row[13] or "{}")
                if isinstance(lh_raw, dict):
                    lighthouse_scores = {
                        str(k): float(v) for k, v in lh_raw.items() if v is not None
                    }
            except Exception:
                lighthouse_scores = {}

            lighthouse_report_path = row[14] or None

            score_details = None
            if row[16]:
                try:
                    score_details = JudgeScore.model_validate_json(row[16])
                except Exception:
                    score_details = None

            section_creativity = []
            if row[17]:
                try:
                    section_raw = json.loads(row[17] or "[]")
                    if isinstance(section_raw, list):
                        section_creativity = [s for s in section_raw if isinstance(s, dict)]
                except Exception:
                    section_creativity = []

            section_creativity_avg = None
            if row[18] is not None:
                try:
                    section_creativity_avg = float(row[18])
                except Exception:
                    section_creativity_avg = None

            section_creativity_core_avg = None
            if row[19] is not None:
                try:
                    section_creativity_core_avg = float(row[19])
                except Exception:
                    section_creativity_core_avg = None

            section_creativity_key_avg = None
            if row[20] is not None:
                try:
                    section_creativity_key_avg = float(row[20])
                except Exception:
                    section_creativity_key_avg = None

            section_creativity_high_count = None
            if row[21] is not None:
                try:
                    section_creativity_high_count = int(row[21])
                except Exception:
                    section_creativity_high_count = None

            planner_model = None
            if row[24]:
                try:
                    planner_model = TeacherModel.model_validate_json(row[24])
                except Exception:
                    planner_model = None

            patcher_models = []
            if row[25]:
                try:
                    patcher_models_raw = json.loads(row[25])
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
                    deterministic_passed=deterministic_passed,
                    deterministic_failures=deterministic_failures,
                    axe_violations=axe_violations,
                    lighthouse_scores=lighthouse_scores if isinstance(lighthouse_scores, dict) else {},
                    lighthouse_report_path=lighthouse_report_path,
                    score=row[15],
                    score_details=score_details,
                    section_creativity=section_creativity,
                    section_creativity_avg=section_creativity_avg,
                    section_creativity_core_avg=section_creativity_core_avg,
                    section_creativity_key_avg=section_creativity_key_avg,
                    section_creativity_high_count=section_creativity_high_count,
                    publishable=bool(row[22]),
                    error=row[23],
                    raw_generator_response=row[26],
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

        # Persist logs to out/<run_id>/run.log so the HTML portal can display them.
        # For resume runs we append; for new runs we truncate.
        set_run_log_file(self.run_dir / "run.log", append=bool(self.resume))

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
                try:
                    async with task_sem:
                        try:
                            await self._process_task(t)
                        except asyncio.CancelledError as e:
                            # Python 3.14: asyncio.CancelledError is BaseException, not Exception.
                            # If any sub-call gets cancelled (timeouts/tooling), we MUST NOT crash the whole run.
                            log_error(f"Task {t.id} cancelled: {e}")
                            await self.manifest.update_task(
                                t.id, status="failed", error=f"CancelledError: {e}"
                            )
                        except Exception as e:
                            log_error(f"Task {t.id} failed: {e}")
                            try:
                                tb_lines = traceback.format_exc().strip().splitlines()
                                for line in tb_lines[-14:]:
                                    log_error(f"Task {t.id} traceback: {line}")
                            except Exception:
                                pass
                            await self.manifest.update_task(t.id, status="failed", error=str(e))
                except asyncio.CancelledError as e:
                    log_error(f"Task {t.id} cancelled (waiting for slot): {e}")
                    await self.manifest.update_task(
                        t.id, status="failed", error=f"CancelledError: {e}"
                    )

            await asyncio.gather(*[asyncio.create_task(_run_one(t)) for t in tasks])

            # Process edit tasks (persisted in manifest so resume doesn't lose them)
            if self.config.pipeline.generate_edit_tasks:
                await self._ensure_edit_tasks_for_completed_landings()
                edit_tasks = await self.manifest.load_pending_edit_tasks()

                if edit_tasks:
                    log_info(f"Processing {len(edit_tasks)} edit tasks...")

                    async def _run_edit(t: Task) -> None:
                        try:
                            async with task_sem:
                                try:
                                    await self._process_task(t)
                                except asyncio.CancelledError as e:
                                    log_error(f"Edit task {t.id} cancelled: {e}")
                                    await self.manifest.update_task(
                                        t.id, status="failed", error=f"CancelledError: {e}"
                                    )
                                except Exception as e:
                                    log_error(f"Edit task {t.id} failed: {e}")
                                    try:
                                        tb_lines = traceback.format_exc().strip().splitlines()
                                        for line in tb_lines[-14:]:
                                            log_error(f"Edit task {t.id} traceback: {line}")
                                    except Exception:
                                        pass
                                    await self.manifest.update_task(
                                        t.id, status="failed", error=str(e)
                                    )
                        except asyncio.CancelledError as e:
                            log_error(f"Edit task {t.id} cancelled (waiting for slot): {e}")
                            await self.manifest.update_task(
                                t.id, status="failed", error=f"CancelledError: {e}"
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

        # Optional: EVOL prompt mutations (deterministic) to increase variety without style drift.
        if (
            (not task.is_edit)
            and bool(getattr(self.config.pipeline, "evol_enabled", False))
            and int(getattr(self.config.pipeline, "evol_passes", 0) or 0) > 0
        ):
            try:
                from titan_factory.evol import mutate_task_prompt

                passes = int(getattr(self.config.pipeline, "evol_passes", 2) or 2)
                evol = mutate_task_prompt(task, passes=passes)
                if evol.applied_ops and evol.prompt and evol.prompt != task.prompt:
                    task = task.model_copy(update={"prompt": evol.prompt})
                    log_info(f"Task {task.id}: EVOL applied ops: {', '.join(evol.applied_ops)}")
            except Exception as e:
                log_warning(f"Task {task.id}: EVOL mutation failed; continuing. Error: {e}")

        # Save initial state
        await self.manifest.save_task(task, status="planning")

        ui_spec = None
        raw_mode = bool(getattr(self.config.pipeline, "raw_generation_enabled", False))

        # Stage 1: Generate UI_SPEC (unless running a "no pipeline" raw baseline)
        if raw_mode:
            log_info(f"Task {task.id}: Raw generation enabled (skipping UI_SPEC planning)")
            await self.manifest.update_task(task.id, status="generating", ui_spec="")
        else:
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
        planner_model = None
        if ui_spec is not None:
            planner_model = TeacherModel(
                provider=self.config.planner.provider,
                model=self.config.planner.model or "unknown",
                publishable=self.config.planner.publishable,
            )

        candidates: list[Candidate] = []
        if raw_mode:
            async for candidate in generate_all_candidates_raw(task, self.config, self.public_only):
                candidates.append(candidate)
                await self.manifest.save_candidate(candidate)
                self.candidates_generated += 1
        else:
            async for candidate in generate_all_candidates(
                task, ui_spec, self.config, self.public_only  # type: ignore[arg-type]
            ):
                if planner_model is not None:
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

        # Stage 3.5: Style gates (cheap deterministic checks) - optional
        passing = [c for c in candidates if c.status == CandidateStatus.BUILD_PASSED]
        if passing and bool(getattr(self.config.pipeline, "style_gates_enabled", False)):
            enforce = bool(getattr(self.config.pipeline, "style_gates_enforce", False))
            log_info(f"Task {task.id}: Running style gates on {len(passing)} build-passed candidates...")
            for i, candidate in enumerate(candidates):
                if candidate.status != CandidateStatus.BUILD_PASSED:
                    continue
                try:
                    result = evaluate_style_gates(candidate.files, task)
                    candidate.style_gate_passed = result.passed
                    candidate.style_gate_failures = list(result.failures or [])
                    candidate.style_gate_warnings = list(result.warnings or [])
                    candidate.style_gate_details = dict(result.details or {})

                    if enforce and not result.passed:
                        candidate.status = CandidateStatus.DISCARDED
                        candidate.error = ("; ".join(result.failures) or "style_gate_failed")[:900]
                        log_warning(
                            f"Task {task.id}: Discarded candidate {candidate.id} by style gate: {candidate.error}"
                        )
                    else:
                        # In observe mode, surface warnings in the log for debugging.
                        for w in candidate.style_gate_warnings[:6]:
                            log_warning(f"Task {task.id}: Candidate {candidate.id}: {w}")
                except Exception as e:
                    log_warning(f"Task {task.id}: Style gate error for candidate {candidate.id}: {e}")

                candidates[i] = candidate
                await self.manifest.save_candidate(candidate)

        # Stage 4: Render passing candidates
        passing = [c for c in candidates if c.status == CandidateStatus.BUILD_PASSED]

        if not passing:
            log_warning(f"Task {task.id}: No candidates passed build")

            # Persist a compact failure reason so the portal can show *why* it failed.
            # Keep this short (fits in tasks.error, and readable in HTML).
            error_bits: list[str] = []
            for c in candidates[:6]:
                preview = ""
                if getattr(c, "error", None):
                    preview = str(getattr(c, "error") or "").strip()
                elif getattr(c, "build_logs", None):
                    lines = str(getattr(c, "build_logs") or "").strip().splitlines()
                    preview = " | ".join([ln.strip() for ln in lines[-3:] if ln.strip()][:3])
                preview = preview.strip()
                if preview:
                    model = (getattr(c, "generator_model", "") or "").strip()
                    tag = f"{model}:" if model else "candidate:"
                    error_bits.append(f"{tag} {preview[:160]}")

            error_summary = "No candidates passed build."
            if error_bits:
                error_summary += " " + " / ".join(error_bits[:3])

            await self.manifest.update_task(
                task.id, status="no_passing_candidates", error=error_summary[:900]
            )
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

                    # Revert if vision quality regressed (score or confidence drop)
                    # We accept a polished candidate ONLY if:
                    # 1. It is still premium (or better)
                    # 2. Confidence hasn't dropped significantly
                    # 3. Vision score (if available) hasn't dropped
                    
                    # If we have numeric scores (from a real judge, not just premium gate), check them.
                    # Note: We need to score the updated candidate first if we want numeric comparison.
                    if not self.config.pipeline.skip_judge or self.config.vision_judge.model:
                         try:
                             from titan_factory.judge import score_candidate
                             # Best-effort scoring for regression check
                             new_score = await score_candidate(updated_candidate, self.config)
                             updated_candidate.score = new_score.score
                             updated_candidate.score_details = new_score
                             
                             old_score_val = float(candidate.score or 0.0)
                             new_score_val = float(updated_candidate.score or 0.0)
                             
                             # Allow tiny fluctuation (0.5), but revert on significant drop
                             if new_score_val < (new_score_val - 0.5):
                                 log_warning(
                                     f"Task {task.id}: Polish regression (score {old_score_val:.1f} -> {new_score_val:.1f}); reverting"
                                 )
                                 candidates[idx] = original
                                 await self.manifest.save_candidate(original)
                                 continue
                         except Exception as e:
                             log_warning(f"Task {task.id}: Failed to score polished candidate: {e}")

                    # Fallback to premium gate confidence check
                    orig_pg = getattr(original, "premium_gate", None)
                    new_pg = getattr(updated_candidate, "premium_gate", None)
                    
                    if orig_pg and new_pg:
                        orig_conf = float(getattr(orig_pg, "confidence", 0.0) or 0.0)
                        new_conf = float(getattr(new_pg, "confidence", 0.0) or 0.0)
                        
                        # If it lost "premium" status, revert
                        if orig_pg.premium and not new_pg.premium:
                             log_warning(f"Task {task.id}: Polish lost premium status; reverting")
                             candidates[idx] = original
                             await self.manifest.save_candidate(original)
                             continue
                             
                        # If confidence dropped significantly (> 0.15), revert
                        if new_conf < (orig_conf - 0.15):
                             log_warning(f"Task {task.id}: Polish confidence dropped ({orig_conf:.2f} -> {new_conf:.2f}); reverting")
                             candidates[idx] = original
                             await self.manifest.save_candidate(original)
                             continue

                    candidates[idx] = updated_candidate
                    await self.manifest.save_candidate(updated_candidate)

            # Optional: section-level creativity refinement (surgical).
            # Uses the vision model to score sections individually, then patches ONLY weak sections
            # to match the strongest section's creativity level. This runs in skip_judge mode
            # without doing full aesthetic scoring or winner selection.
            if getattr(self.config.pipeline, "creativity_refinement_enabled", False):
                rendered = [c for c in candidates if c.status == CandidateStatus.RENDERED]
                if rendered:
                    # Refine only the representative candidate to keep spend predictable.
                    rep = sorted(rendered, key=self._skip_judge_sort_key)[0]
                    rep_idx = next((i for i, c in enumerate(candidates) if c.id == rep.id), None)

                    if rep_idx is not None:
                        max_passes = max(
                            0,
                            int(
                                getattr(
                                    self.config.pipeline, "creativity_max_refinement_passes", 2
                                )
                                or 2
                            ),
                        )
                        min_score = float(
                            getattr(self.config.pipeline, "creativity_min_section_score", 0.7)
                            or 0.7
                        )

                        for _pass in range(max_passes):
                            rep = candidates[rep_idx]
                            if rep.status != CandidateStatus.RENDERED:
                                break

                            sections = await assess_section_creativity(rep, self.config)
                            if not sections:
                                break

                            # Only act on sections the model is reasonably confident about.
                            confident = []
                            for s in sections:
                                try:
                                    conf = float(s.get("confidence") or 0.0)
                                except Exception:
                                    conf = 0.0
                                if conf >= 0.5:
                                    confident.append(s)

                            if not confident:
                                break

                            weak = [s for s in confident if float(s.get("score") or 0.0) < min_score]
                            strong = [s for s in confident if float(s.get("score") or 0.0) >= min_score]

                            # Only refine when quality is mixed (some strong, some weak).
                            if not weak or not strong:
                                break

                            # Keep it surgical: improve at most 2 weak sections, use up to 2 strong anchors.
                            weak.sort(key=lambda s: float(s.get("score") or 0.0))
                            strong.sort(key=lambda s: float(s.get("score") or 0.0), reverse=True)

                            weak_ids = [str(s.get("id") or "").strip() for s in weak[:2] if str(s.get("id") or "").strip()]
                            strong_ids = [str(s.get("id") or "").strip() for s in strong[:2] if str(s.get("id") or "").strip()]

                            if not weak_ids or not strong_ids:
                                break

                            baseline = rep.model_copy(deep=True)

                            rep = await refine_candidate_section_creativity(
                                rep,
                                self.config,
                                strong_section_ids=strong_ids,
                                weak_section_ids=weak_ids,
                                section_feedback=sections,
                            )
                            await self.manifest.save_candidate(rep)

                            # Re-validate build after refinement (and allow build-fix patching if needed)
                            rep.status = CandidateStatus.GENERATED
                            rep.error = None
                            rep.build_logs = ""

                            success, updated_rep = await validate_with_retry(
                                rep,
                                self.config,
                                patcher_fn=patch_candidate,
                            )

                            if not success or updated_rep.status != CandidateStatus.BUILD_PASSED:
                                log_warning(
                                    f"Task {task.id}: Creativity refinement broke build for {rep.id}; reverting"
                                )
                                candidates[rep_idx] = baseline
                                await self.manifest.save_candidate(baseline)
                                break

                            await self.manifest.save_candidate(updated_rep)
                            await render_candidate(updated_rep, self.run_dir, self.config)
                            await self.manifest.save_candidate(updated_rep)

                            # Re-run broken gate for the refined result (revert if it becomes broken)
                            if getattr(self.config.pipeline, "broken_vision_gate_enabled", False):
                                checked = await filter_broken_candidates([updated_rep], self.config)
                                updated_rep = checked[0]
                                await self.manifest.save_candidate(updated_rep)
                                if updated_rep.status == CandidateStatus.DISCARDED:
                                    log_warning(
                                        f"Task {task.id}: Creativity-refined candidate {rep.id} flagged broken; reverting"
                                    )
                                    candidates[rep_idx] = baseline
                                    await self.manifest.save_candidate(baseline)
                                    break

                            # Keep refined candidate.
                            candidates[rep_idx] = updated_rep

            log_warning(
                f"Task {task.id}: Skipping vision judge; accepting rendered candidates"
            )

            accepted = [c for c in candidates if c.status == CandidateStatus.RENDERED]

            if not accepted:
                # Persist a compact failure reason so the portal shows *why* nothing was accepted.
                status_counts: dict[str, int] = {}
                for c in candidates:
                    key = getattr(getattr(c, "status", None), "value", None) or str(getattr(c, "status", ""))
                    key = str(key or "unknown")
                    status_counts[key] = status_counts.get(key, 0) + 1

                bits: list[str] = []
                for c in candidates[:8]:
                    model = (getattr(c, "generator_model", "") or "").strip()
                    status = getattr(getattr(c, "status", None), "value", None) or str(getattr(c, "status", ""))
                    status = str(status or "unknown")
                    detail = (str(getattr(c, "error", "") or "").strip() or "").strip()
                    if detail:
                        bits.append(f"{model}:{status}:{detail[:140]}" if model else f"{status}:{detail[:140]}")

                error_summary = "No rendered candidates to accept."
                if status_counts:
                    pairs = ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items()))
                    error_summary += f" status_counts=({pairs})"
                if bits:
                    error_summary += " examples=" + " / ".join(bits[:3])

                await self.manifest.update_task(
                    task.id, status="no_accepted_candidates", error=error_summary[:900]
                )
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

            # Pick a representative candidate for edit-task seeding AND for winner-based
            # metrics/exports in skip_judge mode.
            #
            # IMPORTANT:
            # - In practice, tasks.selected_candidate_id is used as "winner" in downstream SQL metrics.
            # - If we pick the smallest file instead of the best deterministic quality, we will
            #   artificially depress shippable rate (e.g., selecting a candidate with axe criticals
            #   even when another accepted candidate passes).
            representative = sorted(accepted, key=self._skip_judge_sort_key)[0]

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
            # Optional: run a conservative vision gate that ONLY discards clearly broken renders.
            # This prevents blank/404/error-overlay pages from "winning on technicalities"
            # in non-skip_judge runs as well.
            if getattr(self.config.pipeline, "broken_vision_gate_enabled", False):
                candidates = await filter_broken_candidates(candidates, self.config)
                for candidate in candidates:
                    await self.manifest.save_candidate(candidate)

            candidates = await score_all_candidates(candidates, self.config)

            for candidate in candidates:
                await self.manifest.save_candidate(candidate)

            # Optional: section-level creativity refinement (surgical), now supported in full (non-skip_judge) runs.
            # This is the "creativity is the north star" loop: identify weak sections and patch ONLY those,
            # then re-render + re-score before the normal refinement/winner selection stages.
            if getattr(self.config.pipeline, "creativity_refinement_enabled", False):
                candidates = await self._refine_candidates_section_creativity(task, candidates)

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

    async def _refine_candidates_section_creativity(
        self,
        task: Task,
        candidates: list[Candidate],
    ) -> list[Candidate]:
        """Section-level creativity refinement (surgical) for non-skip_judge runs.

        Why this exists:
        - A page can be "shippable" and still be boring (generic features/testimonials/pricing).
        - Section-level creativity scoring already exists; this loop uses it to surgically upgrade weak sections
          without rewriting the entire page.

        Strategy:
        - Pick ONE representative scored candidate (keeps spend predictable)
        - Identify weak non-utility sections (exclude header/footer/faq)
        - Patch only up to 2 weak sections, anchored on 1–2 strong sections (or hero as fallback)
        - Re-validate build, re-render, run broken gate, then re-score
        """
        max_passes = max(
            0,
            int(getattr(self.config.pipeline, "creativity_max_refinement_passes", 2) or 2),
        )
        if max_passes <= 0:
            return candidates

        min_section_score = float(
            getattr(self.config.pipeline, "creativity_min_section_score", 0.7) or 0.7
        )
        try:
            creativity_gate_min = float(
                getattr(self.config.pipeline, "creativity_gate_min_avg", min_section_score)
                or min_section_score
            )
        except Exception:
            creativity_gate_min = min_section_score

        scored = [c for c in candidates if c.status == CandidateStatus.SCORED]
        if not scored:
            return candidates

        # Prefer deterministic-pass candidates first (shippable is still a necessity).
        det_scored = [c for c in scored if bool(getattr(c, "deterministic_passed", False))]
        if det_scored:
            scored = det_scored

        def _creativity_value(c: Candidate) -> float:
            for attr in ("section_creativity_key_avg", "section_creativity_core_avg", "section_creativity_avg"):
                val = getattr(c, attr, None)
                if val is None:
                    continue
                try:
                    return float(val or 0.0)
                except Exception:
                    continue
            return 0.0

        # Choose the candidate that is already the most creative (and reasonably well-scored),
        # then try to elevate its weak sections.
        scored.sort(key=lambda c: (-_creativity_value(c), -(float(c.score or 0.0))))
        rep = scored[0]
        rep_idx = next((i for i, c in enumerate(candidates) if c.id == rep.id), None)
        if rep_idx is None:
            return candidates

        def _is_utility_section_id(sid: str) -> bool:
            sid_l = (sid or "").lower().strip()
            return (
                ("header" in sid_l)
                or ("nav" in sid_l)
                or ("footer" in sid_l)
                or (sid_l == "faq" or "faq" in sid_l)
                or ("legal" in sid_l)
            )

        for pass_num in range(1, max_passes + 1):
            rep = candidates[rep_idx]
            if rep.status != CandidateStatus.SCORED:
                break

            sections = list(getattr(rep, "section_creativity", []) or [])
            confident = []
            for s in sections:
                if not isinstance(s, dict):
                    continue
                try:
                    conf = float(s.get("confidence") or 0.0)
                except Exception:
                    conf = 0.0
                if conf >= 0.5:
                    confident.append(s)

            # If we don't have usable section data, bail (this should be rare because score_all_candidates
            # computes section creativity when selection/refinement needs it).
            if not confident:
                break

            core = [
                s
                for s in confident
                if not _is_utility_section_id(str(s.get("id") or "").strip())
            ]
            if not core:
                break

            def _score(s: dict) -> float:
                try:
                    return float(s.get("score") or 0.0)
                except Exception:
                    return 0.0

            weak = [s for s in core if _score(s) < min_section_score]
            strong = [s for s in core if _score(s) >= min_section_score]

            if not weak:
                # Nothing to refine.
                break

            weak.sort(key=_score)
            strong.sort(key=_score, reverse=True)

            weak_ids = [str(s.get("id") or "").strip() for s in weak[:2] if str(s.get("id") or "").strip()]
            strong_ids = [str(s.get("id") or "").strip() for s in strong[:2] if str(s.get("id") or "").strip()]

            if not strong_ids:
                # If nothing is "strong", anchor on hero (if present) to keep the pass surgical,
                # otherwise anchor on the first core section.
                hero_id = next((str(s.get("id") or "").strip() for s in core if str(s.get("id") or "").strip() == "hero"), "")
                strong_ids = [hero_id] if hero_id else [weak_ids[0]] if weak_ids else []

            if not weak_ids or not strong_ids:
                break

            baseline = rep.model_copy(deep=True)

            log_info(
                f"Task {task.id}: Creativity refinement pass {pass_num}/{max_passes} "
                f"candidate {rep.id} weak={weak_ids} strong={strong_ids}"
            )

            refined = await refine_candidate_section_creativity(
                rep,
                self.config,
                strong_section_ids=strong_ids,
                weak_section_ids=weak_ids,
                section_feedback=sections,
            )
            await self.manifest.save_candidate(refined)

            # Re-validate build after refinement (and allow build-fix patching if needed)
            refined.status = CandidateStatus.GENERATED
            refined.error = None
            refined.build_logs = ""

            success, validated = await validate_with_retry(
                refined,
                self.config,
                patcher_fn=patch_candidate,
            )

            if not success or validated.status != CandidateStatus.BUILD_PASSED:
                log_warning(
                    f"Task {task.id}: Creativity refinement broke build for {rep.id}; reverting"
                )
                candidates[rep_idx] = baseline
                await self.manifest.save_candidate(baseline)
                break

            await self.manifest.save_candidate(validated)
            await render_candidate(validated, self.run_dir, self.config)
            await self.manifest.save_candidate(validated)

            # Re-run broken gate for the refined result (revert if it becomes broken)
            if getattr(self.config.pipeline, "broken_vision_gate_enabled", False):
                checked = await filter_broken_candidates([validated], self.config)
                validated = checked[0]
                await self.manifest.save_candidate(validated)
                if validated.status == CandidateStatus.DISCARDED:
                    log_warning(
                        f"Task {task.id}: Creativity-refined candidate {rep.id} flagged broken; reverting"
                    )
                    candidates[rep_idx] = baseline
                    await self.manifest.save_candidate(baseline)
                    break

            # Re-score to refresh judge + section creativity aggregates.
            rescored_list = await score_all_candidates([validated], self.config)
            rescored = rescored_list[0]
            await self.manifest.save_candidate(rescored)
            candidates[rep_idx] = rescored

            creativity_after = _creativity_value(rescored)
            log_info(
                f"Task {task.id}: Creativity refinement updated candidate {rescored.id}: "
                f"creativity={creativity_after:.2f} (target {creativity_gate_min:.2f})"
            )

            if creativity_after >= creativity_gate_min:
                break

        return candidates

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

            # If the vision judge failed (e.g., model not available / transient outage),
            # don't run refinement on "error feedback" — it wastes cycles and can degrade output.
            score_issues = list(getattr(candidate.score_details, "issues", []) or []) if candidate.score_details else []
            if any("vision scoring error" in str(x).lower() for x in score_issues):
                log_warning(
                    f"Task {task.id} candidate {candidate.id}: "
                    "Skipping refinement due to vision scoring error"
                )
                candidate.pass_scores = [float(candidate.score or 0.0)]
                candidate.pass_feedback = [candidate.score_details]
                await self.manifest.save_candidate(candidate)
                continue

            # Fix F: Skip refinement for high-creativity candidates that already pass deterministic gates.
            if self._should_skip_refinement_for_high_creativity(candidate):
                creativity_avg = float(getattr(candidate, "section_creativity_avg", 0.0) or 0.0)
                log_info(
                    f"Task {task.id} candidate {candidate.id}: "
                    f"Skipping refinement (deterministic_passed=true, creativity_avg={creativity_avg:.2f})"
                )
                candidate.pass_scores = [float(candidate.score or 0.0)]
                candidate.pass_feedback = [candidate.score_details]
                await self.manifest.save_candidate(candidate)
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

            # Fix F: Skip refinement for high-creativity candidates that already pass deterministic gates.
            if self._should_skip_refinement_for_high_creativity(candidate):
                creativity_avg = float(getattr(candidate, "section_creativity_avg", 0.0) or 0.0)
                log_info(
                    f"Task {task.id} candidate {candidate.id}: "
                    f"Skipping creative director refinement "
                    f"(deterministic_passed=true, creativity_avg={creativity_avg:.2f})"
                )
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
        - Shippable is a necessity (prefer deterministic_passed=True when available)
        - Creativity is the north star (optional creativity gate + creativity-first selection mode)

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

        # Shippable is a necessity: if any deterministic_passed candidates exist, only consider those.
        det_ok = [c for c in eligible if bool(getattr(c, "deterministic_passed", False))]
        if det_ok:
            eligible = det_ok

        def _creativity_metric(c: Candidate) -> tuple[float, str]:
            """Return the best available creativity metric for gating/selection.

            - Prefer key-section creativity (hero/features/proof/pricing/etc)
            - Fall back to non-utility-section creativity (excludes header/footer/faq)
            - Fall back to legacy avg across all sections
            """
            for attr, label in (
                ("section_creativity_key_avg", "key"),
                ("section_creativity_core_avg", "core"),
                ("section_creativity_avg", "all"),
            ):
                val = getattr(c, attr, None)
                if val is None:
                    continue
                try:
                    return float(val or 0.0), label
                except Exception:
                    continue
            return 0.0, "none"

        # Creativity gate (optional): prefer candidates meeting minimum creativity.
        creativity_gate_enabled = bool(getattr(self.config.pipeline, "creativity_gate_enabled", False))
        creativity_gate_enforce = bool(getattr(self.config.pipeline, "creativity_gate_enforce", False))
        try:
            creativity_gate_min = float(getattr(self.config.pipeline, "creativity_gate_min_avg", 0.7) or 0.7)
        except Exception:
            creativity_gate_min = 0.7
        try:
            creativity_gate_min_high = int(
                getattr(self.config.pipeline, "creativity_gate_min_high_sections", 0) or 0
            )
        except Exception:
            creativity_gate_min_high = 0

        def _creativity_gate_pass(c: Candidate) -> bool:
            ca, src = _creativity_metric(c)
            high_count_raw = getattr(c, "section_creativity_high_count", None)
            try:
                high_count = int(high_count_raw) if high_count_raw is not None else None
            except Exception:
                high_count = None

            avg_ok = True
            # If a minimum avg is requested, we require a measurable creativity metric to compare.
            if creativity_gate_min > 0.0:
                avg_ok = (src != "none") and (ca >= creativity_gate_min)

            high_ok = True
            if creativity_gate_min_high > 0:
                high_ok = (high_count is not None) and (high_count >= creativity_gate_min_high)

            return avg_ok and high_ok

        if creativity_gate_enabled:
            # Only apply the gate if we have at least one creativity score.
            has_any_creativity = any(
                (_creativity_metric(c)[1] != "none") or (getattr(c, "section_creativity_high_count", None) is not None)
                for c in eligible
            )
            if not has_any_creativity:
                log_warning(
                    f"Task {eligible[0].task_id if eligible else 'unknown'}: "
                    "Creativity gate enabled but no section_creativity_avg available; "
                    "skipping creativity gate for this task."
                )
            else:
                gated = []
                for c in eligible:
                    if _creativity_gate_pass(c):
                        gated.append(c)
                if gated:
                    eligible = gated
                elif creativity_gate_enforce:
                    log_warning(
                        f"Task {eligible[0].task_id if eligible else 'unknown'}: "
                        "No candidates met creativity gate "
                        f"(min_avg={creativity_gate_min:.2f}, min_high_sections={creativity_gate_min_high})."
                    )
                    return None

        # Sort by score (desc), then by file count (asc), code size (asc), fix rounds (asc)
        def sort_key(c: Candidate) -> tuple:
            total_size = sum(len(f.content) for f in c.files)
            if self.config.pipeline.skip_judge:
                return self._skip_judge_sort_key(c)
            selection_mode = str(getattr(self.config.pipeline, "selection_mode", "weighted") or "weighted").strip()
            raw_jw = getattr(self.config.pipeline, "selection_judge_weight", 0.6)
            raw_cw = getattr(self.config.pipeline, "selection_creativity_weight", 0.4)
            raw_scale = getattr(self.config.pipeline, "selection_creativity_scale", 10.0)
            try:
                judge_weight = float(raw_jw if raw_jw is not None else 0.6)
            except Exception:
                judge_weight = 0.6
            try:
                creativity_weight = float(raw_cw if raw_cw is not None else 0.4)
            except Exception:
                creativity_weight = 0.4
            try:
                creativity_scale = float(raw_scale if raw_scale is not None else 10.0)
            except Exception:
                creativity_scale = 10.0
            judge_score = float(c.score or 0.0)
            creativity_avg, creativity_src = _creativity_metric(c)
            creativity_all = float(getattr(c, "section_creativity_avg", 0.0) or 0.0)
            try:
                creativity_high = int(getattr(c, "section_creativity_high_count", 0) or 0)
            except Exception:
                creativity_high = 0
            # Important: judge score is typically 0..10 while creativity is 0..1.
            # Scale creativity before weighting so it actually influences selection.
            creativity_scaled = creativity_avg * creativity_scale
            weighted = judge_score * judge_weight + creativity_scaled * creativity_weight
            deterministic_ok = bool(getattr(c, "deterministic_passed", False))
            creativity_gate_pass = (not creativity_gate_enabled) or _creativity_gate_pass(c)

            # Log once per candidate for observability.
            log_info(
                f"Task {c.task_id} candidate {c.id}: "
                f"deterministic_passed={getattr(c, 'deterministic_passed', None)} "
                f"judge={judge_score:.2f} creativity={creativity_avg:.2f}({creativity_src}) "
                f"creativity_all={creativity_all:.2f} "
                f"high_sections={creativity_high} "
                f"weighted={weighted:.2f} "
                f"creativity_gate={'PASS' if creativity_gate_pass else 'FAIL'} "
                f"variant={getattr(c, 'variant_index', 0)}"
            )

            # Deterministic pass is required (when available), but creativity is the north star.
            # Two supported strategies:
            # - creativity_first: maximize creativity, then judge score
            # - weighted: maximize weighted (judge + scaled creativity)
            if selection_mode == "creativity_first":
                return (
                    0 if deterministic_ok else 1,
                    0 if creativity_gate_pass else 1,
                    -creativity_high,  # prefer more strong sections first
                    -creativity_avg,  # higher creativity wins
                    -judge_score,  # then judge score
                    int(getattr(c, "variant_index", 0) or 0),
                    len(c.files),
                    total_size,
                    c.fix_rounds,
                    int(getattr(c, "refine_passes", 0) or 0),
                )

            # Default: weighted selection (Fix E), but with correct scaling.
            return (
                0 if deterministic_ok else 1,
                0 if creativity_gate_pass else 1,
                -weighted,
                int(getattr(c, "variant_index", 0) or 0),
                len(c.files),
                total_size,
                c.fix_rounds,
                int(getattr(c, "refine_passes", 0) or 0),
            )

        eligible.sort(key=sort_key)
        return eligible[0]

    def _should_skip_refinement_for_high_creativity(self, candidate: Candidate) -> bool:
        """Fix F: Skip refinement when deterministic gates already pass AND creativity is high.

        This avoids "polishing away" distinctive layouts.
        """
        if not bool(getattr(self.config.pipeline, "refinement_skip_for_high_creativity", True)):
            return False

        if not bool(getattr(candidate, "deterministic_passed", False)):
            return False

        creativity_avg = (
            getattr(candidate, "section_creativity_key_avg", None)
            if getattr(candidate, "section_creativity_key_avg", None) is not None
            else (
                getattr(candidate, "section_creativity_core_avg", None)
                if getattr(candidate, "section_creativity_core_avg", None) is not None
                else getattr(candidate, "section_creativity_avg", None)
            )
        )
        if creativity_avg is None:
            return False

        try:
            creativity_avg_f = float(creativity_avg)
        except Exception:
            return False

        try:
            raw_threshold = getattr(self.config.pipeline, "refinement_creativity_skip_threshold", 0.7)
            threshold = float(raw_threshold if raw_threshold is not None else 0.7)
        except Exception:
            threshold = 0.7

        return creativity_avg_f >= threshold

    @staticmethod
    def _axe_impact_counts(candidate: Candidate) -> tuple[int, int]:
        """Return (critical_count, serious_count) for a candidate's axe violations."""
        critical = 0
        serious = 0
        for violation in candidate.axe_violations or []:
            impact = str(violation.get("impact") or "").lower()
            if impact == "critical":
                critical += 1
            elif impact == "serious":
                serious += 1
        return critical, serious

    def _skip_judge_sort_key(self, candidate: Candidate) -> tuple:
        """Deterministic-first selection key for skip_judge mode.

        Priority order:
        1) Prefer rendered/accepted candidates over build-only
        2) Prefer deterministic_passed=True (axe+Lighthouse)
        3) Minimize axe critical violations
        4) Maximize Lighthouse accessibility
        5) Minimize axe serious violations
        6) Maximize Lighthouse performance
        7) Tie-break: fewer files, smaller code, fewer fix rounds
        """
        status_rank = 0 if candidate.status in (
            CandidateStatus.RENDERED,
            CandidateStatus.ACCEPTED,
            CandidateStatus.SELECTED,
        ) else 1

        passed_rank = 0 if candidate.deterministic_passed else 1
        axe_critical, axe_serious = self._axe_impact_counts(candidate)
        lh_accessibility = float((candidate.lighthouse_scores or {}).get("accessibility") or 0.0)
        lh_performance = float((candidate.lighthouse_scores or {}).get("performance") or 0.0)
        total_size = sum(len(f.content) for f in candidate.files)

        return (
            status_rank,
            passed_rank,
            axe_critical,
            -lh_accessibility,
            axe_serious,
            -lh_performance,
            len(candidate.files),
            total_size,
            candidate.fix_rounds,
        )

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
