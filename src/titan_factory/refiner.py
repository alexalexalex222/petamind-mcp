"""Refinement module - iterative quality improvement based on vision feedback.

Ported from titan-ui-synth-pipeline's 3-pass refinement architecture.
Uses judge feedback to plan targeted fixes, then applies them.

Supports two modes:
1. Score-based refinement (traditional): Uses numeric thresholds
2. Creative Director mode: Uses qualitative feedback to guide refinement
"""

import json
from typing import Any

from titan_factory.config import Config
from titan_factory.providers.base import Message, ProviderFactory
from titan_factory.schema import (
    Candidate,
    CandidateStatus,
    CreativeDirectorFeedback,
    GeneratedFile,
    JudgeScore,
    TeacherModel,
)
from titan_factory.utils import extract_json_strict, log_info, log_warning

# === REFINEMENT PROMPTS (ported from titan-ui-synth-pipeline) ===

REFINE_REASONER_SYSTEM = """You are a technical translator. Convert design feedback into specific code changes.

INPUT:
- Current code files (React/TypeScript/Tailwind)
- Vision QA feedback (scores, issues, suggestions)

OUTPUT (JSON ONLY, no markdown, no code fences):
{
  "changes_required": [
    {
      "priority": 1,
      "target_file": "app/page.tsx",
      "target_element": "what element/section to change",
      "current_problem": "what is wrong",
      "specific_fix": "exactly what to do - code specific",
      "css_changes": "specific Tailwind classes to add/modify if applicable",
      "structure_changes": "specific JSX changes if applicable"
    }
  ],
  "preserve": ["list of things working well that should NOT be changed"]
}

RULES:
- Be extremely specific. The coder will follow this exactly.
- Limit to 5 changes max. Prioritize critical issues first.
- Focus on visual/UX issues from the feedback, not code style.
- Do NOT suggest changes unrelated to the feedback.

CRITICAL OUTPUT RULES:
- Output must start with { and end with }.
- JSON only. No markdown, no commentary.
"""

REFINE_CODER_SYSTEM = """You are implementing specific design improvements to a Next.js + Tailwind codebase.

INPUT:
1) Current code files
2) A list of specific changes to make (from the refine reasoner)

Apply ONLY the requested changes. Do NOT:
- Add unrequested features
- Change unrelated code
- Improve things not mentioned
- Break existing functionality
- Refactor working code

OUTPUT (JSON ONLY):
{
  "files": [
    {
      "path": "app/page.tsx",
      "content": "// Full updated file content here..."
    }
  ],
  "notes": ["brief notes about what was changed"]
}

CRITICAL:
- Return ALL files that need updating, with their FULL content.
- Preserve code that wasn't mentioned in the changes.
- Output must start with { and end with }.
"""

# === CREATIVE DIRECTOR MODE PROMPTS ===
# These prompts work with qualitative feedback instead of numeric scores.
# They emphasize preserving creative choices while addressing production issues.

CREATIVE_DIRECTOR_REASONER_SYSTEM = """You are a technical translator for a CREATIVE DIRECTOR workflow.

Your job is to convert qualitative creative feedback into specific code changes.
This is NOT about "fixing" the design to match a template. It's about making the
design PRODUCTION READY while PRESERVING its creative identity.

INPUT:
- Current code files (React/TypeScript/Tailwind)
- Creative Director feedback with:
  - PRESERVE: Things that are working well - DO NOT CHANGE these
  - MISSING FOR PRODUCTION: Critical fixes needed
  - CREATIVE ELEVATIONS: Optional improvements to consider

PHILOSOPHY:
- Creative choices in "preserve" are SACRED. Do not change them.
- Focus ONLY on items in "missing_for_production"
- "creative_elevations" are suggestions, not requirements
- If the design is unconventional, that's probably intentional
- Less is more - minimal changes are better

OUTPUT (JSON ONLY, no markdown, no code fences):
{
  "changes_required": [
    {
      "priority": 1,
      "target_file": "app/page.tsx",
      "target_element": "what element/section to change",
      "current_problem": "specific production issue from missing_for_production",
      "specific_fix": "exactly what to do - code specific",
      "css_changes": "specific Tailwind classes to add/modify if applicable",
      "structure_changes": "specific JSX changes if applicable",
      "preserves_creative_intent": true
    }
  ],
  "preserve": ["list of creative elements that MUST NOT be changed"],
  "skipped_elevations": ["creative elevations we chose not to implement and why"]
}

RULES:
- ONLY address items from "missing_for_production"
- Limit to 3 changes max - be surgical
- Never change anything in the "preserve" list
- Each change must include "preserves_creative_intent": true
- Do NOT try to "improve" the design beyond production readiness

CRITICAL OUTPUT RULES:
- Output must start with { and end with }.
- JSON only. No markdown, no commentary.
"""

CREATIVE_DIRECTOR_CODER_SYSTEM = """You are implementing SURGICAL production fixes to a Next.js + Tailwind codebase.

This is a CREATIVE DIRECTOR workflow - the design is intentional, possibly unconventional.
Your job is to make it production-ready, NOT to "fix" its style.

INPUT:
1) Current code files
2) Specific production changes to make (minimal list)
3) Things to PRESERVE (do not modify these)

RULES:
- Apply ONLY the listed changes - nothing more
- PRESERVE everything in the preserve list - if in doubt, don't change it
- Do NOT "clean up" code unless explicitly requested
- Do NOT add features
- Do NOT change creative choices (colors, layouts, typography) unless listed
- Maintain the existing design aesthetic
- EMOJI → SVG: If told to remove emojis, replace with simple inline SVG icons (24x24 path-based, use currentColor)

ACCESSIBILITY FIXES (when flagged by axe-core):
- select-name: Add <label htmlFor="id"> before <select id="id"> (sr-only ok), OR add aria-label to select
- button-name: Add aria-label to any non-text button (icons/arrows/dots):
  - <button aria-label="Previous testimonial">…</button>
  - <button aria-label="Close menu">…</button>
  - Dot pagination: <button aria-label="Go to testimonial 2" … />
- label: Add <label htmlFor="id"> for inputs, OR add aria-label to input
- focus-visible: Add focus:ring-2 focus:ring-offset-2 classes to interactive elements

OUTPUT (JSON ONLY):
{
  "files": [
    {
      "path": "app/page.tsx",
      "content": "// Full updated file content here..."
    }
  ],
  "notes": ["brief notes about what was changed"],
  "preserved": ["creative elements that were intentionally kept unchanged"]
}

CRITICAL:
- Return ALL files that need updating, with their FULL content.
- Preserve code that wasn't mentioned in the changes.
- Output must start with { and end with }.
"""


async def plan_refinement(
    candidate: Candidate,
    feedback: JudgeScore,
    config: Config,
) -> dict[str, Any] | None:
    """Plan refinement fixes based on judge feedback.

    Args:
        candidate: Candidate to refine
        feedback: JudgeScore with issues and suggestions
        config: Application config

    Returns:
        Refinement plan dict with changes_required and preserve, or None if planning fails.
    """
    # Build context for the refine reasoner
    files_context = "\n\n".join(
        f"=== {f.path} ===\n{f.content}" for f in candidate.files
    )

    feedback_context = {
        "score": feedback.score,
        "passing": feedback.passing,
        "issues": feedback.issues,
        "highlights": feedback.highlights,
        "fix_suggestions": feedback.fix_suggestions,
    }

    user_prompt = f"""Current code files:

{files_context}

Vision QA Feedback:
{json.dumps(feedback_context, indent=2)}

Plan the specific code changes needed to address the issues and improve the score.
"""

    messages = [
        Message(role="system", content=REFINE_REASONER_SYSTEM),
        Message(role="user", content=user_prompt),
    ]

    provider = ProviderFactory.get(config.refine_reasoner.provider, config)

    try:
        response = await provider.complete(
            messages=messages,
            model=config.refine_reasoner.model,
            max_tokens=config.refine_reasoner.max_tokens,
            temperature=config.refine_reasoner.temperature,
        )

        plan = extract_json_strict(response.content)
        if plan and isinstance(plan.get("changes_required"), list):
            log_info(
                f"Candidate {candidate.id}: Planned {len(plan['changes_required'])} refinement changes"
            )
            return plan
        else:
            log_warning(f"Candidate {candidate.id}: Refinement plan missing changes_required")
            return None

    except Exception as e:
        log_warning(f"Candidate {candidate.id}: Refinement planning failed: {e}")
        return None


async def apply_refinement(
    candidate: Candidate,
    plan: dict[str, Any],
    config: Config,
) -> Candidate | None:
    """Apply planned refinement fixes to candidate files.

    Args:
        candidate: Candidate to refine
        plan: Refinement plan from plan_refinement
        config: Application config

    Returns:
        Updated candidate with refined files, or None if refinement fails.
    """
    # Build context
    files_context = "\n\n".join(
        f"=== {f.path} ===\n{f.content}" for f in candidate.files
    )

    user_prompt = f"""Current code files:

{files_context}

Changes to apply:
{json.dumps(plan["changes_required"], indent=2)}

Things to preserve (do not change):
{json.dumps(plan.get("preserve", []), indent=2)}

Apply ONLY the listed changes. Return the updated files.
"""

    messages = [
        Message(role="system", content=REFINE_CODER_SYSTEM),
        Message(role="user", content=user_prompt),
    ]

    provider = ProviderFactory.get(config.refine_coder.provider, config)

    # Retry logic for truncated responses (like uigen does)
    max_retries = 3
    current_max_tokens = config.refine_coder.max_tokens
    result = None

    for attempt in range(max_retries):
        try:
            response = await provider.complete(
                messages=messages,
                model=config.refine_coder.model,
                max_tokens=current_max_tokens,
                temperature=config.refine_coder.temperature,
            )

            # Check for truncation
            if response.finish_reason == "length":
                new_tokens = int(current_max_tokens * 1.25)
                log_warning(
                    f"Candidate {candidate.id}: Refine coder truncated at {current_max_tokens}, "
                    f"retrying with {new_tokens} (attempt {attempt + 1}/{max_retries})"
                )
                current_max_tokens = new_tokens
                continue

            result = extract_json_strict(response.content)
            break  # Success

        except ValueError:
            # JSON extraction failed - might be truncated without finish_reason
            if attempt < max_retries - 1:
                new_tokens = int(current_max_tokens * 1.25)
                log_warning(
                    f"Candidate {candidate.id}: Refine coder JSON failed, "
                    f"retrying with {new_tokens} (attempt {attempt + 1}/{max_retries})"
                )
                current_max_tokens = new_tokens
                continue
            raise

    if result is None:
        log_warning(f"Candidate {candidate.id}: Refine coder failed after {max_retries} retries")
        return None

    if not isinstance(result.get("files"), list):
        log_warning(f"Candidate {candidate.id}: Refinement coder returned invalid output")
        return None

    # Update candidate files
    updated_files: list[GeneratedFile] = []
    updated_paths = {f["path"] for f in result["files"] if isinstance(f, dict)}

    # Keep unchanged files
    for f in candidate.files:
        if f.path not in updated_paths:
            updated_files.append(f)

    # Add updated files
    for f in result["files"]:
        if isinstance(f, dict) and f.get("path") and f.get("content"):
            updated_files.append(
                GeneratedFile(path=f["path"], content=f["content"])
            )

    # Track refiner models
    refine_models = list(candidate.refine_models)
    refine_models.append(
        TeacherModel(
            provider=config.refine_reasoner.provider,
            model=config.refine_reasoner.model or "unknown",
            publishable=config.refine_reasoner.publishable,
        )
    )
    refine_models.append(
        TeacherModel(
            provider=config.refine_coder.provider,
            model=config.refine_coder.model or "unknown",
            publishable=config.refine_coder.publishable,
        )
    )

    # Create updated candidate
    refined = candidate.model_copy(deep=True)
    refined.files = updated_files
    refined.refine_passes += 1
    refined.refine_models = refine_models
    refined.status = CandidateStatus.GENERATED  # Reset for re-validation
    refined.build_logs = ""
    refined.error = None

    log_info(
        f"Candidate {candidate.id}: Applied refinement pass {refined.refine_passes}"
    )
    return refined


async def refine_candidate(
    candidate: Candidate,
    feedback: JudgeScore,
    config: Config,
) -> Candidate | None:
    """Perform one refinement pass on a candidate.

    This is the main entry point: plans fixes, then applies them.

    Args:
        candidate: Candidate to refine
        feedback: JudgeScore with issues and suggestions
        config: Application config

    Returns:
        Refined candidate ready for re-validation, or None if refinement fails.
    """
    # Step 1: Plan the refinement
    plan = await plan_refinement(candidate, feedback, config)
    if not plan:
        return None

    # Step 2: Apply the refinement
    refined = await apply_refinement(candidate, plan, config)
    return refined


# === CREATIVE DIRECTOR MODE FUNCTIONS ===
# These functions work with qualitative feedback instead of numeric scores.


async def plan_refinement_creative_director(
    candidate: Candidate,
    feedback: CreativeDirectorFeedback,
    config: Config,
) -> dict[str, Any] | None:
    """Plan refinement fixes based on creative director feedback.

    Unlike score-based refinement, this focuses on:
    - Preserving creative choices (from feedback.preserve)
    - Only fixing production issues (from feedback.missing_for_production)
    - Optionally considering creative elevations

    Args:
        candidate: Candidate to refine
        feedback: CreativeDirectorFeedback with qualitative guidance
        config: Application config

    Returns:
        Refinement plan dict with changes_required and preserve, or None if planning fails.
    """
    # If already shippable and not broken, no refinement needed
    if feedback.shippable and not feedback.obviously_broken:
        log_info(f"Candidate {candidate.id}: Already shippable, no refinement needed")
        return None

    # If nothing is missing for production, no refinement needed
    if not feedback.missing_for_production:
        log_info(f"Candidate {candidate.id}: No production issues to fix")
        return None

    # Build context for the creative director reasoner
    files_context = "\n\n".join(
        f"=== {f.path} ===\n{f.content}" for f in candidate.files
    )

    feedback_context = {
        "shippable": feedback.shippable,
        "obviously_broken": feedback.obviously_broken,
        "preserve": feedback.preserve,
        "missing_for_production": feedback.missing_for_production,
        "creative_elevations": feedback.creative_elevations,
        "appropriate_for_type": feedback.appropriate_for_type,
        "type_feedback": feedback.type_feedback,
    }

    user_prompt = f"""Current code files:

{files_context}

Creative Director Feedback:
{json.dumps(feedback_context, indent=2)}

Plan the MINIMAL code changes needed to address ONLY the "missing_for_production" items.
Do NOT change anything in the "preserve" list.
"""

    messages = [
        Message(role="system", content=CREATIVE_DIRECTOR_REASONER_SYSTEM),
        Message(role="user", content=user_prompt),
    ]

    provider = ProviderFactory.get(config.refine_reasoner.provider, config)

    try:
        response = await provider.complete(
            messages=messages,
            model=config.refine_reasoner.model,
            max_tokens=config.refine_reasoner.max_tokens,
            temperature=config.refine_reasoner.temperature,
        )

        plan = extract_json_strict(response.content)
        if plan and isinstance(plan.get("changes_required"), list):
            num_changes = len(plan["changes_required"])
            log_info(
                f"Candidate {candidate.id}: Creative director planned {num_changes} surgical fixes"
            )
            # Ensure preserve list is passed through
            if "preserve" not in plan:
                plan["preserve"] = feedback.preserve
            return plan
        else:
            log_warning(f"Candidate {candidate.id}: Creative director plan missing changes_required")
            return None

    except Exception as e:
        log_warning(f"Candidate {candidate.id}: Creative director planning failed: {e}")
        return None


async def apply_refinement_creative_director(
    candidate: Candidate,
    plan: dict[str, Any],
    config: Config,
) -> Candidate | None:
    """Apply planned creative director fixes to candidate files.

    This uses the creative director coder prompt which emphasizes:
    - Minimal, surgical changes
    - Preserving creative intent
    - Not "improving" beyond the specified fixes

    Args:
        candidate: Candidate to refine
        plan: Refinement plan from plan_refinement_creative_director
        config: Application config

    Returns:
        Updated candidate with refined files, or None if refinement fails.
    """
    # Build context
    files_context = "\n\n".join(
        f"=== {f.path} ===\n{f.content}" for f in candidate.files
    )

    user_prompt = f"""Current code files:

{files_context}

PRODUCTION FIXES TO APPLY (be surgical):
{json.dumps(plan["changes_required"], indent=2)}

CREATIVE ELEMENTS TO PRESERVE (do not modify):
{json.dumps(plan.get("preserve", []), indent=2)}

Apply ONLY the listed production fixes. Return the updated files.
Maintain the design's creative identity.
"""

    messages = [
        Message(role="system", content=CREATIVE_DIRECTOR_CODER_SYSTEM),
        Message(role="user", content=user_prompt),
    ]

    provider = ProviderFactory.get(config.refine_coder.provider, config)

    # Retry logic for truncated responses
    max_retries = 3
    current_max_tokens = config.refine_coder.max_tokens
    result = None

    for attempt in range(max_retries):
        try:
            response = await provider.complete(
                messages=messages,
                model=config.refine_coder.model,
                max_tokens=current_max_tokens,
                temperature=config.refine_coder.temperature,
            )

            # Check for truncation
            if response.finish_reason == "length":
                new_tokens = int(current_max_tokens * 1.25)
                log_warning(
                    f"Candidate {candidate.id}: Creative director coder truncated at {current_max_tokens}, "
                    f"retrying with {new_tokens} (attempt {attempt + 1}/{max_retries})"
                )
                current_max_tokens = new_tokens
                continue

            result = extract_json_strict(response.content)
            break  # Success

        except ValueError:
            # JSON extraction failed - might be truncated without finish_reason
            if attempt < max_retries - 1:
                new_tokens = int(current_max_tokens * 1.25)
                log_warning(
                    f"Candidate {candidate.id}: Creative director coder JSON failed, "
                    f"retrying with {new_tokens} (attempt {attempt + 1}/{max_retries})"
                )
                current_max_tokens = new_tokens
                continue
            raise

    if result is None:
        log_warning(f"Candidate {candidate.id}: Creative director coder failed after {max_retries} retries")
        return None

    if not isinstance(result.get("files"), list):
        log_warning(f"Candidate {candidate.id}: Creative director coder returned invalid output")
        return None

    # Update candidate files
    updated_files: list[GeneratedFile] = []
    updated_paths = {f["path"] for f in result["files"] if isinstance(f, dict)}

    # Keep unchanged files
    for f in candidate.files:
        if f.path not in updated_paths:
            updated_files.append(f)

    # Add updated files
    for f in result["files"]:
        if isinstance(f, dict) and f.get("path") and f.get("content"):
            updated_files.append(
                GeneratedFile(path=f["path"], content=f["content"])
            )

    # Track refiner models
    refine_models = list(candidate.refine_models)
    refine_models.append(
        TeacherModel(
            provider=config.refine_reasoner.provider,
            model=config.refine_reasoner.model or "unknown",
            publishable=config.refine_reasoner.publishable,
        )
    )
    refine_models.append(
        TeacherModel(
            provider=config.refine_coder.provider,
            model=config.refine_coder.model or "unknown",
            publishable=config.refine_coder.publishable,
        )
    )

    # Create updated candidate
    refined = candidate.model_copy(deep=True)
    refined.files = updated_files
    refined.refine_passes += 1
    refined.refine_models = refine_models
    refined.status = CandidateStatus.GENERATED  # Reset for re-validation
    refined.build_logs = ""
    refined.error = None

    log_info(
        f"Candidate {candidate.id}: Applied creative director refinement pass {refined.refine_passes}"
    )
    return refined


async def refine_candidate_creative_director(
    candidate: Candidate,
    feedback: CreativeDirectorFeedback,
    config: Config,
) -> Candidate | None:
    """Perform one creative director refinement pass on a candidate.

    This is the main entry point for creative director mode:
    - Skips refinement if already shippable
    - Plans minimal surgical fixes based on qualitative feedback
    - Applies fixes while preserving creative intent

    Args:
        candidate: Candidate to refine
        feedback: CreativeDirectorFeedback with qualitative guidance
        config: Application config

    Returns:
        Refined candidate ready for re-validation, or None if refinement not needed/fails.
    """
    # Step 1: Plan the refinement (may return None if already shippable)
    plan = await plan_refinement_creative_director(candidate, feedback, config)
    if not plan:
        return None

    # Step 2: Apply the refinement
    refined = await apply_refinement_creative_director(candidate, plan, config)
    return refined
