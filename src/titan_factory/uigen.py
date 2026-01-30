"""UI Generator module - generates code from UI_SPEC."""

import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path

from titan_factory.config import Config, ModelConfig
from titan_factory.providers import Message, ProviderFactory
from titan_factory.schema import (
    Candidate,
    CandidateStatus,
    Task,
    UISpec,
    validate_uigen_output,
)
from titan_factory.utils import (
    extract_json_strict,
    generate_candidate_id,
    log_error,
    log_info,
    log_warning,
)

# Cache for optional prompt file loads (path -> contents)
_PROMPT_FILE_CACHE: dict[str, str] = {}


def _read_prompt_file(path: Path) -> str:
    key = str(path.resolve())
    cached = _PROMPT_FILE_CACHE.get(key)
    if cached is not None:
        return cached
    text = path.read_text(encoding="utf-8")
    _PROMPT_FILE_CACHE[key] = text
    return text


def _get_uigen_system_prompt(config: Config) -> str:
    """Return the UI generator system prompt, optionally overridden by config."""
    override = getattr(config.pipeline, "uigen_system_prompt_path", None)
    if not override:
        return UIGEN_SYSTEM_PROMPT

    p = Path(str(override))
    if not p.is_absolute():
        p = config.project_root / p
    if not p.exists():
        raise FileNotFoundError(f"uigen_system_prompt_path not found: {p}")
    return _read_prompt_file(p)


def _load_uigen_prompt_variants(config: Config) -> list[dict[str, str]]:
    """Load UI generator prompt variants from config.

    If `pipeline.uigen_prompt_variants` is provided, we generate candidates for ALL variants
    in the same run. Otherwise we fall back to the legacy single-prompt behavior via
    `pipeline.uigen_system_prompt_path` (or built-in prompt).

    Returns:
        List of dicts: {id, system_prompt, input_mode}
        input_mode ∈ {"ui_spec","page_brief","both"}
    """
    raw_variants = getattr(config.pipeline, "uigen_prompt_variants", None) or []
    variants: list[dict[str, str]] = []

    def _load_prompt_fragment(fragment: dict, *, variant_idx: int, part_idx: int) -> str:
        """Load a single prompt fragment from a dict spec.

        Supported fragment sources:
        - builtin: uses UIGEN_SYSTEM_PROMPT
        - file: loads from a path
        - inline: uses the literal text in `text`
        - stack: concatenates `parts` recursively
        """
        source = str(fragment.get("source") or "").strip().lower()
        if not source:
            source = "file" if fragment.get("path") else "builtin"

        if source == "builtin":
            return UIGEN_SYSTEM_PROMPT

        if source == "inline":
            text = str(fragment.get("text") or fragment.get("prompt") or "").strip()
            if not text:
                log_warning(
                    f"uigen_prompt_variants[{variant_idx}].parts[{part_idx}] source=inline is empty"
                )
            return text

        if source == "stack":
            parts = fragment.get("parts") or []
            if not isinstance(parts, list) or not parts:
                raise ValueError(
                    f"uigen_prompt_variants[{variant_idx}].parts[{part_idx}] source=stack requires non-empty parts[]"
                )
            texts: list[str] = []
            for j, p in enumerate(parts):
                if not isinstance(p, dict):
                    continue
                t = _load_prompt_fragment(p, variant_idx=variant_idx, part_idx=j)
                if t.strip():
                    texts.append(t.strip())
            return "\n\n---\n\n".join(texts).strip()

        # default: file
        raw_path = fragment.get("path") or fragment.get("prompt_path") or fragment.get("file")
        if not raw_path:
            raise ValueError(
                f"uigen_prompt_variants[{variant_idx}].parts[{part_idx}] missing 'path' for source=file"
            )
        p = Path(str(raw_path))
        if not p.is_absolute():
            p = config.project_root / p
        if not p.exists():
            raise FileNotFoundError(f"uigen prompt file not found: {p}")
        return _read_prompt_file(p).strip()

    if raw_variants:
        seen: set[str] = set()
        for idx, item in enumerate(raw_variants):
            if not isinstance(item, dict):
                continue

            variant_id = str(item.get("id") or f"prompt_{idx}").strip() or f"prompt_{idx}"
            if variant_id in seen:
                variant_id = f"{variant_id}_{idx}"
            seen.add(variant_id)

            input_mode = str(item.get("input_mode") or "auto").strip().lower()

            parts = item.get("parts")
            if isinstance(parts, list) and parts:
                texts: list[str] = []
                for j, part in enumerate(parts):
                    if not isinstance(part, dict):
                        continue
                    t = _load_prompt_fragment(part, variant_idx=idx, part_idx=j)
                    if t.strip():
                        texts.append(t.strip())
                system_prompt = "\n\n---\n\n".join(texts).strip()
            else:
                # Back-compat: treat item itself as a single fragment.
                system_prompt = _load_prompt_fragment(item, variant_idx=idx, part_idx=0)

            if input_mode == "auto":
                # Heuristic: TITAN-style prompts often mention OUTPUT_MODE / PAGE BRIEF.
                lowered = system_prompt.lower()
                if "page brief" in lowered or "output_mode" in lowered:
                    input_mode = "page_brief"
                else:
                    input_mode = "ui_spec"

            if input_mode not in ("ui_spec", "page_brief", "both"):
                log_warning(
                    f"uigen_prompt_variants[{idx}] has unsupported input_mode='{input_mode}', "
                    "defaulting to ui_spec"
                )
                input_mode = "ui_spec"

            variants.append(
                {
                    "id": variant_id,
                    "system_prompt": system_prompt,
                    "input_mode": input_mode,
                }
            )

        return variants

    # Legacy single prompt behavior
    system_prompt = _get_uigen_system_prompt(config)
    input_mode = "page_brief" if getattr(config.pipeline, "uigen_system_prompt_path", None) else "ui_spec"
    return [{"id": "default", "system_prompt": system_prompt, "input_mode": input_mode}]


def _build_titan_page_brief(ui_spec: UISpec) -> str:
    """Build a structured brief aligned with the TITAN_UI system prompts."""
    page_type = "landing"
    try:
        pt = str(getattr(ui_spec, "page_type", "") or "").lower()
        if "dashboard" in pt:
            page_type = "dashboard"
        elif "directory" in pt or "listing" in pt or "category" in pt or "city" in pt:
            page_type = "directory"
    except Exception:
        page_type = "landing"

    vertical = ui_spec.niche.vertical.replace("_", " ")
    brand_name = ui_spec.content.business_name or ui_spec.brand.name
    city = ui_spec.content.city
    offer = ui_spec.content.offer
    audience = ui_spec.content.audience
    cta_primary = ui_spec.cta.primary

    mood = ui_spec.brand.mood
    accent = ui_spec.brand.accent
    tone_tags = ", ".join(ui_spec.brand.style_keywords) if ui_spec.brand.style_keywords else ""

    style_family = getattr(ui_spec.brand, "style_family", None)
    style_persona = getattr(ui_spec.brand, "style_persona", None)
    style_mandatory = getattr(ui_spec.brand, "style_keywords_mandatory", None)
    style_avoid = getattr(ui_spec.brand, "style_avoid", None)
    imagery_style = getattr(ui_spec.brand, "imagery_style", None)
    layout_motif = getattr(ui_spec.brand, "layout_motif", None)

    differentiators = "\n".join(f"- {h}" for h in (ui_spec.content.highlights or [])[:7])
    objections = "\n".join(f"- {f.q}" for f in (ui_spec.content.faq or [])[:10])

    style_mandatory_lines = "\n".join(f"- {x}" for x in (style_mandatory or [])[:12])
    style_avoid_lines = "\n".join(f"- {x}" for x in (style_avoid or [])[:12])

    return (
        "PAGE BRIEF\n\n"
        f"* page_type: {page_type}\n"
        f"* vertical: {vertical}\n"
        f"* brand_name: {brand_name}\n"
        f"* city: {city}\n"
        f"* offer: {offer}\n"
        f"* audience: {audience}\n"
        f"* cta_primary: {cta_primary}\n"
        f"* mood: {mood}\n"
        f"* accent: {accent}\n\n"
        f"* style_family: {style_family or ''}\n"
        f"* style_persona: {style_persona or ''}\n"
        f"* imagery_style: {imagery_style or ''}\n"
        f"* layout_motif: {layout_motif or ''}\n\n"
        f"* style_keywords_mandatory:\n{style_mandatory_lines or '- (none)'}\n"
        f"* style_avoid:\n{style_avoid_lines or '- (none)'}\n\n"
        "OPTIONAL INPUTS\n\n"
        f"* tone_tags: {tone_tags}\n"
        f"* differentiators:\n{differentiators or '- (none provided)'}\n"
        f"* objections:\n{objections or '- (none provided)'}\n\n"
        "OUTPUT_MODE\n\n"
        "* OUTPUT_MODE: TSX_ONLY\n\n"
        "REQUIREMENTS\n\n"
        "* No markdown. No preambles. Compile-safe TSX. Tailwind only. No UI libraries.\n"
    )


# === UI Generator System Prompt ===
# NOTE: We preserve <think> blocks for training data, but parsing requires clean JSON afterward.
UIGEN_SYSTEM_PROMPT = """You are a Next.js App Router UI generator.

You will receive a UI_SPEC JSON. Implement it using TypeScript + Tailwind CSS (no UI libraries).

PRODUCTION-QUALITY BAR (FIRST-PASS MUST BE SHIP-READY):
- Treat this as a real product page you’d happily ship without a second attempt.
- Avoid “basic Tailwind demo” vibes. Use a clear signature layout moment that matches the page type:
  (e.g., bento grid, timeline/stepper, comparison strip, labeled proof wall, pricing clarity panel).
- For LIGHT mood: avoid flat pure-white-only pages; use layered neutral/off-white surfaces and at least one restrained accent-tinted band/gradient
  while keeping WCAG AA contrast.
- For DARK mood: use layered surfaces, subtle borders, and restrained accent glow/gradients (no heavy animation).
- No filler placeholders like [PLACEHOLDER] or lorem ipsum. No fake logos/metrics/reviews.

CREATIVE RISK (MANDATORY - AVOID BORING/GENERIC OUTPUTS):
- DEFAULT TO MEDIUM-HIGH CREATIVITY. Every page MUST have at least one signature layout moment.
- If the input contains a line like "Creative risk: high|medium|low", follow it. Otherwise, assume MEDIUM.
  - high: bold creative risk (memorable hero treatment, unexpected layout breaks, strong visual motif) while staying build-safe.
  - medium: professional with personality - at least one signature moment (asymmetric grid, gradient overlay, layered cards, timeline, bento layout).
  - low: clean execution, but still avoid flat/boring - use subtle depth, shadows, and surface variation.
- NEVER output a page that looks like "generic Tailwind starter template" - every page needs visual identity.
- Creativity must remain manageable: no heavy animation, no external assets, no extra dependencies, minimal client JS.

COLOR VARIETY (MANDATORY - AVOID DEFAULTING TO SAME PALETTE):
- USE THE SPECIFIED ACCENT COLOR FROM UI_SPEC.brand.accent - do NOT substitute with your preferred color.
- Common mistake: defaulting to violet/purple regardless of spec. If spec says "teal", use teal-500/600. If "orange", use orange-500/600.
- For LIGHT mood: incorporate the accent as tinted bands, subtle gradients, or hover states - not just buttons.
- For DARK mood: use the accent for glow effects, borders, and focal points - let it pop against dark surfaces.
- VARY your approach per page type: hero gradient direction, card border vs shadow, accent placement should differ.
- If accent is not specified, ROTATE through these per generation: teal, amber, rose, cyan, lime, fuchsia - NOT always blue/violet.

STYLE ROUTING (HARD CONSTRAINTS - WHEN PRESENT):
- UI_SPEC.brand may include:
  - style_family, style_persona
  - style_keywords_mandatory, style_avoid
  - imagery_style, layout_motif
  Treat these as HARD CONSTRAINTS. Do not override them.
- Anti-monoculture rule: if style_family != cyber_tech:
  - DO NOT use terminal/console/hacker metaphors in copy or visuals.
  - DO NOT default to neon glow aesthetics.
  - DO NOT make the page look like a dense app dashboard unless UI_SPEC.page_type == "admin_dashboard".
  - Avoid using monospace as the primary font voice.
- Ensure style_keywords_mandatory is reflected in the UI. Ensure style_avoid never appears.

OUTPUT FORMAT (STRICT):
1) First output a single <think>...</think> block with your reasoning.
   - Include: brand interpretation, section-by-section layout plan, key components/interactions, accessibility notes.
   - Be detailed but stay reasonably concise (avoid rambling / repeated text).
   - You MUST close the tag with </think>.
2) Immediately after </think>, output ONE JSON object (no markdown fences) with EXACT keys:
   {"files":[{"path":"app/page.tsx","content":"..."}],"notes":["..."]}

ABSOLUTE RULES (discarded if violated):
- ZERO EMOJI CHARACTERS (🚀❌✅🎯💡⭐ etc.) - AUTOMATIC REJECT. Use inline SVG only.
- No text before <think>.
- No markdown, no ``` fences.
- JSON must be valid (double quotes, no trailing commas) and must start with { and end with }.
- No text after the final }.

SELF-QA LOOP (DO THIS INSIDE <think> BEFORE OUTPUT; REVISE IF ANY FAILS):
1) Hero answers what/who/outcome/next-step fast
2) All required sections exist and feel intentional
3) One primary CTA style (repeated placements ok), no competing primaries
4) Proof is labeled (provided/qualified/illustrative), no fake logos/metrics
5) SIGNATURE MOMENT CHECK: Does this page have at least ONE memorable layout element? (bento grid, timeline, comparison strip, gradient hero, asymmetric cards, etc.) If it looks like a plain Tailwind starter, REVISE.
6) COLOR ADHERENCE CHECK: Did I use UI_SPEC.brand.accent (not my default)? Is the accent visible in hero, CTA, and at least one other element? If I defaulted to violet/purple when spec said otherwise, REVISE.
7) A11Y HARD CHECK (machine-verified - failures = auto-reject):
   □ Every <select> has <label htmlFor="id"> (sr-only ok) or aria-label (never rely on placeholder)
   □ Every button without visible text (icons/arrows/dots) has aria-label describing the action
   □ Every <input>/<textarea> has associated <label> or aria-label
   □ Focus rings (focus:ring-*) on all interactive elements
   □ Headings hierarchical (H1 → H2 → H3)
8) Responsive: no overflow; mobile is polished and readable
9) Visual polish: consistent radius/shadow/border; not flat/basic
10) ZERO EMOJIS ANYWHERE - use inline SVG icons ONLY (simple paths, <= 6 icons total)

FORMAT SAFETY:
- Do NOT echo these rules in the output.
- Do NOT output a prose summary outside <think>.
- After </think> you MUST output one valid JSON object with files[]. If you catch yourself writing anything else, stop and correct.

SIZE / COMPACTNESS GUARDS (AVOID TRUNCATION):
- Keep app/page.tsx compact and shippable: prefer ~250–450 lines.
- Keep data arrays small (6–12 cards max per grid). Do not generate huge dummy arrays.
- Keep copy tight: short intros, avoid long paragraphs.

IMPLEMENTATION RULES:
- Next.js App Router + TypeScript + Tailwind only.
- No UI libraries (no shadcn, MUI, Chakra, etc.). No external assets. Use /placeholder.svg or gradients.
- ZERO EMOJI CHARACTERS (🚀❌✅ etc.) - these break the premium feel. For icons, use simple inline SVG paths only.
- Prefer a single file: path must be \"app/page.tsx\" unless absolutely necessary (max 3 files).
- Avoid next/head and external font links; rely on Tailwind defaults.
- Keep code compact and build-safe. Prefer native HTML patterns to reduce JS:
  - FAQ: use <details><summary> (no useState needed)
  - Testimonials: static cards or scroll-snap row (no timers)
- Use semantic HTML and accessible labels.
- If UI_SPEC.page_type == \"edit\" you will be given <CODE_OLD> separately.
  Apply UI_SPEC.edit_task.instructions to that code and output the FULL updated file(s).

ACCESSIBILITY (HARD REQUIREMENTS - MACHINE-VERIFIED - VIOLATIONS = AUTO-REJECT):
These are verified by axe-core. Failures cause automatic rejection in production.

1. EVERY <select> MUST have an accessible name:
   - Pattern A: <label htmlFor=\"sortId\">Sort by</label><select id=\"sortId\">...</select>
   - Pattern B: <select aria-label=\"Sort options\">...</select>
   - IMPORTANT: adjacent text like \"Sort by:\" does NOT label a <select> unless it is a real <label>.
     If you show visible label text, it MUST be:
       <label htmlFor=\"sortId\">Sort by</label>
       <select id=\"sortId\">...</select>
   - If you don’t want a visible label, use an sr-only label OR aria-label:
     <label className=\"sr-only\" htmlFor=\"neighborhood\">Neighborhood</label>
     <select id=\"neighborhood\" aria-label=\"Neighborhood\">...</select>
   - NEVER rely on placeholder/option text as the label (axe still flags select-name)
   - NEVER output <select> without one of these patterns

2. EVERY <button> MUST have an accessible name:
   - Text buttons: the visible text IS the name (no extra work needed)
   - Any button WITHOUT visible text MUST have aria-label (icons/arrows/dots):
     <button aria-label=\"Close menu\" className=\"...\"><svg>...</svg></button>
     <button aria-label=\"Search\" className=\"...\"><svg>...</svg></button>
     <button aria-label=\"Previous testimonial\" className=\"...\"><svg>...</svg></button>
     <button aria-label=\"Go to testimonial 2\" className=\"...\" />
   - If the button's children are ONLY an <svg> (chevrons/arrows/ellipsis/close), it is non-text → aria-label is mandatory.
   - NEVER output a non-text button without aria-label

3. EVERY <input>/<textarea> MUST be labeled:
   - Use <label htmlFor=\"id\"> with matching id, OR aria-label on the input
   - Include aria-describedby for helper text if present

4. Focus states MUST be visible:
   - Add focus:ring-2 focus:ring-offset-2 focus:ring-{accent} to interactive elements
   - Or use focus:outline-none focus-visible:ring-2 pattern

5. Touch targets MUST be >= 44x44px for buttons and links

STYLING GUIDELINES (map UI_SPEC → Tailwind):
- dark mood: slate-950/neutral-950 backgrounds, light text, subtle borders
- light mood: white/neutral-50 backgrounds, dark text, soft shadows
- Accent: blue/teal/violet/green/orange/red/amber/rose/cyan/lime/fuchsia → use matching Tailwind palette (e.g., teal-500)
  - IMPORTANT: use UI_SPEC.brand.accent EXACTLY (do not swap to violet/purple by default)
  - Apply the accent consistently (primary CTA, focus rings, subtle tints/bands), but keep contrast AA.
- density: airy (py-24 gap-10), balanced (py-16 gap-8), compact (py-10 gap-6)
- radius: soft (rounded-2xl/3xl), medium (rounded-xl/lg)
"""


UIGEN_USER_PROMPT_TEMPLATE = """Generate the UI code for this UI_SPEC:

{ui_spec_json}

Requirements:
- Complete, working Next.js App Router code
- TypeScript with proper types
- Tailwind CSS only (no UI libraries)
- Follow the brand settings exactly
- Include all specified sections
- Use placeholder images from /placeholder.svg or gradient backgrounds (no remote images)
- Make it visually stunning and premium (ship-ready on the first pass; no basic/demo vibes)
- CRITICAL: NO EMOJI CHARACTERS (🚀❌✅⭐💡 etc.) - use inline SVG icons only

ACCESSIBILITY (MACHINE-VERIFIED - FAILURES = AUTO-REJECT):
- Every <select> needs <label htmlFor> (sr-only ok) or aria-label (never rely on placeholder)
- Every non-text <button> (icons/arrows/dots) needs aria-label
- Every <input>/<textarea> needs associated label
- All interactive elements need focus:ring-* classes

Output format (STRICT):
<think>...</think>
{{"files":[{{"path":"app/page.tsx","content":"..."}}],"notes":["..."]}}

No markdown. No extra text. The final character must be }}."""


_THINK_BLOCK_RE = re.compile(r"<think>[\s\S]*?</think>\s*", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(
    r"```(?:tsx|typescript|ts|jsx|js)?\s*([\s\S]*?)```",
    re.IGNORECASE,
)


def _strip_think_blocks(text: str) -> str:
    """Remove <think> blocks from a model response."""
    return _THINK_BLOCK_RE.sub("", text or "").strip()


def _extract_first_code_fence(text: str) -> str | None:
    """Extract the first fenced code block (tsx/ts/jsx/js) if present."""
    match = _CODE_FENCE_RE.search(text or "")
    if not match:
        return None
    return match.group(1).strip()


def _looks_like_tsx(code: str) -> bool:
    """Heuristic to detect raw TSX/React code (for non-JSON model outputs)."""
    if not code:
        return False

    snippet = code.lstrip()[:500]
    indicators = [
        snippet.startswith("'use client'"),
        snippet.startswith('"use client"'),
        snippet.startswith("import "),
        "export default" in snippet,
        "function " in snippet and "return" in snippet,
        "className=" in snippet,
        "<main" in snippet,
        "<div" in snippet,
    ]
    return any(indicators)


def _salvage_uigen_output(content: str) -> dict[str, object] | None:
    """Attempt to salvage a UIGEN response that isn't valid JSON.

    Some models occasionally output raw TSX without the requested JSON wrapper.
    We can recover by wrapping it into the expected schema.
    """
    stripped = _strip_think_blocks(content)
    code = _extract_first_code_fence(stripped) or stripped

    if not _looks_like_tsx(code):
        return None

    return {
        "files": [{"path": "app/page.tsx", "content": code}],
        "notes": ["Salvaged from non-JSON model output"],
    }


def _extract_edit_instruction(prompt: str) -> str | None:
    """Extract the 'Task: ...' instruction line from an edit prompt."""
    match = re.search(r"^Task:\s*(.+)$", prompt or "", flags=re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


async def generate_candidate(
    task: Task,
    ui_spec: UISpec,
    generator: ModelConfig,
    variant_index: int,
    config: Config,
    *,
    prompt_variant_id: str,
    system_prompt: str,
    input_mode: str,
) -> Candidate:
    """Generate a single candidate with truncation detection and retry.

    Fix C from GPT-5.2 Pro: Detects truncation via finish_reason and retries
    with higher max_tokens budget.

    Args:
        task: Parent task
        ui_spec: UI specification to implement
        generator: Generator model config
        variant_index: Variant number (0, 1, 2...)
        config: Application configuration

    Returns:
        Generated candidate (may have errors)
    """
    # Add temperature variation for different variants.
    # Keep this deterministic and record the final value on the candidate so
    # temp-sweep experiments can be analyzed from manifest.db.
    temperature = float(generator.temperature or 0.7) + (variant_index * 0.05)
    try:
        cap = float(getattr(config.pipeline, "generator_temp_cap", 1.0) or 1.0)
    except Exception:
        cap = 1.0
    # Safety: never allow a cap below our min temp floor (0.3).
    if cap < 0.3:
        cap = 0.3
    temperature = min(temperature, cap)

    # Disambiguate candidates when the same model appears multiple times in config
    # (e.g., temperature sweeps like 0.3 vs 1.0). Without this, candidate IDs collide
    # and overwrite each other in SQLite/out/ folders.
    generator_key = (
        f"{generator.provider}:{generator.model or 'unknown'}:"
        f"temp={float(generator.temperature or 0.0):.3f}:"
        f"max_tokens={int(generator.max_tokens or 0)}"
    )

    candidate_id = generate_candidate_id(
        task.id,
        generator.model or "",
        variant_index,
        prompt_id=prompt_variant_id,
        generator_key=generator_key,
    )

    candidate = Candidate(
        id=candidate_id,
        task_id=task.id,
        generator_model=generator.model or "unknown",
        variant_index=variant_index,
        generator_temperature=temperature,
        status=CandidateStatus.PENDING,
        ui_spec=ui_spec,
        publishable=generator.publishable,
        uigen_prompt_id=prompt_variant_id,
    )

    try:
        provider = ProviderFactory.get(generator.provider, config)

        if not generator.model:
            raise ValueError("Generator model not configured")

        # Build user prompt
        if input_mode == "page_brief":
            # TITAN_UI prompt expects a page brief; keep output mode TSX_ONLY for pipeline compatibility.
            user_prompt = _build_titan_page_brief(ui_spec)
        elif input_mode == "both":
            # Some "stacked" system prompts may include both TITAN-style instructions and
            # the built-in UIGEN instructions. Provide BOTH a structured brief and the
            # raw UI_SPEC JSON so either prompt style has the full context.
            page_brief = _build_titan_page_brief(ui_spec)
            ui_spec_json = ui_spec.model_dump_json(indent=2)
            user_prompt = (
                f"{page_brief}\n\n"
                "UI_SPEC JSON (from planner)\n\n"
                f"{ui_spec_json}\n"
            )
        else:
            # Default prompt uses the raw UI_SPEC JSON
            ui_spec_json = ui_spec.model_dump_json(indent=2)
            user_prompt = UIGEN_USER_PROMPT_TEMPLATE.format(ui_spec_json=ui_spec_json)

        # For edit tasks, include CODE_OLD separately (do not force the planner to echo it into JSON)
        if task.is_edit and task.code_old:
            instruction = (
                _extract_edit_instruction(task.prompt)
                or (ui_spec.edit_task.instructions if ui_spec.edit_task else None)
                or "Apply the requested refactor to the provided code."
            )
            user_prompt += (
                "\n\nEDIT TASK\n"
                f"Instruction: {instruction}\n\n"
                "<CODE_OLD>\n"
                f"{task.code_old}\n"
                "</CODE_OLD>\n\n"
                "Output the full updated files in the required JSON format. Do not include CODE_OLD in the output."
            )

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]

        log_info(
            f"Generating candidate {candidate_id} with {generator.model} "
            f"(prompt {prompt_variant_id}, variant {variant_index}, temp {temperature:.2f})"
        )

        # Fix C: Retry loop for truncation and empty responses
        max_retries = 2
        max_tokens = generator.max_tokens

        retry_instruction = (
            "CRITICAL FORMAT REMINDER:\n"
            "- Output ONLY:\n"
            "  1) One <think>...</think> block (<=120 words)\n"
            "  2) One valid JSON object\n"
            "- The JSON MUST include a top-level \"files\" array.\n"
            "- \"files\" MUST include at least one entry with:\n"
            "  - path: \"app/page.tsx\"\n"
            "  - content: a string containing the FULL Next.js TSX code (no placeholders).\n"
            "- No markdown/code fences.\n"
            "- No extra text before <think> or after the final }.\n"
        )

        for attempt in range(max_retries + 1):
            # Track the actual temperature used (may change across retries).
            candidate.generator_temperature = temperature

            response = await provider.complete(
                messages=messages,
                model=generator.model,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            # Store raw response (includes <think> blocks for training reasoning)
            candidate.raw_generator_response = response.content

            content = response.content or ""

            # Check for empty response
            if not content.strip():
                if attempt < max_retries:
                    log_warning(
                        f"Candidate {candidate_id}: Empty response, retrying "
                        f"(attempt {attempt + 1}/{max_retries + 1})"
                    )
                    max_tokens = int(max_tokens * 1.25)
                    temperature = max(0.3, temperature - 0.1)
                    continue
                raise RuntimeError("Empty response content from model")

            # Check for truncation via finish_reason
            finish_reason = getattr(response, "finish_reason", None)
            if finish_reason == "length":
                if attempt < max_retries:
                    log_warning(
                        f"Candidate {candidate_id}: Response truncated at {max_tokens} tokens, "
                        f"retrying with {int(max_tokens * 1.25)} (attempt {attempt + 1}/{max_retries + 1})"
                    )
                    max_tokens = int(max_tokens * 1.25)
                    temperature = max(0.3, temperature - 0.1)
                    # Keep retry context small and explicit (avoid growing message history).
                    messages = messages[:2] + [Message(role="user", content=retry_instruction)]
                    continue
                raise RuntimeError(
                    f"Model response truncated (finish_reason=length) at max_tokens={max_tokens}"
                )

            # Extract and validate (strips <think> blocks for JSON parsing)
            try:
                output_data = extract_json_strict(content)
                output = validate_uigen_output(output_data)
            except Exception as parse_error:
                # Try to salvage raw TSX responses that skipped the JSON wrapper
                salvaged = _salvage_uigen_output(content)
                if salvaged is not None:
                    output = validate_uigen_output(salvaged)
                    log_warning(
                        f"Candidate {candidate_id}: Salvaged non-JSON output into UIGenOutput"
                    )
                else:
                    if attempt < max_retries:
                        log_warning(
                            f"Candidate {candidate_id}: Invalid JSON output ({parse_error}), retrying "
                            f"(attempt {attempt + 1}/{max_retries + 1})"
                        )
                        max_tokens = int(max_tokens * 1.25)
                        temperature = max(0.3, temperature - 0.1)
                        messages = messages[:2] + [Message(role="user", content=retry_instruction)]
                        continue
                    raise

            candidate.files = output.files
            candidate.status = CandidateStatus.GENERATED

            log_info(f"Candidate {candidate_id}: Generated {len(output.files)} files")
            break

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

    prompt_variants = _load_uigen_prompt_variants(config)
    if not prompt_variants:
        log_error("No UIGEN prompt variants available")
        return

    # Create all generation tasks
    tasks = []
    for pv in prompt_variants:
        for generator in generators:
            for variant in range(generator.variants):
                tasks.append(
                    generate_candidate(
                        task,
                        ui_spec,
                        generator,
                        variant,
                        config,
                        prompt_variant_id=pv["id"],
                        system_prompt=pv["system_prompt"],
                        input_mode=pv["input_mode"],
                    )
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


async def generate_candidate_raw(
    task: Task,
    generator: ModelConfig,
    variant_index: int,
    config: Config,
    *,
    prompt_variant_id: str,
    system_prompt: str,
    input_mode: str,
) -> Candidate:
    """Generate a single candidate directly from the task prompt (no UI_SPEC).

    This powers a "no pipeline" baseline: system prompt + user prompt → code.
    We still enforce the exact same output format (JSON with files[]) so the
    normal build/render/gate pipeline can evaluate it.
    """
    temperature = float(generator.temperature or 0.7) + (variant_index * 0.05)
    try:
        cap = float(getattr(config.pipeline, "generator_temp_cap", 1.0) or 1.0)
    except Exception:
        cap = 1.0
    if cap < 0.3:
        cap = 0.3
    temperature = min(temperature, cap)

    generator_key = (
        f"{generator.provider}:{generator.model or 'unknown'}:"
        f"temp={float(generator.temperature or 0.0):.3f}:"
        f"max_tokens={int(generator.max_tokens or 0)}"
    )

    candidate_id = generate_candidate_id(
        task.id,
        generator.model or "",
        variant_index,
        prompt_id=prompt_variant_id,
        generator_key=generator_key,
    )

    candidate = Candidate(
        id=candidate_id,
        task_id=task.id,
        generator_model=generator.model or "unknown",
        variant_index=variant_index,
        generator_temperature=temperature,
        status=CandidateStatus.PENDING,
        ui_spec=None,
        publishable=generator.publishable,
        uigen_prompt_id=prompt_variant_id,
    )

    try:
        provider = ProviderFactory.get(generator.provider, config)

        if not generator.model:
            raise ValueError("Generator model not configured")

        raw_task_prompt = (task.prompt or "").strip()
        if not raw_task_prompt:
            raw_task_prompt = "(empty task prompt)"

        # The TITAN_UI long system prompts expect a PAGE BRIEF style input and
        # are sensitive to OUTPUT_MODE for determining what JSON schema to emit.
        # In raw mode, we keep generation "no pipeline" (no UI_SPEC) but still
        # provide an explicit brief wrapper and force TSX_ONLY so the model
        # always returns files[] for downstream build/render/gates.
        if input_mode in ("page_brief", "both", "auto"):
            user_prompt = (
                "PAGE BRIEF\n\n"
                f"{raw_task_prompt}\n\n"
                "OUTPUT_MODE\n\n"
                "* OUTPUT_MODE: TSX_ONLY\n\n"
                "REQUIREMENTS\n\n"
                "* Output must be STRICT: <think>...</think> then ONE JSON object.\n"
                "* JSON MUST include top-level \"files\" with app/page.tsx.\n"
                "* No markdown fences. No extra text after the final }.\n"
            )
        else:
            user_prompt = (
                f"{raw_task_prompt}\n\n"
                "OUTPUT_MODE\n\n"
                "* OUTPUT_MODE: TSX_ONLY\n"
            )

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]

        log_info(
            f"Generating RAW candidate {candidate_id} with {generator.model} "
            f"(prompt {prompt_variant_id}, variant {variant_index}, temp {temperature:.2f})"
        )

        max_retries = 2
        max_tokens = generator.max_tokens

        retry_instruction = (
            "CRITICAL FORMAT REMINDER:\n"
            "- Output ONLY:\n"
            "  1) One <think>...</think> block (<=120 words)\n"
            "  2) One valid JSON object\n"
            "- The JSON MUST include a top-level \"files\" array.\n"
            "- \"files\" MUST include at least one entry with:\n"
            "  - path: \"app/page.tsx\"\n"
            "  - content: a string containing the FULL Next.js TSX code (no placeholders).\n"
            "- No markdown/code fences.\n"
            "- No extra text before <think> or after the final }.\n"
        )

        for attempt in range(max_retries + 1):
            candidate.generator_temperature = temperature

            response = await provider.complete(
                messages=messages,
                model=generator.model,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            candidate.raw_generator_response = response.content
            content = response.content or ""

            if not content.strip():
                if attempt < max_retries:
                    log_warning(
                        f"Candidate {candidate_id}: Empty response, retrying "
                        f"(attempt {attempt + 1}/{max_retries + 1})"
                    )
                    max_tokens = int(max_tokens * 1.25)
                    temperature = max(0.3, temperature - 0.1)
                    continue
                raise RuntimeError("Empty response content from model")

            finish_reason = getattr(response, "finish_reason", None)
            if finish_reason == "length":
                if attempt < max_retries:
                    log_warning(
                        f"Candidate {candidate_id}: Response truncated at {max_tokens} tokens, "
                        f"retrying with {int(max_tokens * 1.25)} (attempt {attempt + 1}/{max_retries + 1})"
                    )
                    max_tokens = int(max_tokens * 1.25)
                    temperature = max(0.3, temperature - 0.1)
                    # Keep retry context small and explicit (avoid growing message history).
                    messages = messages[:2] + [Message(role="user", content=retry_instruction)]
                    continue
                raise RuntimeError(
                    f"Model response truncated (finish_reason=length) at max_tokens={max_tokens}"
                )

            try:
                output_data = extract_json_strict(content)
                output = validate_uigen_output(output_data)
            except Exception as parse_error:
                salvaged = _salvage_uigen_output(content)
                if salvaged is not None:
                    output = validate_uigen_output(salvaged)
                    log_warning(
                        f"Candidate {candidate_id}: Salvaged non-JSON output into UIGenOutput"
                    )
                else:
                    if attempt < max_retries:
                        log_warning(
                            f"Candidate {candidate_id}: Invalid JSON output ({parse_error}), retrying "
                            f"(attempt {attempt + 1}/{max_retries + 1})"
                        )
                        max_tokens = int(max_tokens * 1.25)
                        temperature = max(0.3, temperature - 0.1)
                        messages = messages[:2] + [Message(role="user", content=retry_instruction)]
                        continue
                    raise

            candidate.files = output.files
            candidate.status = CandidateStatus.GENERATED
            log_info(f"Candidate {candidate_id}: Generated {len(output.files)} files (RAW)")
            break

    except Exception as e:
        log_error(f"Candidate {candidate_id}: Raw generation failed - {e}")
        candidate.error = str(e)
        candidate.status = CandidateStatus.DISCARDED

    return candidate


async def generate_all_candidates_raw(
    task: Task,
    config: Config,
    public_only: bool = False,
) -> AsyncIterator[Candidate]:
    """Generate candidates directly from the task prompt (no UI_SPEC planning)."""
    generators = config.ui_generators
    if public_only:
        generators = config.get_publishable_generators()

    if not generators:
        log_error("No UI generators configured")
        return

    prompt_variants = _load_uigen_prompt_variants(config)
    if not prompt_variants:
        log_error("No UIGEN prompt variants available")
        return

    tasks = []
    for pv in prompt_variants:
        for generator in generators:
            for variant in range(generator.variants):
                tasks.append(
                    generate_candidate_raw(
                        task,
                        generator,
                        variant,
                        config,
                        prompt_variant_id=pv["id"],
                        system_prompt=pv["system_prompt"],
                        input_mode=pv["input_mode"],
                    )
                )

    concurrency = config.budget.concurrency_vertex
    for i in range(0, len(tasks), concurrency):
        batch = tasks[i : i + concurrency]
        results = await asyncio.gather(*batch, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                log_error(f"Raw candidate generation failed: {result}")
            elif isinstance(result, Candidate):
                yield result
