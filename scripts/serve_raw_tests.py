#!/usr/bin/env python3
"""
Serve raw test outputs locally for viewing.
Creates a Next.js app for each output and serves them.
"""

import json
import subprocess
import shutil
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import os
import time

RAW_TESTS_DIR = Path(__file__).parent.parent / "out" / "raw_tests"
SERVE_DIR = Path(__file__).parent.parent / "out" / "raw_tests_serve"
TEMPLATE_DIR = Path(__file__).parent.parent / "template"

def extract_tsx_from_json(json_path: Path) -> str | None:
    """Extract TSX content from a raw test JSON file."""
    try:
        data = json.loads(json_path.read_text())

        # Try json_extracted first
        if "json_extracted" in data and data["json_extracted"]:
            extracted = data["json_extracted"]
            if isinstance(extracted, str):
                extracted = json.loads(extracted)
            if isinstance(extracted, dict) and "files" in extracted:
                for f in extracted["files"]:
                    if f.get("path") == "app/page.tsx":
                        return f["content"]

        # Try parsing from content directly
        if "content" in data and data["content"]:
            content = data["content"]
            if "</think>" in content:
                json_part = content.split("</think>", 1)[1].strip()
                try:
                    parsed = json.loads(json_part)
                    if isinstance(parsed, dict) and "files" in parsed:
                        for f in parsed["files"]:
                            if f.get("path") == "app/page.tsx":
                                return f["content"]
                except:
                    pass

        return None
    except Exception as e:
        print(f"Error extracting from {json_path.name}: {e}")
        return None


def setup_nextjs_app(name: str, tsx_content: str, port: int) -> Path:
    """Set up a Next.js app with the given TSX content."""
    app_dir = SERVE_DIR / name

    # Copy template
    if app_dir.exists():
        shutil.rmtree(app_dir)
    shutil.copytree(TEMPLATE_DIR, app_dir)

    # Write page.tsx
    page_path = app_dir / "app" / "page.tsx"
    page_path.write_text(tsx_content)

    return app_dir


def build_app(app_dir: Path) -> bool:
    """Build the Next.js app."""
    try:
        result = subprocess.run(
            ["npm", "run", "build"],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=120
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Build failed for {app_dir.name}: {e}")
        return False


def main():
    # Get all JSON files, deduplicate by taking latest per prompt+model
    json_files = list(RAW_TESTS_DIR.glob("*.json"))

    # Group by prompt_model and take latest
    by_key = {}
    for f in json_files:
        if f.name.startswith("summary"):
            continue
        # Parse: 01_dog_walking_kimi_20251230_080056.json
        parts = f.stem.rsplit("_", 2)  # Split off timestamp parts
        if len(parts) >= 3:
            key = parts[0]  # e.g., "01_dog_walking_kimi"
            # Actually need to be smarter - find the model part
            name = f.stem
            for model in ["kimi", "minimax"]:
                if f"_{model}_" in name:
                    idx = name.index(f"_{model}_")
                    key = name[:idx + len(model) + 1]
                    break

            if key not in by_key or f.stat().st_mtime > by_key[key].stat().st_mtime:
                by_key[key] = f

    json_files = list(by_key.values())
    print(f"Found {len(json_files)} unique outputs to serve")

    # Create serve directory
    SERVE_DIR.mkdir(parents=True, exist_ok=True)

    # Extract TSX from each
    apps = []
    for json_path in sorted(json_files):
        tsx = extract_tsx_from_json(json_path)
        if tsx:
            name = json_path.stem.rsplit("_", 2)[0]  # Remove timestamp
            # Find model
            for model in ["kimi", "minimax"]:
                if f"_{model}_" in json_path.stem:
                    idx = json_path.stem.index(f"_{model}_")
                    name = json_path.stem[:idx + len(model) + 1]
                    break
            apps.append((name, tsx, json_path))
            print(f"  ✓ {name}")
        else:
            print(f"  ✗ {json_path.name} (no TSX found)")

    print(f"\nSetting up {len(apps)} apps...")

    # Set up all apps
    app_dirs = []
    for name, tsx, _ in apps:
        app_dir = setup_nextjs_app(name, tsx, 3000)
        app_dirs.append((name, app_dir))

    print(f"\nBuilding apps (this may take a while)...")

    # Build in parallel
    successful = []
    failed = []

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(build_app, app_dir): (name, app_dir) for name, app_dir in app_dirs}
        for future in futures:
            name, app_dir = futures[future]
            if future.result():
                successful.append((name, app_dir))
                print(f"  ✓ {name}")
            else:
                failed.append(name)
                print(f"  ✗ {name} (build failed)")

    print(f"\n{'='*60}")
    print(f"Built: {len(successful)}/{len(apps)}")
    print(f"Failed: {len(failed)}")

    if failed:
        print(f"\nFailed builds: {', '.join(failed)}")

    # Create index HTML
    index_html = SERVE_DIR / "index.html"
    html_content = """<!DOCTYPE html>
<html>
<head>
    <title>Raw Model Test Results</title>
    <style>
        body { font-family: system-ui; max-width: 1200px; margin: 0 auto; padding: 20px; background: #111; color: #eee; }
        h1 { border-bottom: 1px solid #333; padding-bottom: 10px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px; }
        .card { background: #222; border-radius: 8px; padding: 15px; }
        .card h3 { margin: 0 0 10px 0; font-size: 14px; }
        .card a { color: #0af; text-decoration: none; }
        .card a:hover { text-decoration: underline; }
        .kimi { border-left: 3px solid #f90; }
        .minimax { border-left: 3px solid #09f; }
        .badge { display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 11px; margin-left: 8px; }
        .badge.kimi { background: #f90; color: #000; }
        .badge.minimax { background: #09f; color: #000; }
        .prompt-group { margin-bottom: 30px; }
        .prompt-title { font-size: 18px; margin-bottom: 10px; color: #888; }
    </style>
</head>
<body>
    <h1>Raw Model Test Results</h1>
    <p>25 prompts × 2 models. Click to view each page.</p>
    <p><strong>Legend:</strong> <span class="badge kimi">KIMI</span> <span class="badge minimax">MINIMAX</span></p>
    <div class="grid">
"""

    for name, app_dir in sorted(successful):
        model = "kimi" if "_kimi" in name else "minimax"
        prompt_id = name.replace(f"_{model}", "")
        html_content += f"""
        <div class="card {model}">
            <h3>{prompt_id} <span class="badge {model}">{model.upper()}</span></h3>
            <a href="{name}/out/index.html" target="_blank">View Page →</a>
        </div>
"""

    html_content += """
    </div>
    <p style="margin-top: 40px; color: #666;">Serve this directory: python -m http.server 8080</p>
</body>
</html>
"""

    index_html.write_text(html_content)

    # Export static HTML from each successful build
    print("\nExporting static HTML...")
    for name, app_dir in successful:
        # Next.js builds to .next, we need to export
        # Actually the build already creates static in out/ if configured
        out_dir = app_dir / "out"
        if not out_dir.exists():
            # Try to export
            subprocess.run(
                ["npx", "next", "export"],
                cwd=app_dir,
                capture_output=True,
                timeout=60
            )

    print(f"\n{'='*60}")
    print(f"Done! Serve the results:")
    print(f"  cd {SERVE_DIR}")
    print(f"  python -m http.server 8080")
    print(f"  Open: http://localhost:8080")


if __name__ == "__main__":
    main()
