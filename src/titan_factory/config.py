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


@dataclass
class PipelineConfig:
    """Pipeline stage configuration."""

    # If true, skip vision scoring entirely and accept rendered (build-passed) candidates.
    skip_judge: bool = False

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

    # Parse model configs
    planner = _parse_model_config(models.get("planner", {}))
    ui_generators = [_parse_model_config(g) for g in models.get("ui_generators", [])]
    patcher = _parse_model_config(models.get("patcher", {}))
    polisher = _parse_model_config(models.get("polisher", models.get("patcher", {})))
    vision_judge = _parse_model_config(models.get("vision_judge", {}))
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

    return Config(
        planner=planner,
        ui_generators=ui_generators,
        patcher=patcher,
        polisher=polisher,
        vision_judge=vision_judge,
        refine_reasoner=refine_reasoner,
        refine_coder=refine_coder,
        pipeline=PipelineConfig(
            skip_judge=pipeline.get("skip_judge", False),
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
