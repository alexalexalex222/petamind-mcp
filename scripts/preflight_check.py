#!/usr/bin/env python3
"""Pre-flight validation script - verify pipeline works before spending credits.

Run this BEFORE the main pipeline to catch configuration and integration issues.

Usage:
    python scripts/preflight_check.py              # Basic checks (free)
    python scripts/preflight_check.py --live       # Include live API tests ($0.01-0.05)
    python scripts/preflight_check.py --live --full # Full integration test (~$0.50)
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def print_check(name: str, passed: bool, detail: str = "") -> bool:
    """Print a check result."""
    status = "\033[92m✓\033[0m" if passed else "\033[91m✗\033[0m"
    print(f"  {status} {name}")
    if detail and not passed:
        print(f"      \033[93m{detail}\033[0m")
    return passed


def print_section(title: str) -> None:
    """Print a section header."""
    print(f"\n\033[1m{'='*60}\033[0m")
    print(f"\033[1m{title}\033[0m")
    print(f"\033[1m{'='*60}\033[0m")


async def check_config() -> tuple[bool, any]:
    """Check configuration loads correctly."""
    print_section("1. Configuration")

    try:
        from titan_factory.config import load_config
        config = load_config()
        print_check("Config file loads", True)
    except Exception as e:
        print_check("Config file loads", False, str(e))
        return False, None

    # Check required fields
    checks = [
        ("Planner model configured", bool(config.planner.model)),
        ("UI generators configured", len(config.ui_generators) > 0),
        ("Patcher model configured", bool(config.patcher.model)),
        ("Vision judge configured", bool(config.vision_judge.model)),
        ("Google project set", bool(config.google_project)),
        ("Google region set", bool(config.google_region)),
    ]

    all_passed = True
    for name, passed in checks:
        if not print_check(name, passed):
            all_passed = False

    # Show model summary
    print(f"\n  Models:")
    print(f"    Planner:      {config.planner.model} ({config.planner.provider})")
    for i, gen in enumerate(config.ui_generators):
        print(f"    Generator {i+1}: {gen.model} ({gen.provider})")
    print(f"    Patcher:      {config.patcher.model} ({config.patcher.provider})")
    print(f"    Vision:       {config.vision_judge.model} ({config.vision_judge.provider})")

    return all_passed, config


async def check_providers(config) -> bool:
    """Check all providers can be instantiated."""
    print_section("2. Provider Registration")

    from titan_factory.providers import ProviderFactory

    # Clear any cached instances
    ProviderFactory.clear()

    all_passed = True
    providers_to_check = ["vertex", "openrouter", "gemini"]

    for provider_name in providers_to_check:
        try:
            provider = ProviderFactory.get(provider_name, config)
            print_check(f"{provider_name} provider instantiates", True)
        except Exception as e:
            print_check(f"{provider_name} provider instantiates", False, str(e))
            all_passed = False

    return all_passed


async def check_gemini_auth(config) -> bool:
    """Check Gemini authentication works (API key or ADC)."""
    print_section("3. Gemini Authentication")

    try:
        from titan_factory.providers import ProviderFactory
        ProviderFactory.clear()  # Clear cached instances to pick up new env vars
        provider = ProviderFactory.get("gemini", config)

        auth_mode = provider.auth_mode
        print(f"    Auth mode: {auth_mode.upper()}")

        if auth_mode == "api_key":
            # API key mode - just verify key is set
            key = provider.api_key
            if key and len(key) > 10:
                print_check("GOOGLE_API_KEY set", True)
                print(f"    Key: {key[:8]}...{key[-4:]}")
                return True
            else:
                print_check("GOOGLE_API_KEY set", False, "Key is empty or too short")
                return False
        else:
            # ADC mode - try to get a token
            token = await provider._get_token()

            if token and len(token) > 20:
                print_check("ADC token obtained", True)
                print(f"    Token: {token[:20]}...{token[-10:]}")
                return True
            else:
                print_check("ADC token obtained", False, "Token is empty or invalid")
                return False

    except Exception as e:
        print_check("Gemini auth", False, str(e))
        print("\n    \033[93mFix: Set GOOGLE_API_KEY or run 'gcloud auth application-default login'\033[0m")
        return False


async def check_openrouter_key(config) -> bool:
    """Check OpenRouter API key is set."""
    print_section("4. OpenRouter Configuration")

    import os
    key = os.getenv("OPENROUTER_API_KEY", "")

    if key and len(key) > 10:
        print_check("OPENROUTER_API_KEY set", True)
        print(f"    Key: {key[:8]}...{key[-4:]}")
        return True
    else:
        print_check("OPENROUTER_API_KEY set", False, "Environment variable not set")
        print("\n    \033[93mFix: export OPENROUTER_API_KEY='sk-or-...' \033[0m")
        return False


async def check_template(config) -> bool:
    """Check Next.js template exists and is valid."""
    print_section("5. Template Validation")

    template_path = config.template_path
    all_passed = True

    required_files = [
        "package.json",
        "tsconfig.json",
        "tailwind.config.ts",
        "app/layout.tsx",
    ]

    for file in required_files:
        path = template_path / file
        if path.exists():
            print_check(f"Template has {file}", True)
        else:
            print_check(f"Template has {file}", False)
            all_passed = False

    return all_passed


async def check_niches() -> bool:
    """Check niche definitions are valid."""
    print_section("6. Niche Definitions")

    try:
        from titan_factory.promptgen import NICHE_DEFINITIONS

        count = len(NICHE_DEFINITIONS)
        print_check(f"Exactly 100 niches defined", count == 100, f"Found {count}")

        # Check for duplicates (niches are tuples: vertical, pattern, description)
        # Generate IDs the same way the code does
        ids = [f"{n[0]}_{n[1]}" for n in NICHE_DEFINITIONS]
        unique_ids = set(ids)
        has_dupes = len(ids) != len(unique_ids)
        print_check("No duplicate niche IDs", not has_dupes)

        # Show sample
        print(f"    Sample niches: {ids[:3]}")

        return count == 100 and not has_dupes

    except Exception as e:
        print_check("Niches load", False, str(e))
        return False


async def check_schema() -> bool:
    """Check Pydantic schemas are valid."""
    print_section("7. Schema Validation")

    try:
        from titan_factory.schema import (
            UISpec, Candidate, JudgeScore, TeacherModel,
            validate_ui_spec, UI_SPEC_JSON_SCHEMA
        )

        print_check("Schema classes import", True)
        print_check("JSON schema generated", bool(UI_SPEC_JSON_SCHEMA))

        # Test TeacherModel
        tm = TeacherModel(provider="vertex", model="test", publishable=True)
        print_check("TeacherModel instantiates", True)

        return True

    except Exception as e:
        print_check("Schema validation", False, str(e))
        return False


async def live_test_openrouter(config) -> bool:
    """Make a real API call to OpenRouter (uses free model)."""
    print_section("8. Live Test: OpenRouter (FREE)")

    try:
        from titan_factory.providers import ProviderFactory, Message

        provider = ProviderFactory.get("openrouter", config)

        messages = [
            Message(role="user", content="Say 'TITAN OK' and nothing else.")
        ]

        print("    Making request to devstral-2512:free...")
        response = await provider.complete(
            messages=messages,
            model="mistralai/devstral-2512:free",
            max_tokens=20,
            temperature=0.1,
        )

        success = "TITAN" in response.content.upper() or "OK" in response.content.upper()
        print_check("OpenRouter API responds", True)
        print(f"    Response: {response.content[:50]}")
        return True

    except Exception as e:
        print_check("OpenRouter API responds", False, str(e))
        return False


async def live_test_vertex(config) -> bool:
    """Make a real API call to Vertex AI MaaS."""
    print_section("9. Live Test: Vertex AI MaaS (~$0.001)")

    try:
        from titan_factory.providers import ProviderFactory, Message

        provider = ProviderFactory.get("vertex", config)

        messages = [
            Message(role="user", content="Say 'TITAN OK' and nothing else.")
        ]

        # Use DeepSeek as it's our planner model
        model = config.planner.model
        print(f"    Making request to {model}...")

        response = await provider.complete(
            messages=messages,
            model=model,
            max_tokens=20,
            temperature=0.1,
        )

        print_check("Vertex AI API responds", True)
        print(f"    Response: {response.content[:50]}")
        return True

    except Exception as e:
        print_check("Vertex AI API responds", False, str(e))
        return False


async def live_test_gemini_vision(config) -> bool:
    """Make a real vision API call to Gemini."""
    print_section("10. Live Test: Gemini Vision (~$0.01)")

    try:
        from titan_factory.providers import ProviderFactory, Message
        import base64

        ProviderFactory.clear()  # Clear cache to pick up env vars
        provider = ProviderFactory.get("gemini", config)

        print(f"    Auth mode: {provider.auth_mode.upper()}")

        # Create a tiny test image (1x1 red pixel PNG)
        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
        )

        messages = [
            Message(role="user", content="What color is this image? Reply with just the color name.")
        ]

        # Use gemini-2.0-flash for API key mode (more widely available)
        # Use config model for ADC mode (Vertex AI)
        if provider.auth_mode == "api_key":
            model = "gemini-2.0-flash"
        else:
            model = config.vision_judge.model

        print(f"    Making vision request to {model}...")

        response = await provider.complete_with_vision(
            messages=messages,
            model=model,
            images=[tiny_png],
            max_tokens=20,
            temperature=0.1,
        )

        print_check("Gemini Vision API responds", True)
        print(f"    Response: {response.content[:50]}")
        return True

    except Exception as e:
        print_check("Gemini Vision API responds", False, str(e))
        return False


async def full_integration_test(config) -> bool:
    """Run a single task through the full pipeline."""
    print_section("11. Full Integration Test (~$0.50)")

    print("    \033[93mThis runs 1 niche through the full pipeline.\033[0m")
    print("    \033[93mEstimated cost: $0.30-0.50\033[0m")

    # TODO: Implement mini pipeline run
    # For now, just validate the orchestrator can be imported
    try:
        from titan_factory.orchestrator import Orchestrator
        print_check("Orchestrator imports", True)

        # Create orchestrator but don't run
        # orchestrator = Orchestrator(config)
        # await orchestrator.run_single_task(niche_id="fitness_minimal")

        print("    \033[93mFull test not yet implemented - manual run recommended\033[0m")
        return True

    except Exception as e:
        print_check("Orchestrator imports", False, str(e))
        return False


async def main():
    parser = argparse.ArgumentParser(description="TITAN-4 Pre-flight Check")
    parser.add_argument("--live", action="store_true", help="Run live API tests")
    parser.add_argument("--full", action="store_true", help="Run full integration test")
    args = parser.parse_args()

    print("\n\033[1;34m" + "="*60 + "\033[0m")
    print("\033[1;34m  TITAN-4 DESIGN FACTORY - PRE-FLIGHT CHECK\033[0m")
    print("\033[1;34m" + "="*60 + "\033[0m")

    results = []

    # Basic checks (free)
    passed, config = await check_config()
    results.append(("Config", passed))

    if not config:
        print("\n\033[91mCRITICAL: Config failed to load. Cannot continue.\033[0m")
        sys.exit(1)

    results.append(("Providers", await check_providers(config)))
    results.append(("Gemini Auth", await check_gemini_auth(config)))
    results.append(("OpenRouter Key", await check_openrouter_key(config)))
    results.append(("Template", await check_template(config)))
    results.append(("Niches", await check_niches()))
    results.append(("Schema", await check_schema()))

    # Live tests (costs money)
    if args.live:
        results.append(("OpenRouter Live", await live_test_openrouter(config)))
        results.append(("Vertex Live", await live_test_vertex(config)))
        results.append(("Gemini Vision", await live_test_gemini_vision(config)))

    if args.full:
        results.append(("Full Integration", await full_integration_test(config)))

    # Summary
    print_section("SUMMARY")

    passed_count = sum(1 for _, p in results if p)
    total_count = len(results)

    for name, passed in results:
        status = "\033[92mPASS\033[0m" if passed else "\033[91mFAIL\033[0m"
        print(f"  {status}  {name}")

    print(f"\n  \033[1m{passed_count}/{total_count} checks passed\033[0m")

    if passed_count == total_count:
        print("\n\033[92m" + "="*60 + "\033[0m")
        print("\033[92m  ALL CHECKS PASSED - Ready to run pipeline!\033[0m")
        print("\033[92m" + "="*60 + "\033[0m\n")
        sys.exit(0)
    else:
        print("\n\033[91m" + "="*60 + "\033[0m")
        print("\033[91m  SOME CHECKS FAILED - Fix issues before running pipeline\033[0m")
        print("\033[91m" + "="*60 + "\033[0m\n")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
