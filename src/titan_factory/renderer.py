"""Renderer module - captures screenshots using Playwright."""

import asyncio
import shutil
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright

from titan_factory.config import Config
from titan_factory.schema import Candidate, CandidateStatus
from titan_factory.utils import (
    ensure_dir,
    find_available_port,
    log_error,
    log_info,
    log_warning,
    managed_process,
    run_command,
)
from titan_factory.validator import setup_node_modules_cache, validate_file_path

# Viewport configurations: (name, width, height)
# Names must match what judge.py expects: "mobile", "tablet", "desktop"
VIEWPORTS = [
    ("mobile", 375, 812),     # Mobile
    ("tablet", 768, 1024),    # Tablet
    ("desktop", 1440, 900),   # Desktop
]

_render_semaphore: asyncio.Semaphore | None = None


def _get_render_semaphore(config: Config) -> asyncio.Semaphore:
    global _render_semaphore
    if _render_semaphore is None:
        _render_semaphore = asyncio.Semaphore(max(1, int(config.budget.concurrency_render)))
    return _render_semaphore


async def render_candidate(
    candidate: Candidate,
    run_dir: Path,
    config: Config,
) -> dict[str, str]:
    """Render screenshots for a candidate.

    Args:
        candidate: Validated candidate (build must have passed)
        run_dir: Run output directory
        config: Application configuration

    Returns:
        Dict mapping viewport name to screenshot path
    """
    if candidate.status != CandidateStatus.BUILD_PASSED:
        log_error(f"Candidate {candidate.id}: Cannot render, build not passed")
        return {}

    # Rendering spins up a Next server + Playwright and needs a free port.
    # Keep global concurrency bounded to avoid port races and CPU thrash.
    async with _get_render_semaphore(config):
        # Create output directory
        render_dir = run_dir / "renders" / candidate.task_id / candidate.id
        ensure_dir(render_dir)

        screenshots = {}

        # Ensure node_modules cache exists (shared with validator)
        cache_dir = await setup_node_modules_cache(config)

        # Create temp working directory with the candidate code
        with tempfile.TemporaryDirectory(prefix="titan_render_") as temp_dir:
            work_dir = Path(temp_dir)

            # Copy template
            for item in config.template_path.iterdir():
                if item.name in ("node_modules", ".next"):
                    continue
                if item.is_dir():
                    shutil.copytree(item, work_dir / item.name)
                else:
                    shutil.copy(item, work_dir)

            # Symlink node_modules from cache (avoid npm ci per candidate)
            (work_dir / "node_modules").symlink_to(cache_dir / "node_modules")

            # Write generated files (with the same path allowlist as validator)
            rejected_paths = []
            for f in candidate.files:
                validated_path = validate_file_path(f.path, work_dir)

                if validated_path is None:
                    rejected_paths.append(f.path)
                    continue

                validated_path.parent.mkdir(parents=True, exist_ok=True)
                validated_path.write_text(f.content)

            if rejected_paths:
                log_warning(
                    f"Candidate {candidate.id}: Rejected {len(rejected_paths)} file(s) "
                    f"with invalid paths: {rejected_paths}"
                )

            log_info(f"Candidate {candidate.id}: Building for production...")
            returncode, stdout, stderr = await run_command(
                "npm run build",
                cwd=work_dir,
                timeout_ms=config.pipeline.build_timeout_ms,
            )

            if returncode != 0:
                log_error(f"Candidate {candidate.id}: Build failed during render")
                return {}

            # Find available port
            port = find_available_port(3000)

            # Start Next.js server
            log_info(f"Candidate {candidate.id}: Starting server on port {port}...")

            async with managed_process(f"npm run start -- -p {port}", cwd=work_dir) as proc:
                # Wait for server to be ready
                await _wait_for_server(port, timeout=30)

                # Capture screenshots
                screenshots = await _capture_screenshots(
                    candidate.id,
                    port,
                    render_dir,
                    config.pipeline.render_timeout_ms,
                )

        # Update candidate
        candidate.screenshot_paths = screenshots
        if screenshots:
            candidate.status = CandidateStatus.RENDERED
            log_info(f"Candidate {candidate.id}: Captured {len(screenshots)} screenshots")
        else:
            log_error(f"Candidate {candidate.id}: Failed to capture screenshots")

        return screenshots


async def _wait_for_server(port: int, timeout: int = 30) -> None:
    """Wait for server to be ready.

    Args:
        port: Server port
        timeout: Timeout in seconds
    """
    import httpx

    url = f"http://localhost:{port}"

    for _ in range(timeout * 2):  # Check every 0.5s
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=2)
                if response.status_code in (200, 304):
                    return
        except Exception:
            pass
        await asyncio.sleep(0.5)

    raise TimeoutError(f"Server did not start within {timeout}s")


async def _capture_screenshots(
    candidate_id: str,
    port: int,
    output_dir: Path,
    timeout_ms: int,
) -> dict[str, str]:
    """Capture screenshots at all viewports.

    Args:
        candidate_id: Candidate ID for logging
        port: Server port
        output_dir: Directory to save screenshots
        timeout_ms: Timeout per screenshot

    Returns:
        Dict mapping viewport name to screenshot path
    """
    screenshots = {}
    url = f"http://localhost:{port}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        try:
            for name, width, height in VIEWPORTS:
                context = await browser.new_context(
                    viewport={"width": width, "height": height},
                    # Keep screenshots small enough for vision models and storage.
                    # Retina full-page screenshots can become very large and occasionally
                    # fail vision upload/parsing ("Unable to process input image").
                    device_scale_factor=1,
                )

                page = await context.new_page()

                try:
                    # Navigate and wait for content
                    # Use domcontentloaded for robustness.
                    # Some pages may keep small network requests open (or load an image slowly),
                    # which can cause "networkidle" to hang and timeout.
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

                    # Additional wait for any animations
                    await page.wait_for_timeout(1000)

                    # Capture full page screenshot
                    screenshot_path = output_dir / f"{name}.png"
                    await page.screenshot(
                        path=str(screenshot_path),
                        full_page=True,
                    )

                    screenshots[name] = str(screenshot_path)
                    log_info(f"Candidate {candidate_id}: Captured {name}")

                except Exception as e:
                    log_error(f"Candidate {candidate_id}: Failed to capture {name} - {e}")

                finally:
                    await context.close()

        finally:
            await browser.close()

    return screenshots


async def render_all_candidates(
    candidates: list[Candidate],
    run_dir: Path,
    config: Config,
) -> list[Candidate]:
    """Render all candidates concurrently.

    Args:
        candidates: List of validated candidates
        run_dir: Run output directory
        config: Application configuration

    Returns:
        List of candidates with screenshot paths
    """
    # Filter to only build-passed candidates
    to_render = [c for c in candidates if c.status == CandidateStatus.BUILD_PASSED]

    if not to_render:
        log_info("No candidates to render")
        return candidates

    log_info(f"Rendering {len(to_render)} candidates...")

    # Render sequentially to avoid port conflicts
    # (Could parallelize with proper port allocation)
    for candidate in to_render:
        try:
            await render_candidate(candidate, run_dir, config)
        except Exception as e:
            log_error(f"Candidate {candidate.id}: Render failed - {e}")
            candidate.error = str(e)

    return candidates
