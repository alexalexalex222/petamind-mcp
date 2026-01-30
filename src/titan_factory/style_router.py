"""Deterministic style routing for tasks.

Goal: make "style" a first-class constraint distinct from "boldness".

This module routes a niche+page_type to a style family + persona with:
- hard banlists (avoid cross-vertical cyber/terminal monoculture)
- deterministic mood/accent choices (stable across runs)
- prompt injection blocks that downstream stages can parse reliably

The router is intentionally deterministic: no model calls, no randomness beyond seed hashing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from titan_factory.schema import NicheDefinition, PageType


def _stable_hash(s: str) -> int:
    """Stable 31-bit hash (sha256-based), deterministic across Python runs."""
    return int(hashlib.sha256(s.encode("utf-8")).hexdigest(), 16) % (2**31)


def _density_to_schema_density(density: str) -> str:
    d = str(density or "").strip().lower()
    if d in ("airy", "air", "spacious"):
        return "airy"
    if d in ("dense", "compact", "tight"):
        return "compact"
    return "balanced"


@dataclass(frozen=True)
class StyleDirective:
    family: str
    persona: str
    keywords_mandatory: list[str]
    avoid: list[str]
    density: str
    imagery_style: str
    layout_motif: str
    mood: str
    accent: str

    def schema_density(self) -> str:
        return _density_to_schema_density(self.density)

    def to_prompt_block(self) -> str:
        # Keep format copy/paste-ready and easy for models to parse.
        # The pipeline relies on the exact header line.
        return (
            "STYLE ROUTING (HARD CONSTRAINTS — MUST FOLLOW EXACTLY)\n"
            f"style_family: {self.family}\n"
            f"style_persona: {self.persona}\n"
            f"style_keywords_mandatory: {json.dumps(self.keywords_mandatory, ensure_ascii=False)}\n"
            f"style_avoid: {json.dumps(self.avoid, ensure_ascii=False)}\n"
            f"density: {self.density}\n"
            f"imagery_style: {self.imagery_style}\n"
            f"layout_motif: {self.layout_motif}"
        )

    def to_dict(self) -> dict:
        return {
            "family": self.family,
            "persona": self.persona,
            "keywords_mandatory": list(self.keywords_mandatory),
            "avoid": list(self.avoid),
            "density": self.density,
            "imagery_style": self.imagery_style,
            "layout_motif": self.layout_motif,
            "mood": self.mood,
            "accent": self.accent,
        }


@lru_cache(maxsize=1)
def _load_style_personas() -> dict[str, list[dict]]:
    path = Path(__file__).with_name("style_personas.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("style_personas.json must be an object keyed by family")
    return data


def _family_for_niche(niche: NicheDefinition, page_type: PageType) -> str:
    v = str(getattr(niche, "vertical", "") or "").lower()
    p = str(getattr(niche, "pattern", "") or "").lower()
    pt = str(getattr(page_type, "value", page_type) or "").lower()

    # If it's explicitly a dashboard, allow more "app UI" density without forcing cyber aesthetics.
    is_dashboard = "dashboard" in pt

    # Tech-first niches
    if any(k in v for k in ("saas", "startup", "ai_", "ai", "cloud", "developer", "cyber", "analytics", "fintech")):
        # fintech can be more "financial_data_driven" than cyber_tech.
        if "fintech" in v:
            return "financial_data_driven"
        if "analytics" in v:
            return "financial_data_driven" if not is_dashboard else "cyber_tech"
        if any(k in v for k in ("cyber", "developer")):
            return "cyber_tech"
        return "cyber_tech" if is_dashboard else "cyber_tech"

    # Healthcare / clinical
    if any(k in v for k in ("dental", "clinic", "therapy", "chiropractor", "optometry", "dermatology", "urgent_care", "veterinary", "fertility")):
        return "clean_clinical"

    # Legal / professional
    if any(k in v for k in ("law", "accounting", "consulting", "insurance", "advisor", "estate", "hr", "patent", "immigration")):
        return "trusted_professional"

    # Food / craft
    if any(k in v for k in ("restaurant", "coffee", "bakery", "brewery", "wine", "juice", "catering", "meal_prep", "ghost_kitchen", "florist")):
        return "artisanal_warm" if "artisan" in p or "warm" in p else "editorial_typographic"

    # Fitness / wellness
    if any(k in v for k in ("yoga", "wellness", "spa", "mental_health", "pilates")):
        return "serene_minimal"
    if any(k in v for k in ("martial", "boxing", "crossfit", "climbing", "spin", "swimming", "dance", "personal_training")):
        # personal training is often premium; default to luxury unless explicitly gritty.
        if "personal_training" in v and p in ("premium", "luxurious", "wealth"):
            return "premium_luxury"
        if p in ("industrial", "fierce", "bold"):
            return "industrial_raw"
        return "industrial_raw"

    # Home services + automotive tends industrial/pro.
    if any(k in v for k in ("plumbing", "electrical", "hvac", "roofing", "cleaning_service", "landscaping", "auto_", "car_", "tire", "mechanical")):
        return "industrial_raw" if p in ("industrial", "mechanical", "reliable", "protective") else "trusted_professional"

    # Retail
    if any(k in v for k in ("boutique", "jewelry", "furniture", "electronics", "pet_store")):
        if "pet" in v:
            return "friendly_playful"
        if any(k in v for k in ("jewelry", "boutique")):
            return "premium_luxury"
        return "trusted_professional"

    # Events / entertainment
    if any(k in v for k in ("event", "wedding", "dj", "party", "escape_room", "laser_tag", "bowling", "arcade")):
        return "event_festival" if p in ("festive", "celebration", "nightlife", "gaming", "retro") else "friendly_playful"

    # Nonprofit/community
    if any(k in v for k in ("nonprofit", "church", "community_center", "animal_shelter")):
        return "community_nonprofit"

    # Outdoors-ish
    if any(k in v for k in ("climbing", "landscaping")) or p in ("natural", "adventurous"):
        return "outdoors_rugged"

    # Default: professional/editorial.
    if p in ("editorial", "artistic", "visual", "cinematic", "creative", "identity"):
        return "editorial_typographic"
    return "trusted_professional"


def route_style(
    niche: NicheDefinition,
    page_type: PageType,
    seed: int,
) -> StyleDirective:
    """Route a niche+page_type to a deterministic StyleDirective."""
    personas_by_family = _load_style_personas()

    family = _family_for_niche(niche, page_type)
    personas = personas_by_family.get(family) or []
    if not personas:
        # Fallback to a safe default family if JSON is missing entries.
        family = "trusted_professional"
        personas = personas_by_family.get(family) or []

    if not personas:
        raise ValueError("No style personas available (style_personas.json empty?)")

    persona_idx = _stable_hash(f"{seed}:{niche.id}:{page_type.value}:{family}:persona") % len(personas)
    persona = personas[persona_idx]

    preferred_moods = list(persona.get("preferred_moods") or ["light", "dark"])
    preferred_accents = list(
        persona.get("preferred_accents")
        or ["blue", "teal", "green", "orange", "red", "violet", "amber", "rose", "cyan", "lime", "fuchsia"]
    )

    mood = preferred_moods[_stable_hash(f"{seed}:{niche.id}:{family}:mood") % len(preferred_moods)]
    accent = preferred_accents[_stable_hash(f"{seed}:{niche.id}:{family}:accent") % len(preferred_accents)]

    density = str(persona.get("density") or "balanced")
    # For dashboards, bias toward denser layouts without changing family.
    if page_type == PageType.ADMIN_DASHBOARD and density == "airy":
        density = "balanced"
    density = _density_to_schema_density(density)

    return StyleDirective(
        family=family,
        persona=str(persona.get("persona") or family),
        keywords_mandatory=[str(x) for x in (persona.get("keywords_mandatory") or [])][:12],
        avoid=[str(x) for x in (persona.get("avoid") or [])][:12],
        density=density,
        imagery_style=str(persona.get("imagery_style") or "minimal_none"),
        layout_motif=str(persona.get("layout_motif") or ""),
        mood=str(mood),
        accent=str(accent),
    )
