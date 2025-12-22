"""
FILE: src/titan_factory/uigen.py
PURPOSE: Generates Next.js code from UI_SPEC using Kimi and MiniMax models
"""

import asyncio
from typing import AsyncIterator

from titan_factory.config import Config, ModelConfig
from titan_factory.providers import Message, ProviderFactory
from titan_factory.schema import (
    Candidate,
    CandidateStatus,
    GeneratedFile,
    Task,
    UIGenOutput,
    UISpec,
    validate_uigen_output,
)
from titan_factory.utils import (
    extract_json_strict,
    generate_candidate_id,
    log_error,
    log_info,
)

# === UI Generator System Prompt ===
UIGEN_SYSTEM_PROMPT = """You are a Next.js UI generator. Given a UI_SPEC, output ONLY a JSON object with the generated code.

CRITICAL RULES:
1. Output ONLY valid JSON - no markdown, no explanation, no thinking
2. Use Next.js App Router with TypeScript and Tailwind CSS
3. NO UI LIBRARIES (no shadcn, MUI, Chakra, etc.)
4. Code must be complete and production-ready
5. Follow the UI_SPEC exactly for styling (mood, accent, density, sections)
6. Use semantic HTML and proper accessibility
7. Make it visually premium (Apple/OpenAI/Google quality)
8. Keep code focused - prefer single file when possible

OUTPUT FORMAT:
{
  "files": [
    {"path": "app/page.tsx", "content": "// complete code here"}
  ],
  "notes": ["brief implementation note", "another note"]
}

STYLING GUIDELINES:
- dark mood: Use dark backgrounds (slate-900, zinc-900, neutral-900) with light text
- light mood: Use light backgrounds with dark text
- Accent colors map to Tailwind: blue->blue-500, teal->teal-500, violet->violet-500, etc.
- airy density: Generous padding (py-24, px-8), large gaps
- balanced density: Medium padding (py-16, px-6), standard gaps
- compact density: Tight padding (py-8, px-4), small gaps
- soft radius: rounded-2xl, rounded-3xl
- medium radius: rounded-lg, rounded-xl

Start your response with { and end with }."""


UIGEN_USER_PROMPT_TEMPLATE = """Generate the UI code for this specification:

{ui_spec_json}

Requirements:
- Complete, working Next.js App Router code
- TypeScript with proper types
- Tailwind CSS only (no UI libraries)
- Follow the brand settings exactly
- Include all specified sections
- Use placeholder images from /placeholder.svg or gradient backgrounds
- Make it visually stunning and premium

Output JSON only with files array and notes."""


async def generate_candidate(
    task: Task,
    ui_spec: UISpec,
    generator: ModelConfig,
    variant_index: int,
    config: Config,
) -> Candidate:
    """Generate a single candidate.

    Args:
        task: Parent task
        ui_spec: UI specification to implement
        generator: Generator model config
        variant_index: Variant number (0, 1, 2...)
        config: Application configuration

    Returns:
        Generated candidate (may have errors)
    """
    candidate_id = generate_candidate_id(task.id, generator.model or "", variant_index)

    candidate = Candidate(
        id=candidate_id,
        task_id=task.id,
        generator_model=generator.model or "unknown",
        variant_index=variant_index,
        status=CandidateStatus.PENDING,
        ui_spec=ui_spec,
        publishable=generator.publishable,
    )

    try:
        provider = ProviderFactory.get(generator.provider, config)

        if not generator.model:
            raise ValueError("Generator model not configured")

        # Build prompt with UI spec
        ui_spec_json = ui_spec.model_dump_json(indent=2)
        user_prompt = UIGEN_USER_PROMPT_TEMPLATE.format(ui_spec_json=ui_spec_json)

        # Add temperature variation for different variants
        temperature = generator.temperature + (variant_index * 0.05)
        temperature = min(temperature, 1.0)

        messages = [
            Message(role="system", content=UIGEN_SYSTEM_PROMPT),
            Message(role="user", content=user_prompt),
        ]

        log_info(
            f"Generating candidate {candidate_id} with {generator.model} (variant {variant_index})"
        )

        response = await provider.complete(
            messages=messages,
            model=generator.model,
            max_tokens=generator.max_tokens,
            temperature=temperature,
        )

        # Extract and validate
        output_data = extract_json_strict(response.content)
        output = validate_uigen_output(output_data)

        candidate.files = output.files
        candidate.status = CandidateStatus.GENERATED

        log_info(f"Candidate {candidate_id}: Generated {len(output.files)} files")

    except Exception as e:
        log_error(f"Candidate {candidate_id}: Generation failed - {e}")
        candidate.error = str(e)
        candidate.status = CandidateStatus.DISCARDED

    return candidate


async def generate_all_candidates(
    task: Task,
    ui_spec: UISpec,
    config: Config,
    public_only: bool = False,
) -> AsyncIterator[Candidate]:
    """Generate candidates from all configured generators.

    Args:
        task: Parent task
        ui_spec: UI specification
        config: Application configuration
        public_only: If True, only use publishable generators

    Yields:
        Generated candidates
    """
    generators = config.ui_generators
    if public_only:
        generators = config.get_publishable_generators()

    if not generators:
        log_error("No UI generators configured")
        return

    # Create all generation tasks
    tasks = []
    for generator in generators:
        for variant in range(generator.variants):
            tasks.append(
                generate_candidate(task, ui_spec, generator, variant, config)
            )

    # Run in batches based on concurrency settings
    concurrency = config.budget.concurrency_vertex  # Use vertex as default
    for i in range(0, len(tasks), concurrency):
        batch = tasks[i : i + concurrency]
        results = await asyncio.gather(*batch, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                log_error(f"Candidate generation failed: {result}")
            elif isinstance(result, Candidate):
                yield result
