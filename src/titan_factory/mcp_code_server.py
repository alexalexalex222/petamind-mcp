"""Claude Code MCP server for a Titan-style agentic coding loop.

Goal
----
Expose a single high-level tool (`titan_code_solve`) that:
- Plans with a reasoner (single-pass or Megamind-style ensemble)
- Generates multiple patch candidates (temp diversity)
- Runs deterministic gates (test/lint commands)
- Runs a mandatory vision gate on screenshots (UI screenshots when preview args are provided,
  otherwise diff screenshots)
- Iterates a fix loop and selects a winner

This module is designed to be run over stdio (the MCP transport Claude Code expects):

  petamind-mcp
  (legacy alias: terra-mind-mcp)

Or:

  python -m titan_factory.mcp_code_server
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ContentBlock, ImageContent, TextContent
from playwright.async_api import async_playwright

from titan_factory.config import load_config
from titan_factory.providers import Message, ProviderFactory
from titan_factory.utils import (
    extract_json_strict,
    find_available_port,
    managed_process,
    run_command,
)


_THIS_REPO_ROOT = Path(__file__).resolve().parents[2]


_PATCH_SCHEMA = """{
  "patches": [
    {
      "path": "relative/path/from/repo/root.ext",
      "patch": "unified diff hunks for ONLY this file (must include @@ hunks)"
    }
  ],
  "notes": ["brief notes (<= 8)"]
}"""


_PATCH_GENERATOR_SYSTEM = f"""You are TITAN-CODE, an expert patch generator for software repositories.

GOAL
Given a goal/instruction and selected file context, produce the smallest set of changes
needed to satisfy the goal and pass the repo's deterministic gates.

OUTPUT RULES (ABSOLUTE)
- Output ONLY valid JSON.
- No markdown. No code fences. No explanation. No <think>.
- JSON must start with {{ and end with }}.
- Must match this schema exactly:
{_PATCH_SCHEMA}

PATCH RULES
- Each patch is for ONE file and MUST contain one or more hunks starting with @@.
- Use context lines (starting with a space) so the patch applies cleanly.
- Keep changes minimal; do NOT reformat unrelated code.
- Do NOT change dependencies unless required.
"""


_PATCH_FIXER_SYSTEM = f"""You are TITAN-CODE, a patch FIXER.

You will be given:
1) The goal
2) The failing deterministic command (tests/lint/build)
3) The stdout/stderr (tail)
4) The current contents of files you already touched

Your job is to produce additional minimal patches to make the command pass.

OUTPUT RULES (ABSOLUTE)
- Output ONLY valid JSON.
- Must match this schema exactly:
{_PATCH_SCHEMA}
- Each patch must include @@ hunks.
- Only modify files that are necessary.
"""


_VISION_BROKEN_SYSTEM = """You are a STRICT website screenshot validator.
Your job is NOT to judge aesthetics. Only decide if the page is clearly BROKEN.

Mark broken=true ONLY when you are highly confident the page is broken, such as:
- runtime error overlay, stack trace, red error screen, "Unhandled Runtime Error"
- "Application error", "Something went wrong", Next.js error overlay
- 404 / "page could not be found"
- blank/empty page with almost no visible content
- obvious missing CSS/layout causing the page to be unusable (e.g. everything overlaps as a single blob)

If the page looks like a real website (even if ugly/boring/low quality), broken MUST be false.

OUTPUT RULES:
- Output ONLY valid JSON (no markdown, no <think>, no explanation).
- JSON must start with { and end with }.

OUTPUT FORMAT:
{
  "broken": false,
  "confidence": 0.0,
  "reasons": []
}"""


_VISION_BROKEN_USER = """Decide whether this page is BROKEN.

Be conservative: if unsure, set broken=false.
Return broken=true only if confidence >= {min_confidence}.
"""


_VISION_SCORE_SYSTEM = """You are a HIGH-END UI judge and creative director.

You will be given website screenshots (multiple viewports).
Your job is to score the design from 0.0 to 10.0 and provide actionable feedback.

This is NOT a generic “is it pretty?” check. You must be strict, and you must reward
distinctive creative execution when it is cohesive and usable.

SCORING (each worth 2 points):
1) Craft & polish: typography, spacing rhythm, visual finish, responsive care
2) Hierarchy & usability: scanability, clarity of CTAs, contrast, affordances, navigation
3) Cohesive art direction: consistent mood, color system, type pairing, imagery/icon style
4) Content quality: specific copy (not placeholder), credible structure, proof, clarity
5) Creative signature: at least 1–2 memorable “signature moments” that avoid generic templates

CREATIVE REWARD RULES (IMPORTANT):
- If the site is clean but generic, cap at 7.5 (even if technically correct).
- If the site takes a tasteful, coherent risk that WORKS, allow 8.5–10.
- Minimalism can score high if it feels intentional and premium (not empty/flat).
- Do NOT reward chaos. Novelty only counts when it improves clarity or memorability.

OUTPUT RULES:
- Output ONLY valid JSON (no markdown, no explanation, no <think>).
- JSON must start with { and end with }.

OUTPUT FORMAT:
{
  "score": 0.0,
  "pass": true,
  "issues": ["..."],
  "highlights": ["..."],
  "fix_suggestions": ["..."]
}"""


_VISION_SCORE_USER = """Score these screenshots for goal satisfaction and visual quality.

GOAL:
{goal}

Pass threshold is {threshold}/10.
Be specific in issues and suggestions. Note which viewport has issues.
"""


_DIFF_SCORE_SYSTEM = """You are a code-review judge looking at screenshots of a unified diff.

You will be given one or more screenshots showing code changes.
Score from 0.0 to 10.0 and provide actionable feedback.

SCORING (each worth 2 points):
1) Goal satisfaction: does the change actually implement the goal?
2) Correctness & safety: obvious bugs, edge cases, regressions, missing error handling
3) Test discipline: adds/updates tests when appropriate; avoids flaky behavior
4) Clarity: naming, structure, readability, minimal changes
5) Craft: elegant solution, good UX/devex, avoids “quick hacks”

CREATIVE REWARD RULES:
- Reward elegant simplification and good tests, not pointless complexity.
- Do NOT reward unnecessary refactors.

OUTPUT RULES:
- Output ONLY valid JSON (no markdown, no explanation, no <think>).
- JSON must start with { and end with }.

OUTPUT FORMAT:
{
  "score": 0.0,
  "pass": true,
  "issues": ["..."],
  "highlights": ["..."],
  "fix_suggestions": ["..."]
}"""


_DIFF_SCORE_USER = """These screenshots show a unified diff of code changes.

GOAL:
{goal}

Pass threshold is {threshold}/10.
Be specific in issues and suggestions.
"""


def _client_vision_instructions(*, kind: Literal["ui", "diff"], goal: str, threshold: float, min_confidence: float) -> str:
    """Instructions for client-side (Claude) vision scoring.

    This keeps the MCP server usable with *zero extra cloud credentials*: the server captures
    screenshots, and Claude judges them using built-in vision.
    """
    broken_block = ""
    if kind == "ui":
        broken_block = (
            "BROKEN GATE (UI screenshots only)\n"
            f"- Output broken=true only if confidence >= {min_confidence}\n"
            "- Broken means: runtime error overlay, 404, blank page, unusable layout collapse.\n"
            "- Ugly/boring is NOT broken.\n\n"
        )

    return (
        "VISION JUDGE (CLIENT MODE)\n"
        "You are the vision judge. You will be shown 1+ screenshots.\n\n"
        f"GOAL:\n{goal}\n\n"
        f"PASS THRESHOLD: {threshold}/10\n"
        f"MODE: {kind}\n\n"
        + broken_block
        + "SCORING (0.0–10.0)\n"
        "- Reward coherent creative signature moments when they improve memorability and still read cleanly.\n"
        "- If it's clean but generic/template, cap at 7.5.\n"
        "- If it takes tasteful, cohesive risk that WORKS, allow 8.5–10.\n\n"
        "OUTPUT JSON ONLY (no markdown):\n"
        "{\n"
        '  "broken": {"broken": false, "confidence": 0.0, "reasons": ["..."]},\n'
        '  "score": {\n'
        '    "score": 0.0,\n'
        '    "pass": true,\n'
        '    "issues": ["..."],\n'
        '    "highlights": ["..."],\n'
        '    "fix_suggestions": ["..."]\n'
        "  }\n"
        "}\n"
    )

_VISION_FIXER_SYSTEM = f"""You are TITAN-CODE, a UI refiner driven by vision feedback.

You will be given:
1) The goal
2) A VISION_REPORT JSON containing:
   - broken gate result (broken/confidence/reasons)
   - score result (score/pass/issues/highlights/fix_suggestions)
3) The current contents of files you already touched

Your job is to produce minimal patches that address the vision issues.

IMPORTANT BEHAVIOR
- If some parts are strong and others are weak/boring, ONLY edit the weak parts.
- Do NOT rewrite the whole file unless the vision feedback indicates global structural problems.
- Prefer targeted changes: spacing, hierarchy, section layouts, typography, contrast, CTAs.
- Keep changes deterministic-safe (do not break builds/tests).

OUTPUT RULES (ABSOLUTE)
- Output ONLY valid JSON.
- Must match this schema exactly:
{_PATCH_SCHEMA}
- Each patch must include @@ hunks.
"""

_SECTION_CREATIVITY_SYSTEM = """You are a section-level creativity evaluator for a website screenshot.

You will be given ONE full-page screenshot.
Your job: identify the major sections top-to-bottom and score each section for how DISTINCTIVE / CREATIVE it looks versus generic/template.

SCORING (0.0 to 1.0):
- 1.0: Distinctive, memorable, signature layout moment, cohesive with the page.
- 0.7: Solid and non-generic, some unique structure, visually intentional.
- 0.4: Generic (stacked cards / plain blocks) with minimal uniqueness.
- 0.0: Empty/blank, placeholder, broken-looking, or effectively missing.

IMPORTANT:
- Minimal can still be intentional; don't punish minimal done well.
- If unclear, use score=0.5 confidence<=0.4 and notes=unclear.
- Notes must be <= 8 words. No quotes.

OUTPUT RULES:
- Output ONLY valid JSON (no markdown, no <think>, no explanation).
- JSON must start with { and end with }.

OUTPUT FORMAT:
{
  "sections": [
    {"label": "hero", "score": 0.0, "confidence": 0.0, "notes": "short note"},
    {"label": "features", "score": 0.0, "confidence": 0.0, "notes": "short note"}
  ]
}
"""

_SECTION_CREATIVITY_USER = """Identify 6-12 major sections in this page (top to bottom) and score each section's creativity.

If the page has fewer sections, return fewer.
Return JSON only.
"""

_CREATIVITY_REFINER_SYSTEM = f"""You are TITAN-CODE, a TARGETED UI SECTION REFINER.

Goal: Improve ONLY the weak sections so they match the creativity level of the strong sections,
WITHOUT rewriting the whole page and WITHOUT changing the strong sections.

Rules:
1) Output ONLY valid JSON (no markdown, no explanation, no <think>)
2) Modify ONLY weak sections listed (do not touch strong sections)
3) Do NOT add dependencies or UI libraries
4) Keep changes small and localized; prefer patches
5) No emojis
6) Keep it build-safe and responsive

Creativity requirement (mandatory):
- For EACH weak section, introduce at least ONE signature moment appropriate for the content:
  - bento / asymmetric grid
  - comparison strip
  - proof wall (stats/logos/quotes)
  - timeline/stepper with rhythm
  - interactive chips + preview cards
  - pricing decision helper (if pricing section)

OUTPUT RULES (ABSOLUTE)
- Output ONLY valid JSON.
- Must match this schema exactly:
{_PATCH_SCHEMA}
- Each patch must include @@ hunks.
"""


_CODE_PLAN_SCHEMA = """{
  "summary": "one paragraph",
  "intent": "what success looks like",
  "steps": ["ordered steps"],
  "files_to_read": ["relative paths the coder should inspect"],
  "changes": ["concrete changes to make"],
  "tests": ["commands to run / checks to perform"],
  "risks": ["edge cases / risks"]
}"""


_CODE_REASONER_BOLD_SYSTEM = f"""You are a BOLD engineering reasoner.

Goal: propose an effective (possibly creative) implementation plan, but stay build-safe.
Prefer bold solutions when they simplify the system or reduce long-term complexity.

Output JSON ONLY matching this schema:
{_CODE_PLAN_SCHEMA}
"""


_CODE_REASONER_MINIMAL_SYSTEM = f"""You are a MINIMAL engineering reasoner.

Goal: propose the smallest change that satisfies the goal with the lowest risk.
Avoid refactors unless they are strictly necessary.

Output JSON ONLY matching this schema:
{_CODE_PLAN_SCHEMA}
"""


_CODE_REASONER_SAFE_SYSTEM = f"""You are a SAFE engineering reasoner.

Goal: propose a plan that is robust, well-tested, and avoids subtle regressions.
Prefer explicitness, guardrails, and deterministic validation steps.

Output JSON ONLY matching this schema:
{_CODE_PLAN_SCHEMA}
"""


_CODE_REASONER_SYNTH_SYSTEM = f"""You are a SYNTHESIZER that merges 3 engineering plans (bold/minimal/safe).

Your job:
- keep the LOWEST-RISK aspects of SAFE
- keep the SMALLEST-SCOPE aspects of MINIMAL
- keep the most leveraged simplifications from BOLD
- produce ONE coherent plan (not an average)

Output JSON ONLY matching this schema:
{_CODE_PLAN_SCHEMA}
"""


_HUNK_RE = re.compile(
    r"^@@\s+-(?P<old_start>\d+)(?:,(?P<old_len>\d+))?\s+\+(?P<new_start>\d+)(?:,(?P<new_len>\d+))?\s+@@"
)


def _tail(text: str, max_chars: int = 5000) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return "…(truncated)…\n" + text[-max_chars:]


def _shlex_quote(s: str) -> str:
    if not s:
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_./:-]+", s):
        return s
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _coerce_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    return []


def _merge_unique(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        key = str(item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _read_text(path: Path, *, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 80)] + "\n\n…(truncated)…\n"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _image_content_from_path(path: Path) -> ImageContent | None:
    """Best-effort load a screenshot file as MCP ImageContent (base64)."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except Exception:
        return None

    # MCP ImageContent expects base64-encoded bytes.
    b64 = base64.b64encode(data).decode("ascii")
    return ImageContent(type="image", data=b64, mimeType="image/png")


async def _git_root(repo_path: Path) -> Path | None:
    code, out, _ = await run_command("git rev-parse --show-toplevel", cwd=repo_path, timeout_ms=30_000)
    if code != 0:
        return None
    root = (out or "").strip()
    return Path(root) if root else None


async def _git_head(repo_root: Path) -> str | None:
    code, out, _ = await run_command("git rev-parse HEAD", cwd=repo_root, timeout_ms=30_000)
    if code != 0:
        return None
    return (out or "").strip() or None


async def _make_worktree(*, repo_root: Path, commit: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    code, _, err = await run_command(
        f"git worktree add --detach {_shlex_quote(str(dest))} {_shlex_quote(commit)}",
        cwd=repo_root,
        timeout_ms=120_000,
    )
    return code == 0 and not (err or "").strip().lower().startswith("fatal:")


async def _remove_worktree(*, repo_root: Path, dest: Path) -> None:
    await run_command(
        f"git worktree remove --force {_shlex_quote(str(dest))}",
        cwd=repo_root,
        timeout_ms=120_000,
    )


def _count_patch_deltas(patch_text: str) -> tuple[int, int]:
    adds = 0
    deletes = 0
    for line in (patch_text or "").splitlines():
        if not line:
            continue
        if line.startswith(("diff ", "index ", "--- ", "+++ ", "@@")):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            adds += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletes += 1
    return adds, deletes


def _sanitize_rel_path(rel_path: str) -> str | None:
    rel_path = str(rel_path or "").strip().replace("\\", "/")
    if not rel_path:
        return None
    if rel_path.startswith(("/", "../")) or "/../" in rel_path:
        return None
    return rel_path


def _maybe_symlink_reuse_dirs(*, repo_root: Path, worktree: Path, reuse_dirs: list[str]) -> list[str]:
    """Symlink heavy untracked dirs (like node_modules) into a worktree to avoid reinstalling deps."""
    created: list[str] = []
    repo_root_resolved = repo_root.resolve()
    worktree_resolved = worktree.resolve()

    for raw in reuse_dirs:
        rel = _sanitize_rel_path(raw)
        if not rel:
            continue

        src = (repo_root_resolved / rel).resolve()
        try:
            src.relative_to(repo_root_resolved)
        except Exception:
            continue

        if not src.exists() or not src.is_dir():
            continue

        dst = worktree_resolved / rel
        if dst.exists() or dst.is_symlink():
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            dst.symlink_to(src, target_is_directory=True)
            created.append(rel)
        except Exception:
            # Best-effort optimization only.
            continue

    return created


def _apply_unified_diff_to_text(original_text: str, diff: str) -> str:
    """Apply a simple unified diff to text.

    This is intentionally lightweight (model patches are expected to be small).
    """
    original_lines = (original_text or "").splitlines()
    had_trailing_nl = (original_text or "").endswith("\n")
    diff_lines = (diff or "").splitlines()
    if not diff_lines:
        return original_text

    def find_subsequence(
        haystack: list[str],
        needle: list[str],
        *,
        start_hint: int,
        min_index: int,
        fuzz: int,
    ) -> int:
        if not needle:
            return max(min_index, min(start_hint, len(haystack)))

        n = len(needle)
        max_start = len(haystack) - n
        if max_start < min_index:
            raise ValueError("Patch hunk does not fit target file")

        # Search near the header hint first.
        low = max(min_index, start_hint - fuzz)
        high = min(max_start, start_hint + fuzz)
        candidates: list[int] = []

        for idx in range(low, high + 1):
            if haystack[idx : idx + n] == needle:
                candidates.append(idx)

        # If not found, fall back to a full scan (still bounded by min_index).
        if not candidates:
            for idx in range(min_index, max_start + 1):
                if haystack[idx : idx + n] == needle:
                    candidates.append(idx)

        if not candidates:
            raise ValueError("Patch hunk context not found in target file")

        # Prefer the closest match to the hunk header's start_hint.
        return min(candidates, key=lambda idx: abs(idx - start_hint))

    out: list[str] = []
    src_i = 0
    i = 0

    while i < len(diff_lines):
        line = diff_lines[i]

        # Skip headers/noise until we hit a hunk header.
        if line.startswith(("diff ", "index ", "--- ", "+++ ")):
            i += 1
            continue
        if not line.startswith("@@"):
            i += 1
            continue

        m = _HUNK_RE.match(line)
        if not m:
            i += 1
            continue

        old_start = max(0, int(m.group("old_start")) - 1)

        # Collect hunk body (until next hunk header or diff header).
        j = i + 1
        hunk_body: list[str] = []
        while j < len(diff_lines):
            h = diff_lines[j]
            if h.startswith("@@") or h.startswith(("diff ", "index ", "--- ", "+++ ")):
                break
            hunk_body.append(h)
            j += 1

        expected_old = [h[1:] for h in hunk_body if h.startswith((" ", "-"))]

        # Find the best anchor point for this hunk in the original text.
        if expected_old:
            anchor = find_subsequence(
                original_lines,
                expected_old,
                start_hint=min(old_start, len(original_lines)),
                min_index=src_i,
                fuzz=80,
            )
        else:
            anchor = max(src_i, min(old_start, len(original_lines)))

        # Copy unchanged lines before this hunk.
        if anchor < src_i:
            raise ValueError("Patch hunks are out of order (anchor went backwards)")
        out.extend(original_lines[src_i:anchor])
        src_i = anchor

        # Apply hunk operations.
        for h in hunk_body:
            if h.startswith(" "):
                text = h[1:]
                if src_i >= len(original_lines) or original_lines[src_i] != text:
                    raise ValueError("Patch context mismatch")
                out.append(text)
                src_i += 1
            elif h.startswith("-"):
                text = h[1:]
                if src_i >= len(original_lines) or original_lines[src_i] != text:
                    raise ValueError("Patch delete mismatch")
                src_i += 1
            elif h.startswith("+"):
                out.append(h[1:])
            elif h.startswith("\\"):
                # "\ No newline at end of file"
                continue
            else:
                raise ValueError("Unsupported diff line (missing prefix)")

        i = j
        continue

    # Copy remaining lines.
    while src_i < len(original_lines):
        out.append(original_lines[src_i])
        src_i += 1

    result = "\n".join(out)
    if had_trailing_nl:
        result += "\n"
    return result


async def _apply_patch_bundle(
    *,
    repo_root: Path,
    patches: list[dict[str, str]],
) -> tuple[bool, list[str]]:
    touched: list[str] = []
    repo_resolved = repo_root.resolve()
    diff_git_header_re = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+?)\s*$")

    for item in patches:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path") or "").strip()
        diff = str(item.get("patch") or "").rstrip()
        if not rel or not diff:
            continue

        diff_lines = diff.splitlines()

        # Guardrail: reject multi-file patches accidentally stuffed into one entry.
        for line in diff_lines:
            if line.startswith("diff --git "):
                m = diff_git_header_re.match(line)
                if m:
                    if m.group("a") != rel or m.group("b") != rel:
                        return False, touched
            elif line.startswith("--- "):
                path = line[4:].strip()
                if path.startswith("a/"):
                    path = path[2:]
                if path not in (rel, "/dev/null"):
                    return False, touched
            elif line.startswith("+++ "):
                path = line[4:].strip()
                if path.startswith("b/"):
                    path = path[2:]
                if path not in (rel, "/dev/null"):
                    return False, touched

        # Guardrails: require at least one real hunk header and at least one +/- change.
        if not any(line.startswith("@@") for line in diff_lines):
            return False, touched
        if not any(
            line.startswith(("+", "-")) and not line.startswith(("+++ ", "--- "))
            for line in diff_lines
        ):
            return False, touched

        target = (repo_root / rel).resolve()
        try:
            target.relative_to(repo_resolved)
        except Exception:
            return False, touched

        original = ""
        if target.exists():
            original = target.read_text(encoding="utf-8", errors="replace")
        try:
            patched = _apply_unified_diff_to_text(original, diff)
        except Exception:
            return False, touched
        _write_text(target, patched)
        touched.append(rel)

    return True, touched


def _build_context_blob(
    *,
    repo_root: Path,
    context_files: list[str],
    max_file_chars: int,
    max_total_chars: int | None = None,
) -> str:
    blobs: list[str] = []
    repo_resolved = repo_root.resolve()
    total = 0
    truncated = False
    for rel in context_files:
        rel = str(rel or "").strip()
        if not rel:
            continue
        p = (repo_root / rel).resolve()
        try:
            p.relative_to(repo_resolved)
        except Exception:
            continue
        text = _read_text(p, max_chars=max_file_chars)
        if not text.strip():
            continue
        block = f"=== {rel} ===\n{text}"
        if max_total_chars is not None and max_total_chars > 0:
            if total + len(block) > max_total_chars:
                truncated = True
                break
        blobs.append(block)
        total += len(block) + 2  # account for join spacing

    if truncated:
        blobs.append("…(context truncated)…")
    return "\n\n".join(blobs).strip()


_AUTO_CONTEXT_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "do",
    "for",
    "from",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "we",
    "what",
    "when",
    "where",
    "with",
    "you",
    "your",
}


def _derive_auto_context_queries(goal: str, *, max_queries: int) -> list[str]:
    tokens = re.split(r"[^A-Za-z0-9_]+", str(goal or ""))
    cleaned: list[str] = []
    for t in tokens:
        t = t.strip().lower()
        if not t or len(t) < 4:
            continue
        if t in _AUTO_CONTEXT_STOPWORDS:
            continue
        cleaned.append(t)

    # Prefer longer, more specific tokens.
    cleaned = sorted(_merge_unique(cleaned), key=len, reverse=True)
    return cleaned[: max(1, int(max_queries or 8))]


async def _auto_context_files(
    *,
    repo_root: Path,
    queries: list[str],
    max_files: int,
) -> list[str]:
    repo_resolved = repo_root.resolve()
    queries = [q.strip() for q in queries if str(q).strip()]
    if not queries or max_files <= 0:
        return []

    # Try ripgrep; fall back to grep when rg isn't installed.
    rc, out, _err = await run_command("command -v rg", cwd=repo_root, timeout_ms=5000)
    has_rg = rc == 0 and bool((out or "").strip())

    def _filter_paths(lines: list[str]) -> list[str]:
        out_paths: list[str] = []
        for line in lines:
            rel = str(line or "").strip()
            if not rel:
                continue
            rel = rel.replace("\\", "/")
            if rel.startswith(("/", "../")) or "/../" in rel:
                continue
            p = (repo_root / rel).resolve()
            try:
                p.relative_to(repo_resolved)
            except Exception:
                continue
            if not p.exists() or not p.is_file():
                continue
            out_paths.append(rel)
        return out_paths

    ignore_globs = [
        "!.git/**",
        "!node_modules/**",
        "!.venv/**",
        "!venv/**",
        "!__pycache__/**",
        "!out/**",
        "!.next/**",
        "!dist/**",
        "!build/**",
        "!coverage/**",
        "!*.png",
        "!*.jpg",
        "!*.jpeg",
        "!*.webp",
        "!*.gif",
        "!*.pdf",
        "!*.zip",
    ]

    found: list[str] = []
    for q in queries:
        if len(found) >= max_files:
            break

        if has_rg:
            glob_flags = " ".join([f"--glob {_shlex_quote(g)}" for g in ignore_globs])
            cmd = (
                "rg -l -F -i --hidden --no-messages "
                + glob_flags
                + " "
                + _shlex_quote(q)
            )
            rc, o, e = await run_command(cmd, cwd=repo_root, timeout_ms=30_000)
            # rg: 0=matches, 1=no matches, 2=error
            if rc not in (0, 1):
                _ = e  # keep for debugging if needed
                continue
            if rc == 0 and o:
                found.extend(_filter_paths(o.splitlines()))
        else:
            # grep -RIl is slower but widely available.
            # Use -F for fixed string and exclude heavy dirs.
            cmd = (
                "grep -RIl --binary-files=without-match "
                "--exclude-dir=.git --exclude-dir=node_modules --exclude-dir=out "
                "--exclude-dir=.venv --exclude-dir=venv --exclude-dir=__pycache__ "
                "--exclude-dir=.next --exclude-dir=dist --exclude-dir=build --exclude-dir=coverage "
                + _shlex_quote(q)
                + " ."
            )
            rc, o, _e = await run_command(cmd, cwd=repo_root, timeout_ms=45_000)
            if rc != 0:
                continue
            if o:
                found.extend(_filter_paths(o.splitlines()))

    return _merge_unique(found)[:max_files]


async def _command_exists(*, repo_root: Path, binary: str) -> bool:
    rc, out, _err = await run_command(
        f"command -v {_shlex_quote(str(binary or '').strip())}",
        cwd=repo_root,
        timeout_ms=5000,
    )
    return rc == 0 and bool((out or "").strip())


async def _infer_test_command(repo_root: Path) -> tuple[str, str]:
    """Infer a reasonable default test command for a repo.

    If nothing is detected (or required binaries aren't available), returns ("true", reason)
    so the pipeline can still run (with no deterministic validation).
    """
    repo_root = repo_root.resolve()

    candidates: list[tuple[str, str]] = []

    # Node / JS
    if (repo_root / "package.json").exists():
        if (repo_root / "pnpm-lock.yaml").exists():
            candidates.append(("pnpm test", "Detected package.json + pnpm-lock.yaml"))
        elif (repo_root / "yarn.lock").exists():
            candidates.append(("yarn test", "Detected package.json + yarn.lock"))
        else:
            candidates.append(("npm test", "Detected package.json"))

    # Python
    if any(
        (repo_root / name).exists()
        for name in ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg", "requirements.txt")
    ):
        candidates.append(("pytest -q", "Detected Python project files"))

    # Go
    if (repo_root / "go.mod").exists():
        candidates.append(("go test ./...", "Detected go.mod"))

    # Rust
    if (repo_root / "Cargo.toml").exists():
        candidates.append(("cargo test", "Detected Cargo.toml"))

    # Fall back: no-op gate rather than hard-fail on an obviously wrong default.
    candidates.append(("true", "No test harness detected; skipping deterministic tests"))

    for cmd, reason in candidates:
        binary = (cmd.split() or [""])[0]
        if binary == "true":
            return cmd, reason
        if await _command_exists(repo_root=repo_root, binary=binary):
            return cmd, reason

    return "true", "No suitable test runner found on PATH; skipping deterministic tests"


async def _call_llm_json(
    *,
    provider_name: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    config = load_config()
    provider = ProviderFactory.get(provider_name, config)
    response = await provider.complete(
        messages=[
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    data = extract_json_strict(response.content)
    if not isinstance(data, dict):
        raise ValueError("Model returned non-dict JSON")
    return data


def _extract_files_to_read(plan: dict[str, Any]) -> list[str]:
    raw = plan.get("files_to_read")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        p = str(item or "").strip()
        if p:
            out.append(p)
    return out


async def _generate_plan_megamind(
    *,
    provider_name: str,
    model_bold: str,
    model_minimal: str,
    model_safe: str,
    model_synth: str,
    goal: str,
    context_blob: str,
    max_tokens: int,
) -> dict[str, Any]:
    base_user_prompt = (
        "GOAL\n"
        f"{goal}\n\n"
        "REPO CONTEXT (selected files)\n"
        f"{context_blob if context_blob else '(none provided)'}\n\n"
        "Return JSON only.\n"
    )

    bold = await _call_llm_json(
        provider_name=provider_name,
        model=model_bold,
        system_prompt=_CODE_REASONER_BOLD_SYSTEM,
        user_prompt=base_user_prompt,
        temperature=0.85,
        max_tokens=max_tokens,
    )
    minimal = await _call_llm_json(
        provider_name=provider_name,
        model=model_minimal,
        system_prompt=_CODE_REASONER_MINIMAL_SYSTEM,
        user_prompt=base_user_prompt,
        temperature=0.25,
        max_tokens=max_tokens,
    )
    safe = await _call_llm_json(
        provider_name=provider_name,
        model=model_safe,
        system_prompt=_CODE_REASONER_SAFE_SYSTEM,
        user_prompt=base_user_prompt,
        temperature=0.55,
        max_tokens=max_tokens,
    )

    synth_prompt = (
        "You will be given 3 plans. Merge them into ONE coherent plan.\n\n"
        "BOLD PLAN:\n"
        f"{json.dumps(bold, indent=2, sort_keys=True)}\n\n"
        "MINIMAL PLAN:\n"
        f"{json.dumps(minimal, indent=2, sort_keys=True)}\n\n"
        "SAFE PLAN:\n"
        f"{json.dumps(safe, indent=2, sort_keys=True)}\n\n"
        "Return JSON only.\n"
    )

    synthesized = await _call_llm_json(
        provider_name=provider_name,
        model=model_synth,
        system_prompt=_CODE_REASONER_SYNTH_SYSTEM,
        user_prompt=synth_prompt,
        temperature=0.35,
        max_tokens=max_tokens,
    )

    return {
        "bold": bold,
        "minimal": minimal,
        "safe": safe,
        "synthesized": synthesized,
    }


async def _wait_for_http(url: str, *, timeout_s: float) -> tuple[bool, str]:
    start = time.monotonic()
    last_err = ""
    async with httpx.AsyncClient(timeout=5.0) as client:
        while time.monotonic() - start < timeout_s:
            try:
                r = await client.get(url, follow_redirects=True)
                if 200 <= r.status_code < 500:
                    return True, ""
                last_err = f"HTTP {r.status_code}"
            except Exception as e:
                last_err = str(e)
            await asyncio.sleep(0.35)
    return False, last_err


def _playwright_install_hint(err: BaseException) -> str | None:
    """Return a helpful install hint if Playwright is missing browser binaries."""
    msg = str(err or "")
    lower = msg.lower()
    if not msg:
        return None

    triggers = [
        "executable doesn't exist",
        "executable does not exist",
        "download new browsers",
        "run the following command",
        "playwright install",
    ]
    if any(t in lower for t in triggers):
        return (
            "Playwright Chromium failed to launch (browser binaries may be missing).\n"
            "Fix: run `playwright install chromium` (or `python -m playwright install chromium`) and retry.\n"
            f"Original error: {msg}"
        )

    return None


def _pick_preview_port(*, idx: int, port_start_base: int) -> int:
    """Pick an available port for a preview server.

    IMPORTANT: When running multiple candidates concurrently, port search ranges must not overlap,
    otherwise candidates can race and select the same port. We enforce this by capping the scan
    window to `stride`.

    Env overrides:
    - TITAN_MCP_PORT_START: base port (handled by caller)
    - TITAN_MCP_PORT_STRIDE: spacing between candidate port ranges (default 25)
    - TITAN_MCP_PORT_ATTEMPTS: max scan window inside a range (default = stride, capped to stride)
    """
    stride = int(os.getenv("TITAN_MCP_PORT_STRIDE", "25") or 25)
    if stride < 1:
        stride = 25

    attempts = int(os.getenv("TITAN_MCP_PORT_ATTEMPTS", str(stride)) or stride)
    if attempts < 1:
        attempts = stride
    attempts = min(attempts, stride)

    port_start = int(port_start_base) + (int(idx) * stride)
    return find_available_port(start=port_start, max_attempts=attempts)


async def _capture_screenshots(
    *,
    url: str,
    out_dir: Path,
    viewports: list[dict[str, Any]],
    timeout_ms: int,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as e:
            hint = _playwright_install_hint(e)
            if hint:
                raise RuntimeError(hint) from e
            raise
        try:
            for vp in viewports:
                label = str(vp.get("label") or "desktop")
                width = int(vp.get("width") or 1440)
                height = int(vp.get("height") or 900)

                page = await browser.new_page(viewport={"width": width, "height": height})
                try:
                    await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                    await page.wait_for_timeout(250)
                    shot_path = out_dir / f"{label}.png"
                    await page.screenshot(path=str(shot_path), full_page=True)
                    paths.append(shot_path)
                finally:
                    await page.close()
        finally:
            await browser.close()

    return paths


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _diff_to_html(diff_text: str) -> str:
    """Render a unified diff into a readable HTML page for screenshotting."""
    lines: list[str] = []
    for raw in (diff_text or "").splitlines():
        cls = "ctx"
        if raw.startswith("@@"):
            cls = "hunk"
        elif raw.startswith(("diff ", "index ")):
            cls = "meta"
        elif raw.startswith(("--- ", "+++ ")):
            cls = "file"
        elif raw.startswith("+") and not raw.startswith("+++"):
            cls = "add"
        elif raw.startswith("-") and not raw.startswith("---"):
            cls = "del"

        lines.append(f'<div class="line {cls}"><span class="txt">{_escape_html(raw)}</span></div>')

    body = "\n".join(lines) if lines else '<div class="line empty">EMPTY DIFF</div>'
    return (
        "<!doctype html>\n"
        "<html><head><meta charset='utf-8' />\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1' />\n"
        "<style>\n"
        "body{margin:0;background:#0b1020;color:#e6e6e6;font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,'Liberation Mono','Courier New',monospace;}\n"
        ".wrap{padding:16px;}\n"
        ".line{white-space:pre-wrap;word-break:break-word;border-radius:6px;padding:2px 8px;margin:2px 0;}\n"
        ".meta{color:#93c5fd;background:rgba(59,130,246,.08);}\n"
        ".file{color:#c4b5fd;background:rgba(139,92,246,.10);}\n"
        ".hunk{color:#fcd34d;background:rgba(245,158,11,.10);}\n"
        ".add{background:rgba(34,197,94,.12);}\n"
        ".del{background:rgba(239,68,68,.12);}\n"
        ".ctx{background:rgba(255,255,255,.03);}\n"
        ".empty{color:#fca5a5;background:rgba(239,68,68,.12);}\n"
        "</style></head>\n"
        "<body><div class='wrap'>\n"
        f"{body}\n"
        "</div></body></html>\n"
    )


async def _capture_diff_screenshots(
    *,
    diff_text: str,
    out_dir: Path,
    timeout_ms: int,
) -> list[Path]:
    """Screenshot a diff by rendering it as HTML locally."""
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "diff.html"
    html_path.write_text(_diff_to_html(diff_text), encoding="utf-8")

    # Screenshot the rendered diff. Use a single wide viewport for readability.
    return await _capture_screenshots(
        url=html_path.resolve().as_uri(),
        out_dir=out_dir,
        viewports=[{"label": "diff", "width": 1200, "height": 900}],
        timeout_ms=timeout_ms,
    )


async def _vision_eval(
    *,
    images: list[bytes],
    goal: str,
    threshold: float,
    provider_name: str,
    model: str,
    min_confidence: float,
    kind: Literal["ui", "diff"],
) -> dict[str, Any]:
    config = load_config()
    provider = ProviderFactory.get(provider_name, config)

    if kind == "ui":
        broken = extract_json_strict(
            (
                await provider.complete_with_vision(
                    messages=[
                        Message(role="system", content=_VISION_BROKEN_SYSTEM),
                        Message(
                            role="user",
                            content=_VISION_BROKEN_USER.format(min_confidence=min_confidence),
                        ),
                    ],
                    model=model,
                    images=images,
                    max_tokens=600,
                    temperature=0.1,
                )
            ).content
        )
    else:
        # Diff screenshots aren't "broken pages". Keep schema stable.
        broken = {"broken": False, "confidence": 1.0, "reasons": ["diff_mode"]}

    if kind == "ui":
        score_system = _VISION_SCORE_SYSTEM
        score_user = _VISION_SCORE_USER.format(goal=goal, threshold=threshold)
    else:
        score_system = _DIFF_SCORE_SYSTEM
        score_user = _DIFF_SCORE_USER.format(goal=goal, threshold=threshold)

    score = extract_json_strict(
        (
            await provider.complete_with_vision(
                messages=[
                    Message(role="system", content=score_system),
                    Message(role="user", content=score_user),
                ],
                model=model,
                images=images,
                max_tokens=1200,
                temperature=0.2,
            )
        ).content
    )

    return {"broken": broken, "score": score}


async def _section_creativity_eval(
    *,
    image: bytes,
    provider_name: str,
    model: str,
) -> dict[str, Any]:
    config = load_config()
    provider = ProviderFactory.get(provider_name, config)
    data = await provider.complete_with_vision(
        messages=[
            Message(role="system", content=_SECTION_CREATIVITY_SYSTEM),
            Message(role="user", content=_SECTION_CREATIVITY_USER),
        ],
        model=model,
        images=[image],
        max_tokens=900,
        temperature=0.2,
    )
    return extract_json_strict(data.content)


def _section_creativity_metrics(
    report: dict[str, Any] | None,
    *,
    min_confidence: float,
    min_score: float,
) -> tuple[list[str], list[str], float | None, float | None]:
    if not isinstance(report, dict):
        return [], [], None, None
    sections = report.get("sections")
    if not isinstance(sections, list):
        return [], [], None, None

    strong: list[str] = []
    weak: list[str] = []
    scores: list[float] = []

    for s in sections:
        if not isinstance(s, dict):
            continue
        label = str(s.get("label") or "").strip()
        if not label:
            continue
        try:
            score = float(s.get("score"))
            confidence = float(s.get("confidence"))
        except Exception:
            continue

        if confidence < float(min_confidence):
            continue

        scores.append(score)
        if score >= float(min_score):
            strong.append(label)
        else:
            weak.append(label)

    strong = _merge_unique(strong)
    weak = [w for w in _merge_unique(weak) if w not in set(strong)]
    if not scores:
        return strong, weak, None, None
    return strong, weak, (sum(scores) / len(scores)), min(scores)

async def _run_gates(
    *,
    repo_root: Path,
    test_command: str | None,
    lint_command: str | None,
    timeout_ms: int,
) -> tuple[tuple[int, str, str], tuple[int, str, str]]:
    if test_command:
        test_rc, test_out, test_err = await run_command(
            test_command, cwd=repo_root, timeout_ms=timeout_ms
        )
    else:
        test_rc, test_out, test_err = 0, "", ""

    if lint_command:
        lint_rc, lint_out, lint_err = await run_command(
            lint_command, cwd=repo_root, timeout_ms=timeout_ms
        )
    else:
        lint_rc, lint_out, lint_err = 0, "", ""

    return (test_rc, test_out, test_err), (lint_rc, lint_out, lint_err)


def _write_gate_logs(
    *,
    cand_dir: Path,
    test_out: str,
    test_err: str,
    lint_out: str,
    lint_err: str,
    label: str | None = None,
) -> None:
    _write_text(cand_dir / "test_stdout.txt", test_out or "")
    _write_text(cand_dir / "test_stderr.txt", test_err or "")
    _write_text(cand_dir / "lint_stdout.txt", lint_out or "")
    _write_text(cand_dir / "lint_stderr.txt", lint_err or "")

    if label:
        _write_text(cand_dir / f"test_stdout_{label}.txt", test_out or "")
        _write_text(cand_dir / f"test_stderr_{label}.txt", test_err or "")
        _write_text(cand_dir / f"lint_stdout_{label}.txt", lint_out or "")
        _write_text(cand_dir / f"lint_stderr_{label}.txt", lint_err or "")


def _pick_best_screenshot_dir(screens_dir: Path) -> Path | None:
    for preferred in ("desktop", "tablet", "mobile"):
        p = screens_dir / f"{preferred}.png"
        if p.exists():
            return p
    pngs = sorted(screens_dir.glob("*.png"))
    return pngs[-1] if pngs else None


@dataclass
class CandidateResult:
    index: int
    temperature: float
    ok: bool
    applied: bool
    test_ok: bool
    lint_ok: bool
    vision_ok: bool
    vision_score: float | None
    adds: int
    deletes: int
    fix_rounds: int
    patch: str
    notes: list[str]
    error: str | None
    creativity_avg: float | None = None
    creativity_min: float | None = None
    creativity_strong: int = 0
    creativity_weak: int = 0
    creativity_eval_ok: bool = False


def _select_winner(
    results: list[CandidateResult],
    *,
    allow_best_effort: bool,
) -> CandidateResult | None:
    if not results:
        return None

    use_creativity = any(c.creativity_eval_ok for c in results)

    def pass_all(c: CandidateResult) -> bool:
        return c.ok and c.applied and c.test_ok and c.lint_ok and c.vision_ok

    def key_passing(c: CandidateResult) -> tuple:
        size = c.adds + c.deletes
        # Prefer higher vision score (if available), then smaller diff, then fewer fix rounds.
        return (
            (0 if (not use_creativity) else (0 if c.creativity_eval_ok else 1)),
            (0 if (not use_creativity) else c.creativity_weak),
            (0.0 if (not use_creativity) else -(c.creativity_min or 0.0)),
            (0.0 if (not use_creativity) else -(c.creativity_avg or 0.0)),
            -(c.vision_score or 0.0),
            size,
            c.fix_rounds,
            c.index,
        )

    passing = [c for c in results if pass_all(c)]
    if passing:
        return sorted(passing, key=key_passing)[0]

    if not allow_best_effort:
        return None

    def key_best_effort(c: CandidateResult) -> tuple:
        det_ok = c.test_ok and c.lint_ok
        has_patch = bool((c.patch or "").strip())
        size = c.adds + c.deletes
        return (
            0 if det_ok else 1,
            0 if c.ok else 1,
            0 if has_patch else 1,
            (0 if (not use_creativity) else (0 if c.creativity_eval_ok else 1)),
            (0 if (not use_creativity) else c.creativity_weak),
            (0.0 if (not use_creativity) else -(c.creativity_min or 0.0)),
            (0.0 if (not use_creativity) else -(c.creativity_avg or 0.0)),
            -(c.vision_score or 0.0),
            size,
            c.fix_rounds,
            c.index,
        )

    return sorted(results, key=key_best_effort)[0]


mcp = FastMCP("petamind-mcp")


@mcp.tool()
async def titan_code_solve(
    repo_path: str,
    goal: str,
    *,
    context_files: list[str] | None = None,
    auto_context_mode: Literal["off", "goal", "queries"] = "off",
    auto_context_queries: list[str] | None = None,
    auto_context_max_files: int = 12,
    auto_context_max_queries: int = 8,
    context_max_chars: int = 150_000,
    context_max_file_chars: int = 12_000,
    # Reasoner / planning stage
    planning_mode: Literal["off", "single", "megamind"] = "megamind",
    planner_provider: str = "vertex",
    planner_model: str = "deepseek-ai/deepseek-v3.2-maas",
    planner_bold_model: str | None = None,
    planner_minimal_model: str | None = None,
    planner_safe_model: str | None = None,
    planner_synth_model: str | None = None,
    planner_max_tokens: int = 3000,
    # Patch generation
    provider: str = "vertex",
    model: str = "deepseek-ai/deepseek-v3.2-maas",
    max_candidates: int = 4,
    candidate_concurrency: int = 1,
    temperature_schedule: list[float] | None = None,
    max_tokens: int = 8000,
    worktree_reuse_dirs: list[str] | None = None,
    # Deterministic gates
    test_command: str | None = None,
    lint_command: str | None = None,
    gate_timeout_ms: int = 240_000,
    max_fix_rounds: int = 2,
    # Vision gate (mandatory)
    vision_mode: Literal["auto", "on"] = "auto",
    vision_provider: str = "anthropic_vertex",
    vision_model: str = "claude-opus-4-5@20251101",
    vision_score_threshold: float = 8.0,
    vision_broken_min_confidence: float = 0.85,
    max_vision_fix_rounds: int = 1,
    # Mixed-quality / creativity refinement (requires vision screenshots)
    section_creativity_mode: Literal["off", "auto", "on"] = "auto",
    section_creativity_model: str | None = None,
    section_creativity_min_score: float = 0.7,
    section_creativity_min_confidence: float = 0.6,
    max_creativity_fix_rounds: int = 1,
    preview_command: str | None = None,
    preview_url: str | None = None,
    preview_wait_timeout_s: float = 30.0,
    viewports: list[dict[str, Any]] | None = None,
    # Apply winner
    allow_nonpassing_winner: bool = False,
    apply_to_repo: bool = False,
) -> dict[str, Any]:
    """Generate multiple patch candidates, run gates, (optionally) run vision, pick a winner.

    Notes:
    - The tool operates on a target git repo (`repo_path`), not on this Titan repo.
    - By default it does NOT apply changes; it returns a winner patch (git diff) you can apply.
    """
    repo_root_input = Path(repo_path).expanduser().resolve()
    if not repo_root_input.exists():
        raise FileNotFoundError(f"repo_path not found: {repo_root_input}")

    repo_root = await _git_root(repo_root_input) or repo_root_input
    head = await _git_head(repo_root)
    if head is None:
        raise RuntimeError("repo_path is not a git repo (git rev-parse HEAD failed).")

    run_id = uuid.uuid4().hex[:10]
    out_base = Path(os.getenv("TITAN_MCP_OUT_DIR", str(_THIS_REPO_ROOT / "out" / "mcp-code-runs")))
    run_dir = (out_base / f"code_{run_id}").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    context_files = _coerce_str_list(context_files)
    test_command_inferred = False
    test_command_inferred_reason: str | None = None
    if not test_command:
        test_command_inferred = True
        test_command, test_command_inferred_reason = await _infer_test_command(repo_root)

    if temperature_schedule is None or not temperature_schedule:
        temperature_schedule = [0.2, 0.5, 0.85, 1.0][: max(1, max_candidates)]
    else:
        temperature_schedule = [float(x) for x in temperature_schedule][: max(1, max_candidates)]
    if len(temperature_schedule) < max_candidates:
        temperature_schedule = temperature_schedule + [temperature_schedule[-1]] * (
            max_candidates - len(temperature_schedule)
        )

    if viewports is None or not viewports:
        viewports = [
            {"label": "mobile", "width": 375, "height": 812},
            {"label": "tablet", "width": 768, "height": 1024},
            {"label": "desktop", "width": 1440, "height": 900},
        ]

    candidate_concurrency_int = max(1, int(candidate_concurrency or 1))
    if candidate_concurrency_int > 32:
        raise ValueError("candidate_concurrency too high (max 32).")

    worktree_reuse_dirs = _coerce_str_list(worktree_reuse_dirs)
    if not worktree_reuse_dirs:
        # Safe default: if this is a Node repo and has node_modules, reuse it across worktrees.
        worktree_reuse_dirs = ["node_modules"]

    # === Stage 0: Planning (reasoner) ===
    plan_bundle: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None

    initial_context_blob = _build_context_blob(
        repo_root=repo_root,
        context_files=context_files,
        max_file_chars=int(context_max_file_chars or 12_000),
        max_total_chars=int(context_max_chars or 150_000),
    )

    if planning_mode == "off":
        plan_bundle = None
        plan = None
    elif planning_mode == "single":
        plan = await _call_llm_json(
            provider_name=planner_provider,
            model=planner_model,
            system_prompt=_CODE_REASONER_SAFE_SYSTEM,
            user_prompt=(
                "GOAL\n"
                f"{goal}\n\n"
                "REPO CONTEXT (selected files)\n"
                f"{initial_context_blob if initial_context_blob else '(none provided)'}\n\n"
                "Return JSON only.\n"
            ),
            temperature=0.35,
            max_tokens=int(planner_max_tokens or 3000),
        )
        plan_bundle = {"synthesized": plan}
    elif planning_mode == "megamind":
        bold_model = planner_bold_model or planner_model
        minimal_model = planner_minimal_model or planner_model
        safe_model = planner_safe_model or planner_model
        synth_model = planner_synth_model or planner_model
        plan_bundle = await _generate_plan_megamind(
            provider_name=planner_provider,
            model_bold=bold_model,
            model_minimal=minimal_model,
            model_safe=safe_model,
            model_synth=synth_model,
            goal=goal,
            context_blob=initial_context_blob,
            max_tokens=int(planner_max_tokens or 3000),
        )
        maybe = plan_bundle.get("synthesized")
        plan = maybe if isinstance(maybe, dict) else None
    else:
        raise ValueError("Invalid planning_mode. Use: off | single | megamind.")

    # Expand context_files based on plan.files_to_read (bounded).
    extra_files: list[str] = []
    if isinstance(plan, dict):
        extra_files = _extract_files_to_read(plan)
    context_files = _merge_unique(context_files + extra_files)[:30]

    # Optional: auto-expand context with repo search (helps when context_files are missing).
    auto_mode = str(auto_context_mode or "").strip().lower()
    auto_queries: list[str] = []
    if auto_mode == "goal":
        auto_queries = _derive_auto_context_queries(
            goal, max_queries=int(auto_context_max_queries or 8)
        )
    elif auto_mode == "queries":
        auto_queries = _coerce_str_list(auto_context_queries)
    elif auto_mode in ("off", ""):
        auto_queries = []
    else:
        raise ValueError("Invalid auto_context_mode. Use: off | goal | queries.")

    auto_added: list[str] = []
    if auto_queries and int(auto_context_max_files) > 0:
        before = set(context_files)
        auto_found = await _auto_context_files(
            repo_root=repo_root,
            queries=auto_queries[: max(1, int(auto_context_max_queries or 8))],
            max_files=int(auto_context_max_files),
        )
        context_files = _merge_unique(context_files + auto_found)[:30]
        auto_added = [p for p in context_files if p not in before]
        _write_text(
            run_dir / "auto_context.json",
            json.dumps(
                {
                    "mode": auto_mode,
                    "queries": auto_queries,
                    "added_files": auto_added,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )

    context_blob = _build_context_blob(
        repo_root=repo_root,
        context_files=context_files,
        max_file_chars=int(context_max_file_chars or 12_000),
        max_total_chars=int(context_max_chars or 150_000),
    )

    _write_text(
        run_dir / "request.json",
        json.dumps(
            {
                "repo_root": str(repo_root),
                "goal": goal,
                "auto_context_mode": auto_context_mode,
                "auto_context_queries": auto_context_queries,
                "auto_context_max_files": auto_context_max_files,
                "auto_context_max_queries": auto_context_max_queries,
                "context_max_chars": context_max_chars,
                "context_max_file_chars": context_max_file_chars,
                "planning_mode": planning_mode,
                "planner_provider": planner_provider,
                "planner_model": planner_model,
                "provider": provider,
                "model": model,
                "max_candidates": max_candidates,
                "candidate_concurrency": candidate_concurrency,
                "max_fix_rounds": max_fix_rounds,
                "temperature_schedule": temperature_schedule,
                "test_command": test_command,
                "test_command_inferred": test_command_inferred,
                "test_command_inferred_reason": test_command_inferred_reason,
                "lint_command": lint_command,
                "vision_mode": vision_mode,
                "vision_provider": vision_provider,
                "vision_model": vision_model,
                "vision_score_threshold": vision_score_threshold,
                "section_creativity_mode": section_creativity_mode,
                "section_creativity_model": section_creativity_model,
                "section_creativity_min_score": section_creativity_min_score,
                "section_creativity_min_confidence": section_creativity_min_confidence,
                "max_creativity_fix_rounds": max_creativity_fix_rounds,
                "preview_command": preview_command,
                "preview_url": preview_url,
                "context_files": context_files,
                "allow_nonpassing_winner": allow_nonpassing_winner,
                "worktree_reuse_dirs": worktree_reuse_dirs,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    if plan_bundle is not None:
        _write_text(run_dir / "plan_bundle.json", json.dumps(plan_bundle, indent=2, sort_keys=True) + "\n")
    if plan is not None:
        _write_text(run_dir / "plan.json", json.dumps(plan, indent=2, sort_keys=True) + "\n")

    preview_enabled = bool(preview_command) and bool(preview_url)

    vision_mode_key = str(vision_mode or "auto").strip().lower()
    if vision_mode_key not in ("auto", "on"):
        raise ValueError("vision_mode must be one of: auto | on")
    if vision_mode_key == "on" and not preview_enabled:
        raise ValueError("vision_mode='on' requires preview_command + preview_url")

    vision_kind: Literal["ui", "diff"] = "ui" if preview_enabled else "diff"
    vision_enabled = True  # mandatory

    section_creativity_enabled = section_creativity_mode == "on" or (
        section_creativity_mode == "auto" and preview_enabled
    )
    if section_creativity_mode == "on" and not preview_enabled:
        raise ValueError("section_creativity_mode='on' requires preview_command + preview_url")
    section_creativity_model_eff = section_creativity_model or vision_model

    worktree_lock = asyncio.Lock()
    port_start_base = int(os.getenv("TITAN_MCP_PORT_START", "3000"))
    concurrency = min(candidate_concurrency_int, int(max_candidates or 0)) if int(max_candidates or 0) > 0 else 0
    semaphore = asyncio.Semaphore(concurrency) if concurrency > 0 else None

    async def _run_candidate(idx: int) -> CandidateResult:
        if semaphore is not None:
            await semaphore.acquire()
        try:
            temp = float(temperature_schedule[idx])
            worktree = run_dir / "worktrees" / f"cand_{idx}"
            cand_dir = run_dir / "candidates" / f"{idx}"
            cand_dir.mkdir(parents=True, exist_ok=True)

            async with worktree_lock:
                ok_worktree = await _make_worktree(repo_root=repo_root, commit=head, dest=worktree)
            if not ok_worktree:
                _write_text(
                    cand_dir / "candidate_summary.json",
                    json.dumps(
                        {
                            "index": idx,
                            "candidate_dir": str(cand_dir),
                            "worktree": str(worktree),
                            "temperature": temp,
                            "ok": False,
                            "applied": False,
                            "test_ok": False,
                            "lint_ok": False,
                            "vision_ok": False,
                            "vision_score": None,
                            "adds": 0,
                            "deletes": 0,
                            "fix_rounds": 0,
                            "error": "git worktree add failed",
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                )
                return CandidateResult(
                    index=idx,
                    temperature=temp,
                    ok=False,
                    applied=False,
                    test_ok=False,
                    lint_ok=False,
                    vision_ok=False,
                    vision_score=None,
                    adds=0,
                    deletes=0,
                    fix_rounds=0,
                    patch="",
                    notes=[],
                    error="git worktree add failed",
                )

            candidate_error: str | None = None
            notes: list[str] = []
            patch_text = ""
            adds = 0
            deletes = 0
            applied_ok = False
            test_rc = 0
            lint_rc = 0
            fix_rounds = 0
            vision_ok = False
            vision_score: float | None = None
            creativity_avg: float | None = None
            creativity_min: float | None = None
            creativity_strong = 0
            creativity_weak = 0
            creativity_eval_ok = False

            try:
                reused = _maybe_symlink_reuse_dirs(
                    repo_root=repo_root, worktree=worktree, reuse_dirs=worktree_reuse_dirs
                )
                if reused:
                    _write_text(
                        cand_dir / "worktree_reuse_dirs.json",
                        json.dumps({"reused": reused}, indent=2, sort_keys=True) + "\n",
                    )

                plan_blob = ""
                if isinstance(plan, dict) and plan:
                    plan_blob = "PLAN (reasoner output)\n" + json.dumps(plan, indent=2, sort_keys=True) + "\n\n"

                user_prompt = (
                    "GOAL\n"
                    f"{goal}\n\n"
                    f"{plan_blob}"
                    "REPO CONTEXT (selected files)\n"
                    f"{context_blob if context_blob else '(none provided)'}\n\n"
                    "CONSTRAINTS\n"
                    f"- Repo root: {repo_root}\n"
                    f"- You must produce patches that make `{test_command}` pass.\n"
                    + (f"- Also make `{lint_command}` pass.\n" if lint_command else "")
                    + "- Keep changes minimal.\n"
                    + "- Output JSON only.\n"
                )

                data = await _call_llm_json(
                    provider_name=provider,
                    model=model,
                    system_prompt=_PATCH_GENERATOR_SYSTEM,
                    user_prompt=user_prompt,
                    temperature=temp,
                    max_tokens=int(max_tokens),
                )
                _write_text(cand_dir / "llm_response.json", json.dumps(data, indent=2, sort_keys=True) + "\n")

                raw_patches = data.get("patches") or []
                if not isinstance(raw_patches, list) or not raw_patches:
                    raise ValueError("Model returned no patches[]")
                raw_notes = data.get("notes") or []
                if isinstance(raw_notes, list):
                    notes = [str(x) for x in raw_notes if str(x).strip()][:8]

                applied_ok, touched_files = await _apply_patch_bundle(repo_root=worktree, patches=raw_patches)
                if not applied_ok:
                    raise ValueError("Failed to apply patch bundle")

                (test_rc, test_out, test_err), (lint_rc, lint_out, lint_err) = await _run_gates(
                    repo_root=worktree,
                    test_command=test_command,
                    lint_command=lint_command,
                    timeout_ms=int(gate_timeout_ms),
                )
                _write_gate_logs(
                    cand_dir=cand_dir,
                    test_out=test_out,
                    test_err=test_err,
                    lint_out=lint_out,
                    lint_err=lint_err,
                    label="g0",
                )

                # === Fix loop for deterministic failures ===
                while (test_rc != 0 or lint_rc != 0) and fix_rounds < int(max_fix_rounds):
                    fix_rounds += 1
                    failing_cmd = test_command if test_rc != 0 else (lint_command or "")
                    failing_out = test_out if test_rc != 0 else lint_out
                    failing_err = test_err if test_rc != 0 else lint_err

                    touched_blob = _build_context_blob(
                        repo_root=worktree,
                        context_files=touched_files,
                        max_file_chars=int(context_max_file_chars or 12_000),
                        max_total_chars=int(context_max_chars or 150_000),
                    )

                    fix_prompt = (
                        "GOAL\n"
                        f"{goal}\n\n"
                        f"{plan_blob}"
                        "FAILING COMMAND\n"
                        f"{failing_cmd}\n\n"
                        "STDOUT (tail)\n"
                        f"{_tail(failing_out, 6000)}\n\n"
                        "STDERR (tail)\n"
                        f"{_tail(failing_err, 6000)}\n\n"
                        "CURRENT FILES (edited so far)\n"
                        f"{touched_blob if touched_blob else '(none)'}\n"
                    )

                    fix_data = await _call_llm_json(
                        provider_name=provider,
                        model=model,
                        system_prompt=_PATCH_FIXER_SYSTEM,
                        user_prompt=fix_prompt,
                        temperature=max(0.1, temp - 0.2),
                        max_tokens=int(max_tokens),
                    )
                    _write_text(
                        cand_dir / f"llm_fix_response_{fix_rounds}.json",
                        json.dumps(fix_data, indent=2, sort_keys=True) + "\n",
                    )

                    fix_patches = fix_data.get("patches") or []
                    if not isinstance(fix_patches, list) or not fix_patches:
                        break
                    applied_ok, touched2 = await _apply_patch_bundle(repo_root=worktree, patches=fix_patches)
                    if not applied_ok:
                        break
                    touched_files = _merge_unique(touched_files + touched2)

                    (test_rc, test_out, test_err), (lint_rc, lint_out, lint_err) = await _run_gates(
                        repo_root=worktree,
                        test_command=test_command,
                        lint_command=lint_command,
                        timeout_ms=int(gate_timeout_ms),
                    )
                    _write_gate_logs(
                        cand_dir=cand_dir,
                        test_out=test_out,
                        test_err=test_err,
                        lint_out=lint_out,
                        lint_err=lint_err,
                        label=f"fix{fix_rounds}",
                    )

                test_ok = test_rc == 0
                lint_ok = lint_rc == 0
                if not (test_ok and lint_ok):
                    failing_cmd = test_command if test_rc != 0 else (lint_command or test_command)
                    failing_out = test_out if test_rc != 0 else lint_out
                    failing_err = test_err if test_rc != 0 else lint_err
                    raise RuntimeError(
                        "Deterministic gates failed.\n"
                        f"Failing command: {failing_cmd}\n\n"
                        f"STDOUT (tail):\n{_tail(failing_out)}\n\n"
                        f"STDERR (tail):\n{_tail(failing_err)}\n"
                    )

                # === Vision stage (mandatory) ===
                def _compute_vision_ok(report: dict[str, Any] | None) -> tuple[bool, float | None]:
                    if not isinstance(report, dict):
                        return False, None
                    broken_obj = report.get("broken") or {}
                    score_obj = report.get("score") or {}
                    broken_flag = bool(
                        getattr(broken_obj, "get", lambda _k, _d=None: False)("broken", False)
                    )
                    try:
                        score_val = float(getattr(score_obj, "get", lambda _k, _d=None: None)("score"))
                    except Exception:
                        score_val = None
                    ok_val = (not broken_flag) and (score_val is not None) and (
                        score_val >= float(vision_score_threshold)
                    )
                    return ok_val, score_val

                if vision_kind == "ui":
                    port = _pick_preview_port(idx=idx, port_start_base=port_start_base)
                    cmd = preview_command.format(port=port)
                    url = preview_url.format(port=port)

                    async def _run_preview_and_vision(*, iter_label: str) -> dict[str, Any]:
                        log_dir = cand_dir / "preview_logs"
                        log_dir.mkdir(parents=True, exist_ok=True)
                        stdout_path = log_dir / f"{iter_label}_stdout.txt"
                        stderr_path = log_dir / f"{iter_label}_stderr.txt"
                        tail_state: dict[str, str] = {"stdout": "", "stderr": ""}

                        async def _drain_stream_to_file(
                            stream: asyncio.StreamReader | None,
                            *,
                            out_path: Path,
                            key: str,
                            max_tail_chars: int = 8000,
                        ) -> None:
                            if stream is None:
                                return
                            try:
                                with open(out_path, "w", encoding="utf-8") as f:
                                    while True:
                                        chunk = await stream.readline()
                                        if not chunk:
                                            break
                                        text = chunk.decode(errors="replace")
                                        f.write(text)
                                        tail_state[key] = (tail_state[key] + text)[-max_tail_chars:]
                            except Exception:
                                return

                        report: dict[str, Any] | None = None
                        stdout_task: asyncio.Task[None] | None = None
                        stderr_task: asyncio.Task[None] | None = None
                        raised: BaseException | None = None

                        try:
                            async with managed_process(cmd, cwd=worktree) as _proc:
                                stdout_task = asyncio.create_task(
                                    _drain_stream_to_file(
                                        _proc.stdout, out_path=stdout_path, key="stdout"
                                    )
                                )
                                stderr_task = asyncio.create_task(
                                    _drain_stream_to_file(
                                        _proc.stderr, out_path=stderr_path, key="stderr"
                                    )
                                )

                                ok_http, err_http = await _wait_for_http(
                                    url, timeout_s=float(preview_wait_timeout_s)
                                )
                                if not ok_http:
                                    raise RuntimeError(
                                        "Preview server did not become ready.\n"
                                        f"HTTP wait error: {err_http}\n\n"
                                        f"STDOUT (tail):\n{tail_state['stdout']}\n\n"
                                        f"STDERR (tail):\n{tail_state['stderr']}\n"
                                    )

                                shots = await _capture_screenshots(
                                    url=url,
                                    out_dir=cand_dir / "screens" / iter_label,
                                    viewports=viewports,
                                    timeout_ms=30_000,
                                )
                                images = [p.read_bytes() for p in shots]
                                report = await _vision_eval(
                                    images=images,
                                    goal=goal,
                                    threshold=float(vision_score_threshold),
                                    provider_name=vision_provider,
                                    model=vision_model,
                                    min_confidence=float(vision_broken_min_confidence),
                                    kind="ui",
                                )
                        except BaseException as e:
                            raised = e
                        finally:
                            # Process is terminated by managed_process exit; now let drain tasks flush and finish.
                            for task in (stdout_task, stderr_task):
                                if task is None:
                                    continue
                                try:
                                    await asyncio.wait_for(task, timeout=1.5)
                                except asyncio.TimeoutError:
                                    task.cancel()
                                    await asyncio.gather(task, return_exceptions=True)

                        if raised is not None:
                            raise raised
                        if report is None:
                            raise RuntimeError("Vision eval failed to produce a report")
                        return report

                    last_iter_label = "v0"
                    vision_report = await _run_preview_and_vision(iter_label=last_iter_label)
                    _write_text(
                        cand_dir / "vision_report.json",
                        json.dumps(vision_report, indent=2, sort_keys=True) + "\n",
                    )
                    vision_ok, vision_score = _compute_vision_ok(vision_report)

                    # === Vision-driven fix loop (optional) ===
                    vision_fix_round = 0
                    while (not vision_ok) and vision_fix_round < int(max_vision_fix_rounds):
                        vision_fix_round += 1

                        touched_blob = _build_context_blob(
                            repo_root=worktree,
                            context_files=touched_files,
                            max_file_chars=int(context_max_file_chars or 12_000),
                            max_total_chars=int(context_max_chars or 150_000),
                        )
                        vision_fix_prompt = (
                            "GOAL\n"
                            f"{goal}\n\n"
                            f"{plan_blob}"
                            "VISION_REPORT (JSON)\n"
                            f"{json.dumps(vision_report, indent=2, sort_keys=True)}\n\n"
                            "CURRENT FILES (edited so far)\n"
                            f"{touched_blob if touched_blob else '(none)'}\n"
                        )

                        vision_fix_data = await _call_llm_json(
                            provider_name=provider,
                            model=model,
                            system_prompt=_VISION_FIXER_SYSTEM,
                            user_prompt=vision_fix_prompt,
                            temperature=max(0.1, min(0.6, temp)),
                            max_tokens=int(max_tokens),
                        )
                        _write_text(
                            cand_dir / f"llm_vision_fix_response_{vision_fix_round}.json",
                            json.dumps(vision_fix_data, indent=2, sort_keys=True) + "\n",
                        )

                        vision_fix_patches = vision_fix_data.get("patches") or []
                        if not isinstance(vision_fix_patches, list) or not vision_fix_patches:
                            break
                        applied_ok, touched2 = await _apply_patch_bundle(
                            repo_root=worktree, patches=vision_fix_patches
                        )
                        if not applied_ok:
                            break
                        touched_files = _merge_unique(touched_files + touched2)

                        # Re-run deterministic gates after UI changes (avoid regressions).
                        (test_rc, test_out, test_err), (lint_rc, lint_out, lint_err) = await _run_gates(
                            repo_root=worktree,
                            test_command=test_command,
                            lint_command=lint_command,
                            timeout_ms=int(gate_timeout_ms),
                        )
                        if test_rc != 0 or lint_rc != 0:
                            _write_gate_logs(
                                cand_dir=cand_dir,
                                test_out=test_out,
                                test_err=test_err,
                                lint_out=lint_out,
                                lint_err=lint_err,
                                label=f"vision{vision_fix_round}",
                            )
                            failing_cmd = test_command if test_rc != 0 else (lint_command or test_command)
                            failing_out = test_out if test_rc != 0 else lint_out
                            failing_err = test_err if test_rc != 0 else lint_err
                            raise RuntimeError(
                                "Deterministic gates failed after vision fix.\n"
                                f"Failing command: {failing_cmd}\n\n"
                                f"STDOUT (tail):\n{_tail(failing_out)}\n\n"
                                f"STDERR (tail):\n{_tail(failing_err)}\n"
                            )

                        # Re-run preview + vision.
                        last_iter_label = f"v{vision_fix_round}"
                        vision_report = await _run_preview_and_vision(iter_label=last_iter_label)
                        _write_text(
                            cand_dir / f"vision_report_v{vision_fix_round}.json",
                            json.dumps(vision_report, indent=2, sort_keys=True) + "\n",
                        )
                        vision_ok, vision_score = _compute_vision_ok(vision_report)

                    # === Mixed-quality / section creativity refinement (optional) ===
                    if vision_ok and section_creativity_enabled:
                        screens_dir = cand_dir / "screens" / last_iter_label
                        creativity_shot = _pick_best_screenshot_dir(screens_dir)

                        if creativity_shot is not None:
                            creativity_report: dict[str, Any] | None = None
                            try:
                                creativity_report = await _section_creativity_eval(
                                    image=creativity_shot.read_bytes(),
                                    provider_name=vision_provider,
                                    model=section_creativity_model_eff,
                                )
                                _write_text(
                                    cand_dir / f"section_creativity_report_{last_iter_label}.json",
                                    json.dumps(creativity_report, indent=2, sort_keys=True) + "\n",
                                )
                            except Exception as e:
                                _write_text(
                                    cand_dir / f"section_creativity_error_{last_iter_label}.txt",
                                    str(e) + "\n",
                                )
                                creativity_report = None
                            strong_labels, weak_labels, creativity_avg, creativity_min = (
                                _section_creativity_metrics(
                                    creativity_report,
                                    min_confidence=float(section_creativity_min_confidence),
                                    min_score=float(section_creativity_min_score),
                                )
                            )
                            creativity_strong = len(strong_labels)
                            creativity_weak = len(weak_labels)
                            creativity_eval_ok = creativity_avg is not None
                            _write_text(
                                cand_dir / f"section_creativity_summary_{last_iter_label}.json",
                                json.dumps(
                                    {
                                        "strong_sections": strong_labels,
                                        "weak_sections": weak_labels,
                                        "avg": creativity_avg,
                                        "min": creativity_min,
                                    },
                                    indent=2,
                                    sort_keys=True,
                                )
                                + "\n",
                            )

                            creativity_fix_round = 0
                            while (
                                strong_labels
                                and weak_labels
                                and creativity_fix_round < int(max_creativity_fix_rounds)
                            ):
                                creativity_fix_round += 1

                                touched_blob = _build_context_blob(
                                    repo_root=worktree,
                                    context_files=touched_files,
                                    max_file_chars=int(context_max_file_chars or 12_000),
                                    max_total_chars=int(context_max_chars or 150_000),
                                )

                                creativity_fix_prompt = (
                                    "GOAL\n"
                                    f"{goal}\n\n"
                                    f"{plan_blob}"
                                    "SECTION_CREATIVITY_REPORT (JSON)\n"
                                    f"{json.dumps(creativity_report, indent=2, sort_keys=True)}\n\n"
                                    "STRONG_SECTIONS (do NOT edit)\n"
                                    f"{', '.join(strong_labels)}\n\n"
                                    "WEAK_SECTIONS (edit ONLY these)\n"
                                    f"{', '.join(weak_labels)}\n\n"
                                    "CURRENT FILES (edited so far)\n"
                                    f"{touched_blob if touched_blob else '(none)'}\n"
                                )

                                creativity_fix_data = await _call_llm_json(
                                    provider_name=provider,
                                    model=model,
                                    system_prompt=_CREATIVITY_REFINER_SYSTEM,
                                    user_prompt=creativity_fix_prompt,
                                    temperature=max(0.1, min(0.6, temp)),
                                    max_tokens=int(max_tokens),
                                )
                                _write_text(
                                    cand_dir / f"llm_creativity_fix_response_{creativity_fix_round}.json",
                                    json.dumps(creativity_fix_data, indent=2, sort_keys=True) + "\n",
                                )

                                creativity_fix_patches = creativity_fix_data.get("patches") or []
                                if not isinstance(creativity_fix_patches, list) or not creativity_fix_patches:
                                    break
                                applied_ok, touched2 = await _apply_patch_bundle(
                                    repo_root=worktree, patches=creativity_fix_patches
                                )
                                if not applied_ok:
                                    break
                                touched_files = _merge_unique(touched_files + touched2)

                                (test_rc, test_out, test_err), (lint_rc, lint_out, lint_err) = (
                                    await _run_gates(
                                        repo_root=worktree,
                                        test_command=test_command,
                                        lint_command=lint_command,
                                        timeout_ms=int(gate_timeout_ms),
                                    )
                                )
                                if test_rc != 0 or lint_rc != 0:
                                    _write_gate_logs(
                                        cand_dir=cand_dir,
                                        test_out=test_out,
                                        test_err=test_err,
                                        lint_out=lint_out,
                                        lint_err=lint_err,
                                        label=f"creative{creativity_fix_round}",
                                    )
                                    failing_cmd = (
                                        test_command if test_rc != 0 else (lint_command or test_command)
                                    )
                                    failing_out = test_out if test_rc != 0 else lint_out
                                    failing_err = test_err if test_rc != 0 else lint_err
                                    raise RuntimeError(
                                        "Deterministic gates failed after creativity fix.\n"
                                        f"Failing command: {failing_cmd}\n\n"
                                        f"STDOUT (tail):\n{_tail(failing_out)}\n\n"
                                        f"STDERR (tail):\n{_tail(failing_err)}\n"
                                    )

                                last_iter_label = f"c{creativity_fix_round}"
                                vision_report = await _run_preview_and_vision(iter_label=last_iter_label)
                                _write_text(
                                    cand_dir / f"vision_report_{last_iter_label}.json",
                                    json.dumps(vision_report, indent=2, sort_keys=True) + "\n",
                                )
                                vision_ok, vision_score = _compute_vision_ok(vision_report)
                                if not vision_ok:
                                    raise RuntimeError(
                                        "Vision gate failed after creativity refinement"
                                    )

                                screens_dir = cand_dir / "screens" / last_iter_label
                                creativity_shot = _pick_best_screenshot_dir(screens_dir)
                                if creativity_shot is None:
                                    break

                                creativity_report = None
                                try:
                                    creativity_report = await _section_creativity_eval(
                                        image=creativity_shot.read_bytes(),
                                        provider_name=vision_provider,
                                        model=section_creativity_model_eff,
                                    )
                                    _write_text(
                                        cand_dir / f"section_creativity_report_{last_iter_label}.json",
                                        json.dumps(creativity_report, indent=2, sort_keys=True) + "\n",
                                    )
                                except Exception as e:
                                    _write_text(
                                        cand_dir / f"section_creativity_error_{last_iter_label}.txt",
                                        str(e) + "\n",
                                    )
                                    creativity_report = None
                                strong_labels, weak_labels, creativity_avg, creativity_min = (
                                    _section_creativity_metrics(
                                        creativity_report,
                                        min_confidence=float(section_creativity_min_confidence),
                                        min_score=float(section_creativity_min_score),
                                    )
                                )
                                creativity_strong = len(strong_labels)
                                creativity_weak = len(weak_labels)
                                creativity_eval_ok = creativity_avg is not None

                                _write_text(
                                    cand_dir / f"section_creativity_summary_{last_iter_label}.json",
                                    json.dumps(
                                        {
                                            "strong_sections": strong_labels,
                                            "weak_sections": weak_labels,
                                            "avg": creativity_avg,
                                            "min": creativity_min,
                                        },
                                        indent=2,
                                        sort_keys=True,
                                    )
                                    + "\n",
                                )
                else:
                    # Diff-mode vision: screenshot the git diff and score it.
                    rc_diff, diff_out, diff_err = await run_command(
                        "git diff --no-color", cwd=worktree, timeout_ms=60_000
                    )
                    if rc_diff != 0:
                        raise RuntimeError(f"git diff failed: {diff_err.strip()}")
                    diff_for_vision = diff_out or ""
                    if not diff_for_vision.strip():
                        raise ValueError("Patch applied but produced no git diff changes")

                    diff_screens_dir = cand_dir / "screens" / "diff"
                    shots = await _capture_diff_screenshots(
                        diff_text=diff_for_vision,
                        out_dir=diff_screens_dir,
                        timeout_ms=30_000,
                    )
                    images = [p.read_bytes() for p in shots]
                    vision_report = await _vision_eval(
                        images=images,
                        goal=goal,
                        threshold=float(vision_score_threshold),
                        provider_name=vision_provider,
                        model=vision_model,
                        min_confidence=float(vision_broken_min_confidence),
                        kind="diff",
                    )
                    _write_text(
                        cand_dir / "vision_report_diff.json",
                        json.dumps(vision_report, indent=2, sort_keys=True) + "\n",
                    )
                    vision_ok, vision_score = _compute_vision_ok(vision_report)
                    if not vision_ok:
                        raise RuntimeError("Vision gate failed (diff mode)")

                # Compute final patch AFTER all fix loops (including vision-driven fixes).
                rc, diff_out, diff_err = await run_command(
                    "git diff --no-color", cwd=worktree, timeout_ms=60_000
                )
                if rc != 0:
                    raise RuntimeError(f"git diff failed: {diff_err.strip()}")
                patch_text = diff_out or ""
                if not patch_text.strip():
                    raise ValueError("Patch applied but produced no git diff changes")
                _write_text(cand_dir / "git_diff.patch", patch_text)
                adds, deletes = _count_patch_deltas(patch_text)

            except Exception as e:
                candidate_error = str(e)
                _write_text(cand_dir / "error.txt", (candidate_error or "unknown error") + "\n")
                _write_text(cand_dir / "traceback.txt", traceback.format_exc() + "\n")

            finally:
                # Persist a machine-readable summary even if the candidate failed.
                _write_text(
                    cand_dir / "candidate_summary.json",
                    json.dumps(
                        {
                            "index": idx,
                            "candidate_dir": str(cand_dir),
                            "worktree": str(worktree),
                            "temperature": temp,
                            "ok": bool(candidate_error is None),
                            "applied": bool(applied_ok),
                            "test_ok": bool(test_rc == 0),
                            "lint_ok": bool(lint_rc == 0),
                            "vision_ok": bool(vision_ok),
                            "vision_score": vision_score,
                            "creativity_avg": creativity_avg,
                            "creativity_min": creativity_min,
                            "creativity_strong": int(creativity_strong),
                            "creativity_weak": int(creativity_weak),
                            "creativity_eval_ok": bool(creativity_eval_ok),
                            "adds": int(adds),
                            "deletes": int(deletes),
                            "fix_rounds": int(fix_rounds),
                            "git_diff_patch_file": str(cand_dir / "git_diff.patch"),
                            "error": candidate_error,
                            "error_file": str(cand_dir / "error.txt")
                            if (cand_dir / "error.txt").exists()
                            else None,
                            "traceback_file": str(cand_dir / "traceback.txt")
                            if (cand_dir / "traceback.txt").exists()
                            else None,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                )
                keep = bool(
                    os.getenv("TITAN_MCP_KEEP_WORKTREES", "0").strip() in ("1", "true", "yes")
                )
                if not keep:
                    async with worktree_lock:
                        await _remove_worktree(repo_root=repo_root, dest=worktree)

            ok = candidate_error is None
            return CandidateResult(
                index=idx,
                temperature=temp,
                ok=ok,
                applied=applied_ok,
                test_ok=(test_rc == 0),
                lint_ok=(lint_rc == 0),
                vision_ok=vision_ok,
                vision_score=vision_score,
                creativity_avg=creativity_avg,
                creativity_min=creativity_min,
                creativity_strong=int(creativity_strong),
                creativity_weak=int(creativity_weak),
                creativity_eval_ok=bool(creativity_eval_ok),
                adds=adds,
                deletes=deletes,
                fix_rounds=fix_rounds,
                patch=patch_text,
                notes=notes,
                error=candidate_error,
            )
        finally:
            if semaphore is not None:
                semaphore.release()

    results: list[CandidateResult] = []
    if int(max_candidates) > 0:
        results = await asyncio.gather(*[_run_candidate(i) for i in range(int(max_candidates))])
        results = sorted(results, key=lambda c: c.index)

    winner = _select_winner(results, allow_best_effort=bool(allow_nonpassing_winner))

    applied = False
    apply_error: str | None = None
    apply_skipped_reason: str | None = None
    tests_were_skipped = bool(test_command_inferred and str(test_command).strip() == "true")
    winner_passes_all = bool(
        winner
        and winner.ok
        and winner.applied
        and winner.test_ok
        and winner.lint_ok
        and winner.vision_ok
    )
    if apply_to_repo and winner and winner.patch.strip():
        if tests_were_skipped:
            apply_skipped_reason = (
                "Refusing to apply winner patch automatically because no real test command was run "
                "(test_command was inferred as 'true'). Provide an explicit test_command (or ensure a test runner is "
                "detectable) to enable apply_to_repo."
            )
            _write_text(run_dir / "apply_skipped.txt", apply_skipped_reason + "\n")
        elif not winner_passes_all:
            apply_skipped_reason = (
                "Refusing to apply winner patch because winner does not pass all enabled gates. "
                "Set apply_to_repo=false and apply manually if you still want it."
            )
            _write_text(run_dir / "apply_skipped.txt", apply_skipped_reason + "\n")
        else:
            patch_file = run_dir / "winner.patch"
            _write_text(patch_file, winner.patch)
            rc, _, err = await run_command(
                f"git apply --whitespace=nowarn {_shlex_quote(str(patch_file))}",
                cwd=repo_root,
                timeout_ms=60_000,
            )
            applied = rc == 0
            if not applied:
                apply_error = err
                _write_text(run_dir / "apply_error.txt", err)

    # Persist a machine-readable summary of the run (without inlining large patches).
    _write_text(
        run_dir / "run_summary.json",
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "repo_root": str(repo_root),
                "planning_mode": planning_mode,
                "test_command": test_command,
                "test_command_inferred": test_command_inferred,
                "test_command_inferred_reason": test_command_inferred_reason,
                "tests_were_skipped": tests_were_skipped,
                "lint_command": lint_command,
                "winner": None
                if not winner
                else {
                    "index": winner.index,
                    "candidate_dir": str(run_dir / "candidates" / str(winner.index)),
                    "passes_all_gates": bool(winner_passes_all),
                },
                "winner_passes_all": winner_passes_all if winner else None,
                "applied_to_repo": applied,
                "apply_error": apply_error,
                "apply_skipped_reason": apply_skipped_reason,
                "candidates": [
                    {
                        "index": c.index,
                        "candidate_dir": str(run_dir / "candidates" / str(c.index)),
                        "ok": c.ok,
                        "applied": c.applied,
                        "test_ok": c.test_ok,
                        "lint_ok": c.lint_ok,
                        "vision_ok": c.vision_ok,
                        "vision_score": c.vision_score,
                        "creativity_avg": c.creativity_avg,
                        "creativity_min": c.creativity_min,
                        "creativity_strong": c.creativity_strong,
                        "creativity_weak": c.creativity_weak,
                        "creativity_eval_ok": c.creativity_eval_ok,
                        "adds": c.adds,
                        "deletes": c.deletes,
                        "fix_rounds": c.fix_rounds,
                        "error": c.error,
                    }
                    for c in results
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )

    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "repo_root": str(repo_root),
        "planning_mode": planning_mode,
        "plan": plan,
        "test_command": test_command,
        "test_command_inferred": test_command_inferred,
        "test_command_inferred_reason": test_command_inferred_reason,
        "lint_command": lint_command,
        "winner_passes_all": winner_passes_all if winner else None,
        "winner": None
        if not winner
        else {
            "index": winner.index,
            "candidate_dir": str(run_dir / "candidates" / str(winner.index)),
            "passes_all_gates": winner_passes_all,
            "temperature": winner.temperature,
            "test_ok": winner.test_ok,
            "lint_ok": winner.lint_ok,
            "vision_ok": winner.vision_ok,
            "vision_score": winner.vision_score,
            "creativity_avg": winner.creativity_avg,
            "creativity_min": winner.creativity_min,
            "creativity_strong": winner.creativity_strong,
            "creativity_weak": winner.creativity_weak,
            "creativity_eval_ok": winner.creativity_eval_ok,
            "adds": winner.adds,
            "deletes": winner.deletes,
            "fix_rounds": winner.fix_rounds,
            "patch": winner.patch,
            "error": winner.error,
        },
        "applied_to_repo": applied,
        "apply_error": apply_error,
        "apply_skipped_reason": apply_skipped_reason,
        "candidates": [
            {
                "index": c.index,
                "candidate_dir": str(run_dir / "candidates" / str(c.index)),
                "temperature": c.temperature,
                "ok": c.ok,
                "applied": c.applied,
                "test_ok": c.test_ok,
                "lint_ok": c.lint_ok,
                "vision_ok": c.vision_ok,
                "vision_score": c.vision_score,
                "creativity_avg": c.creativity_avg,
                "creativity_min": c.creativity_min,
                "creativity_strong": c.creativity_strong,
                "creativity_weak": c.creativity_weak,
                "creativity_eval_ok": c.creativity_eval_ok,
                "adds": c.adds,
                "deletes": c.deletes,
                "fix_rounds": c.fix_rounds,
                "notes": c.notes,
                "error": c.error,
            }
            for c in results
        ],
    }


async def _titan_code_eval_patch_impl(
    repo_path: str,
    patches: list[dict[str, str]],
    *,
    goal: str | None = None,
    test_command: str | None = None,
    lint_command: str | None = None,
    gate_timeout_ms: int = 240_000,
    worktree_reuse_dirs: list[str] | None = None,
    # Vision gate (mandatory)
    vision_mode: Literal["auto", "on"] = "auto",
    vision_provider: str = "client",
    vision_model: str = "gemini-2.0-flash",
    vision_score_threshold: float = 8.0,
    vision_broken_min_confidence: float = 0.85,
    preview_command: str | None = None,
    preview_url: str | None = None,
    preview_wait_timeout_s: float = 30.0,
    viewports: list[dict[str, Any]] | None = None,
    # Output / behavior
    keep_worktree: bool = False,
) -> dict[str, Any]:
    """Evaluate a patch bundle against deterministic gates (+ optional vision).

    This is the "primitive" tool for agent-orchestrated workflows:
    Claude Code (and its subagents) can propose patches, then call this tool to
    score/validate them in isolated git worktrees.
    """
    repo_root_input = Path(repo_path).expanduser().resolve()
    if not repo_root_input.exists():
        raise FileNotFoundError(f"repo_path not found: {repo_root_input}")

    repo_root = await _git_root(repo_root_input) or repo_root_input
    head = await _git_head(repo_root)
    if head is None:
        raise RuntimeError("repo_path is not a git repo (git rev-parse HEAD failed).")

    if not isinstance(patches, list) or not patches:
        raise ValueError("patches must be a non-empty list of {path, patch} objects.")

    run_id = uuid.uuid4().hex[:10]
    out_base = Path(os.getenv("TITAN_MCP_OUT_DIR", str(_THIS_REPO_ROOT / "out" / "mcp-eval-runs")))
    run_dir = (out_base / f"eval_{run_id}").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    worktree = run_dir / "worktree"
    cand_dir = run_dir / "candidates" / "0"
    cand_dir.mkdir(parents=True, exist_ok=True)

    # Default gates: prefer inference like titan_code_solve.
    test_command_inferred = False
    test_command_inferred_reason: str | None = None
    if not test_command:
        test_command_inferred = True
        test_command, test_command_inferred_reason = await _infer_test_command(repo_root)

    if viewports is None or not viewports:
        viewports = [
            {"label": "mobile", "width": 375, "height": 812},
            {"label": "tablet", "width": 768, "height": 1024},
            {"label": "desktop", "width": 1440, "height": 900},
        ]

    worktree_reuse_dirs = _coerce_str_list(worktree_reuse_dirs)
    if not worktree_reuse_dirs:
        worktree_reuse_dirs = ["node_modules"]

    goal_text = str(goal or "").strip()

    preview_enabled = bool(preview_command) and bool(preview_url)

    vision_mode_key = str(vision_mode or "auto").strip().lower()
    if vision_mode_key not in ("auto", "on"):
        raise ValueError("vision_mode must be one of: auto | on")
    if vision_mode_key == "on" and not preview_enabled:
        raise ValueError("vision_mode='on' requires preview_command + preview_url")

    vision_kind: Literal["ui", "diff"] = "ui" if preview_enabled else "diff"
    vision_enabled = True  # mandatory

    port_start_base = int(os.getenv("TITAN_MCP_PORT_START", "3000"))
    worktree_lock = asyncio.Lock()

    candidate_error: str | None = None
    applied_ok = False
    test_rc = 0
    lint_rc = 0
    test_out = ""
    test_err = ""
    lint_out = ""
    lint_err = ""
    vision_ok = False
    vision_ok_reason: str | None = None
    vision_score: float | None = None
    vision_scored = False
    screenshot_files: list[str] = []
    patch_text = ""
    adds = 0
    deletes = 0

    try:
        async with worktree_lock:
            ok_worktree = await _make_worktree(repo_root=repo_root, commit=head, dest=worktree)
        if not ok_worktree:
            raise RuntimeError("git worktree add failed")

        reused = _maybe_symlink_reuse_dirs(
            repo_root=repo_root, worktree=worktree, reuse_dirs=worktree_reuse_dirs
        )
        if reused:
            _write_text(
                cand_dir / "worktree_reuse_dirs.json",
                json.dumps({"reused": reused}, indent=2, sort_keys=True) + "\n",
            )

        # Apply patch bundle.
        applied_ok, touched_files = await _apply_patch_bundle(repo_root=worktree, patches=patches)
        if not applied_ok:
            raise RuntimeError("Failed to apply patch bundle")

        # Deterministic gates.
        (test_rc, test_out, test_err), (lint_rc, lint_out, lint_err) = await _run_gates(
            repo_root=worktree,
            test_command=test_command,
            lint_command=lint_command,
            timeout_ms=int(gate_timeout_ms),
        )
        _write_gate_logs(
            cand_dir=cand_dir,
            test_out=test_out,
            test_err=test_err,
            lint_out=lint_out,
            lint_err=lint_err,
            label="gate0",
        )

        # Optional vision gate.
        def _compute_vision_ok(report: dict[str, Any] | None) -> tuple[bool, float | None]:
            if not isinstance(report, dict):
                return False, None
            broken_obj = report.get("broken") or {}
            score_obj = report.get("score") or {}
            broken_flag = bool(getattr(broken_obj, "get", lambda _k, _d=None: False)("broken", False))
            try:
                score_val = float(getattr(score_obj, "get", lambda _k, _d=None: None)("score"))
            except Exception:
                score_val = None
            ok_val = (not broken_flag) and (score_val is not None) and (
                score_val >= float(vision_score_threshold)
            )
            return ok_val, score_val

        vision_provider_key = str(vision_provider or "").strip().lower()
        vision_is_client = vision_provider_key in ("client", "claude", "claude_client")
        vision_model_effective = "client" if vision_is_client else str(vision_model)

        if vision_kind == "ui":
            port = _pick_preview_port(idx=0, port_start_base=port_start_base)
            cmd = preview_command.format(port=port)
            url = preview_url.format(port=port)

            async def _run_preview_and_vision(*, iter_label: str) -> dict[str, Any]:
                nonlocal screenshot_files
                log_dir = cand_dir / "preview_logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                stdout_path = log_dir / f"{iter_label}_stdout.txt"
                stderr_path = log_dir / f"{iter_label}_stderr.txt"
                tail_state: dict[str, str] = {"stdout": "", "stderr": ""}

                async def _drain_stream_to_file(
                    stream: asyncio.StreamReader | None,
                    *,
                    out_path: Path,
                    key: str,
                    max_tail_chars: int = 8000,
                ) -> None:
                    if stream is None:
                        return
                    try:
                        with open(out_path, "w", encoding="utf-8") as f:
                            while True:
                                chunk = await stream.readline()
                                if not chunk:
                                    break
                                text = chunk.decode(errors="replace")
                                f.write(text)
                                tail_state[key] = (tail_state[key] + text)[-max_tail_chars:]
                    except Exception:
                        return

                report: dict[str, Any] | None = None
                stdout_task: asyncio.Task[None] | None = None
                stderr_task: asyncio.Task[None] | None = None
                raised: BaseException | None = None

                try:
                    async with managed_process(cmd, cwd=worktree) as _proc:
                        stdout_task = asyncio.create_task(
                            _drain_stream_to_file(_proc.stdout, out_path=stdout_path, key="stdout")
                        )
                        stderr_task = asyncio.create_task(
                            _drain_stream_to_file(_proc.stderr, out_path=stderr_path, key="stderr")
                        )

                        ok_http, err_http = await _wait_for_http(
                            url, timeout_s=float(preview_wait_timeout_s)
                        )
                        if not ok_http:
                            raise RuntimeError(
                                "Preview server did not become ready.\n"
                                f"HTTP wait error: {err_http}\n\n"
                                f"STDOUT (tail):\n{tail_state['stdout']}\n\n"
                                f"STDERR (tail):\n{tail_state['stderr']}\n"
                            )

                        shots = await _capture_screenshots(
                            url=url,
                            out_dir=cand_dir / "screens" / iter_label,
                            viewports=viewports,
                            timeout_ms=30_000,
                        )
                        screenshot_files = [str(p) for p in shots]

                        if vision_is_client:
                            report = {
                                "mode": "client",
                                "kind": "ui",
                                "screenshots": screenshot_files,
                                "note": "Client-side vision: MCP captured screenshots; the calling model should score them.",
                            }
                        else:
                            images = [p.read_bytes() for p in shots]
                            report = await _vision_eval(
                                images=images,
                                goal=(f"{repo_root.name}: {goal_text}" if goal_text else repo_root.name),
                                threshold=float(vision_score_threshold),
                                provider_name=vision_provider,
                                model=vision_model,
                                min_confidence=float(vision_broken_min_confidence),
                                kind="ui",
                            )
                except BaseException as e:
                    raised = e
                finally:
                    for task in (stdout_task, stderr_task):
                        if task is None:
                            continue
                        try:
                            await asyncio.wait_for(task, timeout=1.5)
                        except asyncio.TimeoutError:
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)

                if raised is not None:
                    raise raised
                if report is None:
                    raise RuntimeError("Vision eval failed to produce a report")
                return report

            vision_report = await _run_preview_and_vision(iter_label="v0")
            _write_text(
                cand_dir / "vision_report.json",
                json.dumps(vision_report, indent=2, sort_keys=True) + "\n",
            )

            if vision_is_client:
                vision_scored = False
                vision_ok = True
                vision_ok_reason = "client_unscored"
                vision_score = None
            else:
                vision_scored = True
                vision_ok, vision_score = _compute_vision_ok(vision_report)
        else:
            # Diff-mode vision: screenshot the git diff and score it.
            rc_diff, diff_out, diff_err = await run_command(
                "git diff --no-color", cwd=worktree, timeout_ms=60_000
            )
            if rc_diff != 0:
                raise RuntimeError(f"git diff failed: {diff_err.strip()}")
            diff_for_vision = diff_out or ""
            if not diff_for_vision.strip():
                raise ValueError("Patch applied but produced no git diff changes")

            diff_screens_dir = cand_dir / "screens" / "diff"
            shots = await _capture_diff_screenshots(
                diff_text=diff_for_vision,
                out_dir=diff_screens_dir,
                timeout_ms=30_000,
            )
            screenshot_files = [str(p) for p in shots]

            if vision_is_client:
                vision_scored = False
                vision_ok = True
                vision_ok_reason = "client_unscored"
                vision_score = None
                vision_report = {
                    "mode": "client",
                    "kind": "diff",
                    "screenshots": screenshot_files,
                    "note": "Client-side vision: MCP captured diff screenshots; the calling model should score them.",
                }
            else:
                images = [p.read_bytes() for p in shots]
                vision_report = await _vision_eval(
                    images=images,
                    goal=(f"{repo_root.name}: {goal_text}" if goal_text else repo_root.name),
                    threshold=float(vision_score_threshold),
                    provider_name=vision_provider,
                    model=vision_model,
                    min_confidence=float(vision_broken_min_confidence),
                    kind="diff",
                )
            _write_text(
                cand_dir / "vision_report_diff.json",
                json.dumps(vision_report, indent=2, sort_keys=True) + "\n",
            )
            if not vision_is_client:
                vision_scored = True
                vision_ok, vision_score = _compute_vision_ok(vision_report)

        # Compute git diff.
        rc, diff_out, diff_err = await run_command("git diff --no-color", cwd=worktree, timeout_ms=60_000)
        if rc != 0:
            raise RuntimeError(f"git diff failed: {diff_err.strip()}")
        patch_text = diff_out or ""
        if not patch_text.strip():
            raise ValueError("Patch applied but produced no git diff changes")
        _write_text(cand_dir / "git_diff.patch", patch_text)
        adds, deletes = _count_patch_deltas(patch_text)

    except Exception as e:
        candidate_error = str(e)
        _write_text(cand_dir / "error.txt", (candidate_error or "unknown error") + "\n")
        _write_text(cand_dir / "traceback.txt", traceback.format_exc() + "\n")

    finally:
        _write_text(
            cand_dir / "candidate_summary.json",
            json.dumps(
                {
                    "index": 0,
                    "candidate_dir": str(cand_dir),
                    "worktree": str(worktree),
                    "ok": bool(candidate_error is None),
                    "applied": bool(applied_ok),
                    "test_ok": bool(test_rc == 0),
                    "lint_ok": bool(lint_rc == 0),
                    "vision_scored": bool(vision_scored),
                    "vision_ok": bool(vision_ok),
                    "vision_ok_reason": vision_ok_reason,
                    "vision_score": vision_score,
                    "screenshot_files": screenshot_files,
                    "adds": int(adds),
                    "deletes": int(deletes),
                    "git_diff_patch_file": str(cand_dir / "git_diff.patch"),
                    "error": candidate_error,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )

        if not keep_worktree:
            async with worktree_lock:
                await _remove_worktree(repo_root=repo_root, dest=worktree)

    passes_all = bool(candidate_error is None and applied_ok and (test_rc == 0) and (lint_rc == 0) and vision_ok)
    _write_text(
        run_dir / "run_summary.json",
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "repo_root": str(repo_root),
                "test_command": test_command,
                "test_command_inferred": test_command_inferred,
                "test_command_inferred_reason": test_command_inferred_reason,
                "lint_command": lint_command,
                "vision_provider": vision_provider,
                    "vision_model": vision_model_effective,
                "vision_kind": vision_kind,
                "vision_scored": bool(vision_scored),
                "passes_all_gates": passes_all,
                "candidate": {
                    "index": 0,
                    "candidate_dir": str(cand_dir),
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )

    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "repo_root": str(repo_root),
        "candidate_dir": str(cand_dir),
        "passes_all_gates": passes_all,
        "passes_all_gates_includes_vision": bool(vision_scored),
        "test_command": test_command,
        "test_command_inferred": test_command_inferred,
        "test_command_inferred_reason": test_command_inferred_reason,
        "lint_command": lint_command,
        "vision_provider": vision_provider,
        "vision_model": vision_model_effective,
        "vision_kind": vision_kind,
        "vision_scored": bool(vision_scored),
        "vision_ok": vision_ok,
        "vision_ok_reason": vision_ok_reason,
        "vision_score": vision_score,
        "screenshot_files": screenshot_files,
        "adds": adds,
        "deletes": deletes,
        "patch": patch_text,
        "error": candidate_error,
    }


@mcp.tool()
async def titan_code_eval_patch(
    repo_path: str,
    patches: list[dict[str, str]],
    *,
    goal: str | None = None,
    test_command: str | None = None,
    lint_command: str | None = None,
    gate_timeout_ms: int = 240_000,
    worktree_reuse_dirs: list[str] | None = None,
    # Vision gate (mandatory)
    vision_mode: Literal["auto", "on"] = "auto",
    vision_provider: str = "client",
    vision_model: str = "gemini-2.0-flash",
    vision_score_threshold: float = 8.0,
    vision_broken_min_confidence: float = 0.85,
    preview_command: str | None = None,
    preview_url: str | None = None,
    preview_wait_timeout_s: float = 30.0,
    viewports: list[dict[str, Any]] | None = None,
    # Output / behavior
    keep_worktree: bool = False,
    include_images: bool = True,
    include_vision_instructions: bool = True,
) -> list[ContentBlock]:
    """MCP tool wrapper for `_titan_code_eval_patch_impl`.

    Returns:
    - JSON summary (TextContent)
    - Optional vision instructions (TextContent) when vision_provider=client
    - Optional screenshots as ImageContent (base64) so Claude can use built-in vision
    """
    result = await _titan_code_eval_patch_impl(
        repo_path=repo_path,
        patches=patches,
        goal=goal,
        test_command=test_command,
        lint_command=lint_command,
        gate_timeout_ms=gate_timeout_ms,
        worktree_reuse_dirs=worktree_reuse_dirs,
        vision_mode=vision_mode,
        vision_provider=vision_provider,
        vision_model=vision_model,
        vision_score_threshold=vision_score_threshold,
        vision_broken_min_confidence=vision_broken_min_confidence,
        preview_command=preview_command,
        preview_url=preview_url,
        preview_wait_timeout_s=preview_wait_timeout_s,
        viewports=viewports,
        keep_worktree=keep_worktree,
    )

    blocks: list[ContentBlock] = []

    if include_vision_instructions and not bool(result.get("vision_scored")):
        kind = str(result.get("vision_kind") or "diff").strip().lower()
        kind_lit: Literal["ui", "diff"] = "ui" if kind == "ui" else "diff"
        goal_for_vision = str(goal or "").strip()
        if not goal_for_vision:
            goal_for_vision = "(no explicit goal provided)"
        blocks.append(
            TextContent(
                type="text",
                text=_client_vision_instructions(
                    kind=kind_lit,
                    goal=goal_for_vision,
                    threshold=float(vision_score_threshold),
                    min_confidence=float(vision_broken_min_confidence),
                ),
            )
        )

    # Always return the machine-readable summary.
    blocks.append(TextContent(type="text", text=json.dumps(result, indent=2, sort_keys=True)))

    if include_images:
        for raw in result.get("screenshot_files") or []:
            p = Path(str(raw))
            img = _image_content_from_path(p)
            if img is not None:
                blocks.append(img)

    return blocks


# Tool name aliases for naming alignment / ergonomics (keeps backwards compatibility).
mcp.tool(name="petamind_solve", description="Alias for titan_code_solve")(titan_code_solve)
mcp.tool(name="petamind_eval_patch", description="Alias for titan_code_eval_patch")(titan_code_eval_patch)
mcp.tool(name="terra_mind_solve", description="Alias for titan_code_solve (legacy)")(titan_code_solve)
mcp.tool(name="terra_mind_eval_patch", description="Alias for titan_code_eval_patch (legacy)")(
    titan_code_eval_patch
)


def main() -> None:
    # MCP stdio transports require clean stdout; keep third-party request logging off by default.
    import logging
    import sys

    # Rich console logging (titan_factory.utils.*) must also avoid stdout.
    from titan_factory.utils import ensure_console_to_stderr

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.WARNING)
    ensure_console_to_stderr()
    mcp.run()


if __name__ == "__main__":
    main()
