"""Judge module - scores candidates using vision models or heuristics."""

import re
from pathlib import Path

from titan_factory.config import Config
from titan_factory.providers import Message, ProviderFactory
from titan_factory.schema import Candidate, CandidateStatus, JudgeScore
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
1. Output ONLY valid JSON - no markdown, no explanation
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

    try:
        response = await provider.complete_with_vision(
            messages=messages,
            model=config.vision_judge.model,
            images=images,
            max_tokens=config.vision_judge.max_tokens,
            temperature=config.vision_judge.temperature,
        )

        score_data = extract_json_strict(response.content)

        # Normalize field names
        passing = score_data.get("pass", score_data.get("passing", False))

        score = JudgeScore(
            score=float(score_data.get("score", 0)),
            passing=passing,
            issues=score_data.get("issues", []),
            highlights=score_data.get("highlights", []),
            fix_suggestions=score_data.get("fix_suggestions", []),
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
            candidate.error = str(e)

    return candidates
