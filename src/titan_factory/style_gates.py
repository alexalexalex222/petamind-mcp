"""Deterministic style gates (cheap, static heuristics).

These gates are meant to prevent a recurring failure mode:
"blank / generic / monoculture pages pass everything, but category-appropriate pages get nitpicked."

Unlike axe/Lighthouse, these are *aesthetic routing compliance* checks:
- Does the code drift into cyber/terminal/neon aesthetics outside cyber_tech?
- Does the code heavily use monospace identity where it doesn't belong?
- Does the code appear to ignore the accent color?

IMPORTANT:
- Default behavior should be OBSERVE (record + warn), not enforce.
- Enforcement should be opt-in via config to avoid throughput collapse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from titan_factory.schema import GeneratedFile, Task

_RE_CLASSNAME_DOUBLE = re.compile(r'className\s*=\s*"([^"]+)"')
_RE_CLASSNAME_SINGLE = re.compile(r"className\s*=\s*'([^']+)'")


@dataclass(frozen=True)
class StyleGateResult:
    passed: bool
    failures: list[str]
    warnings: list[str]
    details: dict


def _get_file(files: list[GeneratedFile], path: str) -> str:
    for f in files:
        if f.path == path:
            return f.content or ""
    return ""


def _extract_root_classnames(tsx: str) -> list[str]:
    """Best-effort: return the first few className strings in the file."""
    hits: list[str] = []
    for regex in (_RE_CLASSNAME_DOUBLE, _RE_CLASSNAME_SINGLE):
        for m in regex.finditer(tsx or ""):
            val = (m.group(1) or "").strip()
            if val:
                hits.append(val)
            if len(hits) >= 5:
                return hits
    return hits


def _split_classes(class_str: str) -> list[str]:
    return [c for c in (class_str or "").strip().split() if c]


def _base_class(cls: str) -> str:
    # Tailwind variant format: "md:hover:bg-slate-950"
    # Use base after last ":".
    base = (cls or "").split(":")[-1]
    return base.strip()


def _is_dark_bg(base: str) -> bool:
    b = (base or "").strip()
    return b.startswith(
        (
            "bg-black",
            "bg-neutral-9",
            "bg-slate-9",
            "bg-zinc-9",
            "bg-gray-9",
            "bg-stone-9",
        )
    )


def _is_light_bg(base: str) -> bool:
    b = (base or "").strip()
    return b.startswith(
        (
            "bg-white",
            "bg-neutral-50",
            "bg-slate-50",
            "bg-zinc-50",
            "bg-gray-50",
            "bg-stone-50",
        )
    )


def _count_accent_tokens(text: str, accent: str) -> int:
    if not accent:
        return 0
    # Count Tailwind color token usage like "bg-teal-500", "from-amber-200", etc.
    pattern = re.compile(
        rf"\b(?:bg|text|border|ring|from|to|via|fill|stroke)-{re.escape(accent)}-\d{{2,3}}\b"
    )
    return len(pattern.findall(text or ""))


def evaluate_style_gates(files: list[GeneratedFile], task: Task) -> StyleGateResult:
    """Evaluate deterministic style-routing compliance for a candidate."""
    failures: list[str] = []
    warnings: list[str] = []
    details: dict = {}

    style_family = str(getattr(task, "style_family", "") or "").strip().lower()
    expected_mood = str(getattr(task, "theme_mood", "") or "").strip().lower()
    expected_accent = str(getattr(task, "theme_accent", "") or "").strip().lower()

    if not style_family:
        return StyleGateResult(
            passed=True,
            failures=[],
            warnings=["style_gate:skipped (no style_family on task)"],
            details={"skipped": True},
        )

    page_tsx = _get_file(files, "app/page.tsx")
    globals_css = _get_file(files, "app/globals.css")
    full_text = "\n\n".join([page_tsx, globals_css])
    full_lower = full_text.lower()

    # === 1) Anti-cyber monoculture checks ===
    # If the routed family is not cyber_tech, strongly discourage cyber/terminal motifs.
    if style_family != "cyber_tech":
        # Avoid false positives from "console.log"
        cyber_keywords = [
            " terminal",
            " cli",
            " hacker",
            " hacking",
            " neon",
            " glitch",
            " matrix",
            " cyber",
            " exploit",
        ]
        hits = []
        for kw in cyber_keywords:
            if kw.strip() == "cli":
                # catch " CLI" / "cli " but avoid "client"
                if re.search(r"\bcli\b", full_lower):
                    hits.append("cli")
                continue
            if kw.strip() == "terminal":
                if re.search(r"\bterminal\b", full_lower):
                    hits.append("terminal")
                continue
            if kw.strip() == "console":
                continue
            if kw.strip() == "cyber":
                if re.search(r"\bcyber\b", full_lower):
                    hits.append("cyber")
                continue
            if kw.strip() and kw.strip() in full_lower:
                hits.append(kw.strip())

        # Monospace identity: allowed in small doses, but not as the main voice.
        mono_count = len(re.findall(r"\bfont-mono\b", full_lower))
        details["font_mono_count"] = mono_count
        if mono_count >= 8:
            hits.append("font-mono")

        # Neon/glow heavy usage can be spotted via Tailwind classes.
        glow_count = len(re.findall(r"\b(?:shadow-|drop-shadow-|blur-)\b", full_lower))
        details["glow_like_token_count"] = glow_count

        if hits:
            failures.append(
                "style_gate:cyber_leak (found non-cyber motifs: " + ", ".join(sorted(set(hits))) + ")"
            )

    # === 2) Mood mismatch heuristic (root background) ===
    # Best-effort: only warn; do not fail by default (some designs include a dark band).
    if expected_mood in ("light", "dark") and page_tsx:
        roots = _extract_root_classnames(page_tsx)
        details["root_classname_samples"] = roots[:3]
        if roots:
            root_classes = _split_classes(roots[0])
            dark_bg = []
            light_bg = []
            for cls in root_classes:
                base = _base_class(cls)
                if ":" in cls:
                    continue  # ignore variant classes (dark:, md:, etc.) for mood baseline
                if _is_dark_bg(base):
                    dark_bg.append(base)
                if _is_light_bg(base):
                    light_bg.append(base)
            if expected_mood == "light" and dark_bg:
                warnings.append("style_gate:mood_mismatch_root (expected light, found dark bg tokens)")
                details["root_dark_bg_tokens"] = dark_bg[:6]
            if expected_mood == "dark" and light_bg:
                warnings.append("style_gate:mood_mismatch_root (expected dark, found light bg tokens)")
                details["root_light_bg_tokens"] = light_bg[:6]

    # === 3) Accent adherence heuristic ===
    if expected_accent:
        accent_count = _count_accent_tokens(full_text, expected_accent)
        details["accent_token_count"] = accent_count
        if accent_count < 2:
            warnings.append("style_gate:accent_underused")

    passed = len(failures) == 0
    return StyleGateResult(passed=passed, failures=failures, warnings=warnings, details=details)

