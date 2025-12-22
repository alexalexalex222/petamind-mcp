"""Pydantic models and JSON Schema definitions for TITAN Factory."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# === Enums ===


class PageType(str, Enum):
    """Supported page types."""

    LANDING = "landing"
    DIRECTORY_HOME = "directory_home"
    CITY_INDEX = "city_index"
    CATEGORY_INDEX = "category_index"
    LISTING_PROFILE = "listing_profile"
    ADMIN_DASHBOARD = "admin_dashboard"
    EDIT = "edit"


class Mood(str, Enum):
    """UI mood/theme."""

    DARK = "dark"
    LIGHT = "light"


class Accent(str, Enum):
    """Accent color options."""

    BLUE = "blue"
    TEAL = "teal"
    VIOLET = "violet"
    GREEN = "green"
    ORANGE = "orange"
    RED = "red"


class Radius(str, Enum):
    """Border radius style."""

    SOFT = "soft"
    MEDIUM = "medium"


class Density(str, Enum):
    """Layout density."""

    AIRY = "airy"
    BALANCED = "balanced"
    COMPACT = "compact"


class Navigation(str, Enum):
    """Navigation style."""

    MINIMAL = "minimal"
    STANDARD = "standard"


# === UI_SPEC Schema ===


class Niche(BaseModel):
    """Niche identification."""

    id: str = Field(..., description="Unique niche identifier")
    vertical: str = Field(..., description="Industry vertical (e.g., 'fitness', 'legal')")
    pattern: str = Field(..., description="UI pattern flavor (e.g., 'editorial', 'minimal')")


class Brand(BaseModel):
    """Brand configuration."""

    name: str = Field(..., description="Brand/business name")
    mood: Mood = Field(..., description="Dark or light theme")
    accent: Accent = Field(..., description="Primary accent color")
    style_keywords: list[str] = Field(
        ...,
        description="Style descriptors",
        min_length=1,
        max_length=5,
    )
    radius: Radius = Field(default=Radius.MEDIUM, description="Border radius style")
    density: Density = Field(default=Density.BALANCED, description="Layout density")


class CTA(BaseModel):
    """Call-to-action configuration."""

    primary: str = Field(..., description="Primary CTA text")
    secondary: str | None = Field(default=None, description="Optional secondary CTA")


class Testimonial(BaseModel):
    """Testimonial content."""

    name: str = Field(..., description="Customer name")
    text: str = Field(..., description="Testimonial text")


class FAQ(BaseModel):
    """FAQ item."""

    q: str = Field(..., description="Question")
    a: str = Field(..., description="Answer")


class Content(BaseModel):
    """Page content configuration."""

    business_name: str = Field(..., description="Business name")
    city: str = Field(..., description="City/location")
    offer: str = Field(..., description="Main value proposition")
    audience: str = Field(..., description="Target audience")
    highlights: list[str] = Field(
        ...,
        description="Key feature highlights",
        min_length=1,
        max_length=6,
    )
    testimonials: list[Testimonial] = Field(
        default_factory=list,
        description="Customer testimonials",
        max_length=4,
    )
    faq: list[FAQ] = Field(
        default_factory=list,
        description="FAQ items",
        max_length=6,
    )


class Section(BaseModel):
    """Layout section configuration."""

    id: str = Field(..., description="Section identifier")
    must_include: list[str] | None = Field(
        default=None,
        description="Required elements in section",
    )
    optional: bool = Field(default=False, description="Whether section is optional")


class Layout(BaseModel):
    """Page layout configuration."""

    sections: list[Section] = Field(..., description="Page sections in order")
    navigation: Navigation = Field(default=Navigation.STANDARD, description="Nav style")
    notes: str = Field(default="", description="Additional layout notes")


class EditTask(BaseModel):
    """Edit/refactor task configuration."""

    enabled: bool = Field(default=False, description="Whether this is an edit task")
    instructions: str = Field(default="", description="Edit instructions")
    code_old: str = Field(default="", description="Original code to edit")


class UISpec(BaseModel):
    """Complete UI specification."""

    niche: Niche
    page_type: PageType
    brand: Brand
    cta: CTA
    content: Content
    layout: Layout
    edit_task: EditTask = Field(default_factory=EditTask)

    class Config:
        use_enum_values = True


# === Generated File Schema ===


class GeneratedFile(BaseModel):
    """A generated file."""

    path: str = Field(..., description="File path relative to project root")
    content: str = Field(..., description="File content")


class UIGenOutput(BaseModel):
    """Output from UI generator."""

    files: list[GeneratedFile] = Field(
        ...,
        description="Generated files",
        min_length=1,
    )
    notes: list[str] = Field(default_factory=list, description="Brief notes about implementation")


class PatchOutput(BaseModel):
    """Output from patcher."""

    files: list[GeneratedFile] | None = Field(
        default=None,
        description="Full file rewrites",
    )
    patches: list[dict] | None = Field(
        default=None,
        description="Unified diff patches",
    )


# === Training Output Schema ===


class TrainingOutput(BaseModel):
    """Final training data output format."""

    ui_spec: UISpec
    files: list[GeneratedFile]


# === Judge Schema ===


class JudgeScore(BaseModel):
    """Vision judge scoring output."""

    score: float = Field(..., ge=0, le=10, description="Score 0-10")
    passing: bool = Field(..., description="Whether candidate passes threshold")
    issues: list[str] = Field(default_factory=list, description="Identified issues")
    highlights: list[str] = Field(default_factory=list, description="Positive highlights")
    fix_suggestions: list[str] = Field(default_factory=list, description="Suggested fixes")


class PremiumGate(BaseModel):
    """Vision-based premium/ship-ready assessment (boolean, not a numeric score)."""

    premium: bool = Field(..., description="Whether the page looks premium/ship-ready")
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence in the premium/non-premium classification (0-1)",
    )
    issues: list[str] = Field(default_factory=list, description="Top issues blocking premium quality")
    fix_suggestions: list[str] = Field(
        default_factory=list, description="Concrete suggestions to reach premium quality"
    )


class CreativeDirectorFeedback(BaseModel):
    """Qualitative creative director feedback - replaces numeric scoring.

    Instead of harsh numeric scores that punish creative risk-taking,
    this provides rich qualitative feedback that guides refinement
    while preserving creative emergence.
    """

    # Simple gates
    shippable: bool = Field(
        ...,
        description="Is this production-ready as-is? (yes = stop refining)",
    )
    obviously_broken: bool = Field(
        default=False,
        description="Is there a critical error (blank page, runtime error, missing content)? Separate from 'imperfect'.",
    )

    # What to preserve (don't let refinement break these)
    preserve: list[str] = Field(
        default_factory=list,
        description="Creative choices and elements that are working well - do NOT change these",
    )

    # What's needed for production
    missing_for_production: list[str] = Field(
        default_factory=list,
        description="Specific things that MUST be fixed before shipping (not style preferences)",
    )

    # Creative elevation suggestions
    creative_elevations: list[str] = Field(
        default_factory=list,
        description="How a master designer would elevate this - optional improvements, not requirements",
    )

    # Site-specific context
    appropriate_for_type: bool = Field(
        default=True,
        description="Is the layout/length/density appropriate for this type of site?",
    )
    type_feedback: str = Field(
        default="",
        description="Feedback on whether the design matches the site type's intent",
    )


# === Task Schema ===


class Task(BaseModel):
    """A single generation task."""

    id: str = Field(..., description="Deterministic task ID")
    niche_id: str = Field(..., description="Parent niche ID")
    page_type: PageType
    seed: int = Field(..., description="Random seed for variety")
    prompt: str = Field(..., description="Task prompt text")
    is_edit: bool = Field(default=False, description="Whether this is an edit task")
    code_old: str | None = Field(default=None, description="Original code for edit tasks")


class NicheDefinition(BaseModel):
    """Niche definition."""

    id: str
    vertical: str
    pattern: str
    description: str


# === Candidate Schema ===


class CandidateStatus(str, Enum):
    """Candidate processing status."""

    PENDING = "pending"
    GENERATED = "generated"
    BUILD_FAILED = "build_failed"
    BUILD_PASSED = "build_passed"
    RENDERED = "rendered"
    SCORED = "scored"
    ACCEPTED = "accepted"
    SELECTED = "selected"
    DISCARDED = "discarded"


class TeacherModel(BaseModel):
    """Tracks a model used in the generation chain."""

    provider: str = Field(..., description="Provider name (vertex, openrouter)")
    model: str = Field(..., description="Model ID")
    publishable: bool = Field(default=True, description="Whether model output is publishable")


class Candidate(BaseModel):
    """A generated candidate."""

    id: str = Field(..., description="Candidate ID")
    task_id: str = Field(..., description="Parent task ID")
    generator_model: str = Field(..., description="Model that generated this")
    variant_index: int = Field(..., description="Variant number")
    status: CandidateStatus = Field(default=CandidateStatus.PENDING)
    ui_spec: UISpec | None = Field(default=None)
    files: list[GeneratedFile] = Field(default_factory=list)
    build_logs: str = Field(default="")
    fix_rounds: int = Field(default=0)
    screenshot_paths: dict[str, str] = Field(default_factory=dict)
    score: float | None = Field(default=None)
    score_details: JudgeScore | None = Field(default=None)
    premium_gate: PremiumGate | None = Field(
        default=None,
        description="Optional premium/ship-ready assessment from a vision model",
    )
    creative_director_feedback: "CreativeDirectorFeedback | None" = Field(
        default=None,
        description="Qualitative creative director feedback (replaces numeric scoring when enabled)",
    )
    publishable: bool = Field(default=True)
    error: str | None = Field(default=None)
    polish_rounds: int = Field(
        default=0,
        description="Number of polish (quality improvement) rounds applied (separate from build-fix rounds)",
    )

    # === REFINEMENT LOOP TRACKING (ported from titan-ui-synth-pipeline) ===
    refine_passes: int = Field(
        default=0,
        description="Number of refinement passes applied (0 = initial generation only)",
    )
    pass_scores: list[float] = Field(
        default_factory=list,
        description="Scores after each pass [pass1_score, pass2_score, pass3_score, ...]",
    )
    pass_feedback: list[JudgeScore | None] = Field(
        default_factory=list,
        description="JudgeScore feedback after each pass (for refinement reasoning)",
    )
    refine_models: list[TeacherModel] = Field(
        default_factory=list,
        description="Models used for refinement passes (refine_reasoner + refine_coder per pass)",
    )

    # Raw model response - includes <think> blocks for training reasoning
    raw_generator_response: str | None = Field(
        default=None,
        description="Full generator response including <think> reasoning blocks"
    )
    # Prompt variant ID used for UI generation (when multiple uigen prompts are used in one run)
    uigen_prompt_id: str = Field(
        default="default",
        description="UI generator prompt variant ID",
    )

    # Teacher chain - tracks all models used to produce this candidate
    planner_model: TeacherModel | None = Field(
        default=None,
        description="Model that generated the UI_SPEC"
    )
    patcher_models: list[TeacherModel] = Field(
        default_factory=list,
        description="Models that patched build errors (in order)"
    )

    def compute_publishable(self) -> bool:
        """Compute publishable based on full teacher chain.

        A candidate is only publishable if ALL models in the chain
        are publishable (planner AND generator AND all patchers AND all refiners).
        """
        # Check generator (already stored in publishable field from generator config)
        if not self.publishable:
            return False

        # Check planner
        if self.planner_model and not self.planner_model.publishable:
            return False

        # Check all patchers
        for patcher in self.patcher_models:
            if not patcher.publishable:
                return False

        # Check all refiner models
        for refiner in self.refine_models:
            if not refiner.publishable:
                return False

        return True


# === JSON Schema Export ===

UI_SPEC_JSON_SCHEMA = UISpec.model_json_schema()


def validate_ui_spec(data: dict) -> UISpec:
    """Validate and parse UI_SPEC JSON.

    Args:
        data: Raw JSON dict

    Returns:
        Validated UISpec

    Raises:
        ValidationError: If validation fails
    """
    return UISpec.model_validate(data)


def validate_uigen_output(data: dict) -> UIGenOutput:
    """Validate UI generator output.

    Args:
        data: Raw JSON dict

    Returns:
        Validated UIGenOutput

    Raises:
        ValidationError: If validation fails
    """
    return UIGenOutput.model_validate(data)


def validate_judge_score(data: dict) -> JudgeScore:
    """Validate judge score output.

    Args:
        data: Raw JSON dict

    Returns:
        Validated JudgeScore

    Raises:
        ValidationError: If validation fails
    """
    # The judge prompt historically used "pass" while our schema uses "passing".
    # Normalize to support both.
    if "passing" not in data and "pass" in data:
        data = dict(data)
        data["passing"] = data.get("pass")

    return JudgeScore.model_validate(data)
