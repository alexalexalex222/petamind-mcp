"""Patcher module - fixes build errors and optionally polishes UI quality."""

import asyncio
import re

from titan_factory.config import Config
from titan_factory.providers import Message, ProviderFactory
from titan_factory.schema import (
    Candidate,
    GeneratedFile,
    TeacherModel,
)
from titan_factory.utils import (
    extract_json_strict,
    format_build_error,
    log_error,
    log_info,
    log_warning,
    truncate_text,
)

# === Patcher System Prompt ===
PATCHER_SYSTEM_PROMPT = """You are a code fixer for Next.js + TypeScript + Tailwind projects.

Given build error logs and the current code, fix the issues and output ONLY a JSON object.

CRITICAL RULES:
1. Output ONLY valid JSON - no markdown, no explanation, no <think>
2. Fix the specific errors shown in the logs
3. Keep the visual design intact - only fix build/type errors
4. Do NOT change the styling or layout unless it causes errors
5. Keep the output SMALL to avoid truncation: prefer minimal diffs over full rewrites

OUTPUT FORMAT (choose one):

Option A - Full file replacement:
{
  "files": [
    {"path": "app/page.tsx", "content": "// complete fixed code"}
  ]
}

Option B - Unified diff patches:
{
  "patches": [
    {"path": "app/page.tsx", "patch": "--- a/app/page.tsx\\n+++ b/app/page.tsx\\n@@ -10,3 +10,3 @@\\n-old line\\n+new line"}
  ]
}

Prefer Option B (patches) whenever possible to keep responses compact.
Only use Option A when you truly need to rewrite most of the file or the diff would be too large/confusing.
If the file is large, you MUST use patches to avoid token limits.

Start with { and end with }."""


PATCHER_USER_PROMPT_TEMPLATE = """Fix these build errors:

{build_logs}

Current code:
{current_files}

UI Specification (for context):
{ui_spec_summary}

{judge_issues}

Output JSON with either "files" (full replacement) or "patches" (unified diff)."""

POLISHER_SYSTEM_PROMPT = """You are a UI POLISHER for Next.js App Router + TypeScript + Tailwind projects.

Goal: upgrade the UI to feel premium/ship-ready WITHOUT breaking the build.

Rules:
1) Output ONLY valid JSON - no markdown, no explanation, no <think>
2) Keep the overall information architecture and intent intact.
   - You MAY improve spacing, typography, hierarchy, section styling, and microcopy.
   - You MAY adjust layout details (grids, card layouts, spacing) to increase polish.
   - You MUST NOT introduce new dependencies or UI libraries.
3) Do not add external assets, remote images, or font links. No emojis.
4) Be conservative about file count: prefer updating app/page.tsx only (max 3 files total).
5) Keep code compact; avoid huge dummy arrays or excessively long copy.

OUTPUT FORMAT (choose one):

Option A - Full file replacement:
{
  "files": [
    {"path": "app/page.tsx", "content": "// complete polished code"}
  ]
}

Option B - Unified diff patches:
{
  "patches": [
    {"path": "app/page.tsx", "patch": "--- a/app/page.tsx\\n+++ b/app/page.tsx\\n@@ -10,3 +10,3 @@\\n-old line\\n+new line"}
  ]
}

Prefer Option A (full replacement) for clarity.
Start with { and end with }."""


POLISHER_USER_PROMPT_TEMPLATE = """Polish this UI to be premium/ship-ready.

UI_SPEC (context):
{ui_spec_summary}

Current code:
{current_files}

Quality issues to address (from vision gate, if any):
{quality_issues}

Suggested fixes (if any):
{quality_fixes}

Output JSON with either "files" (full replacement) or "patches" (unified diff).
"""


CREATIVITY_REFINER_SYSTEM_PROMPT = """You are a TARGETED UI SECTION REFINER for Next.js App Router + TypeScript + Tailwind projects.

Goal: Improve ONLY the specified weak section(s) so they match the creativity level of the strong section(s),
WITHOUT rewriting the whole page and WITHOUT changing the strong sections.

This is not a "polish" pass. It must create a VISIBLE creativity lift in the weak sections.
If your changes are only spacing/typography tweaks, you failed the task.

Rules:
1) Output ONLY valid JSON - no markdown, no explanation, no <think>
2) Modify ONLY the weak sections listed (do not touch strong sections)
3) Preserve the brand mood/accent and overall page structure
4) Do NOT add dependencies or UI libraries; Tailwind only
5) No emojis. If you need icons, use minimal inline SVG (<= 6 total)
6) Keep output SMALL: prefer unified diff patches; avoid full rewrites
7) Maintain accessibility: labels for inputs/selects, aria-label for icon buttons, focus rings, heading hierarchy

CREATIVITY REQUIREMENTS (MANDATORY)
- For EACH weak section, introduce at least ONE signature moment appropriate for its content:
  - bento grid / asymmetric layout
  - comparison strip
  - proof wall (dense micro-proofs, stats, logos, quotes)
  - timeline / stepper with visual rhythm
  - interactive filter chips + preview cards (for search/filter sections)
  - pricing tier "decision helper" (for pricing-related weak sections)
- Add micro-interactions ONLY where they reinforce clarity (hover/focus states, subtle transitions).
- Keep it build-safe and responsive.

ANTI-PATTERNS (AVOID)
- "Three identical cards with an icon" as the only idea.
- Flat generic sections that could belong to any site.
- Turning the section into a dense dashboard.
- Style drift (no cyber/terminal vibes unless the task is explicitly cyber_tech).

OUTPUT FORMAT (prefer patches):

Option A - Full file replacement (only if unavoidable):
{
  "files": [
    {"path": "app/page.tsx", "content": "// complete updated code"}
  ]
}

Option B - Unified diff patches (preferred):
{
  "patches": [
    {"path": "app/page.tsx", "patch": "--- a/app/page.tsx\\n+++ b/app/page.tsx\\n@@ -10,3 +10,3 @@\\n-old line\\n+new line"}
  ]
}

Prefer Option B (patches). Start with { and end with }."""


CREATIVITY_REFINER_USER_PROMPT_TEMPLATE = """Section-level creativity refinement request.

Strong sections (PRESERVE; do NOT change):
{strong_sections}

Weak sections (IMPROVE ONLY these):
{weak_sections}

Section feedback (scores/notes):
{section_feedback}

UI Specification (context):
{ui_spec_summary}

Current code:
{current_files}

Return JSON with either \"patches\" (preferred) or \"files\"."""


_FENCED_CODE_RE = re.compile(
    r"```(?P<lang>[a-zA-Z0-9_-]+)?\s*(?P<body>[\s\S]*?)```",
    re.IGNORECASE,
)
_SEPARATOR_PAGE_RE = re.compile(
    r"===\s*app/page\.tsx\s*===\s*(?P<body>[\s\S]*?)(?:\n===|\Z)",
    re.IGNORECASE,
)


def _salvage_patch_output(text: str) -> dict | None:
    """Salvage patch output when strict JSON parsing fails.

    Some models produce near-miss JSON (invalid string escaping) or skip the JSON
    wrapper entirely and emit raw TSX in code fences. For build-fix patching we
    can accept best-effort extraction of a full `app/page.tsx` replacement.
    """
    if not text or not text.strip():
        return None

    # Guardrail: do NOT treat unified-diff/patch JSON as TSX.
    # A common failure mode is a model returning a JSON "patches" object with raw
    # newlines in strings (invalid JSON). That output contains `export default` and
    # JSX in the diff, which can trick naive TSX salvage and overwrite app/page.tsx
    # with the patch payload itself.
    lowered = text.lower()
    if '"patches"' in lowered or '"files"' in lowered or "--- a/" in lowered or "+++ b/" in lowered:
        return None

    # Strategy 1: "=== app/page.tsx ===" blocks
    m = _SEPARATOR_PAGE_RE.search(text)
    if m:
        body = (m.group("body") or "").strip()
        if "export default" in body:
            return {"files": [{"path": "app/page.tsx", "content": body}]}

    # Strategy 2: fenced code blocks (tsx/ts/jsx/js/plain)
    for match in _FENCED_CODE_RE.finditer(text):
        lang = (match.group("lang") or "").strip().lower()
        body = (match.group("body") or "").strip()

        # Skip likely JSON blocks; extract_json_strict already tried those.
        if lang in ("json", "jsonc"):
            continue

        if "export default" in body and "<" in body:
            return {"files": [{"path": "app/page.tsx", "content": body}]}

    # Strategy 3: raw TSX without fences (last resort)
    stripped = text.strip()
    # Only accept when the output actually *starts* like a TSX module.
    tsx_starts = (
        "'use client'",
        '"use client"',
        "import ",
        "export default",
        "export default function",
        "export default async function",
    )
    if stripped.startswith("```"):
        return None
    if not stripped.startswith(tsx_starts):
        return None
    if "export default" in stripped and "<" in stripped:
        tsx_signals = ("className=", "return (", "return(", "export default function")
        if any(s in stripped for s in tsx_signals):
            return {"files": [{"path": "app/page.tsx", "content": stripped}]}

    return None


async def patch_candidate(
    candidate: Candidate,
    build_logs: str,
    config: Config,
    judge_issues: list[str] | None = None,
) -> Candidate:
    """Attempt to fix a candidate's build errors.

    Args:
        candidate: Candidate with build errors
        build_logs: Build error output
        config: Application configuration
        judge_issues: Optional issues from vision judge

    Returns:
        Updated candidate with fixed code
    """
    if not config.patcher.model:
        log_error("Patcher model not configured")
        return candidate

    # Deterministic fast-fix: Next.js App Router client-component directive.
    # Common failure: generator uses React hooks in `app/page.tsx` without `'use client'`,
    # causing a build error. Fixing this is safe and faster than an LLM call.
    lowered_logs = (build_logs or "").lower()
    if (
        "it only works in a client component" in lowered_logs
        and "marked with \"use client\"" in lowered_logs
        and "app/page.tsx" in lowered_logs
    ):
        for f in candidate.files:
            if f.path != "app/page.tsx":
                continue
            content = f.content or ""
            head = "\n".join(content.splitlines()[:5]).lower()
            if "use client" not in head:
                f.content = "'use client';\n\n" + content.lstrip("\n")
                candidate.fix_rounds += 1
                candidate.error = None
                log_info(f"Candidate {candidate.id}: Added 'use client' directive to app/page.tsx")
        return candidate

    provider = ProviderFactory.get(config.patcher.provider, config)

    # Format current files
    current_files = ""
    for f in candidate.files:
        current_files += f"\n=== {f.path} ===\n{f.content}\n"

    # Format UI spec summary
    ui_spec_summary = ""
    if candidate.ui_spec:
        ui_spec_summary = f"""
Brand: {candidate.ui_spec.brand.name} ({candidate.ui_spec.brand.mood}, {candidate.ui_spec.brand.accent})
Page type: {candidate.ui_spec.page_type}
Style: {', '.join(candidate.ui_spec.brand.style_keywords)}
"""

    # Format judge issues if present
    issues_text = ""
    if judge_issues:
        issues_text = "\nVision judge issues to address:\n" + "\n".join(
            f"- {issue}" for issue in judge_issues
        )

    user_prompt = PATCHER_USER_PROMPT_TEMPLATE.format(
        build_logs=format_build_error(build_logs, ""),
        current_files=truncate_text(current_files, 10000),
        ui_spec_summary=ui_spec_summary,
        judge_issues=issues_text,
    )

    messages = [
        Message(role="system", content=PATCHER_SYSTEM_PROMPT),
        Message(role="user", content=user_prompt),
    ]

    log_info(f"Patching candidate {candidate.id} (round {candidate.fix_rounds + 1})")

    try:
        max_retries = 2
        max_tokens = config.patcher.max_tokens
        patch_data: dict | None = None

        for attempt in range(max_retries + 1):
            response = await provider.complete(
                messages=messages,
                model=config.patcher.model,
                max_tokens=max_tokens,
                temperature=config.patcher.temperature,
            )

            content = response.content or ""
            if not content.strip():
                if attempt < max_retries:
                    log_warning(
                        f"Candidate {candidate.id}: Patcher returned empty output, retrying "
                        f"(attempt {attempt + 1}/{max_retries + 1})"
                    )
                    max_tokens = int(max_tokens * 1.25)
                    continue
                raise RuntimeError("Patcher returned empty response content")

            finish_reason = getattr(response, "finish_reason", None)
            if finish_reason == "length":
                if attempt < max_retries:
                    log_warning(
                        f"Candidate {candidate.id}: Patcher response truncated at {max_tokens} tokens, "
                        f"retrying with {int(max_tokens * 1.25)} (attempt {attempt + 1}/{max_retries + 1})"
                    )
                    max_tokens = int(max_tokens * 1.25)
                    messages = messages + [
                        Message(
                            role="user",
                            content=(
                                "Your last output was truncated. Re-output ONLY the FULL JSON object.\n"
                                "No markdown fences. No <think>. Start with { and end with }.\n"
                                "IMPORTANT: If embedding code in JSON strings, escape newlines as \\n "
                                "and quotes as \\\"."
                            ),
                        )
                    ]
                    continue
                raise RuntimeError(
                    f"Patcher response truncated (finish_reason=length) at max_tokens={max_tokens}"
                )

            try:
                patch_data_obj = extract_json_strict(content)
            except Exception as parse_error:
                salvaged = _salvage_patch_output(content)
                if salvaged is not None:
                    patch_data_obj = salvaged
                    log_warning(
                        f"Candidate {candidate.id}: Salvaged non-JSON patcher output into PatchOutput"
                    )
                else:
                    if attempt < max_retries:
                        log_warning(
                            f"Candidate {candidate.id}: Patcher returned invalid JSON ({parse_error}), retrying "
                            f"(attempt {attempt + 1}/{max_retries + 1})"
                        )
                        max_tokens = int(max_tokens * 1.25)
                        messages = messages + [
                            Message(
                                role="user",
                                content=(
                                    "Your last output did not match the required format. "
                                    "Re-output ONLY a single JSON object with either:\n"
                                    '{"files":[{"path":"app/page.tsx","content":"..."}]}\n'
                                    'or {"patches":[{"path":"app/page.tsx","patch":"..."}]}\n\n'
                                    "No markdown fences. No extra text. Final character must be }.\n"
                                    "If embedding code in JSON strings, escape newlines as \\n and quotes as \\\"."
                                ),
                            )
                        ]
                        continue
                    raise

            if not isinstance(patch_data_obj, dict):
                raise ValueError(
                    f"Expected patcher JSON object, got {type(patch_data_obj).__name__}"
                )

            patch_data = patch_data_obj
            break

        if patch_data is None:
            raise RuntimeError("Patcher did not return usable output after retries")

        # Record the patcher model in the teacher chain
        patcher_model = TeacherModel(
            provider=config.patcher.provider,
            model=config.patcher.model,
            publishable=config.patcher.publishable,
        )

        # Handle full file replacement
        if "files" in patch_data and patch_data["files"]:
            candidate.files = [
                GeneratedFile(path=f["path"], content=f["content"])
                for f in patch_data["files"]
            ]
            candidate.fix_rounds += 1
            candidate.patcher_models.append(patcher_model)
            log_info(f"Candidate {candidate.id}: Applied {len(candidate.files)} file fixes")

        # Handle unified diff patches
        elif "patches" in patch_data and patch_data["patches"]:
            for patch in patch_data["patches"]:
                path = patch["path"]
                diff = patch["patch"]
                _apply_patch(candidate, path, diff)
            candidate.fix_rounds += 1
            candidate.patcher_models.append(patcher_model)
            log_info(f"Candidate {candidate.id}: Applied {len(patch_data['patches'])} patches")

        else:
            log_error(f"Candidate {candidate.id}: Patcher returned empty response")

    except asyncio.CancelledError as e:
        log_error(f"Candidate {candidate.id}: Patching cancelled - {e}")
        candidate.error = f"CancelledError: {e}"
    except Exception as e:
        log_error(f"Candidate {candidate.id}: Patching failed - {e}")
        candidate.error = str(e)

    return candidate


async def polish_candidate(
    candidate: Candidate,
    config: Config,
    *,
    quality_issues: list[str] | None = None,
    quality_fixes: list[str] | None = None,
) -> Candidate:
    """Polish a candidate's UI quality (not just build errors).

This is intended to be used in skip_judge mode to reduce "basic/demo" outputs
while still keeping a "no losers except broken" policy.
"""
    if not getattr(config, "polisher", None) or not config.polisher.model:
        log_warning("Polisher model not configured; skipping polish")
        return candidate

    provider = ProviderFactory.get(config.polisher.provider, config)

    # Format current files
    current_files = ""
    for f in candidate.files:
        current_files += f"\n=== {f.path} ===\n{f.content}\n"

    # UI spec summary
    ui_spec_summary = ""
    if candidate.ui_spec:
        ui_spec_summary = f"""
Brand: {candidate.ui_spec.brand.name} ({candidate.ui_spec.brand.mood}, {candidate.ui_spec.brand.accent})
Page type: {candidate.ui_spec.page_type}
Style: {', '.join(candidate.ui_spec.brand.style_keywords)}
""".strip()

    issues_text = "\n".join(f"- {i}" for i in (quality_issues or [])[:8]).strip() or "(none)"
    fixes_text = "\n".join(f"- {i}" for i in (quality_fixes or [])[:8]).strip() or "(none)"

    user_prompt = POLISHER_USER_PROMPT_TEMPLATE.format(
        ui_spec_summary=ui_spec_summary or "(unknown)",
        current_files=truncate_text(current_files, 12000),
        quality_issues=issues_text,
        quality_fixes=fixes_text,
    )

    messages = [
        Message(role="system", content=POLISHER_SYSTEM_PROMPT),
        Message(role="user", content=user_prompt),
    ]

    log_info(f"Polishing candidate {candidate.id} (round {candidate.polish_rounds + 1})")

    try:
        max_retries = 2
        max_tokens = config.polisher.max_tokens
        patch_data: dict | None = None

        for attempt in range(max_retries + 1):
            response = await provider.complete(
                messages=messages,
                model=config.polisher.model,
                max_tokens=max_tokens,
                temperature=config.polisher.temperature,
            )

            content = response.content or ""
            if not content.strip():
                if attempt < max_retries:
                    log_warning(
                        f"Candidate {candidate.id}: Polisher returned empty output, retrying "
                        f"(attempt {attempt + 1}/{max_retries + 1})"
                    )
                    max_tokens = int(max_tokens * 1.25)
                    continue
                raise RuntimeError("Polisher returned empty response content")

            finish_reason = getattr(response, "finish_reason", None)
            if finish_reason == "length":
                if attempt < max_retries:
                    log_warning(
                        f"Candidate {candidate.id}: Polisher response truncated at {max_tokens} tokens, "
                        f"retrying with {int(max_tokens * 1.25)} (attempt {attempt + 1}/{max_retries + 1})"
                    )
                    max_tokens = int(max_tokens * 1.25)
                    messages = messages + [
                        Message(
                            role="user",
                            content=(
                                "Your last output was truncated. Re-output ONLY the FULL JSON object.\n"
                                "No markdown fences. No <think>. Start with { and end with }.\n"
                                "IMPORTANT: If embedding code in JSON strings, escape newlines as \\n "
                                "and quotes as \\\"."
                            ),
                        )
                    ]
                    continue
                raise RuntimeError(
                    f"Polisher response truncated (finish_reason=length) at max_tokens={max_tokens}"
                )

            try:
                patch_data_obj = extract_json_strict(content)
            except Exception as parse_error:
                salvaged = _salvage_patch_output(content)
                if salvaged is not None:
                    patch_data_obj = salvaged
                    log_warning(
                        f"Candidate {candidate.id}: Salvaged non-JSON polisher output into PatchOutput"
                    )
                else:
                    if attempt < max_retries:
                        log_warning(
                            f"Candidate {candidate.id}: Polisher returned invalid JSON ({parse_error}), retrying "
                            f"(attempt {attempt + 1}/{max_retries + 1})"
                        )
                        max_tokens = int(max_tokens * 1.25)
                        messages = messages + [
                            Message(
                                role="user",
                                content=(
                                    "Your last output did not match the required format. "
                                    "Re-output ONLY a single JSON object with either:\n"
                                    '{"files":[{"path":"app/page.tsx","content":"..."}]}\n'
                                    'or {"patches":[{"path":"app/page.tsx","patch":"..."}]}\n\n'
                                    "No markdown fences. No extra text. Final character must be }.\n"
                                    "If embedding code in JSON strings, escape newlines as \\n and quotes as \\\"."
                                ),
                            )
                        ]
                        continue
                    raise

            if not isinstance(patch_data_obj, dict):
                raise ValueError(
                    f"Expected polisher JSON object, got {type(patch_data_obj).__name__}"
                )

            patch_data = patch_data_obj
            break

        if patch_data is None:
            raise RuntimeError("Polisher did not return usable output after retries")

        polisher_model = TeacherModel(
            provider=config.polisher.provider,
            model=config.polisher.model,
            publishable=config.polisher.publishable,
        )

        if "files" in patch_data and patch_data["files"]:
            candidate.files = [
                GeneratedFile(path=f["path"], content=f["content"])
                for f in patch_data["files"]
            ]
            candidate.polish_rounds += 1
            candidate.patcher_models.append(polisher_model)
            log_info(f"Candidate {candidate.id}: Applied {len(candidate.files)} polished file(s)")

        elif "patches" in patch_data and patch_data["patches"]:
            for patch in patch_data["patches"]:
                path = patch["path"]
                diff = patch["patch"]
                _apply_patch(candidate, path, diff)
            candidate.polish_rounds += 1
            candidate.patcher_models.append(polisher_model)
            log_info(f"Candidate {candidate.id}: Applied {len(patch_data['patches'])} polish patch(es)")

        else:
            log_warning(f"Candidate {candidate.id}: Polisher returned empty JSON payload")

    except asyncio.CancelledError as e:
        log_warning(f"Candidate {candidate.id}: Polishing cancelled (keeping original): {e}")
    except Exception as e:
        log_warning(f"Candidate {candidate.id}: Polishing failed (keeping original): {e}")

    return candidate


async def refine_candidate_section_creativity(
    candidate: Candidate,
    config: Config,
    *,
    strong_section_ids: list[str],
    weak_section_ids: list[str],
    section_feedback: list[dict] | None = None,
) -> Candidate:
    """Surgically refine weak sections to match strong-section creativity.

This is used in skip_judge mode as a targeted quality improvement step.
It is NOT meant to rewrite the entire page.
"""
    if not getattr(config, "polisher", None) or not config.polisher.model:
        log_warning("Creativity refiner: polisher model not configured; skipping")
        return candidate

    strong_section_ids = [str(s).strip() for s in (strong_section_ids or []) if str(s).strip()]
    weak_section_ids = [str(s).strip() for s in (weak_section_ids or []) if str(s).strip()]

    if not weak_section_ids or not strong_section_ids:
        return candidate

    provider = ProviderFactory.get(config.polisher.provider, config)

    current_files = ""
    for f in candidate.files:
        current_files += f"\n=== {f.path} ===\n{f.content}\n"

    ui_spec_summary = ""
    if candidate.ui_spec:
        ui_spec_summary = f"""
Brand: {candidate.ui_spec.brand.name} ({candidate.ui_spec.brand.mood}, {candidate.ui_spec.brand.accent})
Page type: {candidate.ui_spec.page_type}
Style: {', '.join(candidate.ui_spec.brand.style_keywords)}
Sections: {', '.join([s.id for s in (candidate.ui_spec.layout.sections or [])][:12])}
""".strip()

    def _bullets(items: list[str]) -> str:
        return "\n".join(f"- {x}" for x in items[:12]) or "(none)"

    fb_lines: list[str] = []
    for s in (section_feedback or [])[:20]:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip()
        notes = str(s.get("notes") or "").strip()
        try:
            score_f = float(s.get("score") or 0.0)
        except Exception:
            score_f = 0.0
        if sid:
            fb_lines.append(f"- {sid}: {score_f:.2f} ({notes})".strip())
    section_feedback_text = "\n".join(fb_lines) if fb_lines else "(none)"

    user_prompt = CREATIVITY_REFINER_USER_PROMPT_TEMPLATE.format(
        strong_sections=_bullets(strong_section_ids),
        weak_sections=_bullets(weak_section_ids),
        section_feedback=section_feedback_text,
        ui_spec_summary=ui_spec_summary or "(unknown)",
        current_files=truncate_text(current_files, 14000),
    )

    messages = [
        Message(role="system", content=CREATIVITY_REFINER_SYSTEM_PROMPT),
        Message(role="user", content=user_prompt),
    ]

    log_info(
        f"Creativity refiner: Candidate {candidate.id} "
        f"(weak={weak_section_ids[:4]} strong={strong_section_ids[:4]})"
    )

    try:
        max_retries = 2
        max_tokens = int(config.polisher.max_tokens or 2000)
        patch_data: dict | None = None

        for attempt in range(max_retries + 1):
            response = await provider.complete(
                messages=messages,
                model=config.polisher.model,
                max_tokens=max_tokens,
                temperature=config.polisher.temperature,
            )

            content = response.content or ""
            if not content.strip():
                if attempt < max_retries:
                    log_warning(
                        f"Candidate {candidate.id}: Creativity refiner returned empty output, retrying "
                        f"(attempt {attempt + 1}/{max_retries + 1})"
                    )
                    max_tokens = int(max_tokens * 1.25)
                    continue
                raise RuntimeError("Creativity refiner returned empty response content")

            finish_reason = getattr(response, "finish_reason", None)
            if finish_reason == "length":
                if attempt < max_retries:
                    log_warning(
                        f"Candidate {candidate.id}: Creativity refiner output truncated at {max_tokens} tokens, "
                        f"retrying with {int(max_tokens * 1.25)} (attempt {attempt + 1}/{max_retries + 1})"
                    )
                    max_tokens = int(max_tokens * 1.25)
                    messages = messages + [
                        Message(
                            role="user",
                            content=(
                                "Your last output was truncated. Re-output ONLY the FULL JSON object.\n"
                                "Prefer unified diff patches to keep output short.\n"
                                "No markdown. Start with { and end with }.\n"
                                "Escape newlines as \\n and quotes as \\\" in JSON strings."
                            ),
                        )
                    ]
                    continue
                raise RuntimeError(
                    f"Creativity refiner response truncated (finish_reason=length) at max_tokens={max_tokens}"
                )

            try:
                patch_data_obj = extract_json_strict(content)
            except Exception as parse_error:
                salvaged = _salvage_patch_output(content)
                if salvaged is not None:
                    patch_data_obj = salvaged
                    log_warning(
                        f"Candidate {candidate.id}: Salvaged non-JSON creativity output into PatchOutput"
                    )
                else:
                    if attempt < max_retries:
                        log_warning(
                            f"Candidate {candidate.id}: Creativity refiner returned invalid JSON ({parse_error}), retrying "
                            f"(attempt {attempt + 1}/{max_retries + 1})"
                        )
                        max_tokens = int(max_tokens * 1.25)
                        messages = messages + [
                            Message(
                                role="user",
                                content=(
                                    "Re-output ONLY a single JSON object with either:\n"
                                    '{"patches":[{"path":"app/page.tsx","patch":"..."}]}\n'
                                    'or {"files":[{"path":"app/page.tsx","content":"..."}]}\n\n'
                                    "No markdown. Final character must be }.\n"
                                    "Escape newlines as \\n and quotes as \\\"."
                                ),
                            )
                        ]
                        continue
                    raise

            if not isinstance(patch_data_obj, dict):
                raise ValueError(
                    f"Expected creativity refiner JSON object, got {type(patch_data_obj).__name__}"
                )

            patch_data = patch_data_obj
            break

        if patch_data is None:
            raise RuntimeError("Creativity refiner did not return usable output after retries")

        # Track the refiner model in the teacher chain.
        refiner_model = TeacherModel(
            provider=config.polisher.provider,
            model=config.polisher.model,
            publishable=config.polisher.publishable,
        )

        if "files" in patch_data and patch_data["files"]:
            candidate.files = [
                GeneratedFile(path=f["path"], content=f["content"])
                for f in patch_data["files"]
            ]
            candidate.patcher_models.append(refiner_model)
            candidate.polish_rounds = int(getattr(candidate, "polish_rounds", 0) or 0) + 1
            log_info(
                f"Candidate {candidate.id}: Creativity refiner applied {len(candidate.files)} file(s)"
            )

        elif "patches" in patch_data and patch_data["patches"]:
            for patch in patch_data["patches"]:
                path = patch["path"]
                diff = patch["patch"]
                _apply_patch(candidate, path, diff)
            candidate.patcher_models.append(refiner_model)
            candidate.polish_rounds = int(getattr(candidate, "polish_rounds", 0) or 0) + 1
            log_info(
                f"Candidate {candidate.id}: Creativity refiner applied {len(patch_data['patches'])} patch(es)"
            )
        else:
            log_warning(
                f"Candidate {candidate.id}: Creativity refiner returned empty patches/files; skipping"
            )

    except asyncio.CancelledError as e:
        log_warning(f"Candidate {candidate.id}: Creativity refinement cancelled (skipping): {e}")
    except Exception as e:
        log_warning(f"Candidate {candidate.id}: Creativity refinement failed: {e}")
        candidate.error = str(e)

    return candidate


def _apply_patch(candidate: Candidate, path: str, diff: str) -> None:
    """Apply a unified diff patch to a file.

    Simple implementation that handles basic patches.

    Args:
        candidate: Candidate to patch
        path: File path
        diff: Unified diff string
    """
    # Find the file
    target = None
    for f in candidate.files:
        if f.path == path:
            target = f
            break

    if target is None:
        candidate.files.append(GeneratedFile(path=path, content=""))
        log_error(f"Patch target file not found: {path}")
        return

    original_text = target.content or ""
    original_lines = original_text.splitlines()
    had_trailing_nl = original_text.endswith("\n")

    diff_lines = (diff or "").splitlines()
    if not diff_lines:
        return

    out: list[str] = []
    src_i = 0  # 0-based index into original_lines

    hunk_re = re.compile(r"^@@\s+-(?P<old_start>\\d+)(?:,(?P<old_len>\\d+))?\\s+\\+(?P<new_start>\\d+)(?:,(?P<new_len>\\d+))?\\s+@@")

    i = 0
    while i < len(diff_lines):
        line = diff_lines[i]

        # Skip diff headers
        if line.startswith(("diff ", "index ", "--- ", "+++ ")):
            i += 1
            continue

        if not line.startswith("@@"):
            i += 1
            continue

        m = hunk_re.match(line)
        if not m:
            i += 1
            continue

        old_start = int(m.group("old_start")) - 1  # convert to 0-based

        # Copy unchanged lines before hunk
        while src_i < old_start and src_i < len(original_lines):
            out.append(original_lines[src_i])
            src_i += 1

        i += 1  # move past @@ header

        # Apply hunk lines until next hunk/header
        while i < len(diff_lines):
            h = diff_lines[i]
            if h.startswith("@@") or h.startswith(("diff ", "index ", "--- ", "+++ ")):
                break

            if h.startswith(" "):
                out.append(h[1:])
                src_i += 1
            elif h.startswith("-"):
                src_i += 1
            elif h.startswith("+"):
                out.append(h[1:])
            elif h.startswith("\\"):
                # "\ No newline at end of file" - ignore
                pass
            else:
                # Non-standard line (model error). Treat as context to avoid crashes.
                out.append(h)
                src_i += 1
            i += 1

        continue

    # Copy any remaining original lines after last hunk
    while src_i < len(original_lines):
        out.append(original_lines[src_i])
        src_i += 1

    new_text = "\n".join(out)
    if had_trailing_nl:
        new_text += "\n"
    target.content = new_text
