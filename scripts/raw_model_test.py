#!/usr/bin/env python3
"""
Raw Model Test - No Pipeline (25 Diverse Websites)
Compare Kimi K2 vs MiniMax M2 output quality without any pipeline processing.
Just system prompt + user prompt → raw output across 25 different site types.

Usage:
    python scripts/raw_model_test.py                    # Run all 25 prompts on both models
    python scripts/raw_model_test.py --model kimi       # Run all 25 on Kimi only
    python scripts/raw_model_test.py --limit 5          # Run first 5 prompts only
    python scripts/raw_model_test.py --index 3          # Run only prompt #3
    python scripts/raw_model_test.py --concurrency 2    # Run 2 at a time
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# 25 Diverse Website Prompts
TEST_PROMPTS = [
    # 1-5: Local Services
    {
        "id": "01_dog_walking",
        "mood": "LIGHT", "accent": "teal",
        "prompt": "Premium dog walking service in Austin. GPS tracking, certified handlers, daily photo updates. $25/walk, monthly packages available. 500+ happy pups served."
    },
    {
        "id": "02_tattoo_studio",
        "mood": "DARK", "accent": "red",
        "prompt": "High-end tattoo studio in Brooklyn. Custom designs, award-winning artists, sterile environment. Consultations required. Featured in Inked Magazine."
    },
    {
        "id": "03_yoga_studio",
        "mood": "LIGHT", "accent": "violet",
        "prompt": "Boutique yoga studio in Denver. Hot yoga, meditation, aerial classes. First class free. 200+ 5-star reviews. Certified instructors."
    },
    {
        "id": "04_auto_detailing",
        "mood": "DARK", "accent": "orange",
        "prompt": "Mobile auto detailing in Miami. Ceramic coating, paint correction, interior restoration. We come to you. Satisfaction guaranteed. 1000+ cars detailed."
    },
    {
        "id": "05_bakery",
        "mood": "LIGHT", "accent": "pink",
        "prompt": "Artisan bakery in Portland. Sourdough, croissants, custom cakes. Organic ingredients, baked fresh daily. Order online, pickup or delivery."
    },

    # 6-10: SaaS/Tech
    {
        "id": "06_analytics_saas",
        "mood": "DARK", "accent": "blue",
        "prompt": "Real-time analytics platform for e-commerce. Track conversions, customer journeys, revenue attribution. Integrates with Shopify, WooCommerce. Free 14-day trial."
    },
    {
        "id": "07_project_management",
        "mood": "LIGHT", "accent": "green",
        "prompt": "AI-powered project management for remote teams. Auto-scheduling, workload balancing, Slack integration. Used by 10K+ teams. SOC2 certified."
    },
    {
        "id": "08_email_marketing",
        "mood": "DARK", "accent": "violet",
        "prompt": "Email marketing automation for creators. Drag-drop builder, AI subject lines, advanced segmentation. Free up to 1000 subscribers. 99.9% deliverability."
    },
    {
        "id": "09_api_platform",
        "mood": "DARK", "accent": "cyan",
        "prompt": "Unified API for payment processing. One integration, 50+ payment methods. 99.99% uptime. PCI compliant. Used by Fortune 500 companies."
    },
    {
        "id": "10_design_tool",
        "mood": "LIGHT", "accent": "orange",
        "prompt": "Browser-based design tool for non-designers. AI-generated layouts, brand kit management, team collaboration. Free tier available. 50K+ users."
    },

    # 11-15: E-commerce/Products
    {
        "id": "11_coffee_subscription",
        "mood": "DARK", "accent": "amber",
        "prompt": "Premium coffee subscription. Single-origin beans, roasted fresh, shipped weekly. Personalized taste profiles. Skip or cancel anytime. $15/bag."
    },
    {
        "id": "12_skincare_brand",
        "mood": "LIGHT", "accent": "rose",
        "prompt": "Clean skincare for sensitive skin. Dermatologist-developed, fragrance-free, cruelty-free. 30-day money-back guarantee. As seen in Vogue."
    },
    {
        "id": "13_fitness_equipment",
        "mood": "DARK", "accent": "red",
        "prompt": "Smart home gym equipment. AI personal trainer, compact design, 500+ workouts. Financing available. 10K+ home gyms shipped."
    },
    {
        "id": "14_pet_food",
        "mood": "LIGHT", "accent": "green",
        "prompt": "Fresh pet food delivered. Vet-formulated, human-grade ingredients, personalized portions. 90% of dogs show improved health in 30 days."
    },
    {
        "id": "15_sustainable_fashion",
        "mood": "LIGHT", "accent": "teal",
        "prompt": "Sustainable fashion marketplace. Verified ethical brands, carbon-neutral shipping, circular fashion program. B-Corp certified."
    },

    # 16-20: Professional Services
    {
        "id": "16_law_firm",
        "mood": "DARK", "accent": "blue",
        "prompt": "Tech startup law firm in San Francisco. IP, fundraising, M&A. Fixed-fee packages. Trusted by 200+ YC companies. Free 30-min consultation."
    },
    {
        "id": "17_accounting",
        "mood": "LIGHT", "accent": "green",
        "prompt": "Accounting for freelancers and creators. Tax prep, quarterly estimates, bookkeeping. Flat monthly fee. Save an average of $3K in taxes."
    },
    {
        "id": "18_therapy_practice",
        "mood": "LIGHT", "accent": "violet",
        "prompt": "Online therapy practice. Licensed therapists, flexible scheduling, insurance accepted. Specializing in anxiety, depression, relationships. HIPAA compliant."
    },
    {
        "id": "19_consulting",
        "mood": "DARK", "accent": "amber",
        "prompt": "Growth consulting for DTC brands. Revenue optimization, customer acquisition, retention strategies. Average 3X ROI for clients. Book a strategy call."
    },
    {
        "id": "20_recruiting",
        "mood": "LIGHT", "accent": "blue",
        "prompt": "Technical recruiting for startups. Senior engineers, product managers, designers. Success-based fees. Average 21-day time-to-hire. 95% retention rate."
    },

    # 21-25: Unique/Creative
    {
        "id": "21_escape_room",
        "mood": "DARK", "accent": "red",
        "prompt": "Immersive escape rooms in Chicago. Horror, mystery, sci-fi themes. 2-8 players. Corporate team building available. #1 rated on TripAdvisor."
    },
    {
        "id": "22_music_lessons",
        "mood": "LIGHT", "accent": "violet",
        "prompt": "Online music lessons for adults. Guitar, piano, voice. Learn songs you love. Flexible scheduling, patient instructors. First lesson free."
    },
    {
        "id": "23_coworking",
        "mood": "LIGHT", "accent": "teal",
        "prompt": "Design-forward coworking in Seattle. Private offices, hot desks, meeting rooms. 24/7 access, craft coffee, community events. Day passes available."
    },
    {
        "id": "24_wedding_planning",
        "mood": "LIGHT", "accent": "rose",
        "prompt": "Luxury wedding planning in Napa Valley. Full-service coordination, vendor relationships, day-of management. 100+ dream weddings. Featured in Martha Stewart."
    },
    {
        "id": "25_arcade_gaming",
        "mood": "DARK", "accent": "teal",
        "prompt": "Premium pixel-art arcade in Minneapolis. Members-only lounge, monthly tournaments, authentic cabinet collection. Free trial, monthly plans. 10K+ happy gamers."
    },
]

from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleRequest
import urllib.request
import urllib.error

# Config
PROJECT_ID = "gen-lang-client-0707199026"
REGION = "global"
VERTEX_BASE = f"https://aiplatform.googleapis.com/v1beta1/projects/{PROJECT_ID}/locations/{REGION}/endpoints/openapi"

# Models
MODELS = {
    "kimi": "moonshotai/kimi-k2-thinking-maas",
    "minimax": "minimaxai/minimax-m2-maas",
}

# Load system prompt
PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "titan_ui_system_long.txt"


def get_token():
    """Get fresh Vertex AI token."""
    key_paths = [
        Path.home() / ".config/kilo-code/kilo-code-vertex-key.json",
        Path.home() / ".config/gcloud/application_default_credentials.json",
    ]

    for key_path in key_paths:
        if key_path.exists():
            credentials = service_account.Credentials.from_service_account_file(
                str(key_path),
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            credentials.refresh(GoogleRequest())
            return credentials.token

    # Try default credentials
    import google.auth
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(GoogleRequest())
    return credentials.token


def load_system_prompt():
    """Load the TITAN UI system prompt."""
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text()
    else:
        print(f"Warning: System prompt not found at {PROMPT_PATH}")
        return "You are a UI code generator. Output valid JSON with TSX code."


def call_model(model_key: str, user_prompt: str, system_prompt: str) -> dict:
    """Call a model via Vertex MaaS and return response."""
    model_id = MODELS[model_key]
    token = get_token()

    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 12000,
        "temperature": 0.85,
    }

    req = urllib.request.Request(
        f"{VERTEX_BASE}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST"
    )

    print(f"\n{'='*60}")
    print(f"Calling {model_key.upper()} ({model_id})...")
    print(f"{'='*60}")

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode())
            return {
                "model": model_key,
                "model_id": model_id,
                "success": True,
                "content": data["choices"][0]["message"]["content"],
                "usage": data.get("usage", {}),
            }
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        return {
            "model": model_key,
            "model_id": model_id,
            "success": False,
            "error": f"HTTP {e.code}: {error_body[:500]}",
        }
    except Exception as e:
        return {
            "model": model_key,
            "model_id": model_id,
            "success": False,
            "error": str(e),
        }


def save_output(result: dict, user_prompt: str, output_dir: Path):
    """Save model output to file."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{result['model']}_{timestamp}.json"
    filepath = output_dir / filename

    output = {
        "timestamp": timestamp,
        "model": result["model"],
        "model_id": result["model_id"],
        "user_prompt": user_prompt,
        "success": result["success"],
    }

    if result["success"]:
        output["content"] = result["content"]
        output["usage"] = result.get("usage", {})

        # Try to extract just the JSON part (after </think>)
        content = result["content"]
        if "</think>" in content:
            json_part = content.split("</think>", 1)[1].strip()
            output["json_extracted"] = json_part
    else:
        output["error"] = result["error"]

    filepath.write_text(json.dumps(output, indent=2))
    print(f"Saved: {filepath}")
    return filepath


def build_full_prompt(test_case: dict) -> str:
    """Build the full user prompt from a test case."""
    return f"""OUTPUT_MODE=TSX_ONLY
Creative risk: high
Brand mood: {test_case['mood']}
Brand accent: {test_case['accent']}

Create a landing page for the following business:
{test_case['prompt']}

Make it feel premium and distinctive, not generic. Follow all accessibility requirements."""


def run_single_test(test_case: dict, model_key: str, system_prompt: str, output_dir: Path) -> dict:
    """Run a single test case for a single model."""
    prompt_id = test_case["id"]
    user_prompt = build_full_prompt(test_case)

    print(f"  [{prompt_id}] {model_key.upper()}...")

    result = call_model(model_key, user_prompt, system_prompt)
    result["prompt_id"] = prompt_id
    result["test_case"] = test_case

    if result["success"]:
        # Save with prompt ID in filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{prompt_id}_{model_key}_{timestamp}.json"
        filepath = output_dir / filename

        output = {
            "timestamp": timestamp,
            "prompt_id": prompt_id,
            "model": model_key,
            "model_id": result["model_id"],
            "test_case": test_case,
            "user_prompt": user_prompt,
            "success": True,
            "content": result["content"],
            "usage": result.get("usage", {}),
        }

        # Extract JSON part
        content = result["content"]
        if "</think>" in content:
            json_part = content.split("</think>", 1)[1].strip()
            output["json_extracted"] = json_part

        filepath.write_text(json.dumps(output, indent=2))
        result["filepath"] = str(filepath)
        print(f"    OK - saved to {filename}")
    else:
        print(f"    FAILED: {result['error'][:100]}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Raw model test - 25 diverse websites")
    parser.add_argument("--model", choices=["kimi", "minimax"], help="Test only one model")
    parser.add_argument("--limit", type=int, help="Limit to first N prompts")
    parser.add_argument("--index", type=int, help="Run only prompt at this index (0-24)")
    parser.add_argument("--concurrency", type=int, default=1, help="How many to run in parallel")
    parser.add_argument("--output-dir", type=Path, default=Path("out/raw_tests"), help="Output directory")
    parser.add_argument("--list", action="store_true", help="List all prompts and exit")

    args = parser.parse_args()

    # List mode
    if args.list:
        print("Available test prompts:")
        for i, tc in enumerate(TEST_PROMPTS):
            print(f"  {i:2d}. [{tc['id']}] {tc['mood']}/{tc['accent']} - {tc['prompt'][:60]}...")
        return

    system_prompt = load_system_prompt()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Select prompts
    if args.index is not None:
        prompts = [TEST_PROMPTS[args.index]]
    elif args.limit:
        prompts = TEST_PROMPTS[:args.limit]
    else:
        prompts = TEST_PROMPTS

    # Select models
    if args.model:
        models = [args.model]
    else:
        models = ["kimi", "minimax"]

    total_tasks = len(prompts) * len(models)
    print(f"Running {len(prompts)} prompts × {len(models)} models = {total_tasks} total generations")
    print(f"Output directory: {args.output_dir}")
    print(f"Concurrency: {args.concurrency}")
    print()

    all_results = []
    success_count = 0
    fail_count = 0

    if args.concurrency == 1:
        # Sequential
        for i, test_case in enumerate(prompts):
            print(f"\n[{i+1}/{len(prompts)}] {test_case['id']}")
            for model_key in models:
                result = run_single_test(test_case, model_key, system_prompt, args.output_dir)
                all_results.append(result)
                if result["success"]:
                    success_count += 1
                else:
                    fail_count += 1
    else:
        # Parallel with ThreadPoolExecutor
        tasks = [(tc, m) for tc in prompts for m in models]

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(run_single_test, tc, m, system_prompt, args.output_dir): (tc, m)
                for tc, m in tasks
            }

            for future in as_completed(futures):
                result = future.result()
                all_results.append(result)
                if result["success"]:
                    success_count += 1
                else:
                    fail_count += 1

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Total: {total_tasks} | Success: {success_count} | Failed: {fail_count}")
    print()

    # Per-model breakdown
    for model_key in models:
        model_results = [r for r in all_results if r["model"] == model_key]
        model_success = sum(1 for r in model_results if r["success"])
        print(f"  {model_key.upper()}: {model_success}/{len(model_results)} succeeded")

    # List failures
    failures = [r for r in all_results if not r["success"]]
    if failures:
        print(f"\nFailures:")
        for r in failures:
            print(f"  - {r.get('prompt_id', '?')} / {r['model']}: {r.get('error', 'unknown')[:80]}")

    print(f"\nOutputs saved to: {args.output_dir}")

    # Save summary JSON
    summary_path = args.output_dir / f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    summary = {
        "timestamp": datetime.now().isoformat(),
        "total": total_tasks,
        "success": success_count,
        "failed": fail_count,
        "models": models,
        "prompts_count": len(prompts),
        "results": [
            {
                "prompt_id": r.get("prompt_id"),
                "model": r["model"],
                "success": r["success"],
                "filepath": r.get("filepath"),
                "error": r.get("error"),
            }
            for r in all_results
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"Summary saved to: {summary_path}")


if __name__ == "__main__":
    main()
