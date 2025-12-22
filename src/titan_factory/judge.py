"""Judge module - scores candidates using vision models or heuristics."""

import asyncio
import re
from pathlib import Path

from titan_factory.config import Config
from titan_factory.providers import Message, ProviderFactory
from titan_factory.schema import (
    Candidate,
    CandidateStatus,
    CreativeDirectorFeedback,
    JudgeScore,
    PremiumGate,
)
from titan_factory.utils import extract_json_strict, log_error, log_info, log_warning

# === Vision Judge Prompt ===
JUDGE_SYSTEM_PROMPT = """You are a UI design quality judge. Score the provided screenshots on a scale of 0-10.

SCORING CRITERIA (each worth 2 points):
1. Visual polish: Premium feel, attention to detail, typography quality
2. Layout: Proper spacing, alignment, visual hierarchy, responsive design
3. Branding: Consistent colors, mood, style throughout
4. Usability: Clear CTAs, readable text, intuitive navigation
5. Completeness: All sections present, no broken layouts, no placeholder issues

CRITICAL RULES:
1. Output ONLY valid JSON - no markdown, no explanation, no <think>
2. Be strict but fair - only score 8+ for truly premium designs
3. Note specific issues that could be fixed

OUTPUT FORMAT:
{
  "score": 7.5,
  "pass": true,
  "issues": ["Heading text too small on mobile", "CTA button needs more contrast"],
  "highlights": ["Excellent typography choices", "Clean layout"],
  "fix_suggestions": ["Increase heading font size by 20%", "Add bg-blue-600 to CTA"]
}

Start with { and end with }."""


JUDGE_USER_PROMPT = """Score these screenshots of a {page_type} page.

SCREENSHOTS PROVIDED (in order):
{viewport_labels}

Expected style:
- Mood: {mood}
- Accent color: {accent}
- Density: {density}
- Style: {style_keywords}

Evaluate the design quality. Pass threshold is {threshold}/10.
Be specific in issues and suggestions. Note which viewport has issues."""

# Viewport dimensions for labeling
VIEWPORT_LABELS = {
    "mobile": "Mobile (375×812)",
    "tablet": "Tablet (768×1024)",
    "desktop": "Desktop (1440×900)",
}


BROKEN_GATE_SYSTEM_PROMPT = """You are a STRICT website screenshot validator.
Your job is NOT to judge aesthetics. Only decide if the page is clearly BROKEN.

Mark broken=true ONLY when you are highly confident the page is broken, such as:
- runtime error overlay, stack trace, red error screen, "Unhandled Runtime Error"
- "Application error", "Something went wrong", Next.js error overlay
- 404 / "page could not be found"
- blank/empty page with almost no visible content
- obvious missing CSS/layout causing the page to be unusable (e.g. everything overlaps as a single blob)

If the page looks like a real website (even if ugly, boring, or low quality), broken MUST be false.

OUTPUT RULES:
- Output ONLY valid JSON (no markdown, no <think>, no explanation).
- JSON must start with { and end with }.

OUTPUT FORMAT:
{
  "broken": false,
  "confidence": 0.0,
  "reasons": []
}"""


BROKEN_GATE_USER_PROMPT = """Decide whether this page is BROKEN.

Be conservative: if unsure, set broken=false.
Return broken=true only if confidence >= {min_confidence}.
"""


PREMIUM_GATE_SYSTEM_PROMPT = """You are a STRICT premium website quality validator.
Your job is to decide whether the page looks SHIP-READY and PREMIUM.

This is NOT about "does it compile" and NOT about "is it broken". The page may be functional
but still not premium. Premium means: strong visual polish, clear hierarchy, coherent design,
and looks like something a real product team would ship.

Rules:
- Be strict: set premium=true ONLY when the page clearly looks premium.
- If the page is merely "fine" or "basic", premium must be false.
- If you are unsure, premium=false with low confidence.

OUTPUT RULES:
- Output ONLY valid JSON (no markdown, no <think>, no explanation).
- JSON must start with { and end with }.

OUTPUT FORMAT:
{
  "premium": false,
  "confidence": 0.0,
  "issues": [],
  "fix_suggestions": []
}"""


PREMIUM_GATE_USER_PROMPT = """Decide whether this page is premium/ship-ready.

Be strict: premium=true only if you'd ship it as a "premium" site without redesign.
If it's basic, generic, or under-designed, premium=false.

Keep the output SHORT to avoid truncation:
- issues: max 4 items (short phrases)
- fix_suggestions: max 4 items (short phrases)

Do not include any additional keys. Do not include any text outside the JSON.
"""


# === CREATIVE DIRECTOR MODE ===
# Qualitative feedback instead of numeric scoring.
# Encourages creative risk-taking and emergence.

CREATIVE_DIRECTOR_SYSTEM_PROMPT = """You are a world-class creative director reviewing a website design.

Your job is NOT to give a numeric score. Instead, provide rich qualitative feedback
that helps improve the design while PRESERVING creative emergence and risk-taking.

IMPORTANT PHILOSOPHY:
- "Different" is NOT "broken". Unconventional designs may be intentional.
- Creative risk-taking should be ENCOURAGED, not punished.
- Focus on production readiness, not personal taste.
- A simple, minimal design is valid if executed well.
- An experimental, bold design is valid if it works.

WHAT MAKES SOMETHING "SHIPPABLE":
- No critical errors (blank page, runtime errors, missing content)
- Core functionality is present (navigation works, CTAs visible)
- Text is readable, layout is coherent
- Responsive across viewports
- NO EMOJI CHARACTERS (🚀❌✅🎯💡 etc.) - emojis look cheap/unprofessional

EMOJI CHECK: If you see ANY emoji characters in the UI (buttons, headings, cards, etc.),
add "Remove emoji characters - use SVG icons instead" to missing_for_production.
Emojis are a hard requirement violation.

WHAT DOES NOT MAKE SOMETHING "NOT SHIPPABLE":
- Unusual color choices (that's creative expression)
- Unconventional layouts (that's experimentation)
- Bold typography (that's design intent)
- Minimal/sparse design (that's a valid style)

OUTPUT RULES:
- Output ONLY valid JSON (no markdown, no <think>, no explanation)
- JSON must start with { and end with }
- Be GENEROUS with creative choices
- Be SPECIFIC with production issues

OUTPUT FORMAT:
{
  "shippable": true,
  "obviously_broken": false,
  "preserve": ["list of creative choices to keep"],
  "missing_for_production": ["only critical issues"],
  "creative_elevations": ["suggestions from a master designer"],
  "appropriate_for_type": true,
  "type_feedback": "brief note on layout/length fit"
}"""


CREATIVE_DIRECTOR_USER_PROMPT = """Review these screenshots as a creative director.

SITE TYPE: {page_type}
INTENDED STYLE: {mood} mood, {accent} accent, {style_keywords}
DENSITY: {density}

SCREENSHOTS PROVIDED:
{viewport_labels}

Answer these questions through your JSON output:

1. SHIPPABLE: Could this go live as-is? (not "is it perfect" - just "is it ready")
2. OBVIOUSLY BROKEN: Is there a critical error making this unusable?
3. PRESERVE: What creative choices are working? What should we NOT change?
4. MISSING FOR PRODUCTION: Only list things that MUST be fixed (not preferences)
5. CREATIVE ELEVATIONS: How would a master designer take this to the next level?
6. APPROPRIATE FOR TYPE: Does the layout/length fit this type of site?

Be generous with creative expression. Be specific with production issues.
Remember: unconventional ≠ broken."""


async def get_creative_director_feedback(
    candidate: Candidate,
    config: Config,
) -> CreativeDirectorFeedback | None:
    """Get qualitative creative director feedback instead of numeric score.

    This replaces the traditional judge scoring with rich qualitative feedback
    that guides refinement while preserving creative emergence.

    Args:
        candidate: Rendered candidate with screenshots
        config: Application configuration

    Returns:
        CreativeDirectorFeedback or None if feedback fails
    """
    # Accept both RENDERED and SCORED candidates (scoring happens before CD feedback)
    if candidate.status not in (CandidateStatus.RENDERED, CandidateStatus.SCORED):
        log_error(f"Candidate {candidate.id}: Cannot get feedback, not rendered (status: {candidate.status})")
        return None

    if not config.vision_judge.model:
        log_warning("No vision model configured for creative director feedback")
        return None

    provider = ProviderFactory.get(config.vision_judge.provider, config)

    # Load screenshots
    images = []
    viewport_labels = []

    for i, viewport in enumerate(["mobile", "tablet", "desktop"], 1):
        path = candidate.screenshot_paths.get(viewport)
        if not path:
            continue
        try:
            with open(path, "rb") as f:
                images.append(f.read())
                label = VIEWPORT_LABELS.get(viewport, viewport)
                viewport_labels.append(f"Image {i}: {label}")
        except Exception as e:
            log_error(f"Failed to load screenshot {path}: {e}")

    if not images:
        return None

    # Build prompt
    ui_spec = candidate.ui_spec
    user_prompt = CREATIVE_DIRECTOR_USER_PROMPT.format(
        page_type=ui_spec.page_type if ui_spec else "unknown",
        mood=ui_spec.brand.mood if ui_spec else "unknown",
        accent=ui_spec.brand.accent if ui_spec else "unknown",
        density=ui_spec.brand.density if ui_spec else "balanced",
        style_keywords=", ".join(ui_spec.brand.style_keywords) if ui_spec else "",
        viewport_labels="\n".join(viewport_labels),
    )

    messages = [
        Message(role="system", content=CREATIVE_DIRECTOR_SYSTEM_PROMPT),
        Message(role="user", content=user_prompt),
    ]

    try:
        response = await provider.complete_with_vision(
            messages=messages,
            model=config.vision_judge.model,
            images=images,
            max_tokens=config.vision_judge.max_tokens,
            temperature=config.vision_judge.temperature,
        )

        data = extract_json_strict(response.content)

        if isinstance(data, list):
            data = next((x for x in data if isinstance(x, dict)), None)
            if data is None:
                raise ValueError("Creative director returned list without object")

        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data).__name__}")

        def _as_str_list(value) -> list[str]:
            if value is None:
                return []
            if isinstance(value, list):
                return [str(v) for v in value if str(v).strip()]
            return [str(value)]

        feedback = CreativeDirectorFeedback(
            shippable=bool(data.get("shippable", False)),
            obviously_broken=bool(data.get("obviously_broken", False)),
            preserve=_as_str_list(data.get("preserve", [])),
            missing_for_production=_as_str_list(data.get("missing_for_production", [])),
            creative_elevations=_as_str_list(data.get("creative_elevations", [])),
            appropriate_for_type=bool(data.get("appropriate_for_type", True)),
            type_feedback=str(data.get("type_feedback", "")),
        )

        status = "SHIPPABLE" if feedback.shippable else "NEEDS WORK"
        if feedback.obviously_broken:
            status = "BROKEN"

        log_info(f"Candidate {candidate.id}: Creative Director says {status}")
        if feedback.preserve:
            log_info(f"  Preserve: {feedback.preserve[:2]}")
        if feedback.missing_for_production:
            log_info(f"  Missing: {feedback.missing_for_production[:2]}")

        return feedback

    except Exception as e:
        log_error(f"Candidate {candidate.id}: Creative director feedback failed - {e}")
        return None


async def assess_premium_candidates(
    candidates: list[Candidate],
    config: Config,
) -> list[Candidate]:
    """Optionally label candidates as premium/ship-ready using the configured vision model.

This does NOT discard candidates. It's used to:
- audit quality distribution when skip_judge=true
- decide whether to trigger an automatic polish pass (if enabled)

On judge/model failures, we default to premium_gate=None (or premium=false confidence=0)
to avoid accidental filtering/polishing.
"""
    if not getattr(config.pipeline, "premium_vision_gate_enabled", False):
        return candidates

    if not config.vision_judge.model:
        log_warning("premium_vision_gate_enabled=true but no vision_judge.model configured; skipping")
        return candidates

    to_check = [c for c in candidates if c.status == CandidateStatus.RENDERED]
    if not to_check:
        return candidates

    provider = ProviderFactory.get(config.vision_judge.provider, config)
    sem = asyncio.Semaphore(max(1, int(getattr(config.budget, "concurrency_gemini", 2) or 2)))

    async def _check_one(candidate: Candidate) -> None:
        desktop_path = candidate.screenshot_paths.get("desktop") if candidate.screenshot_paths else None
        if not desktop_path:
            return

        try:
            with open(desktop_path, "rb") as f:
                img_bytes = f.read()
        except Exception as e:
            log_warning(f"Candidate {candidate.id}: Failed to read screenshot for premium gate: {e}")
            return

        messages = [
            Message(role="system", content=PREMIUM_GATE_SYSTEM_PROMPT),
            Message(role="user", content=PREMIUM_GATE_USER_PROMPT),
        ]

        async with sem:
            try:
                resp = await provider.complete_with_vision(
                    messages=messages,
                    model=config.vision_judge.model,
                    images=[img_bytes],
                    # Keep small to reduce truncation risk; we only need a boolean + short lists.
                    max_tokens=min(350, int(config.vision_judge.max_tokens or 350)),
                    temperature=0.0,
                )
            except Exception as e:
                log_warning(f"Candidate {candidate.id}: Premium vision gate failed (skipping): {e}")
                return

        try:
            data = extract_json_strict(resp.content)
        except Exception as e:
            log_warning(f"Candidate {candidate.id}: Premium gate returned non-JSON (skipping): {e}")
            return

        if not isinstance(data, dict):
            return

        premium_raw = data.get("premium", False)
        premium = bool(premium_raw) if isinstance(premium_raw, (bool, int)) else str(premium_raw).lower() == "true"

        conf_raw = data.get("confidence", 0.0)
        try:
            confidence = float(conf_raw or 0.0)
        except Exception:
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        issues = data.get("issues", [])
        if not isinstance(issues, list):
            issues = [str(issues)]
        issues_str = [str(i) for i in issues if str(i).strip()][:6]

        fixes = data.get("fix_suggestions", [])
        if not isinstance(fixes, list):
            fixes = [str(fixes)]
        fixes_str = [str(i) for i in fixes if str(i).strip()][:6]

        candidate.premium_gate = PremiumGate(
            premium=premium,
            confidence=confidence,
            issues=issues_str,
            fix_suggestions=fixes_str,
        )

    log_info(f"Premium vision gate: labeling {len(to_check)} rendered candidates...")
    await asyncio.gather(*[_check_one(c) for c in to_check])
    return candidates


async def filter_broken_candidates(
    candidates: list[Candidate],
    config: Config,
) -> list[Candidate]:
    """Optionally discard visually broken renders using the configured vision model.

This is designed for "accept-all" pipelines:
- It does NOT score or pick winners.
- It only discards candidates that are clearly broken even though they rendered.

If the vision model fails, we default to NOT discarding (avoid false positives).

Args:
    candidates: Candidates (some may be rendered)
    config: Application configuration

Returns:
    Updated candidates list (broken renders marked DISCARDED)
"""
    if not getattr(config.pipeline, "broken_vision_gate_enabled", False):
        return candidates

    if not config.vision_judge.model:
        log_warning("broken_vision_gate_enabled=true but no vision_judge.model configured; skipping")
        return candidates

    to_check = [c for c in candidates if c.status == CandidateStatus.RENDERED]
    if not to_check:
        return candidates

    provider = ProviderFactory.get(config.vision_judge.provider, config)
    min_conf = float(getattr(config.pipeline, "broken_vision_gate_min_confidence", 0.85) or 0.85)

    async def _check_one(candidate: Candidate) -> None:
        desktop_path = candidate.screenshot_paths.get("desktop") if candidate.screenshot_paths else None
        if not desktop_path:
            return

        try:
            with open(desktop_path, "rb") as f:
                img_bytes = f.read()
        except Exception as e:
            log_warning(f"Candidate {candidate.id}: Failed to read screenshot for broken gate: {e}")
            return

        messages = [
            Message(role="system", content=BROKEN_GATE_SYSTEM_PROMPT),
            Message(role="user", content=BROKEN_GATE_USER_PROMPT.format(min_confidence=min_conf)),
        ]

        try:
            resp = await provider.complete_with_vision(
                messages=messages,
                model=config.vision_judge.model,
                images=[img_bytes],
                max_tokens=min(800, int(config.vision_judge.max_tokens or 800)),
                temperature=0.0,
            )
        except Exception as e:
            # Do not discard on judge failure
            log_warning(f"Candidate {candidate.id}: Broken vision gate failed (keeping): {e}")
            return

        try:
            data = extract_json_strict(resp.content)
        except Exception as e:
            log_warning(f"Candidate {candidate.id}: Broken gate returned non-JSON (keeping): {e}")
            return

        if not isinstance(data, dict):
            return

        broken_raw = data.get("broken", False)
        broken = bool(broken_raw) if isinstance(broken_raw, (bool, int)) else str(broken_raw).lower() == "true"

        conf_raw = data.get("confidence", 0.0)
        try:
            confidence = float(conf_raw or 0.0)
        except Exception:
            confidence = 0.0

        reasons = data.get("reasons", [])
        if not isinstance(reasons, list):
            reasons = [str(reasons)]
        reasons_str = [str(r) for r in reasons if str(r).strip()]

        if broken and confidence >= min_conf:
            candidate.status = CandidateStatus.DISCARDED
            candidate.error = (
                f"Vision broken gate: broken=true confidence={confidence:.2f} "
                + (f"reasons={reasons_str[:5]}" if reasons_str else "")
            )
            log_warning(f"Candidate {candidate.id}: Discarded as broken (confidence {confidence:.2f})")
        else:
            # Keep candidate; optionally store a marker in score_details for auditing
            if candidate.score_details is None:
                candidate.score_details = JudgeScore(
                    score=0.0,
                    passing=True,
                    issues=reasons_str[:5] if broken else [],
                    highlights=[],
                    fix_suggestions=[],
                )

    log_info(f"Broken vision gate: checking {len(to_check)} rendered candidates...")
    await asyncio.gather(*[_check_one(c) for c in to_check])
    return candidates


async def score_candidate(
    candidate: Candidate,
    config: Config,
) -> JudgeScore:
    """Score a candidate using vision model or heuristics.

    Args:
        candidate: Rendered candidate with screenshots
        config: Application configuration

    Returns:
        JudgeScore with score and feedback
    """
    if candidate.status != CandidateStatus.RENDERED:
        log_error(f"Candidate {candidate.id}: Cannot score, not rendered")
        return JudgeScore(
            score=0,
            passing=False,
            issues=["Candidate not rendered"],
        )

    # Check if vision model is configured
    if config.vision_judge.model:
        return await _score_with_vision(candidate, config)
    else:
        log_warning("No vision model configured, using heuristic scorer")
        return _score_with_heuristics(candidate, config)


async def _score_with_vision(candidate: Candidate, config: Config) -> JudgeScore:
    """Score using vision model.

    Args:
        candidate: Candidate to score
        config: Application configuration

    Returns:
        JudgeScore from vision model
    """
    provider = ProviderFactory.get(config.vision_judge.provider, config)

    # Load screenshots with viewport labels
    images = []
    viewport_labels = []

    # Process in consistent order: mobile, tablet, desktop
    for i, viewport in enumerate(["mobile", "tablet", "desktop"], 1):
        path = candidate.screenshot_paths.get(viewport)
        if not path:
            continue

        try:
            with open(path, "rb") as f:
                images.append(f.read())
                label = VIEWPORT_LABELS.get(viewport, viewport)
                viewport_labels.append(f"Image {i}: {label}")
        except Exception as e:
            log_error(f"Failed to load screenshot {path}: {e}")

    if not images:
        return JudgeScore(
            score=0,
            passing=False,
            issues=["No screenshots available"],
        )

    # Build prompt with viewport labels
    ui_spec = candidate.ui_spec
    user_prompt = JUDGE_USER_PROMPT.format(
        page_type=ui_spec.page_type if ui_spec else "unknown",
        mood=ui_spec.brand.mood if ui_spec else "unknown",
        accent=ui_spec.brand.accent if ui_spec else "unknown",
        density=ui_spec.brand.density if ui_spec else "balanced",
        style_keywords=", ".join(ui_spec.brand.style_keywords) if ui_spec else "",
        threshold=config.pipeline.vision_score_threshold,
        viewport_labels="\n".join(viewport_labels),
    )

    messages = [
        Message(role="system", content=JUDGE_SYSTEM_PROMPT),
        Message(role="user", content=user_prompt),
    ]

    def _as_str_list(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value if str(v).strip()]
        return [str(value)]

    try:
        response = await provider.complete_with_vision(
            messages=messages,
            model=config.vision_judge.model,
            images=images,
            max_tokens=config.vision_judge.max_tokens,
            temperature=config.vision_judge.temperature,
        )

        score_data = extract_json_strict(response.content)

        # Some models occasionally wrap the object in a JSON list.
        # Normalize to a dict payload.
        if isinstance(score_data, list):
            first_obj = next((x for x in score_data if isinstance(x, dict)), None)
            if first_obj is None:
                raise ValueError("Judge returned a JSON list without an object payload")
            score_data = first_obj

        if not isinstance(score_data, dict):
            raise ValueError(f"Expected JSON object, got {type(score_data).__name__}")

        # Normalize field names
        passing_raw = score_data.get("pass", score_data.get("passing", False))
        passing = bool(passing_raw) if isinstance(passing_raw, (bool, int)) else str(passing_raw).lower() == "true"

        score = JudgeScore(
            score=float(score_data.get("score", 0) or 0),
            passing=passing,
            issues=_as_str_list(score_data.get("issues", [])),
            highlights=_as_str_list(score_data.get("highlights", [])),
            fix_suggestions=_as_str_list(score_data.get("fix_suggestions", [])),
        )

        log_info(
            f"Candidate {candidate.id}: Vision score {score.score:.1f} "
            f"({'PASS' if score.passing else 'FAIL'})"
        )

        return score

    except Exception as e:
        log_error(f"Candidate {candidate.id}: Vision scoring failed - {e}")
        return JudgeScore(
            score=0,
            passing=False,
            issues=[f"Vision scoring error: {e}"],
        )


def _score_with_heuristics(candidate: Candidate, config: Config) -> JudgeScore:
    """Score using HTML/CSS heuristics (fallback).

    This is a basic fallback when no vision model is available.
    It checks code quality indicators rather than visual quality.

    Args:
        candidate: Candidate to score
        config: Application configuration

    Returns:
        Heuristic-based JudgeScore
    """
    issues = []
    highlights = []
    score = 5.0  # Start at middle

    # Get main page content
    main_file = None
    for f in candidate.files:
        if f.path == "app/page.tsx":
            main_file = f
            break

    if not main_file:
        return JudgeScore(
            score=0,
            passing=False,
            issues=["No app/page.tsx found"],
        )

    content = main_file.content

    # Check for TypeScript typing
    if ": React.FC" in content or "interface " in content or "type " in content:
        score += 0.5
        highlights.append("Good TypeScript usage")

    # Check for Tailwind classes
    tailwind_patterns = [
        r"className=[\"'][^\"']*flex",
        r"className=[\"'][^\"']*grid",
        r"className=[\"'][^\"']*gap-",
        r"className=[\"'][^\"']*p[xy]?-",
        r"className=[\"'][^\"']*m[xy]?-",
    ]
    tailwind_count = sum(1 for p in tailwind_patterns if re.search(p, content))
    if tailwind_count >= 3:
        score += 1.0
        highlights.append("Good Tailwind usage")
    elif tailwind_count == 0:
        score -= 1.0
        issues.append("Limited Tailwind usage detected")

    # Check for responsive classes
    if "sm:" in content or "md:" in content or "lg:" in content:
        score += 0.5
        highlights.append("Responsive design implemented")
    else:
        score -= 0.5
        issues.append("Missing responsive breakpoints")

    # Check for semantic HTML
    semantic_tags = ["<header", "<main", "<section", "<footer", "<nav", "<article"]
    semantic_count = sum(1 for tag in semantic_tags if tag in content)
    if semantic_count >= 3:
        score += 0.5
        highlights.append("Good semantic HTML")
    elif semantic_count == 0:
        issues.append("Missing semantic HTML elements")

    # Check for accessibility
    if "aria-" in content or "role=" in content:
        score += 0.5
        highlights.append("ARIA attributes present")
    if 'alt="' in content or "alt={" in content:
        score += 0.3
        highlights.append("Image alt attributes present")

    # Check for proper sections
    section_keywords = ["hero", "feature", "testimonial", "pricing", "faq", "cta"]
    section_count = sum(1 for kw in section_keywords if kw.lower() in content.lower())
    if section_count >= 4:
        score += 1.0
        highlights.append(f"Good section coverage ({section_count}/6)")
    elif section_count < 2:
        score -= 1.0
        issues.append("Missing expected page sections")

    # Check file size (too small = incomplete, too large = bloated)
    file_size = len(content)
    if file_size < 1000:
        score -= 1.5
        issues.append("Code seems incomplete (too short)")
    elif file_size > 15000:
        score -= 0.5
        issues.append("Code may be overly complex")
    elif 3000 < file_size < 10000:
        score += 0.5
        highlights.append("Appropriate code size")

    # Check for hardcoded lorem ipsum or placeholder
    if "lorem ipsum" in content.lower():
        score -= 0.5
        issues.append("Contains lorem ipsum placeholder text")

    # Check for dark mode implementation if specified
    if candidate.ui_spec and candidate.ui_spec.brand.mood == "dark":
        dark_indicators = ["bg-gray-900", "bg-slate-900", "bg-zinc-900", "bg-black", "dark:"]
        if any(ind in content for ind in dark_indicators):
            score += 0.5
            highlights.append("Dark theme implemented")
        else:
            score -= 0.5
            issues.append("Dark theme not properly implemented")

    # Clamp score
    score = max(0, min(10, score))

    # Determine pass/fail
    threshold = config.pipeline.vision_score_threshold
    passing = score >= threshold

    log_info(
        f"Candidate {candidate.id}: Heuristic score {score:.1f} "
        f"({'PASS' if passing else 'FAIL'})"
    )

    return JudgeScore(
        score=score,
        passing=passing,
        issues=issues,
        highlights=highlights,
        fix_suggestions=[
            f"Fix: {issue}" for issue in issues[:3]
        ],
    )


async def score_all_candidates(
    candidates: list[Candidate],
    config: Config,
) -> list[Candidate]:
    """Score all rendered candidates.

    Args:
        candidates: List of candidates
        config: Application configuration

    Returns:
        Candidates with scores
    """
    to_score = [c for c in candidates if c.status == CandidateStatus.RENDERED]

    if not to_score:
        log_info("No candidates to score")
        return candidates

    log_info(f"Scoring {len(to_score)} candidates...")

    for candidate in to_score:
        try:
            score = await score_candidate(candidate, config)
            candidate.score = score.score
            candidate.score_details = score
            candidate.status = CandidateStatus.SCORED
        except Exception as e:
            log_error(f"Candidate {candidate.id}: Scoring failed - {e}")
            # If vision scoring fails, fall back to a pass-through score so
            # build-passed candidates are not discarded.
            candidate.error = str(e)
            candidate.score = 0
            candidate.score_details = JudgeScore(
                score=0,
                passing=True,
                issues=[f"Vision scoring error: {e}"],
                highlights=[],
                fix_suggestions=[],
            )
            candidate.status = CandidateStatus.SCORED

    return candidates
