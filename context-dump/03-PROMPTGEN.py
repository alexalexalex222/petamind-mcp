"""Prompt generation for niches and tasks."""

import hashlib
import json
import random
from pathlib import Path
from typing import Iterator

from titan_factory.config import Config
from titan_factory.schema import NicheDefinition, PageType, Task
from titan_factory.utils import log_info


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
    moods = ["dark", "light"]
    mood = rng.choice(moods)

    accents = ["blue", "teal", "violet", "green", "orange"]
    accent = rng.choice(accents)

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
        prompt = f"""Create a {mood} themed landing page for {business_name}, a {niche.description.lower()} in {city}.

Style: {vibe}, {accent} accent
CTA: "{cta}"

Include: hero with headline + subheadline, trust indicators, how-it-works section (3-4 steps), testimonials (use placeholders), pricing tiers, FAQ, and final CTA.

Make it {niche.pattern} and premium. No UI libraries."""

    elif page_type == PageType.DIRECTORY_HOME:
        prompt = f"""Create a {mood} directory homepage for finding {niche.vertical.replace("_", " ")} services.

Style: {vibe}, {accent} accent

Include: search bar with filters (location, category), featured listings grid (cards with image, name, rating, location), category quick-links, and footer.

Make navigation intuitive and mobile-friendly. No UI libraries."""

    elif page_type == PageType.CITY_INDEX:
        prompt = f"""Create a {mood} city index page for {niche.vertical.replace("_", " ")} in {city}.

Style: {vibe}, {accent} accent

Include: city hero with local stats, filterable grid of 8-12 listings (placeholder cards), map placeholder, local tips section, and related cities.

Optimize for local SEO structure. No UI libraries."""

    elif page_type == PageType.CATEGORY_INDEX:
        category = niche.vertical.replace("_", " ").title()
        prompt = f"""Create a {mood} category index page for "{category}" services.

Style: {vibe}, {accent} accent

Include: category hero with description, subcategory filters, grid of 12+ listings, sort options (rating, distance, price), and pagination.

Cards should show: image, name, rating, location, price range. No UI libraries."""

    elif page_type == PageType.LISTING_PROFILE:
        prompt = f"""Create a {mood} listing detail page for {business_name}, a {niche.description.lower()}.

Style: {vibe}, {accent} accent
CTA: "{cta}"

Include: hero with gallery placeholder, business info (hours, contact, location), services/offerings grid, reviews section with ratings, and booking/contact form.

Make it conversion-focused. No UI libraries."""

    elif page_type == PageType.ADMIN_DASHBOARD:
        prompt = f"""Create a {mood} admin dashboard for managing a {niche.vertical.replace("_", " ")} business.

Style: {vibe}, {accent} accent

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

Keep the existing design language but improve as specified. Maintain {mood} theme with {accent} accent.

<CODE_OLD>
{code_old or "// Original code will be provided"}
</CODE_OLD>

No UI libraries. Output complete updated code."""

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

    for niche_def in niches:
        niche = NicheDefinition(
            id=niche_def.id,
            vertical=niche_def.vertical,
            pattern=niche_def.pattern,
            description=niche_def.description,
        )

        task_count = 0

        # Generate one task per page type
        for i, page_type in enumerate(page_types):
            if task_count >= tasks_per_niche:
                break

            seed = stable_hash(f"{niche.id}:{page_type.value}")

            task_id = hashlib.sha256(
                f"{niche.id}:{page_type.value}:{seed}".encode()
            ).hexdigest()[:16]

            prompt = generate_task_prompt(niche, page_type, seed)

            yield Task(
                id=task_id,
                niche_id=niche.id,
                page_type=page_type,
                seed=seed,
                prompt=prompt,
                is_edit=False,
            )
            task_count += 1

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

    with open(output_path, "w") as f:
        json.dump([n.model_dump() for n in niches], f, indent=2)

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
    with open(output_path, "w") as f:
        for task in generate_tasks(config):
            f.write(json.dumps(task.model_dump()) + "\n")
            count += 1

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

    return tasks
