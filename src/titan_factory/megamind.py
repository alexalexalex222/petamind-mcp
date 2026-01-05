"""Megamind 3-Pass Reasoning Module.

Implements a 3-pass ensemble reasoning architecture for UI planning:

Pass 1: Three parallel sub-reasoners with different thinking styles:
  - Bold: Takes creative risks, pushes boundaries, signature moments
  - Minimal: Clean, conservative, production-safe, clarity-focused
  - Safe: Balanced approach, best practices, accessible design

Pass 2: Synthesizer merges the best ideas from all 3 plans:
  - Picks the strongest hero/CTA from Bold
  - Preserves clarity/simplicity from Minimal
  - Ensures accessibility/best practices from Safe
  - Outputs a unified "best-of-3" UI_SPEC

Pass 3: All UI generators implement the synthesized plan with their own style.

Inspired by:
- Iterative refinement loops that improve pass rates via targeted correction
- Ensemble methods that increase output diversity then converge
- The "wisdom of crowds" effect from multiple reasoning paths
"""

import asyncio
import json
from typing import NamedTuple

from titan_factory.config import Config, ModelConfig
from titan_factory.providers import Message, ProviderFactory
from titan_factory.schema import Task, UISpec, validate_ui_spec
from titan_factory.utils import extract_json_strict, log_error, log_info, log_warning


class ReasonerPlan(NamedTuple):
    """Output from a single sub-reasoner."""

    style: str  # "bold", "minimal", or "safe"
    ui_spec: UISpec | None
    reasoning: str  # The <think> block content
    error: str | None


class MegamindResult(NamedTuple):
    """Final output from Megamind reasoning."""

    synthesized_spec: UISpec
    plans: list[ReasonerPlan]  # All 3 sub-reasoner plans
    synthesis_reasoning: str  # How the synthesizer merged them


# === Sub-Reasoner System Prompts ===

_UI_SPEC_SCHEMA = """UI_SPEC SCHEMA (shape only; you must fill real values):
{
  "niche": {"id": "string", "vertical": "string", "pattern": "string"},
  "page_type": "landing|directory_home|city_index|category_index|listing_profile|admin_dashboard|edit",
  "brand": {
    "name": "string",
    "mood": "dark|light",
    "accent": "blue|teal|violet|green|orange|red|amber|rose|cyan|lime|fuchsia",
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
      {"id": "hero", "must_include": ["headline", "subheadline", "primary_cta", "trust_chips"], "optional": false}
    ],
    "navigation": "minimal|standard",
    "notes": "string"
  },
  "edit_task": {"enabled": false, "instructions": "", "code_old": ""}
}"""


BOLD_REASONER_PROMPT = f"""You are a BOLD UI specification generator - you push creative boundaries.

Your role in the ensemble: Generate a plan that takes tasteful creative RISKS.
- Include at least one "signature layout moment" (bento grid, timeline, comparison strip, proof wall)
- Push beyond safe/boring choices while staying build-safe
- Choose unexpected but coherent accent/mood combinations
- Prioritize memorability and visual impact

Given a task prompt, output a SINGLE JSON object matching the UI_SPEC schema EXACTLY.

CRITICAL OUTPUT RULES:
1. First output your reasoning in a <think>...</think> block
2. Then output ONLY valid JSON (no markdown fences, no commentary)
3. JSON must start with {{ and end with }}

{_UI_SPEC_SCHEMA}

ENUM VALUES (use exactly):
- page_type: landing | directory_home | city_index | category_index | listing_profile | admin_dashboard | edit
- brand.mood: dark | light
- brand.accent: blue | teal | violet | green | orange | red | amber | rose | cyan | lime | fuchsia
- brand.radius: soft | medium
- brand.density: airy | balanced | compact

CONTENT REQUIREMENTS:
- content.highlights: EXACTLY 3 short strings
- content.testimonials: EXACTLY 2 items with name AND text fields
- content.faq: EXACTLY 3 items with q AND a fields
- Use realistic names/copy, not placeholders

In your <think> block, explain:
1. What makes this plan BOLD/memorable
2. The signature moment you're including
3. Why this risk is worth taking

Return the complete JSON only after </think>."""


MINIMAL_REASONER_PROMPT = f"""You are a MINIMAL UI specification generator - you prioritize clarity and restraint.

Your role in the ensemble: Generate a plan that is CLEAN and PRODUCTION-SAFE.
- Focus on clarity, whitespace, and breathing room
- Choose the simplest layout that accomplishes the goal
- Prioritize readability and fast comprehension
- Avoid novelty for novelty's sake

Given a task prompt, output a SINGLE JSON object matching the UI_SPEC schema EXACTLY.

CRITICAL OUTPUT RULES:
1. First output your reasoning in a <think>...</think> block
2. Then output ONLY valid JSON (no markdown fences, no commentary)
3. JSON must start with {{ and end with }}

{_UI_SPEC_SCHEMA}

ENUM VALUES (use exactly):
- page_type: landing | directory_home | city_index | category_index | listing_profile | admin_dashboard | edit
- brand.mood: dark | light
- brand.accent: blue | teal | violet | green | orange | red | amber | rose | cyan | lime | fuchsia
- brand.radius: soft | medium
- brand.density: airy | balanced | compact

CONTENT REQUIREMENTS:
- content.highlights: EXACTLY 3 short strings
- content.testimonials: EXACTLY 2 items with name AND text fields
- content.faq: EXACTLY 3 items with q AND a fields
- Use realistic names/copy, not placeholders

In your <think> block, explain:
1. What makes this plan MINIMAL/clean
2. What complexity you deliberately avoided
3. Why less is more for this brief

Return the complete JSON only after </think>."""


SAFE_REASONER_PROMPT = f"""You are a SAFE UI specification generator - you balance creativity with accessibility.

Your role in the ensemble: Generate a plan that follows BEST PRACTICES.
- Ensure WCAG AA accessibility throughout
- Use proven layout patterns that work well
- Balance visual appeal with usability
- Think about all users (mobile, keyboard, screen reader)

Given a task prompt, output a SINGLE JSON object matching the UI_SPEC schema EXACTLY.

CRITICAL OUTPUT RULES:
1. First output your reasoning in a <think>...</think> block
2. Then output ONLY valid JSON (no markdown fences, no commentary)
3. JSON must start with {{ and end with }}

{_UI_SPEC_SCHEMA}

ENUM VALUES (use exactly):
- page_type: landing | directory_home | city_index | category_index | listing_profile | admin_dashboard | edit
- brand.mood: dark | light
- brand.accent: blue | teal | violet | green | orange | red | amber | rose | cyan | lime | fuchsia
- brand.radius: soft | medium
- brand.density: airy | balanced | compact

CONTENT REQUIREMENTS:
- content.highlights: EXACTLY 3 short strings
- content.testimonials: EXACTLY 2 items with name AND text fields
- content.faq: EXACTLY 3 items with q AND a fields
- Use realistic names/copy, not placeholders

In your <think> block, explain:
1. What makes this plan SAFE/accessible
2. The best practices you're applying
3. How this serves all users well

Return the complete JSON only after </think>."""


# === Megamind v2 (style-lock + amplify) ===

STYLE_GUARDIAN_REASONER_PROMPT = f"""You are STYLE_GUARDIAN_REASONER: a category stylist and brand-cohesion enforcer.

GOAL
Produce a UI_SPEC that is (1) category-appropriate, (2) stylistically coherent, and (3) build-safe.
You do NOT invent a new aesthetic. You EXECUTE the routed aesthetic precisely.

HARD CONSTRAINTS (NON-NEGOTIABLE)
1) The task prompt may include a block named:
   "STYLE ROUTING (HARD CONSTRAINTS — MUST FOLLOW EXACTLY)"
   Treat it as absolute. Do not contradict it.
2) The task prompt may include:
   "Theme override (must follow exactly): brand.mood: ... brand.accent: ..."
   Copy those values EXACTLY into UI_SPEC.brand.mood and UI_SPEC.brand.accent.
3) If style routing is present, populate these optional UI_SPEC.brand fields so generators can follow them:
   - style_family, style_persona, style_keywords_mandatory, style_avoid, imagery_style, layout_motif
4) If style_family != cyber_tech:
   - Hard-ban terminal/console/hacker metaphors and neon-glow aesthetics
   - Avoid heavy monospace as the primary typographic voice
   - Avoid dense app-dashboard layouts on marketing pages (unless page_type is admin_dashboard)

STRUCTURE REQUIREMENTS
- Output a complete landing-page structure with these sections (IDs may be slightly customized but must cover the intent):
  header_nav, hero, problem_tension, how_it_works, benefits_features, social_proof, pricing_offer, faq, final_cta, footer
- Keep it 10–12 sections total.
- Ensure at least TWO signature layout moments that fit the routed style_family (not “cyber by default”):
  - One should be above-the-fold (hero treatment / hero composition)
  - One should be mid-page (benefits_features / social_proof / pricing_offer)

OUTPUT RULES
1) First output your reasoning in a <think>...</think> block
2) Then output ONLY valid JSON (no markdown fences, no commentary)
3) JSON must start with {{ and end with }}

{_UI_SPEC_SCHEMA}

Return the complete JSON only after </think>."""


BOLD_LAYOUT_REASONER_PROMPT = f"""You are BOLD_LAYOUT_REASONER: a creative layout director.

GOAL
Produce a UI_SPEC with memorable structure and visual rhythm WITHOUT violating style routing.

HARD CONSTRAINTS (NON-NEGOTIABLE)
- STYLE ROUTING block is absolute. Theme override mood/accent are locked.
- If style_family != cyber_tech: NO terminal/CLI/hacker metaphors; no neon glow “tech” look by default.
- Avoid app-like density for marketing pages (no sidebars, no dashboards, no data tables unless page_type=admin_dashboard).

LAYOUT REQUIREMENTS
- Must include 2–3 signature layout moments chosen from:
  bento grid, timeline/stepper, comparison strip, proof wall, split hero with layered cards,
  editorial pull-quote rail, stepped process, diagonal section break, asymmetrical grid.
- The signature moment(s) must match the routed style_family.
- Keep section count 10–12 and include all required landing sections.
- IMPORTANT: Do NOT let the middle of the page collapse into generic blocks.
  benefits_features, social_proof, and pricing_offer must each have a non-generic structure
  (e.g., bento/accordion hybrid, proof wall, pricing clarity panel, comparison strip).

OUTPUT RULES
1) First output your reasoning in a <think>...</think> block
2) Then output ONLY valid JSON (no markdown fences, no commentary)
3) JSON must start with {{ and end with }}

{_UI_SPEC_SCHEMA}

Return the complete JSON only after </think>."""


BOLD_CONVERSION_REASONER_PROMPT = f"""You are BOLD_CONVERSION_REASONER: a CRO strategist.

GOAL
Produce a UI_SPEC that maximizes clarity, trust, and action — with persuasive structure and objection handling — while staying faithful to routed style.

HARD CONSTRAINTS (NON-NEGOTIABLE)
- STYLE ROUTING block is absolute. Theme override mood/accent are locked.
- If style_family != cyber_tech: NO terminal/CLI/hacker metaphors; no neon glow “tech” look by default.
- Avoid dense SaaS dashboard patterns on marketing pages.

CONVERSION REQUIREMENTS
- Must include:
  - Clear primary CTA and (optional) secondary CTA
  - Social proof with specificity (testimonials, stats, outcomes)
  - Pricing/offer with 2–3 tiers OR a single offer with clear inclusions
  - FAQ with at least 6 objections (price, time, fit, results, logistics, risk)
  - Risk reversal / reassurance (trial, guarantee, transparency pact)
- Add ONE conversion signature moment (required): comparison strip, proof wall, guarantee block, or who-it's-for/not-for.
  Ensure social_proof and pricing_offer are not generic 3-column cards by default.

OUTPUT RULES
1) First output your reasoning in a <think>...</think> block
2) Then output ONLY valid JSON (no markdown fences, no commentary)
3) JSON must start with {{ and end with }}

{_UI_SPEC_SCHEMA}

Return the complete JSON only after </think>."""


SYNTHESIZER_PROMPT_V2 = """You are the UI_SPEC SYNTHESIZER (STYLE-LOCK AMPLIFIER).

You received 3 plans:
1) STYLE_GUARDIAN: category-appropriate style + cohesion (source of truth for style)
2) BOLD_LAYOUT: signature layout moments
3) BOLD_CONVERSION: CRO structure and objections

YOUR JOB
Create ONE best UI_SPEC that:
- Locks style to STYLE_GUARDIAN (no blending, no “averaging into tech”)
- Amplifies distinctive signature moments from BOLD_LAYOUT and/or BOLD_CONVERSION
- Keeps strong conversion structure without adding app-like density

HARD RULES
1) Copy these fields EXACTLY from STYLE_GUARDIAN:
   - brand.mood
   - brand.accent
   - brand.style_family/style_persona (if present)
   - brand.style_keywords_mandatory/style_avoid (if present)
2) Do NOT introduce anything from style_avoid.
3) If style_family != cyber_tech: do not introduce neon/terminal/hacker motifs or heavy monospace identity.
4) Ensure required landing sections are present (header, hero, problem, how_it_works, benefits, proof, pricing, faq, final_cta, footer).
5) Keep 10–12 sections total.
6) Must include at least TWO signature layout moments:
   - One above-the-fold (hero)
   - One mid-page (benefits/proof/pricing)
   Prefer the most category-appropriate ones.

CRITICAL OUTPUT RULES:
1) First output your synthesis reasoning in a <think>...</think> block
2) Then output ONLY valid JSON (no markdown fences, no commentary)
3) JSON must start with { and end with }

Return the unified JSON only after </think>."""


# === Synthesizer System Prompt ===

SYNTHESIZER_PROMPT = """You are a SYNTHESIZER that merges the best ideas from three UI plans.

You will receive THREE UI_SPEC plans from different reasoners:
1. BOLD: Creative risks, signature moments, memorability
2. MINIMAL: Clean, simple, production-safe
3. SAFE: Accessible, best practices, proven patterns

Your job: Create the BEST POSSIBLE unified plan by:
- Taking the strongest hero/CTA approach (often from Bold)
- Preserving clarity and simplicity (from Minimal)
- Ensuring accessibility and best practices (from Safe)
- Resolving conflicts intelligently
- NOT just averaging - pick the BEST element for each decision

CRITICAL OUTPUT RULES:
1. First output your synthesis reasoning in a <think>...</think> block
2. Then output ONLY valid JSON (no markdown fences, no commentary)
3. JSON must start with { and end with }

In your <think> block, explain FOR EACH MAJOR DECISION:
- Which plan you're drawing from
- Why that choice is best
- Any modifications you're making

The final JSON should be a COMPLETE UI_SPEC that:
- Is coherent (not a Frankenstein of mismatched parts)
- Takes the best creative elements that work together
- Maintains accessibility and production-readiness
- Would make all three reasoners proud

Return the unified JSON only after </think>."""


def _megamind_model_for_style(style: str, config: Config) -> ModelConfig:
    """Pick the configured model for a given Megamind stage.

    Defaults to the main planner model for backwards compatibility.
    """
    style_key = str(style or "").strip().lower()
    if style_key == "bold":
        return config.megamind_bold or config.planner
    if style_key == "minimal":
        return config.megamind_minimal or config.planner
    if style_key == "safe":
        return config.megamind_safe or config.planner
    if style_key == "style_guardian":
        return config.megamind_safe or config.planner
    if style_key == "bold_layout":
        return config.megamind_bold or config.planner
    if style_key == "bold_conversion":
        return config.megamind_minimal or config.planner
    if style_key == "synthesizer":
        return config.megamind_synthesizer or config.planner
    return config.planner


async def _run_sub_reasoner(
    task: Task,
    style: str,
    system_prompt: str,
    config: Config,
) -> ReasonerPlan:
    """Run a single sub-reasoner to generate a plan.

    Args:
        task: The task to plan
        style: Reasoner style ("bold", "minimal", "safe")
        system_prompt: The system prompt for this reasoner
        config: Application configuration

    Returns:
        ReasonerPlan with the reasoner's output
    """
    model_cfg = _megamind_model_for_style(style, config)
    provider = ProviderFactory.get(model_cfg.provider, config)

    if not model_cfg.model:
        return ReasonerPlan(
            style=style,
            ui_spec=None,
            reasoning="",
            error="Megamind reasoner model not configured",
        )

    messages = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=task.prompt),
    ]

    log_info(
        f"Megamind {style.upper()} reasoner starting with "
        f"{model_cfg.provider}:{model_cfg.model}"
    )

    # BOLD planners are the most likely to hit truncation or format drift.
    # Give them one extra retry so we preserve signature-moment diversity.
    max_retries = 2 if str(style).lower() in ("bold", "bold_layout") else 1
    temperature = float(model_cfg.temperature or 0.7)
    max_tokens = int(model_cfg.max_tokens or 2000)

    for attempt in range(max_retries + 1):
        try:
            response = await provider.complete(
                messages=messages,
                model=model_cfg.model,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            content = response.content or ""
            finish_reason = getattr(response, "finish_reason", None)
            if finish_reason == "length" and attempt < max_retries:
                log_warning(
                    f"Megamind {style.upper()} reasoner: output truncated at {max_tokens} tokens; retrying with higher limit"
                )
                max_tokens = int(max_tokens * 1.25)
                temperature = max(0.3, temperature - 0.1)
                messages = messages + [
                    Message(
                        role="user",
                        content=(
                            "Your last output was truncated. Re-output the FULL UI_SPEC JSON only "
                            "(no markdown, no commentary). Start with { and end with }."
                        ),
                    )
                ]
                continue

            # Extract <think> block for training data
            import re

            think_match = re.search(r"<think>([\s\S]*?)</think>", content, re.IGNORECASE)
            reasoning = think_match.group(1).strip() if think_match else ""

            # Extract and validate JSON
            spec_data = extract_json_strict(content)
            if not isinstance(spec_data, dict):
                raise ValueError(f"UI_SPEC must be a JSON object, got {type(spec_data).__name__}")
            ui_spec = validate_ui_spec(spec_data)

            log_info(f"Megamind {style.upper()} reasoner: Plan validated successfully")

            return ReasonerPlan(
                style=style,
                ui_spec=ui_spec,
                reasoning=reasoning,
                error=None,
            )

        except Exception as e:
            if attempt < max_retries:
                log_warning(
                    f"Megamind {style.upper()} reasoner: parse failed ({e}); retrying with stricter JSON-only instruction"
                )
                # Reduce temperature and add a strict repair instruction.
                temperature = max(0.3, temperature - 0.2)
                max_tokens = int(max_tokens * 1.25)
                messages = messages + [
                    Message(
                        role="user",
                        content=(
                            "Your last output did not validate. Re-output ONLY a single valid UI_SPEC JSON object "
                            "(no markdown, no commentary). Start with { and end with }."
                        ),
                    )
                ]
                continue

            log_error(f"Megamind {style.upper()} reasoner failed: {e}")
            return ReasonerPlan(
                style=style,
                ui_spec=None,
                reasoning="",
                error=str(e),
            )


async def _run_synthesizer(
    task: Task,
    plans: list[ReasonerPlan],
    config: Config,
    *,
    system_prompt: str = SYNTHESIZER_PROMPT,
) -> tuple[UISpec, str]:
    """Run the synthesizer to merge plans.

    Args:
        task: The original task
        plans: List of ReasonerPlan from sub-reasoners
        config: Application configuration

    Returns:
        Tuple of (synthesized UISpec, synthesis reasoning)

    Raises:
        ValueError: If synthesis fails
    """
    model_cfg = _megamind_model_for_style("synthesizer", config)
    provider = ProviderFactory.get(model_cfg.provider, config)

    if not model_cfg.model:
        raise ValueError("Megamind synthesizer model not configured")

    # Build the user prompt with all three plans
    plans_text = ""
    for plan in plans:
        if plan.ui_spec:
            plans_text += f"\n\n=== {plan.style.upper()} PLAN ===\n"
            plans_text += f"Reasoning: {plan.reasoning[:500]}...\n" if len(plan.reasoning) > 500 else f"Reasoning: {plan.reasoning}\n"
            plans_text += f"UI_SPEC\n{plan.ui_spec.model_dump_json(indent=2)}\n"
        else:
            plans_text += f"\n\n=== {plan.style.upper()} PLAN (FAILED) ===\n"
            plans_text += f"Error: {plan.error}\n"

    user_prompt = f"""Original Task:
{task.prompt}

Here are the three plans to synthesize:
{plans_text}

Create the BEST unified UI_SPEC by merging the strongest elements from each plan.
Output your reasoning in <think>...</think>, then the final JSON."""

    messages = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=user_prompt),
    ]

    log_info(
        f"Megamind SYNTHESIZER starting with {model_cfg.provider}:{model_cfg.model}"
    )

    response = await provider.complete(
        messages=messages,
        model=model_cfg.model,
        max_tokens=model_cfg.max_tokens,
        temperature=model_cfg.temperature,
    )

    content = response.content or ""

    # Extract <think> block
    import re

    think_match = re.search(r"<think>([\s\S]*?)</think>", content, re.IGNORECASE)
    synthesis_reasoning = think_match.group(1).strip() if think_match else ""

    # Extract and validate JSON
    spec_data = extract_json_strict(content)
    if not isinstance(spec_data, dict):
        raise ValueError(f"UI_SPEC must be a JSON object, got {type(spec_data).__name__}")
    ui_spec = validate_ui_spec(spec_data)

    log_info("Megamind SYNTHESIZER: Unified plan validated successfully")

    return ui_spec, synthesis_reasoning


async def generate_ui_spec_megamind(
    task: Task,
    config: Config,
) -> MegamindResult:
    """Generate UI_SPEC using 3-pass Megamind reasoning.

    This is the main entry point for Megamind planning.

    Pass 1: Run 3 sub-reasoners in parallel (bold, minimal, safe)
    Pass 2: Synthesize the best ideas into a unified plan
    Pass 3: Return the synthesized spec for UI generators

    Args:
        task: The task to plan
        config: Application configuration

    Returns:
        MegamindResult with synthesized spec and all reasoning

    Raises:
        ValueError: If all sub-reasoners fail or synthesis fails
    """
    log_info(f"Megamind: Starting 3-pass reasoning for task {task.id}")

    # === PASS 1: Run 3 sub-reasoners in parallel ===
    log_info("Megamind Pass 1: Running 3 sub-reasoners in parallel...")

    use_v2 = bool(getattr(config.pipeline, "megamind_v2_enabled", False))
    if use_v2:
        reasoner_tasks = [
            _run_sub_reasoner(task, "style_guardian", STYLE_GUARDIAN_REASONER_PROMPT, config),
            _run_sub_reasoner(task, "bold_layout", BOLD_LAYOUT_REASONER_PROMPT, config),
            _run_sub_reasoner(task, "bold_conversion", BOLD_CONVERSION_REASONER_PROMPT, config),
        ]
    else:
        reasoner_tasks = [
            _run_sub_reasoner(task, "bold", BOLD_REASONER_PROMPT, config),
            _run_sub_reasoner(task, "minimal", MINIMAL_REASONER_PROMPT, config),
            _run_sub_reasoner(task, "safe", SAFE_REASONER_PROMPT, config),
        ]

    plans = await asyncio.gather(*reasoner_tasks)
    plans = list(plans)

    # Check how many succeeded
    successful_plans = [p for p in plans if p.ui_spec is not None]
    log_info(f"Megamind Pass 1: {len(successful_plans)}/3 sub-reasoners succeeded")

    if len(successful_plans) == 0:
        errors = [f"{p.style}: {p.error}" for p in plans]
        raise ValueError(f"All Megamind sub-reasoners failed: {errors}")

    # === PASS 2: Synthesize the plans ===
    log_info("Megamind Pass 2: Synthesizing best ideas...")

    if len(successful_plans) == 1:
        # Only one plan succeeded - use it directly
        log_warning("Megamind: Only 1 plan available, using it directly without synthesis")
        only_plan = successful_plans[0]
        return MegamindResult(
            synthesized_spec=only_plan.ui_spec,  # type: ignore
            plans=plans,
            synthesis_reasoning=f"Single plan fallback: used {only_plan.style} plan directly",
        )

    try:
        synthesized_spec, synthesis_reasoning = await _run_synthesizer(
            task,
            plans,
            config,
            system_prompt=(SYNTHESIZER_PROMPT_V2 if use_v2 else SYNTHESIZER_PROMPT),
        )
    except Exception as e:
        # Synthesis failed - fall back to the first successful plan
        log_error(f"Megamind synthesis failed: {e}")
        log_warning("Megamind: Falling back to first successful plan")
        fallback_plan = successful_plans[0]
        return MegamindResult(
            synthesized_spec=fallback_plan.ui_spec,  # type: ignore
            plans=plans,
            synthesis_reasoning=f"Synthesis fallback: {e}. Used {fallback_plan.style} plan.",
        )

    log_info("Megamind Pass 2: Synthesis complete!")

    # === PASS 3: Return for UI generators ===
    return MegamindResult(
        synthesized_spec=synthesized_spec,
        plans=plans,
        synthesis_reasoning=synthesis_reasoning,
    )


# === Shared Context Tool (for agent coordination) ===


class SharedContext:
    """Shared context for agent coordination.

    Allows agents to read/write to a shared state during reasoning.
    This enables coordination without direct communication.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._history: list[tuple[str, str, str]] = []  # (agent, key, value)

    def write(self, agent: str, key: str, value: str) -> None:
        """Write a value to shared context.

        Args:
            agent: Agent identifier (e.g., "bold", "synthesizer")
            key: Context key
            value: Value to store
        """
        self._store[key] = value
        self._history.append((agent, key, value))

    def read(self, key: str) -> str | None:
        """Read a value from shared context.

        Args:
            key: Context key

        Returns:
            Value if found, None otherwise
        """
        return self._store.get(key)

    def get_history(self) -> list[tuple[str, str, str]]:
        """Get full history of context writes.

        Returns:
            List of (agent, key, value) tuples
        """
        return list(self._history)

    def get_state(self) -> dict[str, str]:
        """Get current state snapshot.

        Returns:
            Current context state
        """
        return dict(self._store)
