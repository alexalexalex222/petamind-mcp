"""Configuration management for TITAN Factory."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


@dataclass
class ModelConfig:
    """Configuration for a single model."""

    provider: str
    model: str | None
    publishable: bool = True
    max_tokens: int = 2000
    temperature: float = 0.7
    variants: int = 1
    # Provider-specific overrides
    # For GeminiProvider: force Vertex ADC even if GEMINI_API_KEY/GOOGLE_API_KEY is set.
    force_adc: bool = False


@dataclass
class PipelineConfig:
    """Pipeline stage configuration."""

    # If true, skip vision scoring entirely and accept rendered (build-passed) candidates.
    skip_judge: bool = False
    # If true, bypass UI_SPEC planning and have the UI generator(s) produce code
    # directly from the task prompt. This is used for "no pipeline" baselines.
    raw_generation_enabled: bool = False

    # === STYLE ROUTING (first-class aesthetic constraints) ===
    # When enabled, promptgen deterministically routes each niche to a style family/persona and
    # injects a "STYLE ROUTING (HARD CONSTRAINTS — MUST FOLLOW EXACTLY)" block into the task prompt.
    # The planner/generators should treat this as a hard constraint to avoid cross-vertical style monoculture.
    style_routing_enabled: bool = False

    # === STYLE GATES (cheap deterministic checks) ===
    # Run static heuristics on generated code to detect obvious style-family drift (e.g. cyber/terminal leak).
    # Default is observe-only; enforcement is opt-in.
    style_gates_enabled: bool = False
    style_gates_enforce: bool = False

    # === MEGAMIND 3-PASS REASONING ===
    # When enabled, uses 3 parallel sub-reasoners (bold/minimal/safe) to generate plans,
    # then synthesizes them into a unified best-of-3 plan before UI generation.
    megamind_enabled: bool = False
    # When enabled, use Megamind v2 reasoners (STYLE_GUARDIAN + BOLD_LAYOUT + BOLD_CONVERSION)
    # and a style-lock synthesizer to prevent cross-vertical style drift.
    megamind_v2_enabled: bool = False

    # === EVOL MUTATIONS (prompt diversification without style drift) ===
    evol_enabled: bool = False
    evol_passes: int = 2

    # === REFINEMENT LOOP CONFIG (ported from titan-ui-synth-pipeline) ===
    # Enable iterative refinement: if score < threshold, refine and re-score.
    refinement_enabled: bool = True
    # Score threshold for pass 2 refinement. If score < this after initial judge, refine.
    refine_pass2_threshold: float = 8.0
    # Score threshold for pass 3 refinement. If score < this after pass 2, refine again.
    refine_pass3_threshold: float = 8.5
    # Maximum refinement passes (not counting initial generation).
    max_refine_passes: int = 2

    # === CREATIVE DIRECTOR MODE ===
    # When enabled, replaces numeric scoring with qualitative creative director feedback.
    # This mode is more generous with creative risk-taking and focuses on production readiness
    # rather than aesthetic preferences. Refinement is guided by specific feedback rather than
    # score thresholds.
    creative_director_mode: bool = False
    # If true (and skip_judge is true), run a conservative vision pass that ONLY
    # discards clearly broken renders (runtime error overlays, 404 pages, blank pages).
    # It will NOT filter for "bad" aesthetics.
    broken_vision_gate_enabled: bool = False
    # Minimum confidence required to discard as broken. Higher = fewer false positives.
    broken_vision_gate_min_confidence: float = 0.85
    # If true, run a premium/ship-ready boolean gate (does not discard by default).
    # Primarily used to decide whether to trigger an automatic polish pass.
    premium_vision_gate_enabled: bool = False
    # Minimum confidence required to consider the premium gate decision actionable.
    # For example, if premium=false but confidence < threshold, we avoid polishing to reduce churn.
    premium_vision_gate_min_confidence: float = 0.75

    # === SECTION-LEVEL CREATIVITY REFINEMENT (skip_judge mode) ===
    # When enabled, uses the vision model to score sections (hero/features/testimonials/etc.)
    # for "distinctive vs generic". If mixed quality is detected, the pipeline applies a
    # surgical code patch to improve ONLY the weak sections, re-renders, and re-evaluates.
    creativity_refinement_enabled: bool = False
    creativity_min_section_score: float = 0.7
    creativity_max_refinement_passes: int = 2

    # === WINNER SELECTION (non-skip_judge mode) ===
    # Selection mode:
    # - weighted: deterministic_passed first, then weighted score (judge + creativity)
    # - creativity_first: deterministic_passed first, then creativity, then judge score
    selection_mode: str = "weighted"

    # Blend numeric vision score (0-10) with section creativity (0-1) to avoid
    # selecting "safe" winners when a more distinctive candidate is close.
    selection_judge_weight: float = 0.6
    selection_creativity_weight: float = 0.4
    # Scale section creativity to judge-score scale when computing weighted selection.
    # Judge score is typically 0..10 while section creativity is 0..1.
    selection_creativity_scale: float = 10.0

    # === TEMPERATURE CAP (advanced experimentation) ===
    # By default we cap UI generator temperatures at 1.0 for stability.
    # Set > 1.0 to explore higher-entropy outputs (may reduce format compliance).
    generator_temp_cap: float = 1.0

    # === CREATIVITY GATE (north star) ===
    # When enabled, prefer candidates that meet a minimum creativity threshold.
    # This does not replace deterministic gates; it complements them:
    # shippable is necessary; creativity is the north star.
    creativity_gate_enabled: bool = False
    creativity_gate_min_avg: float = 0.7
    # Optional: require N "high" sections (score >= 0.7, confidence >= 0.5) to avoid
    # punishing pages with a distinctive hero + mid-page moment but a more utilitarian FAQ/footer.
    # When 0, this requirement is disabled.
    creativity_gate_min_high_sections: int = 0
    # If true, tasks with no candidate meeting the creativity gate will have no winner selected.
    creativity_gate_enforce: bool = False

    # === REFINEMENT SKIP POLICY (Fix F) ===
    # Refinement tends to regress distinctive layouts toward generic patterns.
    # Skip refinement when deterministic gates already pass AND creativity is high.
    refinement_skip_for_high_creativity: bool = True
    refinement_creativity_skip_threshold: float = 0.7
    vision_score_threshold: float = 8.0
    max_fix_rounds: int = 2
    polish_loop_enabled: bool = True
    # Maximum number of polish attempts per candidate (quality improvement, not build fixes).
    polish_max_rounds: int = 1
    # Limit how many candidates per task we attempt to polish (to control spend).
    polish_max_candidates_per_task: int = 1
    # If true, generate follow-up edit tasks after landing pages complete.
    # Disable this when you want max_tasks to be an exact upper bound.
    generate_edit_tasks: bool = True
    # Optional override for the UI generator system prompt.
    # If set, uigen will read this file and use it instead of the built-in prompt.
    # Path can be absolute or relative to project root.
    uigen_system_prompt_path: str | None = None
    # Optional prompt variant list for the UI generator stage.
    # When provided (non-empty), uigen will generate candidates for ALL listed prompt variants
    # per model+variant, in the same run.
    #
    # YAML example:
    #   uigen_prompt_variants:
    #     - id: builtin
    #       source: builtin
    #       input_mode: ui_spec
    #     - id: titan
    #       source: file
    #       path: prompts/titan_ui_system_long.txt
    #       input_mode: page_brief
    #     - id: stacked
    #       input_mode: both
    #       parts:
    #         - source: builtin
    #         - source: file
    #           path: prompts/titan_ui_system_long.txt
    #         - source: inline
    #           text: |
    #             GLOBAL OVERRIDES:
    #             - No emojis. Inline SVG only.
    #
    # Supported fields per item:
    # - id: string (required; unique)
    # - source: "builtin" | "file" | "inline" | "stack" (default: "file" if path is present)
    # - path: string (required when source=="file")
    # - text: string (required when source=="inline")
    # - parts: list[dict] (required when source=="stack" OR to explicitly build a stacked prompt)
    # - input_mode: "ui_spec" | "page_brief" | "both" | "auto" (default: "auto")
    uigen_prompt_variants: list[dict[str, Any]] = field(default_factory=list)
    # Task prompt style pack used by promptgen (planner input).
    # - "niche": current niche/local-business prompts (default)
    # - "extended": SaaS/ecom/app-shell style prompts
    # - "os": OS demo prompts (dashboard-focused)
    # - "mixed": deterministic mix of the above
    task_prompt_pack: str = "niche"
    # Shuffle task order to avoid sampling only the earliest niches when max_tasks is set.
    shuffle_tasks: bool = True
    task_shuffle_seed: int = 1337
    # Optional list of page types to include (e.g., ["landing"]).
    # Leave empty to include the full default set.
    page_type_filter: list[str] = field(default_factory=list)
    tasks_per_niche: int = 7
    total_niches: int = 100
    model_timeout_ms: int = 120000
    build_timeout_ms: int = 240000
    render_timeout_ms: int = 90000
    # Starting port for Next.js servers during rendering. Use distinct ranges per run
    # when launching multiple pipelines in parallel to avoid port collisions.
    render_port_start: int = 3000

    # === DETERMINISTIC QUALITY GATES ===
    # Run cheap, measurable validation BEFORE subjective vision judging.
    # Intended to increase "shippable" rate and reduce wasted judge calls.
    deterministic_gates_enabled: bool = False
    # If true, failing deterministic gates will discard candidates (status=DISCARDED).
    deterministic_gates_enforce: bool = False

    # Accessibility (axe-core) gate
    axe_gate_enabled: bool = True
    # Fail only on these impacts (axe impact values: minor/moderate/serious/critical)
    axe_fail_impacts: list[str] = field(default_factory=lambda: ["critical"])
    axe_timeout_ms: int = 60000

    # Lighthouse gate (performance/accessibility/best-practices/seo)
    lighthouse_gate_enabled: bool = True
    lighthouse_preset: str = "desktop"  # "desktop" or "mobile"
    lighthouse_timeout_ms: int = 180000
    # Category score thresholds (0.0-1.0). Keys: performance/accessibility/best_practices/seo
    lighthouse_min_scores: dict[str, float] = field(
        default_factory=lambda: {
            "performance": 0.35,
            "accessibility": 0.70,
            "best_practices": 0.70,
            "seo": 0.60,
        }
    )


@dataclass
class BudgetConfig:
    """Budget and rate limiting configuration."""

    # How many pipeline tasks to run concurrently.
    # Note: Provider/build/render steps apply their own concurrency limits.
    task_concurrency: int = 1

    concurrency_vertex: int = 5
    concurrency_openrouter: int = 10
    concurrency_gemini: int = 2
    concurrency_build: int = 4
    concurrency_render: int = 1
    requests_per_min_vertex: int = 60
    requests_per_min_openrouter: int = 100
    # Gemini (Vertex / Google) rate limiting is separate from Vertex MaaS models.
    # Default conservatively to avoid 429s during vision scoring.
    requests_per_min_gemini: int = 20
    max_total_tasks: int | None = None
    stop_after_usd: float | None = None


@dataclass
class ExportConfig:
    """Export configuration."""

    holdout_niches: int = 12
    validation_split: float = 0.08
    holdout_niche_ids: list[str] = field(default_factory=list)


@dataclass
class GCSConfig:
    """Google Cloud Storage configuration."""

    bucket: str | None = None
    prefix: str = "titan-factory-outputs"
    upload_interval_tasks: int = 50


@dataclass
class VertexConfig:
    """Vertex AI configuration."""

    endpoint_template: str = (
        "https://{region}-aiplatform.googleapis.com/v1/projects/{project}"
        "/locations/{region}/endpoints/openapi/chat/completions"
    )


@dataclass
class OpenRouterConfig:
    """OpenRouter configuration."""

    base_url: str = "https://openrouter.ai/api/v1/chat/completions"


@dataclass
class Config:
    """Main configuration container."""

    # Models
    planner: ModelConfig
    ui_generators: list[ModelConfig]
    patcher: ModelConfig
    polisher: ModelConfig
    vision_judge: ModelConfig

    # Pipeline
    pipeline: PipelineConfig

    # Budget
    budget: BudgetConfig

    # Export
    export: ExportConfig

    # Cloud
    gcs: GCSConfig
    vertex: VertexConfig
    openrouter: OpenRouterConfig

    # Refiner models for iterative refinement loop (optional, defaults to patcher/planner)
    refine_reasoner: ModelConfig | None = None  # Plans refinement fixes based on judge feedback
    refine_coder: ModelConfig | None = None  # Applies targeted fixes
    # Megamind per-reasoner model configs (optional; fallback to planner config)
    megamind_bold: ModelConfig | None = None
    megamind_minimal: ModelConfig | None = None
    megamind_safe: ModelConfig | None = None
    megamind_synthesizer: ModelConfig | None = None

    # Paths
    project_root: Path = field(default_factory=lambda: Path(__file__).parent.parent.parent)
    template_path: Path = field(default_factory=Path)
    prompts_path: Path = field(default_factory=Path)
    out_path: Path = field(default_factory=Path)

    # Environment
    google_project: str = ""
    google_region: str = ""
    openrouter_api_key: str = ""

    def __post_init__(self) -> None:
        """Set up paths after initialization."""
        if not self.template_path.exists():
            self.template_path = self.project_root / "templates" / "nextjs_app_router_tailwind"
        if not self.prompts_path.exists():
            self.prompts_path = self.project_root / "prompts"
        if not self.out_path.exists():
            self.out_path = self.project_root / "out"

        # Load from environment if not set
        if not self.google_project:
            self.google_project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
        if not self.google_region:
            self.google_region = os.getenv("GOOGLE_CLOUD_REGION", "us-central1")
        if not self.openrouter_api_key:
            self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "")

    def get_vertex_endpoint(self) -> str:
        """Get the Vertex AI endpoint URL."""
        return self.vertex.endpoint_template.format(
            project=self.google_project,
            region=self.google_region,
        )

    def get_publishable_generators(self) -> list[ModelConfig]:
        """Get only publishable UI generators."""
        return [g for g in self.ui_generators if g.publishable]


def _parse_model_config(data: dict[str, Any]) -> ModelConfig:
    """Parse a model configuration from dict."""
    return ModelConfig(
        provider=data.get("provider", "vertex"),
        model=data.get("model"),
        publishable=data.get("publishable", True),
        max_tokens=data.get("max_tokens", 2000),
        temperature=data.get("temperature", 0.7),
        variants=data.get("variants", 1),
        force_adc=bool(data.get("force_adc", False)),
    )


def load_config(config_path: str | Path | None = None) -> Config:
    """Load configuration from YAML file.

    Args:
        config_path: Path to config file. Defaults to config/config.yaml.

    Returns:
        Loaded configuration.
    """
    # Determine config path
    if config_path is None:
        config_path = os.getenv("TITAN_CONFIG_PATH")
    if config_path is None:
        project_root = Path(__file__).parent.parent.parent
        config_path = project_root / "config" / "config.yaml"
    else:
        config_path = Path(config_path)

    # Load YAML
    with open(config_path) as f:
        data = yaml.safe_load(f)

    models = data.get("models", {})
    pipeline = data.get("pipeline", {})
    budget = data.get("budget", {})
    export = data.get("export", {})
    gcs = data.get("gcs", {})
    vertex = data.get("vertex", {})
    openrouter = data.get("openrouter", {})

    selection_cfg = pipeline.get("selection", {})
    if not isinstance(selection_cfg, dict):
        selection_cfg = {}

    creativity_gate_cfg = pipeline.get("creativity_gate", {})
    if not isinstance(creativity_gate_cfg, dict):
        creativity_gate_cfg = {}

    refinement_cfg = pipeline.get("refinement", {})
    if not isinstance(refinement_cfg, dict):
        refinement_cfg = {}

    selection_mode = str(selection_cfg.get("mode", pipeline.get("selection_mode", "weighted")) or "weighted").strip()

    # Selection weights (Fix E). Support both nested and legacy flat keys.
    try:
        selection_judge_weight = float(
            selection_cfg.get("judge_weight", pipeline.get("selection_judge_weight", 0.6))
        )
    except Exception:
        selection_judge_weight = 0.6

    try:
        selection_creativity_weight = float(
            selection_cfg.get(
                "creativity_weight", pipeline.get("selection_creativity_weight", 0.4)
            )
        )
    except Exception:
        selection_creativity_weight = 0.4

    try:
        selection_creativity_scale = float(
            selection_cfg.get(
                "creativity_scale", pipeline.get("selection_creativity_scale", 10.0)
            )
        )
    except Exception:
        selection_creativity_scale = 10.0

    try:
        generator_temp_cap = float(pipeline.get("generator_temp_cap", 1.0) or 1.0)
    except Exception:
        generator_temp_cap = 1.0

    creativity_gate_enabled = bool(
        creativity_gate_cfg.get(
            "enabled", pipeline.get("creativity_gate_enabled", False)
        )
    )
    try:
        creativity_gate_min_avg = float(
            creativity_gate_cfg.get(
                "min_avg", pipeline.get("creativity_gate_min_avg", 0.7)
            )
        )
    except Exception:
        creativity_gate_min_avg = 0.7
    try:
        creativity_gate_min_high_sections = int(
            creativity_gate_cfg.get(
                "min_high_sections",
                pipeline.get("creativity_gate_min_high_sections", 0),
            )
            or 0
        )
    except Exception:
        creativity_gate_min_high_sections = 0
    creativity_gate_enforce = bool(
        creativity_gate_cfg.get(
            "enforce", pipeline.get("creativity_gate_enforce", False)
        )
    )

    # Refinement skip policy (Fix F). Support both nested and legacy flat keys.
    refinement_skip_for_high_creativity = bool(
        refinement_cfg.get(
            "skip_for_high_creativity",
            pipeline.get("refinement_skip_for_high_creativity", True),
        )
    )
    try:
        refinement_creativity_skip_threshold = float(
            refinement_cfg.get(
                "creativity_skip_threshold",
                pipeline.get("refinement_creativity_skip_threshold", 0.7),
            )
        )
    except Exception:
        refinement_creativity_skip_threshold = 0.7

    # Parse model configs
    planner = _parse_model_config(models.get("planner", {}))
    ui_generators = [_parse_model_config(g) for g in models.get("ui_generators", [])]
    patcher = _parse_model_config(models.get("patcher", {}))
    polisher = _parse_model_config(models.get("polisher", models.get("patcher", {})))
    vision_judge = _parse_model_config(models.get("vision_judge", {}))
    # Megamind reasoner models (optional; fallback happens in megamind.py)
    megamind_bold = (
        _parse_model_config(models.get("megamind_bold"))
        if models.get("megamind_bold")
        else None
    )
    megamind_minimal = (
        _parse_model_config(models.get("megamind_minimal"))
        if models.get("megamind_minimal")
        else None
    )
    megamind_safe = (
        _parse_model_config(models.get("megamind_safe"))
        if models.get("megamind_safe")
        else None
    )
    megamind_synthesizer = (
        _parse_model_config(models.get("megamind_synthesizer"))
        if models.get("megamind_synthesizer")
        else None
    )
    # Refiner models (optional, fallback to patcher for refine_coder, planner for refine_reasoner)
    refine_reasoner = (
        _parse_model_config(models.get("refine_reasoner"))
        if models.get("refine_reasoner")
        else _parse_model_config(models.get("planner", {}))
    )
    refine_coder = (
        _parse_model_config(models.get("refine_coder"))
        if models.get("refine_coder")
        else _parse_model_config(models.get("patcher", {}))
    )

    project_root = Path(__file__).parent.parent.parent

    # Deterministic gate defaults (duplicated here so config parsing can merge user overrides
    # without accidentally zeroing the defaults when the key is missing).
    default_lighthouse_min_scores: dict[str, float] = {
        "performance": 0.35,
        "accessibility": 0.70,
        "best_practices": 0.70,
        "seo": 0.60,
    }
    lighthouse_min_scores = dict(default_lighthouse_min_scores)
    lh_overrides = pipeline.get("lighthouse_min_scores")
    if isinstance(lh_overrides, dict):
        # Keep only scalar values that can be cast to float
        for k, v in lh_overrides.items():
            try:
                lighthouse_min_scores[str(k)] = float(v)
            except Exception:
                continue

    return Config(
        planner=planner,
        ui_generators=ui_generators,
        patcher=patcher,
        polisher=polisher,
        vision_judge=vision_judge,
        refine_reasoner=refine_reasoner,
        refine_coder=refine_coder,
        megamind_bold=megamind_bold,
        megamind_minimal=megamind_minimal,
        megamind_safe=megamind_safe,
        megamind_synthesizer=megamind_synthesizer,
        pipeline=PipelineConfig(
            skip_judge=pipeline.get("skip_judge", False),
            raw_generation_enabled=bool(pipeline.get("raw_generation_enabled", False)),
            style_routing_enabled=bool(pipeline.get("style_routing_enabled", False)),
            style_gates_enabled=bool(pipeline.get("style_gates_enabled", False)),
            style_gates_enforce=bool(pipeline.get("style_gates_enforce", False)),
            # Megamind 3-pass reasoning
            megamind_enabled=pipeline.get("megamind_enabled", False),
            megamind_v2_enabled=bool(pipeline.get("megamind_v2_enabled", False)),
            evol_enabled=bool(pipeline.get("evol_enabled", False)),
            evol_passes=int(pipeline.get("evol_passes", 2) or 2),
            # Refinement loop config
            refinement_enabled=pipeline.get("refinement_enabled", True),
            refine_pass2_threshold=float(pipeline.get("refine_pass2_threshold", 8.0)),
            refine_pass3_threshold=float(pipeline.get("refine_pass3_threshold", 8.5)),
            max_refine_passes=int(pipeline.get("max_refine_passes", 2)),
            creative_director_mode=pipeline.get("creative_director_mode", False),
            broken_vision_gate_enabled=pipeline.get("broken_vision_gate_enabled", False),
            broken_vision_gate_min_confidence=float(
                pipeline.get("broken_vision_gate_min_confidence", 0.85) or 0.85
            ),
            premium_vision_gate_enabled=pipeline.get("premium_vision_gate_enabled", False),
            premium_vision_gate_min_confidence=float(
                pipeline.get("premium_vision_gate_min_confidence", 0.75) or 0.75
            ),
            creativity_refinement_enabled=bool(pipeline.get("creativity_refinement_enabled", False)),
            creativity_min_section_score=float(
                pipeline.get("creativity_min_section_score", 0.7) or 0.7
            ),
            creativity_max_refinement_passes=int(
                pipeline.get("creativity_max_refinement_passes", 2) or 2
            ),
            selection_mode=selection_mode,
            selection_judge_weight=selection_judge_weight,
            selection_creativity_weight=selection_creativity_weight,
            selection_creativity_scale=selection_creativity_scale,
            generator_temp_cap=generator_temp_cap,
            creativity_gate_enabled=creativity_gate_enabled,
            creativity_gate_min_avg=creativity_gate_min_avg,
            creativity_gate_min_high_sections=creativity_gate_min_high_sections,
            creativity_gate_enforce=creativity_gate_enforce,
            refinement_skip_for_high_creativity=refinement_skip_for_high_creativity,
            refinement_creativity_skip_threshold=refinement_creativity_skip_threshold,
            vision_score_threshold=pipeline.get("vision_score_threshold", 8.0),
            max_fix_rounds=pipeline.get("max_fix_rounds", 2),
            polish_loop_enabled=pipeline.get("polish_loop_enabled", True),
            polish_max_rounds=int(pipeline.get("polish_max_rounds", 1) or 1),
            polish_max_candidates_per_task=int(
                pipeline.get("polish_max_candidates_per_task", 1) or 1
            ),
            generate_edit_tasks=pipeline.get("generate_edit_tasks", True),
            uigen_system_prompt_path=pipeline.get("uigen_system_prompt_path"),
            uigen_prompt_variants=list(pipeline.get("uigen_prompt_variants") or []),
            task_prompt_pack=str(pipeline.get("task_prompt_pack", "niche") or "niche"),
            shuffle_tasks=pipeline.get("shuffle_tasks", True),
            task_shuffle_seed=pipeline.get("task_shuffle_seed", 1337),
            page_type_filter=pipeline.get("page_type_filter", []),
            tasks_per_niche=pipeline.get("tasks_per_niche", 7),
            total_niches=pipeline.get("total_niches", 100),
            model_timeout_ms=pipeline.get("model_timeout_ms", 120000),
            build_timeout_ms=pipeline.get("build_timeout_ms", 240000),
            render_timeout_ms=pipeline.get("render_timeout_ms", 90000),
            render_port_start=int(pipeline.get("render_port_start", 3000) or 3000),
            # Deterministic quality gates (axe + Lighthouse)
            deterministic_gates_enabled=bool(pipeline.get("deterministic_gates_enabled", False)),
            deterministic_gates_enforce=bool(pipeline.get("deterministic_gates_enforce", False)),
            axe_gate_enabled=bool(pipeline.get("axe_gate_enabled", True)),
            axe_fail_impacts=list(pipeline.get("axe_fail_impacts") or ["critical"]),
            axe_timeout_ms=int(pipeline.get("axe_timeout_ms", 60000) or 60000),
            lighthouse_gate_enabled=bool(pipeline.get("lighthouse_gate_enabled", True)),
            lighthouse_preset=str(pipeline.get("lighthouse_preset", "desktop") or "desktop"),
            lighthouse_timeout_ms=int(pipeline.get("lighthouse_timeout_ms", 180000) or 180000),
            lighthouse_min_scores=lighthouse_min_scores,
        ),
        budget=BudgetConfig(
            task_concurrency=budget.get("task_concurrency", 1),
            concurrency_vertex=budget.get("concurrency_vertex", 5),
            concurrency_openrouter=budget.get("concurrency_openrouter", 10),
            concurrency_gemini=budget.get("concurrency_gemini", 2),
            concurrency_build=budget.get("concurrency_build", 4),
            concurrency_render=budget.get("concurrency_render", 1),
            requests_per_min_vertex=budget.get("requests_per_min_vertex", 60),
            requests_per_min_openrouter=budget.get("requests_per_min_openrouter", 100),
            requests_per_min_gemini=budget.get("requests_per_min_gemini", 20),
            max_total_tasks=budget.get("max_total_tasks"),
            stop_after_usd=budget.get("stop_after_usd"),
        ),
        export=ExportConfig(
            holdout_niches=export.get("holdout_niches", 12),
            validation_split=export.get("validation_split", 0.08),
            holdout_niche_ids=export.get("holdout_niche_ids", []),
        ),
        gcs=GCSConfig(
            bucket=gcs.get("bucket"),
            prefix=gcs.get("prefix", "titan-factory-outputs"),
            upload_interval_tasks=gcs.get("upload_interval_tasks", 50),
        ),
        vertex=VertexConfig(
            endpoint_template=vertex.get(
                "endpoint_template",
                "https://{region}-aiplatform.googleapis.com/v1/projects/{project}"
                "/locations/{region}/endpoints/openapi/chat/completions",
            )
        ),
        openrouter=OpenRouterConfig(
            base_url=openrouter.get("base_url", "https://openrouter.ai/api/v1/chat/completions")
        ),
        project_root=project_root,
        template_path=project_root / "templates" / "nextjs_app_router_tailwind",
        prompts_path=project_root / "prompts",
        out_path=project_root / "out",
    )
