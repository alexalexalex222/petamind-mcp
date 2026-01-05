"""Renderer module - captures screenshots using Playwright."""

import asyncio
import json
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Any

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
_port_start_lock = asyncio.Lock()


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

            proc_cm = None
            port = None

            # Start Next on a stable port. Under render concurrency > 1, selecting a free
            # port is racy unless we serialize "pick port + start server + confirm ready".
            async with _port_start_lock:
                port_start = int(getattr(config.pipeline, "render_port_start", 3000) or 3000)
                port = find_available_port(port_start)
                log_info(f"Candidate {candidate.id}: Starting server on port {port}...")
                proc_cm = managed_process(f"npm run start -- -p {port}", cwd=work_dir)
                await proc_cm.__aenter__()
                await _wait_for_server(port, timeout=30)

            try:
                # Capture screenshots
                gates_enabled = bool(getattr(config.pipeline, "deterministic_gates_enabled", False))
                axe_source = _load_axe_source_if_enabled(work_dir, config) if gates_enabled else None
                screenshots, axe_violations = await _capture_screenshots(
                    candidate.id,
                    port,
                    render_dir,
                    config.pipeline.render_timeout_ms,
                    axe_source=axe_source,
                    axe_timeout_ms=getattr(config.pipeline, "axe_timeout_ms", 60000),
                )

                # Deterministic gates (axe + Lighthouse) run BEFORE subjective vision judging.
                # These are best-effort: failures should not crash the pipeline.
                if gates_enabled:
                    await _run_deterministic_gates(
                        candidate=candidate,
                        work_dir=work_dir,
                        url=f"http://127.0.0.1:{port}",
                        output_dir=render_dir,
                        config=config,
                        axe_violations=axe_violations,
                        axe_available=axe_source is not None,
                    )
            finally:
                if proc_cm is not None:
                    await proc_cm.__aexit__(None, None, None)

        # Update candidate
        candidate.screenshot_paths = screenshots
        if screenshots:
            if candidate.status != CandidateStatus.DISCARDED:
                candidate.status = CandidateStatus.RENDERED
                log_info(f"Candidate {candidate.id}: Captured {len(screenshots)} screenshots")
            else:
                log_warning(
                    f"Candidate {candidate.id}: Captured {len(screenshots)} screenshots but discarded by gates"
                )
        else:
            log_error(f"Candidate {candidate.id}: Failed to capture screenshots")

        return screenshots


def _load_axe_source_if_enabled(work_dir: Path, config: Config) -> str | None:
    """Load axe-core source (minified) from node_modules if enabled."""
    if not getattr(config.pipeline, "axe_gate_enabled", False):
        return None

    axe_path = work_dir / "node_modules" / "axe-core" / "axe.min.js"
    if not axe_path.exists():
        log_warning("axe-core not found in template dependencies; skipping axe gate")
        return None

    try:
        return axe_path.read_text(encoding="utf-8")
    except Exception as e:
        log_warning(f"Failed to read axe-core script: {e}")
        return None


async def _run_deterministic_gates(
    *,
    candidate: Candidate,
    work_dir: Path,
    url: str,
    output_dir: Path,
    config: Config,
    axe_violations: list[dict[str, Any]] | None,
    axe_available: bool,
) -> None:
    """Run deterministic quality gates (axe + Lighthouse) and annotate candidate.

    If deterministic_gates_enforce is true, failing candidates are marked DISCARDED.
    """
    failures: list[str] = []

    # === RENDER INTEGRITY (blank/empty page protection) ===
    # A blank/empty page can pass axe + Lighthouse because there's effectively
    # nothing to audit. Catch that deterministically via screenshot size.
    desktop_png = output_dir / "desktop.png"
    if desktop_png.exists():
        try:
            size_b = int(desktop_png.stat().st_size)
        except Exception:
            size_b = -1

        # 1440×900 truly blank pages compress extremely small (~7–15 KB).
        # Real pages are typically 100 KB+.
        if 0 <= size_b < 20_000:
            failures.append(f"render:blank_screenshot:{size_b}")
    else:
        failures.append("render:missing_desktop_screenshot")

    # === AXE ===
    if getattr(config.pipeline, "axe_gate_enabled", False):
        if not axe_available:
            failures.append("axe:missing_dependency")
            candidate.axe_violations = []
        else:
            candidate.axe_violations = list(axe_violations or [])

        fail_impacts = {
            str(x).strip().lower()
            for x in (getattr(config.pipeline, "axe_fail_impacts", ["critical"]) or [])
            if str(x).strip()
        }

        axe_failed = False
        for v in candidate.axe_violations:
            impact = str(v.get("impact") or "").strip().lower()
            if impact and impact in fail_impacts:
                axe_failed = True
                failures.append(f"axe:{impact}:{v.get('id')}")

        if not axe_available:
            log_warning(f"Candidate {candidate.id}: axe skipped (missing dependency)")
        elif not candidate.axe_violations:
            log_info(f"Candidate {candidate.id}: axe found 0 violations")
        elif axe_failed:
            log_warning(
                f"Candidate {candidate.id}: axe failed ({len(candidate.axe_violations)} violations)"
            )
        else:
            log_info(
                f"Candidate {candidate.id}: axe passed ({len(candidate.axe_violations)} violations)"
            )

    # === LIGHTHOUSE ===
    if getattr(config.pipeline, "lighthouse_gate_enabled", False):
        lh_scores, lh_report_path, lh_error = await _run_lighthouse(
            url=url,
            work_dir=work_dir,
            output_dir=output_dir,
            config=config,
        )

        candidate.lighthouse_scores = lh_scores
        candidate.lighthouse_report_path = lh_report_path

        if lh_error:
            failures.append(f"lighthouse:error:{lh_error}")
        else:
            min_scores: dict[str, float] = getattr(config.pipeline, "lighthouse_min_scores", {}) or {}
            for key, min_val in min_scores.items():
                try:
                    min_f = float(min_val)
                except Exception:
                    continue

                score_val = candidate.lighthouse_scores.get(key)
                if score_val is None:
                    failures.append(f"lighthouse:missing:{key}")
                    continue
                if float(score_val) < min_f:
                    failures.append(f"lighthouse:{key}:{score_val:.2f}<{min_f:.2f}")

    candidate.deterministic_failures = failures
    candidate.deterministic_passed = len(failures) == 0

    if candidate.deterministic_passed:
        log_info(f"Candidate {candidate.id}: deterministic gates PASS")
        return

    log_warning(
        f"Candidate {candidate.id}: deterministic gates FAIL ({len(failures)} issues)"
    )

    if getattr(config.pipeline, "deterministic_gates_enforce", False):
        candidate.status = CandidateStatus.DISCARDED
        candidate.error = "Deterministic gates failed: " + "; ".join(failures[:10])


async def _run_lighthouse(
    *,
    url: str,
    work_dir: Path,
    output_dir: Path,
    config: Config,
) -> tuple[dict[str, float], str | None, str | None]:
    """Run Lighthouse against a local URL.

    Returns:
        (scores, report_path, error)
    """
    lighthouse_bin = work_dir / "node_modules" / ".bin" / "lighthouse"
    if not lighthouse_bin.exists():
        log_warning("Lighthouse binary not found in template dependencies; skipping")
        return {}, None, "missing_binary"

    report_path = output_dir / "lighthouse.json"

    preset = str(getattr(config.pipeline, "lighthouse_preset", "desktop") or "desktop").strip()
    if preset not in ("desktop", "mobile"):
        preset = "desktop"

    # Prefer Playwright's Chromium so the pipeline doesn't depend on system Chrome installs.
    chrome_path = None
    try:
        async with async_playwright() as p:
            chrome_path = p.chromium.executable_path
    except Exception:
        chrome_path = None

    cmd = (
        f"{shlex.quote(str(lighthouse_bin))} {shlex.quote(url)} "
        f"--output json --output-path {shlex.quote(str(report_path))} "
        "--quiet "
        "--only-categories=performance,accessibility,best-practices,seo "
        f"--preset={shlex.quote(preset)} "
    )
    if chrome_path:
        cmd += f"--chrome-path={shlex.quote(str(chrome_path))} "
    # Keep flags minimal for portability.
    cmd += "--chrome-flags='--headless --disable-gpu' "

    returncode, stdout, stderr = await run_command(
        cmd,
        cwd=work_dir,
        timeout_ms=int(getattr(config.pipeline, "lighthouse_timeout_ms", 180000) or 180000),
    )
    if returncode != 0:
        err_preview = (stderr or stdout or "").strip().splitlines()[-1:]  # last line only
        log_warning(f"Lighthouse failed (rc={returncode}): {err_preview}")
        return {}, str(report_path) if report_path.exists() else None, "run_failed"

    if not report_path.exists():
        log_warning("Lighthouse finished but report file missing")
        return {}, None, "missing_report"

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as e:
        log_warning(f"Failed to parse Lighthouse JSON: {e}")
        return {}, str(report_path), "invalid_json"

    categories = report.get("categories") if isinstance(report, dict) else None
    if not isinstance(categories, dict):
        return {}, str(report_path), "missing_categories"

    def _score(cat_key: str) -> float | None:
        cat = categories.get(cat_key)
        if not isinstance(cat, dict):
            return None
        score = cat.get("score")
        try:
            return float(score) if score is not None else None
        except Exception:
            return None

    scores: dict[str, float] = {}
    perf = _score("performance")
    if perf is not None:
        scores["performance"] = perf
    a11y = _score("accessibility")
    if a11y is not None:
        scores["accessibility"] = a11y
    bp = _score("best-practices")
    if bp is not None:
        scores["best_practices"] = bp
    seo = _score("seo")
    if seo is not None:
        scores["seo"] = seo

    log_info(
        "Lighthouse scores: "
        + ", ".join(f"{k}={v:.2f}" for k, v in scores.items())
    )

    return scores, str(report_path), None


async def _wait_for_server(port: int, timeout: int = 30) -> None:
    """Wait for server to be ready.

    Args:
        port: Server port
        timeout: Timeout in seconds
    """
    import httpx

    # Use 127.0.0.1 instead of localhost to avoid DNS/proxy edge cases.
    url = f"http://127.0.0.1:{port}"

    # Reuse one client (cheaper) and ignore environment proxy vars (more reliable).
    async with httpx.AsyncClient(trust_env=False) as client:
        for _ in range(timeout * 2):  # Check every 0.5s
            try:
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
    *,
    axe_source: str | None = None,
    axe_timeout_ms: int = 60000,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Capture screenshots at all viewports.

    Args:
        candidate_id: Candidate ID for logging
        port: Server port
        output_dir: Directory to save screenshots
        timeout_ms: Timeout per screenshot

    Returns:
        Tuple of:
          - Dict mapping viewport name to screenshot path
          - Axe-core violation summaries (empty if axe not run)
    """
    screenshots = {}
    url = f"http://127.0.0.1:{port}"
    axe_violations: list[dict[str, Any]] = []

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

                    # Run axe on ONE viewport (desktop) to keep it cheap.
                    if axe_source and name == "desktop" and not axe_violations:
                        try:
                            await page.add_script_tag(content=axe_source)

                            async def _run() -> Any:
                                return await page.evaluate("async () => await axe.run()")

                            results = await asyncio.wait_for(
                                _run(),
                                timeout=max(1.0, float(axe_timeout_ms) / 1000.0),
                            )
                            axe_violations = _summarize_axe_violations(results)
                        except Exception as e:
                            log_warning(f"Candidate {candidate_id}: axe run failed - {e}")

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

    return screenshots, axe_violations


def _summarize_axe_violations(results: Any) -> list[dict[str, Any]]:
    """Return a small, stable subset of axe-core violations for storage."""
    if not isinstance(results, dict):
        return []
    raw = results.get("violations", [])
    if not isinstance(raw, list):
        return []

    summarized: list[dict[str, Any]] = []
    for v in raw:
        if not isinstance(v, dict):
            continue
        nodes = v.get("nodes", [])
        nodes_count = len(nodes) if isinstance(nodes, list) else 0

        summarized.append(
            {
                "id": v.get("id"),
                "impact": v.get("impact"),
                "description": v.get("description"),
                "help": v.get("help"),
                "helpUrl": v.get("helpUrl"),
                "nodes": nodes_count,
            }
        )

    # Keep deterministic ordering for DB diffs and easier reading.
    summarized.sort(key=lambda x: (str(x.get("impact") or ""), str(x.get("id") or "")))
    return summarized


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
