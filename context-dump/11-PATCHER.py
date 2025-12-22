"""Patcher module - fixes build errors using Devstral."""

from titan_factory.config import Config
from titan_factory.providers import Message, ProviderFactory
from titan_factory.schema import (
    Candidate,
    GeneratedFile,
    PatchOutput,
    TeacherModel,
    UISpec,
)
from titan_factory.utils import (
    extract_json_strict,
    format_build_error,
    log_error,
    log_info,
    truncate_text,
)

# === Patcher System Prompt ===
PATCHER_SYSTEM_PROMPT = """You are a code fixer for Next.js + TypeScript + Tailwind projects.

Given build error logs and the current code, fix the issues and output ONLY a JSON object.

CRITICAL RULES:
1. Output ONLY valid JSON - no markdown, no explanation
2. Fix the specific errors shown in the logs
3. Keep the visual design intact - only fix build/type errors
4. Do NOT change the styling or layout unless it causes errors

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

Prefer Option A (full replacement) for clarity. Use patches only for small changes.

Start with { and end with }."""


PATCHER_USER_PROMPT_TEMPLATE = """Fix these build errors:

{build_logs}

Current code:
{current_files}

UI Specification (for context):
{ui_spec_summary}

{judge_issues}

Output JSON with either "files" (full replacement) or "patches" (unified diff)."""


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
        response = await provider.complete(
            messages=messages,
            model=config.patcher.model,
            max_tokens=config.patcher.max_tokens,
            temperature=config.patcher.temperature,
        )

        # Parse response
        patch_data = extract_json_strict(response.content)

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

    except Exception as e:
        log_error(f"Candidate {candidate.id}: Patching failed - {e}")
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
    for f in candidate.files:
        if f.path == path:
            # Parse diff (simplified - just look for +/- lines)
            lines = f.content.split("\n")
            new_lines = []

            # This is a simplified patch application
            # In production, use a proper diff library
            for line in diff.split("\n"):
                if line.startswith("@@"):
                    continue
                elif line.startswith("-") and not line.startswith("---"):
                    # Remove line (skip it)
                    continue
                elif line.startswith("+") and not line.startswith("+++"):
                    # Add line
                    new_lines.append(line[1:])
                elif line.startswith(" "):
                    # Context line
                    new_lines.append(line[1:])

            if new_lines:
                f.content = "\n".join(new_lines)
            return

    # File not found, add it
    candidate.files.append(GeneratedFile(path=path, content=""))
    log_error(f"Patch target file not found: {path}")
