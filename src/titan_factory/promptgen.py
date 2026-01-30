"""Prompt generation for niches and tasks."""

import hashlib
import json
import os
import random
from collections.abc import Iterator
from pathlib import Path

from titan_factory.config import Config
from titan_factory.schema import NicheDefinition, PageType, Task
from titan_factory.utils import log_info, log_warning


def stable_hash(s: str) -> int:
    """Generate a stable hash that doesn't change between Python runs.

    Python's built-in hash() is randomized per-process for security,
    which breaks deterministic task ID generation and resumability.

    Args:
        s: String to hash

    Returns:
        Stable 31-bit positive integer
    """
    return int(hashlib.sha256(s.encode()).hexdigest(), 16) % (2**31)


# Accent/mood hints:
# Some planner models tend to overuse a single "default" aesthetic (commonly violet + white/black).
# To keep dataset distribution healthy, we inject deterministic theme overrides per task prompt.
_ACCENT_CHOICES: list[str] = [
    "blue",
    "teal",
    "green",
    "orange",
    "red",
    "violet",
    "amber",
    "rose",
    "cyan",
    "lime",
    "fuchsia",
]
_MOOD_CHOICES: list[str] = ["light", "dark"]


def _deterministic_brand_theme(seed: int, niche: "NicheDefinition") -> tuple[str, str]:
    """Choose a deterministic (mood, accent) pair for a task.

    This is intentionally simple and stable:
    - It does NOT depend on Python's randomized hash()
    - It produces repeatable variety across tasks
    """
    mood = _MOOD_CHOICES[stable_hash(f"{seed}:{niche.id}:mood") % len(_MOOD_CHOICES)]
    accent = _ACCENT_CHOICES[stable_hash(f"{seed}:{niche.id}:accent") % len(_ACCENT_CHOICES)]
    return mood, accent


def _maybe_route_style(config: Config, niche: NicheDefinition, page_type: PageType, seed: int):
    """Return a StyleDirective if style routing is enabled (else None)."""
    if page_type == PageType.EDIT:
        return None
    if not bool(getattr(config.pipeline, "style_routing_enabled", False)):
        return None
    try:
        from titan_factory.style_router import route_style

        return route_style(niche=niche, page_type=page_type, seed=seed)
    except Exception as e:
        log_warning(f"Style routing failed; continuing without routing. Error: {e}")
        return None

# === 100 Niches ===
# Format: (vertical, pattern, description)
NICHE_DEFINITIONS: list[tuple[str, str, str]] = [
    # Fitness & Wellness (12)
    ("martial_arts", "bold", "Martial arts gyms with powerful, dynamic presence"),
    ("yoga_studio", "calm", "Yoga studios with serene, mindful aesthetics"),
    ("crossfit_box", "industrial", "CrossFit gyms with raw, gritty energy"),
    ("personal_training", "premium", "Luxury personal training with high-end feel"),
    ("pilates_studio", "elegant", "Pilates studios with refined, graceful design"),
    ("boxing_gym", "fierce", "Boxing gyms with intense, competitive edge"),
    ("climbing_gym", "adventurous", "Climbing centers with outdoor-inspired design"),
    ("spin_studio", "energetic", "Spin studios with vibrant, music-forward vibe"),
    ("swimming_pool", "clean", "Swimming facilities with aquatic, fresh aesthetics"),
    ("wellness_spa", "luxurious", "Wellness spas with opulent, relaxing atmosphere"),
    ("dance_studio", "artistic", "Dance studios with expressive, creative energy"),
    # Legal & Professional (10)
    ("law_firm", "authoritative", "Law firms with commanding, trustworthy presence"),
    ("accounting_firm", "precise", "Accounting firms with clean, numbers-focused design"),
    ("consulting_agency", "strategic", "Consulting agencies with insight-driven aesthetics"),
    ("hr_services", "approachable", "HR services with people-focused, warm design"),
    ("real_estate_agency", "aspirational", "Real estate with dream-home inspired visuals"),
    ("insurance_broker", "secure", "Insurance with protection-focused, reliable feel"),
    ("financial_advisor", "wealth", "Financial advisors with prosperity-driven design"),
    ("patent_attorney", "innovative", "Patent attorneys with invention-focused aesthetics"),
    ("immigration_lawyer", "welcoming", "Immigration lawyers with inclusive, hopeful design"),
    ("estate_planning", "legacy", "Estate planning with timeless, family-focused feel"),
    # Food & Beverage (10)
    ("restaurant", "culinary", "Restaurants with food-photography-forward design"),
    ("coffee_shop", "artisan", "Coffee shops with craft, handmade aesthetics"),
    ("bakery", "warm", "Bakeries with cozy, fresh-baked atmosphere"),
    ("food_truck", "street", "Food trucks with urban, mobile-friendly design"),
    ("brewery", "craft", "Breweries with industrial, craft-beer vibes"),
    ("wine_bar", "sophisticated", "Wine bars with refined, sommelier-inspired design"),
    ("juice_bar", "fresh", "Juice bars with health-focused, vibrant aesthetics"),
    ("catering_service", "elegant", "Catering with event-focused, elegant presentation"),
    ("meal_prep", "efficient", "Meal prep services with time-saving, organized feel"),
    ("ghost_kitchen", "modern", "Ghost kitchens with delivery-first, digital design"),
    # Healthcare (10)
    ("dental_clinic", "bright", "Dental clinics with clean, smile-focused design"),
    ("chiropractor", "healing", "Chiropractors with spine-health, relief-focused aesthetics"),
    ("physical_therapy", "recovery", "PT clinics with movement, rehabilitation focus"),
    ("mental_health", "supportive", "Mental health with calming, safe-space design"),
    ("dermatology", "radiant", "Dermatology with skin-health, beauty-focused aesthetics"),
    ("optometry", "clear", "Optometry with vision-focused, precision design"),
    ("veterinary", "caring", "Vet clinics with pet-friendly, compassionate feel"),
    ("plastic_surgery", "transformation", "Plastic surgery with before-after, aspirational design"),
    ("fertility_clinic", "hopeful", "Fertility clinics with family-building, gentle aesthetics"),
    ("urgent_care", "efficient", "Urgent care with quick, reliable service focus"),
    # Home Services (10)
    ("plumbing", "reliable", "Plumbers with dependable, no-nonsense design"),
    ("electrical", "technical", "Electricians with safety-focused, professional aesthetics"),
    ("hvac", "comfort", "HVAC with temperature-focused, comfort-driven design"),
    ("landscaping", "natural", "Landscapers with outdoor, garden-inspired aesthetics"),
    ("cleaning_service", "spotless", "Cleaning services with fresh, organized feel"),
    ("roofing", "protective", "Roofers with shelter, protection-focused design"),
    ("painting", "colorful", "Painters with before-after, transformation aesthetics"),
    ("pest_control", "safe", "Pest control with protection, home-safety focus"),
    ("moving_company", "smooth", "Movers with stress-free, organized transition feel"),
    ("handyman", "versatile", "Handymen with jack-of-all-trades, capable design"),
    # Education & Learning (8)
    ("tutoring", "academic", "Tutoring with achievement-focused, educational design"),
    ("music_school", "melodic", "Music schools with instrument-inspired, creative aesthetics"),
    ("language_school", "global", "Language schools with multicultural, travel-inspired design"),
    ("coding_bootcamp", "tech", "Coding bootcamps with developer-focused, modern aesthetics"),
    ("driving_school", "road", "Driving schools with safety, independence-focused design"),
    ("art_school", "creative", "Art schools with gallery-inspired, expressive aesthetics"),
    ("test_prep", "strategic", "Test prep with score-focused, achievement design"),
    ("preschool", "playful", "Preschools with colorful, child-friendly aesthetics"),
    # Technology & SaaS (10)
    ("saas_startup", "modern", "SaaS startups with product-forward, clean design"),
    ("ai_company", "futuristic", "AI companies with cutting-edge, neural aesthetics"),
    ("cybersecurity", "fortress", "Cybersecurity with protection-focused, secure design"),
    ("cloud_services", "scalable", "Cloud services with infrastructure, reliability focus"),
    ("mobile_app", "gestural", "Mobile apps with touch-first, app-store aesthetics"),
    ("developer_tools", "code", "Dev tools with terminal-inspired, hacker aesthetics"),
    ("analytics_platform", "data", "Analytics with dashboard-focused, insight design"),
    ("ecommerce_platform", "conversion", "E-commerce with sales-focused, shopping design"),
    ("fintech", "trust", "Fintech with security-focused, money-management aesthetics"),
    ("healthtech", "care", "Healthtech with patient-first, medical design"),
    # Creative & Media (8)
    ("photo_studio", "visual", "Photo studios with portfolio-forward, gallery design"),
    ("video_production", "cinematic", "Video production with film-inspired, motion aesthetics"),
    ("graphic_design", "creative", "Design agencies with portfolio, process-focused design"),
    ("marketing_agency", "results", "Marketing agencies with metrics-driven, bold aesthetics"),
    ("podcast_studio", "audio", "Podcast studios with sound-focused, broadcast design"),
    ("music_producer", "beats", "Music producers with studio-inspired, rhythm aesthetics"),
    ("web_agency", "digital", "Web agencies with browser-inspired, tech design"),
    ("branding_agency", "identity", "Branding agencies with logo-focused, strategy aesthetics"),
    # Events & Entertainment (8)
    ("event_venue", "celebration", "Event venues with party-focused, elegant design"),
    ("wedding_planner", "romantic", "Wedding planners with love-focused, dreamy aesthetics"),
    ("dj_service", "nightlife", "DJ services with club-inspired, beat-driven design"),
    ("party_rentals", "festive", "Party rentals with celebration, equipment-focused design"),
    ("escape_room", "mysterious", "Escape rooms with puzzle-focused, immersive aesthetics"),
    ("laser_tag", "futuristic", "Laser tag with neon, gaming-inspired design"),
    ("bowling_alley", "retro", "Bowling alleys with vintage, fun-focused aesthetics"),
    ("arcade", "gaming", "Arcades with pixel-art, nostalgia-driven design"),
    # Automotive (6)
    ("auto_repair", "mechanical", "Auto repair with garage-inspired, trusted design"),
    ("car_dealership", "showroom", "Dealerships with inventory-focused, sleek aesthetics"),
    ("car_detailing", "shine", "Detailing with before-after, transformation design"),
    ("tire_shop", "road", "Tire shops with safety, performance-focused aesthetics"),
    ("auto_body", "restoration", "Body shops with craftsmanship, repair-focused design"),
    # Retail & Shopping (6)
    ("boutique", "curated", "Boutiques with hand-picked, exclusive aesthetics"),
    ("jewelry_store", "precious", "Jewelry stores with luxury, sparkle-focused design"),
    ("florist", "botanical", "Florists with flower-forward, romantic aesthetics"),
    ("pet_store", "playful", "Pet stores with animal-friendly, fun design"),
    ("furniture_store", "interior", "Furniture with room-setting, lifestyle aesthetics"),
    ("electronics", "tech", "Electronics with gadget-focused, modern design"),
    # Nonprofit & Community (4)
    ("nonprofit", "mission", "Nonprofits with cause-focused, impact aesthetics"),
    ("church", "spiritual", "Churches with faith-focused, welcoming design"),
    ("community_center", "inclusive", "Community centers with gathering-focused aesthetics"),
    ("animal_shelter", "rescue", "Shelters with adoption-focused, heartwarming design"),
]


def generate_niches() -> list[NicheDefinition]:
    """Generate all 100 niche definitions.

    Returns:
        List of niche definitions

    Raises:
        AssertionError: If niche count is not exactly 100
    """
    niches = []
    for vertical, pattern, description in NICHE_DEFINITIONS:
        niche_id = f"{vertical}_{pattern}"
        niches.append(
            NicheDefinition(
                id=niche_id,
                vertical=vertical,
                pattern=pattern,
                description=description,
            )
        )

    # Enforce exactly 100 niches to prevent config/data drift
    assert len(niches) == 100, (
        f"Expected exactly 100 niches but got {len(niches)}. "
        f"Update NICHE_DEFINITIONS to have exactly 100 entries."
    )

    return niches


def generate_task_prompt(
    niche: NicheDefinition,
    page_type: PageType,
    seed: int,
    is_edit: bool = False,
    code_old: str | None = None,
) -> str:
    """Generate a task prompt.

    Creates short, human-like prompts with sufficient constraints.

    Args:
        niche: The niche definition
        page_type: Type of page to generate
        seed: Random seed for variety
        is_edit: Whether this is an edit task
        code_old: Original code for edit tasks

    Returns:
        Task prompt string
    """
    rng = random.Random(seed)

    # City pool for variety
    cities = [
        "Austin", "Denver", "Seattle", "Portland", "Nashville", "Atlanta",
        "Miami", "Boston", "Chicago", "Phoenix", "San Diego", "Minneapolis",
        "Charlotte", "Salt Lake City", "Raleigh", "Tampa", "Oakland", "Brooklyn",
    ]
    city = rng.choice(cities)

    # Style variations
    vibes = [
        "ultra-clean with generous whitespace",
        "bold with striking typography",
        "minimal with subtle animations",
        "editorial with elegant sections",
        "modern with card-based layout",
    ]
    vibe = rng.choice(vibes)

    # Generate business name
    prefixes = ["", "The ", "", ""]
    suffixes = ["", " Co", " Studio", " Lab", " House", ""]
    base_names = {
        "martial_arts": ["Iron Fist", "Dragon Academy", "Combat Arts", "Warriors Path"],
        "yoga_studio": ["Serenity", "Flow", "Lotus", "Namaste"],
        "law_firm": ["Sterling & Associates", "Justice Partners", "Legal Edge"],
        "restaurant": ["Ember", "Salt & Vine", "The Kitchen", "Harvest Table"],
        "dental_clinic": ["Bright Smiles", "Pearl Dental", "Clear View Dental"],
        "saas_startup": ["FlowStack", "DataPulse", "CloudSync", "MetricHub"],
    }
    name_pool = base_names.get(niche.vertical, [niche.vertical.replace("_", " ").title()])
    business_name = rng.choice(prefixes) + rng.choice(name_pool) + rng.choice(suffixes)

    # CTAs
    primary_ctas = {
        "landing": ["Get Started", "Book Now", "Start Free", "Learn More", "Try Free"],
        "directory_home": ["Find Near You", "Browse All", "Search Now", "Explore"],
        "listing_profile": ["Book Appointment", "Contact Us", "Get Quote", "Visit"],
        "admin_dashboard": ["View Analytics", "Manage", "Settings"],
    }
    cta = rng.choice(primary_ctas.get(page_type.value, ["Get Started"]))

    # Build prompt based on page type
    if page_type == PageType.LANDING:
        prompt = f"""Create a premium landing page for {business_name}, a {niche.description.lower()} in {city}.

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Palette seed: {seed} (use this to vary the palette across tasks; avoid always defaulting to green/teal)
CTA: "{cta}"

Include: hero with headline + subheadline, trust indicators, how-it-works section (3-4 steps), testimonials (use placeholders), pricing tiers, FAQ, and final CTA.

Make it {niche.pattern} and premium. No UI libraries."""

    elif page_type == PageType.DIRECTORY_HOME:
        prompt = f"""Create a premium directory homepage for finding {niche.vertical.replace("_", " ")} services.

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Palette seed: {seed} (use this to vary the palette across tasks; avoid always defaulting to green/teal)

Include: search bar with filters (location, category), featured listings grid (cards with image, name, rating, location), category quick-links, and footer.

Make navigation intuitive and mobile-friendly. No UI libraries."""

    elif page_type == PageType.CITY_INDEX:
        prompt = f"""Create a premium city index page for {niche.vertical.replace("_", " ")} in {city}.

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Palette seed: {seed} (use this to vary the palette across tasks; avoid always defaulting to green/teal)

Include: city hero with local stats, filterable grid of 8-12 listings (placeholder cards), map placeholder, local tips section, and related cities.

Optimize for local SEO structure. No UI libraries."""

    elif page_type == PageType.CATEGORY_INDEX:
        category = niche.vertical.replace("_", " ").title()
        prompt = f"""Create a premium category index page for "{category}" services.

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Palette seed: {seed} (use this to vary the palette across tasks; avoid always defaulting to green/teal)

Include: category hero with description, subcategory filters, grid of 12+ listings, sort options (rating, distance, price), and pagination.

Cards should show: image, name, rating, location, price range. No UI libraries."""

    elif page_type == PageType.LISTING_PROFILE:
        prompt = f"""Create a premium listing detail page for {business_name}, a {niche.description.lower()}.

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Palette seed: {seed} (use this to vary the palette across tasks; avoid always defaulting to green/teal)
CTA: "{cta}"

Include: hero with gallery placeholder, business info (hours, contact, location), services/offerings grid, reviews section with ratings, and booking/contact form.

Make it conversion-focused. No UI libraries."""

    elif page_type == PageType.ADMIN_DASHBOARD:
        prompt = f"""Create a premium admin dashboard for managing a {niche.vertical.replace("_", " ")} business.

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Palette seed: {seed} (use this to vary the palette across tasks; avoid always defaulting to green/teal)

Include: sidebar navigation, stats overview (4-6 metric cards), recent activity table, chart placeholder, quick actions, and notification area.

Make it clean and scannable. No UI libraries."""

    elif page_type == PageType.EDIT:
        edit_instructions = [
            "Add a dark mode toggle that persists preference",
            "Improve mobile responsiveness for the hero section",
            "Add subtle scroll animations using CSS",
            "Refactor to use CSS variables for theming",
            "Add a sticky header with backdrop blur",
            "Improve accessibility with proper ARIA labels",
        ]
        instruction = rng.choice(edit_instructions)

        prompt = f"""Refactor this {niche.vertical.replace("_", " ")} page.

Task: {instruction}

Keep the existing design language but improve as specified. Maintain the overall look and feel and keep the palette cohesive."""

        # For production runs we usually inject CODE_OLD separately (Task.code_old),
        # but allow embedding when explicitly provided (useful for debugging).
        if code_old:
            prompt += f"""

<CODE_OLD>
{code_old}
</CODE_OLD>"""

        prompt += """

No UI libraries. Output complete updated code."""

    return prompt.strip()


_EXT_PERSONAS: list[str] = [
    "solo founders shipping a v1",
    "operators at 10–200 person teams",
    "product managers who need clarity without meetings",
    "customer support leads who triage high volume",
    "finance teams closing the month faster",
    "data analysts who want fewer dashboards",
    "agency owners managing multiple clients",
    "developers who want predictable workflows",
]


_EXT_PRODUCT_ONE_LINERS: list[str] = [
    "A calm workspace that turns scattered requests into a weekly plan.",
    "A lightweight dashboard that keeps the few metrics that matter visible.",
    "A fast docs + onboarding hub that answers questions in seconds.",
    "An automation layer that reduces manual copy/paste work across tools.",
    "A billing and usage view that makes limits and costs obvious.",
]


_EXT_FEATURES: list[str] = [
    "Smart triage inbox",
    "Weekly planning view",
    "Audit-friendly change log",
    "One-click exports",
    "Team permission presets",
    "Usage alerts",
    "Role-based dashboards",
]


_EXT_JTBDS: list[str] = [
    "know what to do next without feeling behind",
    "make decisions from one trusted view",
    "reduce context switching across tools",
    "ship updates with less coordination overhead",
    "keep costs and limits transparent for the team",
]


_EXT_ECOM_CATEGORIES: list[str] = [
    "supplements",
    "apparel",
    "gadgets",
    "home goods",
    "coffee gear",
    "skincare",
]


def _extended_brand_name(rng: random.Random, niche: NicheDefinition) -> str:
    # Reuse the existing naming approach, but allow more "product-y" options.
    extra = [
        "Northstar",
        "Relay",
        "Orbit",
        "Nova",
        "Juniper",
        "Cinder",
        "Quartz",
        "Aster",
    ]
    base = rng.choice(extra + [niche.vertical.replace("_", " ").title()])
    suffix = rng.choice(["", " Labs", " Studio", " OS", " Cloud", " Works", " Co"])
    return f"{base}{suffix}".strip()


def _extended_cta(rng: random.Random) -> str:
    return rng.choice(
        [
            "Start free",
            "Get started",
            "Book a 15‑minute demo",
            "See pricing",
            "Join the waitlist",
            "Shop bestsellers",
        ]
    )


def generate_task_prompt_extended(
    niche: NicheDefinition,
    page_type: PageType,
    seed: int,
) -> str:
    """Generate an "extended" task prompt (SaaS/ecom/app shells/OS demos).

    These are still used as planner input, so keep them deterministic and concise.
    """
    rng = random.Random(seed)

    cities = [
        "Remote",
        "San Francisco",
        "New York",
        "Austin",
        "Denver",
        "Seattle",
    ]
    rng.choice(cities)

    vibes = [
        "ultra-clean with generous whitespace",
        "minimal with subtle depth and calm typography",
        "modern with card-based layout and clear hierarchy",
        "editorial with refined sections and strong typography",
    ]
    vibe = rng.choice(vibes)

    brand = _extended_brand_name(rng, niche)
    persona = rng.choice(_EXT_PERSONAS)
    product_line = rng.choice(_EXT_PRODUCT_ONE_LINERS)
    cta = _extended_cta(rng)

    # Map extended templates into existing PageType buckets for the pipeline:
    # - LANDING: marketing + docs + ecom home + waitlist + comparisons
    # - LISTING_PROFILE: ecom PDP
    # - ADMIN_DASHBOARD: app shells + dashboards
    if page_type == PageType.LISTING_PROFILE:
        category = rng.choice(_EXT_ECOM_CATEGORIES)
        product_name = rng.choice(
            [
                f"{brand} Starter Kit",
                f"{brand} Daily Essentials",
                f"{brand} Pro Edition",
                f"{brand} Classic",
            ]
        )
        variants = rng.choice(["size", "color", "size + color", "none"])
        price = rng.choice(["$49", "$79", "$129", "$19"])
        variants_line = f"{variants}" if variants != "none" else "omit"

        return f"""category: ecom_product_page
intent: generate

Build an e-commerce product detail page (PDP).

Product: {product_name}
Price: {price}
Variants: {variants_line}
Primary CTA: "Add to cart"

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Rules:
- No emojis. Inline SVG icons <= 3 total.
- No UI libraries. Tailwind only.
- No fake reviews. If showing reviews/ratings, label as "Illustrative example" and avoid numeric ratings unless provided.

Required:
- Gallery area (mock blocks)
- Product title + price + key benefits (3)
- Variant selectors (accessible)
- Shipping/returns block
- FAQ (>=6)

Output JSON only with app/page.tsx and any components.""".strip()

    if page_type == PageType.ADMIN_DASHBOARD:
        template = rng.choice(["analytics", "crm", "billing", "app_shell", "os_desktop", "os_browser", "os_settings"])

        if template == "analytics":
            return f"""category: dashboard
intent: generate

Build an analytics dashboard for {brand}.
Audience: {persona}

Include:
- KPI strip (3 or 5) with placeholder numbers
- Date range control + filters row
- Main table (status + owner + last updated)
- Empty/loading/error states
- One clear primary action

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Rules: no chart libraries; if trend needed use tiny inline SVG sparkline only. No emojis. SVG icons <= 4.
Output JSON only.""".strip()

        if template == "crm":
            return f"""category: dashboard
intent: generate

Build a lightweight CRM pipeline dashboard for {brand}.
Primary action: "Add lead"

Include:
- Pipeline stages (kanban-like, simple)
- Lead list table fallback for mobile
- Search + filters
- Empty state for "no leads"

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Rules: no heavy drag/drop libraries; minimal client component allowed. No emojis. SVG icons <= 4.
Output JSON only.""".strip()

        if template == "billing":
            return f"""category: dashboard
intent: generate

Build an account/billing dashboard for {brand}.

Include:
- Plan summary card
- Payment method panel (no real card data)
- Invoices table
- Usage panel (bars made with divs)
- FAQ (>=6) for billing issues

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Rules: no emojis. SVG icons <= 2. Output JSON only.""".strip()

        if template == "app_shell":
            return f"""category: app_shell
intent: generate

Build a product "app shell" (logged-in experience) for {brand}.

Include:
- Top bar (brand + global search)
- Left nav (5 items)
- Main content area with example dashboard/table
- Right panel (help/insights)
- Empty/loading/error states

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Rules: No UI libs. No emojis. Inline SVG icons <= 6 total.
Output JSON only.""".strip()

        if template == "os_desktop":
            return """category: os_desktop
intent: generate

Create a “desktop OS” interface as a web UI demo.
Name: OrchidOS
Goal: feel premium and original (not a macOS clone), but familiar.

Required layout:
- Menu bar (left: logo + app name; right: time + system controls)
- Dock (6 apps max; app icons must be simple lettermarks in rounded squares — NOT emojis)
- Desktop area with 2 windows open by default:
  1) “Finder-like” file browser window
  2) “Notes” window
- Window controls: close/minimize/maximize (simple buttons, accessible labels)
- Optional: command palette (Ctrl+K) as a small client component

Rules:
- No UI libraries. No emojis.
- Inline SVG icons <= 6 total (use sparingly; prefer lettermarks for app icons).
- Must include reduced-motion handling.
Output JSON only.""".strip()

        if template == "os_browser":
            return """category: os_browser
intent: generate

Build a “browser OS” workspace UI demo.
Name: Nova Workspace

Required:
- Left sidebar with spaces/projects
- Top tab strip (3 tabs) with clean active state
- Main content: a “web page” panel + optional split view
- Bottom status bar with network/sync indicators (no emojis)
- A settings drawer panel (client component, isolated)

Rules: No UI libs. Tailwind only. Inline SVG icons <= 6 total, custom and consistent.
Output JSON only.""".strip()

        # os_settings
        return """category: os_settings
intent: generate

Create an OS “Settings” app UI.

Sections:
- Appearance (theme, accent)
- Privacy (permissions toggles)
- Notifications (per-app settings)
- Keyboard (shortcuts)
- About (system info)

Rules:
- Use accessible toggles/controls (buttons + aria, no UI libs)
- Include empty state for “no apps installed” in notifications
- No emojis. SVG icons <= 4.
Output JSON only.""".strip()

    # Default LANDING extended templates
    template = rng.choice(["G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "E1"])

    if template == "G2":
        feature = rng.choice(_EXT_FEATURES)
        jtbd = rng.choice(_EXT_JTBDS)
        return f"""category: landing
intent: generate

Create a landing page for a single feature (not a full product).

Brand: {brand}
Feature: {feature}
Who it’s for: {persona}
Job-to-be-done: {jtbd}
Primary CTA: "{cta}"

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Required:
- Hero with 3 outcomes (not features) + 3 trust chips + digest(3)
- Before/after section (calm, factual)
- “How it works” steps (3)
- Screenshot/mock area (no external images; use lightweight mock blocks)
- Proof policy section (how you verify / what’s illustrative)
- FAQ (>=6)

Rules: No emojis. Inline SVG icons <= 4 total. Logo mark hints at the feature’s mechanism.
Output JSON only with app/page.tsx and any components.""".strip()

    if template == "G3":
        positioning = rng.choice(
            [
                "Clarity-first workflow, without busy dashboards.",
                "A calm control panel for what actually matters.",
                "Make the next action obvious—without context switching.",
            ]
        )
        return f"""category: product_page
intent: generate

Build a “Product Overview” page (marketing site).

Brand: {brand}
Audience: {persona}
Positioning: {positioning}
Primary CTA: "{cta}"

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Must include:
- Hero + digest(3) + trust chips(3)
- 3 use-case cards (VoC phrasing)
- Feature → Outcome mapping (table or grid)
- Integrations row (NO real brand logos unless provided; use generic placeholders labeled)
- Security/Privacy/Control panel (trust-first)
- FAQ (>=6)
- Final CTA + what happens next

Rules: No emojis. Inline SVG icons <= 6 total, custom and consistent. Output JSON only.""".strip()

    if template == "G4":
        return f"""category: pricing_page
intent: generate

Build a premium pricing page for {brand}.
Plans: 2–3 tiers
Primary CTA: "{cta}"
Secondary CTA: link-only ("Contact sales" or "Compare plans")

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Required:
- Pricing cards with best-for tags + included items + exclusions
- How billing works + What happens after you start blocks
- FAQ (>=8) including refunds, cancellation, data, limits
- Proof labeling: never invent metrics; qualify anything not provided

Rules: No emojis. Inline SVG icons <= 4. Output JSON only.""".strip()

    if template == "G5":
        competitors = rng.choice(
            [
                ("Competitor A", "Competitor B"),
                ("Spreadsheets", "All-in-one suites"),
                ("Legacy dashboards", "Generic templates"),
            ]
        )
        return f"""category: comparison_page
intent: generate

Create a competitor comparison page.
Brand: {brand}
Competitors: {competitors[0]}, {competitors[1]} (do NOT claim “best” unless backed)
Goal: help a buyer choose honestly.

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Must include:
- Clear comparison table (feature/outcome rows)
- Where we’re not a fit (2–3 bullets)
- Proof policy (verified vs illustrative)
- FAQ (>=6)

Rules: No emojis. No fake badges or reviews. Output JSON only.""".strip()

    if template == "G6":
        return f"""category: docs_home
intent: generate

Build a docs homepage (clean, scannable).
Brand: {brand}
Sections: Getting Started, Guides, API, Examples, Troubleshooting

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Required:
- Search bar (client component isolated)
- Card grid for major sections
- Quickstart code block area (<pre><code>)
- Common tasks list (5)
- Footer with version + links

Rules: No emojis. Inline SVG icons <= 3. Output JSON only.""".strip()

    if template == "G7":
        return f"""category: home_page
intent: generate

Build a premium editorial homepage for a product blog.
Brand: {brand}
Content: featured post, latest posts, categories, newsletter

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Rules:
- Strong typography hierarchy
- No clickbait copy
- Use tags, read-time chips, and a clean grid
- Newsletter CTA includes what happens next + privacy line
- No emojis. Inline SVG icons <= 2.
Output JSON only.""".strip()

    if template == "G8":
        return f"""category: waitlist_page
intent: generate

Create a waitlist page.
Brand: {brand}
What it is: {product_line}
Who it’s for: {persona}
Primary CTA: "Join the waitlist"

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Required:
- Hero + digest(3) + trust chips(3)
- Form with label/helper/error + aria-describedby
- What you’ll get (3 bullets)
- What happens next transparency block
- FAQ (>=6)

Rules: No emojis. Inline SVG icons <= 3. Output JSON only.""".strip()

    if template == "E1":
        category = rng.choice(_EXT_ECOM_CATEGORIES)
        return f"""category: ecom_home
intent: generate

Build an e-commerce homepage.
Brand: {brand}
Category: {category}
Primary CTA: "Shop bestsellers"
Secondary CTA: link-only

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Must include:
- Hero with category promise + digest(3) + trust chips(3)
- Bestseller grid (8 items, placeholder data objects)
- Value props row (5)
- Returns/shipping policy preview (trust)
- FAQ (>=6)

Rules: No emojis. No fake reviews. If showing ratings, label as “example UI” and avoid numbers unless provided.
Inline SVG icons <= 4. Output JSON only.""".strip()

    # G1 default
    return f"""category: home_page
intent: generate

Build a premium SaaS homepage.

Brand: {brand}
Product: {product_line}
Audience: {persona}
Primary CTA: "{cta}"
Secondary CTA: link-only

Style: {vibe}
Theme: choose light or dark, and choose a cohesive accent color palette that fits the brand (do not default to the same color every time)
Required sections:
- Header with story-driven logo mark (inline SVG, not a triangle)
- Hero: H1 + subhead + primary CTA + 3 trust chips + key info digest (3 bullets)
- How it works (3 steps)
- Value props (5 bullets, outcome-led)
- Proof wall (3 items, labeled provided/qualified/illustrative)
- Pricing summary (2 tiers)
- FAQ (>=6; include skeptical objections)
- Final CTA with what happens next + privacy line

Rules:
- No emojis. Inline SVG icons <= 6 total; icons must be custom and consistent.
- No UI libraries. Tailwind only.
- No fake logos/metrics/reviews. Qualify claims.

Output JSON only with app/page.tsx and any components.""".strip()


def generate_task_prompt_packed(
    config: Config,
    niche: NicheDefinition,
    page_type: PageType,
    seed: int,
    is_edit: bool = False,
    code_old: str | None = None,
    style_directive=None,
) -> str:
    pack = str(getattr(config.pipeline, "task_prompt_pack", "niche") or "niche").lower().strip()

    prompt: str

    if pack == "niche":
        prompt = generate_task_prompt(niche, page_type, seed, is_edit=is_edit, code_old=code_old)
    elif pack == "extended":
        prompt = generate_task_prompt_extended(niche, page_type, seed)
    elif pack == "os":
        # Only meaningful for dashboard-like pages; fall back otherwise.
        if page_type == PageType.ADMIN_DASHBOARD:
            # Force an OS template deterministically by reusing the extended generator's OS branch.
            # The template selector inside generate_task_prompt_extended already includes os_* cases.
            prompt = generate_task_prompt_extended(niche, page_type, seed=stable_hash(f"{seed}:os"))
        else:
            prompt = generate_task_prompt(niche, page_type, seed, is_edit=is_edit, code_old=code_old)
    elif pack == "mixed":
        rng = random.Random(seed)
        choice = rng.choices(["niche", "extended", "os"], weights=[0.5, 0.4, 0.1], k=1)[0]
        if choice == "extended":
            prompt = generate_task_prompt_extended(niche, page_type, seed)
        elif choice == "os":
            if page_type == PageType.ADMIN_DASHBOARD:
                prompt = generate_task_prompt_extended(niche, page_type, seed=stable_hash(f"{seed}:os"))
            else:
                # For non-dashboard pages, "os" becomes "extended" (still non-niche variety).
                prompt = generate_task_prompt_extended(niche, page_type, seed=stable_hash(f"{seed}:extended"))
        else:
            prompt = generate_task_prompt(niche, page_type, seed, is_edit=is_edit, code_old=code_old)
    else:
        # Unknown pack -> default safe behavior.
        log_warning(f"Unknown task_prompt_pack '{pack}', falling back to niche prompts")
        prompt = generate_task_prompt(niche, page_type, seed, is_edit=is_edit, code_old=code_old)

    # Add deterministic palette hint (helps reduce repeated default palettes without forcing a color).
    if "Palette seed:" not in prompt:
        prompt = (
            prompt.rstrip()
            + "\n\n"
            + f"Palette seed: {seed} (use this to vary the palette across tasks; avoid always defaulting to violet/indigo)"
        )

    # Optional: deterministic style routing block (first-class constraint).
    if not is_edit and "STYLE ROUTING (HARD CONSTRAINTS" not in prompt:
        if style_directive is not None:
            prompt = prompt.rstrip() + "\n\n" + style_directive.to_prompt_block()

    # Force deterministic mood/accent selection (non-edit tasks only) to prevent the planner
    # from collapsing onto the same palette across many tasks.
    if not is_edit and "brand.accent:" not in prompt and "brand.mood:" not in prompt:
        mood, accent = (
            (style_directive.mood, style_directive.accent)
            if style_directive is not None
            else _deterministic_brand_theme(seed, niche)
        )
        prompt = (
            prompt.rstrip()
            + "\n\n"
            + "Theme override (must follow exactly):\n"
            + f"brand.mood: {mood}\n"
            + f"brand.accent: {accent}"
        )

    # Add a deterministic creative-risk dial without forcing a specific style.
    # This gives the planner and generator a clear instruction to avoid "boring" pages
    # while still staying production-safe and maintainable.
    if "Creative risk:" not in prompt:
        risk_level = "high"
        if is_edit or page_type == PageType.EDIT:
            risk_level = "low"
        else:
            rng = random.Random(stable_hash(f"{seed}:creative_risk"))
            risk_level = rng.choices(["high", "medium", "low"], weights=[0.65, 0.25, 0.10], k=1)[0]

        prompt = (
            prompt.rstrip()
            + "\n\n"
            + f"Creative risk: {risk_level}\n"
            + "Guidance:\n"
            + "- high: take a tasteful creative risk (signature layout moment + unique motif) while keeping UX clear and build-safe.\n"
            + "- medium: mostly professional but include one signature moment (bento/timeline/comparison/proof wall) so it doesn’t feel generic.\n"
            + "- low: clean professional execution; avoid risky novelty; prioritize clarity and accessibility.\n"
        )

    return prompt.strip()


def generate_tasks(config: Config) -> Iterator[Task]:
    """Generate all tasks for the pipeline.

    Creates tasks_per_niche tasks per niche, covering all page types
    plus edit tasks (at least 20% of tasks).

    Args:
        config: Application configuration

    Yields:
        Task objects
    """
    niches = generate_niches()
    tasks_per_niche = config.pipeline.tasks_per_niche

    # Page types to cover (excluding edit, handled separately)
    page_types = [
        PageType.LANDING,
        PageType.DIRECTORY_HOME,
        PageType.CITY_INDEX,
        PageType.CATEGORY_INDEX,
        PageType.LISTING_PROFILE,
        PageType.ADMIN_DASHBOARD,
    ]

    # Optional filter: restrict to a subset of page types (e.g. ["landing"])
    if config.pipeline.page_type_filter:
        filtered: list[PageType] = []
        for name in config.pipeline.page_type_filter:
            try:
                filtered.append(PageType(name))
            except Exception:
                log_warning(f"Unknown page_type_filter entry ignored: {name}")
        if filtered:
            page_types = filtered
        else:
            log_warning("page_type_filter provided but none valid; using defaults")

    for niche_def in niches:
        niche = NicheDefinition(
            id=niche_def.id,
            vertical=niche_def.vertical,
            pattern=niche_def.pattern,
            description=niche_def.description,
        )

        if not page_types:
            continue

        # Generate exactly tasks_per_niche tasks per niche, wrapping page types as needed.
        # This enables landing-only runs to produce 2+ distinct landing tasks per niche.
        for i in range(max(0, int(tasks_per_niche))):
            page_type = page_types[i % len(page_types)]
            repeat_index = i // len(page_types)

            # Preserve historical IDs for the first cycle (repeat_index=0)
            # so existing runs remain resumable/deterministic.
            if repeat_index == 0:
                seed = stable_hash(f"{niche.id}:{page_type.value}")
            else:
                seed = stable_hash(f"{niche.id}:{page_type.value}:{repeat_index}")

            task_id = hashlib.sha256(
                f"{niche.id}:{page_type.value}:{seed}".encode()
            ).hexdigest()[:16]

            style_directive = _maybe_route_style(config, niche, page_type, seed)
            prompt = generate_task_prompt_packed(
                config,
                niche,
                page_type,
                seed,
                style_directive=style_directive,
            )
            # Record deterministic routing metadata for observability.
            if style_directive is not None:
                theme_mood = style_directive.mood
                theme_accent = style_directive.accent
            else:
                theme_mood, theme_accent = _deterministic_brand_theme(seed, niche)

            yield Task(
                id=task_id,
                niche_id=niche.id,
                page_type=page_type,
                seed=seed,
                prompt=prompt,
                is_edit=False,
                style_family=getattr(style_directive, "family", None) if style_directive else None,
                style_persona=getattr(style_directive, "persona", None) if style_directive else None,
                style_keywords_mandatory=getattr(style_directive, "keywords_mandatory", []) if style_directive else [],
                style_avoid=getattr(style_directive, "avoid", []) if style_directive else [],
                style_density=getattr(style_directive, "density", None) if style_directive else None,
                style_imagery_style=getattr(style_directive, "imagery_style", None) if style_directive else None,
                style_layout_motif=getattr(style_directive, "layout_motif", None) if style_directive else None,
                theme_mood=theme_mood,
                theme_accent=theme_accent,
            )

        # NOTE: Edit tasks are generated dynamically by the orchestrator
        # after a landing page is successfully generated. This ensures
        # edit tasks have real code_old from actual generated pages.
        # See orchestrator._create_edit_task_from_winner()


def save_niches(config: Config) -> Path:
    """Save niche definitions to JSON.

    Args:
        config: Application configuration

    Returns:
        Path to saved file
    """
    niches = generate_niches()
    output_path = config.prompts_path / "niches.json"

    config.prompts_path.mkdir(parents=True, exist_ok=True)

    # Atomically write to avoid corruption when multiple pipeline processes run in parallel.
    tmp_path = output_path.with_name(f"{output_path.name}.tmp.{os.getpid()}")
    with open(tmp_path, "w") as f:
        json.dump([n.model_dump() for n in niches], f, indent=2)
    os.replace(tmp_path, output_path)

    log_info(f"Saved {len(niches)} niches to {output_path}")
    return output_path


def save_tasks(config: Config) -> tuple[Path, int]:
    """Save all tasks to JSONL.

    Args:
        config: Application configuration

    Returns:
        Tuple of (path to saved file, task count)
    """
    output_path = config.prompts_path / "tasks.jsonl"
    config.prompts_path.mkdir(parents=True, exist_ok=True)

    count = 0
    # Atomically write to avoid corruption when multiple pipeline processes run in parallel.
    tmp_path = output_path.with_name(f"{output_path.name}.tmp.{os.getpid()}")
    with open(tmp_path, "w") as f:
        for task in generate_tasks(config):
            f.write(json.dumps(task.model_dump()) + "\n")
            count += 1
    os.replace(tmp_path, output_path)

    log_info(f"Saved {count} tasks to {output_path}")
    return output_path, count


def load_tasks(config: Config) -> list[Task]:
    """Load tasks from JSONL.

    Args:
        config: Application configuration

    Returns:
        List of tasks
    """
    tasks_path = config.prompts_path / "tasks.jsonl"

    if not tasks_path.exists():
        save_tasks(config)

    tasks = []
    with open(tasks_path) as f:
        for line in f:
            tasks.append(Task.model_validate_json(line))

    if config.pipeline.shuffle_tasks:
        rng = random.Random(config.pipeline.task_shuffle_seed)
        rng.shuffle(tasks)

    return tasks
