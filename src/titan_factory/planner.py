"""Planner module - generates UI_SPEC from task prompts."""

from titan_factory.config import Config
from titan_factory.providers import Message, ProviderFactory
from titan_factory.schema import Task, UISpec, validate_ui_spec
from titan_factory.utils import extract_json_strict, log_error, log_info, log_warning

# Megamind import (lazy to avoid circular imports)
_megamind_module = None


def _get_megamind():
    """Lazy import of megamind module."""
    global _megamind_module
    if _megamind_module is None:
        from titan_factory import megamind

        _megamind_module = megamind
    return _megamind_module

# === Planner System Prompt ===
# NOTE: This stage must be maximally strict because it gates the rest of the pipeline.
PLANNER_SYSTEM_PROMPT = """You are a UI specification generator.

Given a task prompt, output a SINGLE JSON object matching the UI_SPEC schema EXACTLY.

ABSOLUTE OUTPUT RULES (discarded if violated):
- Output MUST be valid JSON (double quotes, no trailing commas).
- Output MUST be ONLY the JSON object (no markdown, no code fences, no commentary, no <think>).
- Output MUST start with { and end with }.

ENUM VALUES (use these lowercase strings exactly):
- page_type: landing | directory_home | city_index | category_index | listing_profile | admin_dashboard | edit
- brand.mood: dark | light
- brand.accent: blue | teal | violet | green | orange | red | amber | rose | cyan | lime | fuchsia
- brand.radius: soft | medium
- brand.density: airy | balanced | compact
- layout.navigation: minimal | standard
- brand.style_keywords: 1-12 short descriptors (free-form strings; keep them concise)

THEME SELECTION (WHEN NOT SPECIFIED IN THE TASK):
- If the task prompt explicitly specifies mood/accent, honor it.
- Otherwise, choose brand.mood and brand.accent yourself to fit the business and audience.
- Avoid always defaulting to the same accent (e.g., green/teal). Use the full accent set over time.
- Ensure the chosen mood/accent pairing supports premium contrast and readability.

IMPORTANT:
- If the task prompt contains explicit lines like:
  - "brand.mood: light|dark"
  - "brand.accent: blue|teal|violet|green|orange|red|amber|rose|cyan|lime|fuchsia"
  treat them as HARD REQUIREMENTS. Your UI_SPEC MUST match them exactly.
  (These lines are injected to keep dataset color distribution healthy; do not override them.)

STYLE ROUTING (WHEN PRESENT IN THE TASK PROMPT):
- The task prompt may include a block starting with:
  "STYLE ROUTING (HARD CONSTRAINTS — MUST FOLLOW EXACTLY)"
  Treat it as a HARD REQUIREMENT:
  - Do NOT drift into a different aesthetic (e.g., cyber/terminal/neon) unless style_family explicitly allows it.
  - Do NOT introduce motifs/keywords listed in style_avoid.
  - Ensure brand.style_keywords reflects style_keywords_mandatory (and excludes style_avoid).
  - If present, copy these into UI_SPEC.brand fields to preserve intent downstream:
    * style_family, style_persona, style_keywords_mandatory, style_avoid, imagery_style, layout_motif

CREATIVE RISK (WHEN PRESENT IN THE TASK PROMPT):
- The task prompt may include a line like: "Creative risk: high|medium|low".
- Interpret it as:
  - high: choose a bolder blueprint and ensure at least one "signature layout moment" is planned in layout.notes
    (e.g., bento grid, timeline/stepper, comparison strip, proof wall with labeled evidence, pricing clarity panel).
  - medium: keep it professional but ensure at least one signature moment so it doesn't feel generic.
  - low: keep it clean/professional; prioritize clarity and accessibility over novelty.
- DO NOT invent flashy behavior that requires heavy client JS. Keep it build-safe and maintainable.

CONTENT REQUIREMENTS (for consistency):
- content.highlights: EXACTLY 3 short strings
- content.testimonials: EXACTLY 2 items
- content.faq: EXACTLY 3 items
- Use realistic names/copy, not placeholders like [PLACEHOLDER].

EDIT TASK RULES:
- If page_type == \"edit\": edit_task.enabled MUST be true AND include non-empty instructions.
  IMPORTANT: Do NOT echo the full CODE_OLD into JSON. Set edit_task.code_old to an empty string.
  (The original code is provided separately in the pipeline.)
- Otherwise: edit_task.enabled MUST be false and instructions/code_old MUST be empty strings.

UI_SPEC SCHEMA (shape only; you must fill real values):
{
  \"niche\": {\"id\": \"string\", \"vertical\": \"string\", \"pattern\": \"string\"},
  \"page_type\": \"landing|directory_home|city_index|category_index|listing_profile|admin_dashboard|edit\",
  \"brand\": {
    \"name\": \"string\",
    \"mood\": \"dark|light\",
    \"accent\": \"blue|teal|violet|green|orange|red|amber|rose|cyan|lime|fuchsia\",
    \"style_keywords\": [\"string\"],
    \"style_family\": \"string or null (optional)\",
    \"style_persona\": \"string or null (optional)\",
    \"style_keywords_mandatory\": [\"string\"] (optional),
    \"style_avoid\": [\"string\"] (optional),
    \"imagery_style\": \"string or null (optional)\",
    \"layout_motif\": \"string or null (optional)\",
    \"radius\": \"soft|medium\",
    \"density\": \"airy|balanced|compact\"
  },
  \"cta\": {\"primary\": \"string\", \"secondary\": \"string or null\"},
  \"content\": {
    \"business_name\": \"string\",
    \"city\": \"string\",
    \"offer\": \"string\",
    \"audience\": \"string\",
    \"highlights\": [\"string\", \"string\", \"string\"],
    \"testimonials\": [{\"name\": \"string\", \"text\": \"string\"}],
    \"faq\": [{\"q\": \"string\", \"a\": \"string\"}]
  },
  \"layout\": {
    \"sections\": [
      {\"id\": \"hero\", \"must_include\": [\"headline\", \"subheadline\", \"primary_cta\", \"trust_chips\"], \"optional\": false}
    ],
    \"navigation\": \"minimal|standard\",
    \"notes\": \"string\"
  },
  \"edit_task\": {\"enabled\": false, \"instructions\": \"\", \"code_old\": \"\"}
}

Return the JSON only."""


def _apply_style_routing_to_ui_spec(ui_spec: UISpec, task: Task) -> UISpec:
    """Inject deterministic style-routing metadata from Task into UI_SPEC.

    The UI generator consumes ONLY the UI_SPEC, so any style routing information must be
    carried forward explicitly (not just in the task prompt).
    """
    if not getattr(task, "style_family", None):
        return ui_spec

    data = ui_spec.model_dump()
    brand = data.get("brand") if isinstance(data.get("brand"), dict) else {}
    layout = data.get("layout") if isinstance(data.get("layout"), dict) else {}

    # Hard-lock theme overrides if present (prevents drift in planning).
    if getattr(task, "theme_mood", None):
        brand["mood"] = task.theme_mood
    if getattr(task, "theme_accent", None):
        brand["accent"] = task.theme_accent

    # Attach style routing metadata.
    brand["style_family"] = task.style_family
    if getattr(task, "style_persona", None):
        brand["style_persona"] = task.style_persona

    mandatory = list(getattr(task, "style_keywords_mandatory", []) or [])
    avoid = list(getattr(task, "style_avoid", []) or [])
    brand["style_keywords_mandatory"] = mandatory[:16]
    brand["style_avoid"] = avoid[:16]
    if getattr(task, "style_imagery_style", None):
        brand["imagery_style"] = task.style_imagery_style
    if getattr(task, "style_layout_motif", None):
        brand["layout_motif"] = task.style_layout_motif

    # Density mapping: router uses airy/balanced/dense, schema uses airy/balanced/compact.
    density = str(getattr(task, "style_density", "") or "").strip().lower()
    if density in ("dense", "compact", "tight"):
        brand["density"] = "compact"
    elif density in ("airy", "air", "spacious"):
        brand["density"] = "airy"

    # Ensure style keywords contain all mandatory and exclude avoid.
    avoid_set = {str(x).strip().lower() for x in avoid if str(x).strip()}
    existing = list(brand.get("style_keywords") or [])
    merged: list[str] = []
    for item in list(mandatory) + list(existing):
        s = str(item).strip()
        if not s:
            continue
        if s.lower() in avoid_set:
            continue
        if s not in merged:
            merged.append(s)
    if not merged:
        merged = ["clean", "modern", "premium"]
    brand["style_keywords"] = merged[:12]

    # Add a compact note that the generator can follow without changing overall layout plan.
    notes = str(layout.get("notes") or "").strip()
    if "Style routing:" not in notes:
        style_lines: list[str] = []
        style_lines.append(f"Style routing: {task.style_family} — {task.style_persona or ''}".rstrip())
        if getattr(task, "style_layout_motif", None):
            style_lines.append(f"Motif: {task.style_layout_motif}")
        if getattr(task, "style_imagery_style", None):
            style_lines.append(f"Imagery style: {task.style_imagery_style}")
        if avoid:
            style_lines.append(f"Avoid motifs: {', '.join(avoid[:8])}")
        if style_lines:
            notes = (notes + ("\n\n" if notes else "") + "\n".join(style_lines)).strip()
            layout["notes"] = notes

    data["brand"] = brand
    data["layout"] = layout
    return validate_ui_spec(data)


async def generate_ui_spec(
    task: Task,
    config: Config,
) -> UISpec:
    """Generate UI_SPEC for a task.

    Uses the configured planner model (default: DeepSeek on Vertex).
    If megamind_enabled is True, uses 3-pass Megamind reasoning instead.

    Args:
        task: The task to plan
        config: Application configuration

    Returns:
        Validated UISpec

    Raises:
        ValueError: If generation or validation fails
    """
    # Check if Megamind is enabled
    if config.pipeline.megamind_enabled:
        log_info(f"Task {task.id}: Using Megamind 3-pass reasoning")
        megamind = _get_megamind()
        result = await megamind.generate_ui_spec_megamind(task, config)
        log_info(
            f"Task {task.id}: Megamind synthesized plan from "
            f"{sum(1 for p in result.plans if p.ui_spec is not None)}/3 sub-reasoners"
        )
        return _apply_style_routing_to_ui_spec(result.synthesized_spec, task)

    # Standard single-pass planning
    provider = ProviderFactory.get(config.planner.provider, config)

    if not config.planner.model:
        raise ValueError("Planner model not configured")

    messages = [
        Message(role="system", content=PLANNER_SYSTEM_PROMPT),
        Message(role="user", content=task.prompt),
    ]

    log_info(f"Planning task {task.id} with {config.planner.model}")

    # Retry loop: truncation and occasional non-JSON outputs do happen.
    max_retries = 2
    max_tokens = config.planner.max_tokens
    temperature = config.planner.temperature

    for attempt in range(max_retries + 1):
        response = await provider.complete(
            messages=messages,
            model=config.planner.model,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        finish_reason = getattr(response, "finish_reason", None)
        if finish_reason == "length" and attempt < max_retries:
            log_warning(
                f"Task {task.id}: Planner output truncated at {max_tokens} tokens, "
                f"retrying with {int(max_tokens * 1.25)} (attempt {attempt + 1}/{max_retries + 1})"
            )
            max_tokens = int(max_tokens * 1.25)
            temperature = max(0.3, temperature - 0.1)
            messages = messages + [
                Message(
                    role="user",
                    content=(
                        "Your last output was truncated. Re-output the FULL UI_SPEC JSON only. "
                        "No markdown, no <think>, start with { and end with }."
                    ),
                )
            ]
            continue

        # Extract and validate JSON
        try:
            spec_data = extract_json_strict(response.content)
            ui_spec = validate_ui_spec(spec_data)
            log_info(f"Task {task.id}: UI_SPEC validated successfully")
            return _apply_style_routing_to_ui_spec(ui_spec, task)
        except Exception as e:
            if attempt < max_retries:
                log_warning(
                    f"Task {task.id}: Failed to parse UI_SPEC ({e}), retrying "
                    f"(attempt {attempt + 1}/{max_retries + 1})"
                )
                temperature = max(0.3, temperature - 0.1)
                messages = messages + [
                    Message(
                        role="user",
                        content=(
                            "Your last output did not validate. Output ONLY a single valid UI_SPEC JSON object "
                            "matching the schema exactly. No extra text. Ensure it ends with }."
                        ),
                    )
                ]
                continue
            log_error(f"Task {task.id}: Failed to parse UI_SPEC - {e}")
            raise ValueError(f"Failed to generate valid UI_SPEC: {e}") from e
