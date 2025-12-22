"""Validator module - runs Next.js builds to validate candidates."""

import asyncio
import fnmatch
import shutil
import tempfile
from pathlib import Path

from titan_factory.config import Config
from titan_factory.schema import Candidate, CandidateStatus
from titan_factory.utils import ensure_dir, log_error, log_info, log_warning, run_command


# Allowlist of file paths models are permitted to generate
# Prevents path traversal attacks and limits scope of generated code
ALLOWED_PATH_PATTERNS = [
    "app/page.tsx",
    "app/layout.tsx",
    "app/globals.css",
    "app/*/page.tsx",         # app/about/page.tsx, etc.
    "app/**/page.tsx",        # Nested routes
    "components/*.tsx",
    "components/**/*.tsx",
    "lib/*.ts",
    "lib/**/*.ts",
    "public/*",
    "styles/*.css",
]


def is_path_allowed(file_path: str) -> bool:
    """Check if a generated file path is in the allowlist.

    Args:
        file_path: Path to check (relative to project root)

    Returns:
        True if path is allowed, False otherwise
    """
    # Normalize path (remove leading/trailing slashes, normalize separators)
    normalized = file_path.strip("/").replace("\\", "/")

    # Block any path with parent directory traversal
    if ".." in normalized:
        return False

    # Block absolute paths
    if normalized.startswith("/"):
        return False

    # Check against allowlist patterns
    for pattern in ALLOWED_PATH_PATTERNS:
        if fnmatch.fnmatch(normalized, pattern):
            return True

    return False


def validate_file_path(file_path: str, work_dir: Path) -> Path | None:
    """Validate and resolve a file path safely.

    Args:
        file_path: Relative file path from model output
        work_dir: Working directory root

    Returns:
        Resolved absolute path if valid, None if rejected
    """
    if not is_path_allowed(file_path):
        log_warning(f"Rejected file path not in allowlist: {file_path}")
        return None

    # Resolve the path and verify it stays within work_dir
    resolved = (work_dir / file_path).resolve()

    try:
        resolved.relative_to(work_dir.resolve())
    except ValueError:
        log_warning(f"Path traversal attempt detected: {file_path}")
        return None

    return resolved

# Cache for node_modules to avoid repeated npm ci
_node_modules_cache: Path | None = None


async def setup_node_modules_cache(config: Config) -> Path:
    """Set up cached node_modules directory.

    Args:
        config: Application configuration

    Returns:
        Path to cached node_modules
    """
    global _node_modules_cache

    if _node_modules_cache and _node_modules_cache.exists():
        return _node_modules_cache

    # Create cache directory
    cache_dir = config.out_path / "cache" / "node_modules_template"
    ensure_dir(cache_dir)

    # Check if already populated
    if (cache_dir / "node_modules").exists():
        _node_modules_cache = cache_dir
        log_info("Using cached node_modules")
        return cache_dir

    # Copy template and install
    log_info("Setting up node_modules cache (this may take a minute)...")

    # Copy package files
    shutil.copy(config.template_path / "package.json", cache_dir)
    package_lock = config.template_path / "package-lock.json"
    if package_lock.exists():
        shutil.copy(package_lock, cache_dir)

    # Run npm ci
    returncode, stdout, stderr = await run_command(
        "npm ci",
        cwd=cache_dir,
        timeout_ms=300000,  # 5 minutes for install
    )

    if returncode != 0:
        log_error(f"npm ci failed: {stderr}")
        raise RuntimeError(f"Failed to install dependencies: {stderr}")

    _node_modules_cache = cache_dir
    log_info("node_modules cache ready")
    return cache_dir


async def validate_candidate(
    candidate: Candidate,
    config: Config,
) -> tuple[bool, str]:
    """Validate a candidate by running Next.js build.

    Args:
        candidate: Candidate to validate
        config: Application configuration

    Returns:
        Tuple of (success, build_logs)
    """
    if not candidate.files:
        return False, "No files to validate"

    # Ensure node_modules cache exists
    cache_dir = await setup_node_modules_cache(config)

    # Create temp working directory
    with tempfile.TemporaryDirectory(prefix="titan_build_") as temp_dir:
        work_dir = Path(temp_dir)

        # Copy template structure
        for item in config.template_path.iterdir():
            if item.name == "node_modules":
                continue
            if item.is_dir():
                shutil.copytree(item, work_dir / item.name)
            else:
                shutil.copy(item, work_dir)

        # Symlink node_modules from cache
        (work_dir / "node_modules").symlink_to(cache_dir / "node_modules")

        # Write generated files (with path validation)
        rejected_paths = []
        for f in candidate.files:
            validated_path = validate_file_path(f.path, work_dir)

            if validated_path is None:
                rejected_paths.append(f.path)
                continue

            # Ensure parent directory exists
            validated_path.parent.mkdir(parents=True, exist_ok=True)

            # Write content
            validated_path.write_text(f.content)

        if rejected_paths:
            log_warning(
                f"Candidate {candidate.id}: Rejected {len(rejected_paths)} file(s) "
                f"with invalid paths: {rejected_paths}"
            )

        # Run build
        log_info(f"Building candidate {candidate.id}...")

        returncode, stdout, stderr = await run_command(
            "npm run build",
            cwd=work_dir,
            timeout_ms=config.pipeline.build_timeout_ms,
        )

        build_logs = f"{stdout}\n{stderr}"

        if returncode == 0:
            log_info(f"Candidate {candidate.id}: Build succeeded")
            candidate.status = CandidateStatus.BUILD_PASSED
            return True, build_logs
        else:
            log_error(f"Candidate {candidate.id}: Build failed")
            candidate.status = CandidateStatus.BUILD_FAILED
            candidate.build_logs = build_logs
            return False, build_logs


async def validate_with_retry(
    candidate: Candidate,
    config: Config,
    patcher_fn=None,
) -> tuple[bool, Candidate]:
    """Validate candidate with patching retries.

    IMPORTANT: This function may replace the candidate with a patched version.
    The caller MUST use the returned candidate, not the original reference.

    Args:
        candidate: Candidate to validate
        config: Application configuration
        patcher_fn: Optional async function to patch failures

    Returns:
        Tuple of (success, candidate) - the candidate may be a patched version
    """
    max_rounds = config.pipeline.max_fix_rounds

    for attempt in range(max_rounds + 1):
        success, build_logs = await validate_candidate(candidate, config)

        if success:
            return True, candidate

        # Try patching if we have retries left and a patcher
        if attempt < max_rounds and patcher_fn:
            log_info(
                f"Candidate {candidate.id}: Attempting fix (round {attempt + 1}/{max_rounds})"
            )
            patched = await patcher_fn(candidate, build_logs, config)

            # Copy patched fields back to original candidate (in-place mutation)
            # This ensures the caller's reference stays valid
            candidate.files = patched.files
            candidate.fix_rounds = patched.fix_rounds
            candidate.build_logs = patched.build_logs
            candidate.error = patched.error

            if candidate.error:
                log_error(f"Candidate {candidate.id}: Patching failed, giving up")
                break
        else:
            break

    return False, candidate


async def get_build_output_dir(candidate: Candidate, config: Config) -> Path | None:
    """Get the build output directory for a validated candidate.

    This is used for production builds that need .next/standalone.

    Args:
        candidate: Validated candidate
        config: Application configuration

    Returns:
        Path to .next directory, or None if not available
    """
    # In our setup, we use temp directories, so this returns None
    # In a production setup, you might want to persist build outputs
    return None
