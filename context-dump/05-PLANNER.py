"""
FILE: src/titan_factory/planner.py
PURPOSE: Generates UI_SPEC from task prompts using DeepSeek on Vertex AI
"""

from titan_factory.config import Config
from titan_factory.providers import Message, ProviderFactory
from titan_factory.schema import Task, UISpec, validate_ui_spec
from titan_factory.utils import extract_json_strict, log_error, log_info

# === Planner System Prompt ===
PLANNER_SYSTEM_PROMPT = """You are a UI specification generator. Given a task prompt, output ONLY a JSON object matching the UI_SPEC schema.

CRITICAL RULES:
1. Output ONLY valid JSON - no markdown, no explanation, no thinking
2. Follow the exact schema structure
3. Use realistic placeholder content (not [PLACEHOLDER])
4. Style keywords must be from: premium, clean, modern, minimal, bold, soft, editorial
5. Accent must be one of: blue, teal, violet, green, orange, red
6. Mood must be: dark or light
7. Radius must be: soft or medium
8. Density must be: airy, balanced, or compact

UI_SPEC SCHEMA:
{
  "niche": {"id": "string", "vertical": "string", "pattern": "string"},
  "page_type": "landing|directory_home|city_index|category_index|listing_profile|admin_dashboard|edit",
  "brand": {
    "name": "string",
    "mood": "dark|light",
    "accent": "blue|teal|violet|green|orange|red",
    "style_keywords": ["string"],
    "radius": "soft|medium",
    "density": "airy|balanced|compact"
  },
  "cta": {"primary": "string", "secondary": "string or null"},
  "content": {
    "business_name": "string",
    "city": "string",
    "offer": "string",
    "audience": "string",
    "highlights": ["string", "string", "string"],
    "testimonials": [{"name": "string", "text": "string"}],
    "faq": [{"q": "string", "a": "string"}]
  },
  "layout": {
    "sections": [
      {"id": "hero", "must_include": ["headline", "subheadline", "primary_cta"], "optional": false}
    ],
    "navigation": "minimal|standard",
    "notes": "string"
  },
  "edit_task": {
    "enabled": false,
    "instructions": "",
    "code_old": ""
  }
}

Output the JSON only. Start with { and end with }."""


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

    response = await provider.complete(
        messages=messages,
        model=config.planner.model,
        max_tokens=config.planner.max_tokens,
        temperature=config.planner.temperature,
    )

    # Extract and validate JSON
    try:
        spec_data = extract_json_strict(response.content)
        ui_spec = validate_ui_spec(spec_data)
        log_info(f"Task {task.id}: UI_SPEC validated successfully")
        return ui_spec
    except Exception as e:
        log_error(f"Task {task.id}: Failed to parse UI_SPEC - {e}")
        raise ValueError(f"Failed to generate valid UI_SPEC: {e}") from e
