"""Deterministic EVOL-style prompt mutations (no extra model calls).

Purpose:
- Increase prompt diversity WITHOUT changing the underlying niche intent
- Keep style anchored (do not drift across routed style families)
- Avoid extra spend / provider load by staying deterministic

This is intentionally conservative: it appends short, build-safe constraints rather than
rewriting the entire prompt.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from titan_factory.schema import Task


def _stable_hash(s: str) -> int:
    return int(hashlib.sha256(s.encode("utf-8")).hexdigest(), 16) % (2**31)


@dataclass(frozen=True)
class EvolResult:
    prompt: str
    applied_ops: list[str]


_OPS = [
    "STYLE_ANCHORED",
    "ADD_CONSTRAINTS",
    "DEEPEN_AUDIENCE",
    "CONCRETIZE_OFFER",
    "ADD_OBJECTIONS",
    "ADD_SIGNATURE_MOMENT",
]


def _pick_ops(task: Task, passes: int) -> list[str]:
    ops: list[str] = []
    base = f"{task.seed}:{task.id}:evol"
    for i in range(max(0, int(passes))):
        # 2 ops per pass, deterministic
        a = _OPS[_stable_hash(f"{base}:{i}:a") % len(_OPS)]
        b = _OPS[_stable_hash(f"{base}:{i}:b") % len(_OPS)]
        for op in (a, b):
            if op not in ops:
                ops.append(op)
    return ops[:6]


def mutate_task_prompt(task: Task, *, passes: int = 2) -> EvolResult:
    prompt = str(task.prompt or "").rstrip()
    if not prompt:
        return EvolResult(prompt=prompt, applied_ops=[])

    ops = _pick_ops(task, passes)
    if not ops:
        return EvolResult(prompt=prompt, applied_ops=[])

    style_family = str(getattr(task, "style_family", "") or "").strip()
    style_persona = str(getattr(task, "style_persona", "") or "").strip()
    keywords = list(getattr(task, "style_keywords_mandatory", []) or [])
    avoid = list(getattr(task, "style_avoid", []) or [])

    # If style routing is enabled, ensure we ALWAYS inject a signature moment requirement.
    # This is a deterministic "creativity floor" that increases variety without adding model calls.
    if style_family:
        if "ADD_SIGNATURE_MOMENT" not in ops:
            ops.append("ADD_SIGNATURE_MOMENT")
        # If we have mandatory keywords, anchor them too.
        if keywords and "STYLE_ANCHORED" not in ops:
            ops.insert(0, "STYLE_ANCHORED")

    blocks: list[str] = []
    blocks.append("EVOL MUTATIONS (VARIETY WITHOUT STYLE DRIFT)")
    blocks.append("- Do not change the niche intent. Do not add external assets. No UI libraries.")
    if style_family or style_persona:
        blocks.append(f"- Style is locked: {style_family or '(unknown)'} — {style_persona or ''}".strip())
    if avoid:
        blocks.append(f"- Avoid motifs: {', '.join(avoid[:8])}")

    # === Ops ===
    if "STYLE_ANCHORED" in ops and keywords:
        blocks.append("\nSTYLE ANCHOR (MUST SHOW IN VISUAL LANGUAGE)")
        for k in keywords[:6]:
            blocks.append(f"- {k}")

    if "ADD_CONSTRAINTS" in ops:
        blocks.append("\nVARIATION CONSTRAINTS (PICK 2–3 AND EXECUTE)")
        options = [
            "Include a comparison strip vs a common alternative (e.g., 'traditional' vs 'our approach').",
            "Add a proof wall with 6 micro-proofs (short labeled evidence chips).",
            "Add a 3-step onboarding preview (what happens after the CTA).",
            "Add a transparent guarantee / reassurance block with clear terms.",
            "Add a 'Who it's for / not for' section with crisp bullets.",
        ]
        # Deterministically pick 3
        for j in range(3):
            blocks.append(f"- {options[_stable_hash(f'{task.id}:{task.seed}:constraints:{j}') % len(options)]}")

    if "DEEPEN_AUDIENCE" in ops:
        blocks.append("\nAUDIENCE SHARPENING (WRITE COPY FOR A REAL PERSON)")
        audience_bits = [
            "Busy professionals with limited time windows.",
            "Beginners who want clarity and reassurance.",
            "Parents coordinating schedules and budgets.",
            "Enthusiasts who care about quality and craft.",
            "Price-sensitive shoppers comparing options.",
        ]
        blocks.append(
            "- Target audience detail: "
            + audience_bits[_stable_hash(f"{task.id}:{task.seed}:audience") % len(audience_bits)]
        )

    if "CONCRETIZE_OFFER" in ops:
        blocks.append("\nOFFER DETAIL (MAKE IT SPECIFIC)")
        offer_bits = [
            "Include 3 tier inclusions that feel concrete (not generic).",
            "Add a clear first step CTA (what happens next, in one sentence).",
            "Add 1 differentiator the competitor can't easily claim.",
        ]
        for j in range(2):
            blocks.append(f"- {offer_bits[_stable_hash(f'{task.id}:{task.seed}:offer:{j}') % len(offer_bits)]}")

    if "ADD_OBJECTIONS" in ops:
        blocks.append("\nOBJECTIONS TO ADDRESS (FAQ SHOULD ANSWER THESE)")
        objections = [
            "Price: 'Is it worth it compared to cheaper options?'",
            "Fit: 'Will this work for my skill level / situation?'",
            "Time: 'How much time does it take each week?'",
            "Results: 'When will I see outcomes?'",
            "Logistics: 'Where is it / what are the hours / how booking works?'",
            "Trust: 'What proof do you have?'",
            "Risk: 'What if it isn't a fit—can I cancel?'",
        ]
        # Pick 5 deterministically
        picked = []
        for j in range(5):
            picked.append(objections[_stable_hash(f"{task.id}:{task.seed}:obj:{j}") % len(objections)])
        for o in picked:
            blocks.append(f"- {o}")

    if "ADD_SIGNATURE_MOMENT" in ops:
        blocks.append("\nSIGNATURE MOMENT (REQUIRED)")
        hero_moments = [
            "Bento grid hero with 3–5 cards that each reveal a different value prop.",
            "Split hero with layered cards (depth + rhythm), still scannable.",
            "Editorial hero band (pull-quote rail + bold typographic hierarchy).",
        ]
        mid_moments = [
            "Timeline/stepper 'How it works' with labeled steps and subtle motion.",
            "Comparison strip (two-column) with clear labels and visual cues.",
            "Proof wall with labeled evidence chips + 2 standout testimonials.",
            "Pricing clarity panel (tiers + inclusions sidebar + highlight).",
        ]
        hero_pick = hero_moments[_stable_hash(f"{task.id}:{task.seed}:moment:hero") % len(hero_moments)]
        mid_pick = mid_moments[_stable_hash(f"{task.id}:{task.seed}:moment:mid") % len(mid_moments)]
        blocks.append("- Include TWO signature moments:")
        blocks.append(f"  - Above-the-fold: {hero_pick}")
        blocks.append(f"  - Mid-page: {mid_pick}")
        blocks.append("- Keep them build-safe (minimal client JS).")

    mutated = prompt + "\n\n" + "\n".join(blocks)
    return EvolResult(prompt=mutated.strip(), applied_ops=ops)
