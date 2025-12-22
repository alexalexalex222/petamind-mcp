"""Planner module - generates UI_SPEC from task prompts."""

from titan_factory.config import Config
from titan_factory.providers import Message, ProviderFactory
from titan_factory.schema import Task, UISpec, validate_ui_spec
from titan_factory.utils import extract_json_strict, log_error, log_info, log_warning

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
- brand.accent: blue | teal | violet | green | orange | red
- brand.radius: soft | medium
- brand.density: airy | balanced | compact
- layout.navigation: minimal | standard
- brand.style_keywords: 1-5 values from: premium, clean, modern, minimal, bold, soft, editorial

THEME SELECTION (WHEN NOT SPECIFIED IN THE TASK):
- If the task prompt explicitly specifies mood/accent, honor it.
- Otherwise, choose brand.mood and brand.accent yourself to fit the business and audience.
- Avoid always defaulting to the same accent (e.g., green/teal). Use the full accent set over time.
- Ensure the chosen mood/accent pairing supports premium contrast and readability.

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
    \"accent\": \"blue|teal|violet|green|orange|red\",
    \"style_keywords\": [\"string\"],
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


async def generate_ui_spec(
    task: Task,
    config: Config,
) -> UISpec:
    """Generate UI_SPEC for a task.

    Uses the configured planner model (default: DeepSeek on Vertex).

    Args:
        task: The task to plan
        config: Application configuration

    Returns:
        Validated UISpec

    Raises:
        ValueError: If generation or validation fails
    """
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
            temperature = max(0.2, temperature - 0.1)
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
            return ui_spec
        except Exception as e:
            if attempt < max_retries:
                log_warning(
                    f"Task {task.id}: Failed to parse UI_SPEC ({e}), retrying "
                    f"(attempt {attempt + 1}/{max_retries + 1})"
                )
                temperature = max(0.2, temperature - 0.1)
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
