"""Validator module - runs Next.js builds to validate candidates."""

import asyncio
import fnmatch
import hashlib
import re
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
    "app/*.tsx",             # Allow small helper components under app/
    "app/*/page.tsx",         # app/about/page.tsx, etc.
    "app/**/page.tsx",        # Nested routes
    "app/**/*.tsx",          # Allow colocated components (e.g., app/WaitlistForm.tsx)
    "components/*.tsx",
    "components/**/*.tsx",
    "lib/*.ts",
    "lib/**/*.ts",
    "public/*",
    "styles/*.css",
]

_CLIENT_HOOK_REGEX = re.compile(
    r"\buse(State|Effect|Memo|Callback|Ref|Reducer|LayoutEffect|Transition|DeferredValue|Id|Context)\b"
)
_CLIENT_EVENT_HANDLER_REGEX = re.compile(
    r"\bon(Click|Submit|Change|Input|KeyDown|KeyUp|KeyPress|Focus|Blur|MouseEnter|MouseLeave)\s*="
)
_USE_CLIENT_DIRECTIVE_REGEX = re.compile(r"^\s*(['\"])use client\1\s*;?\s*$", re.MULTILINE)
_USE_SERVER_DIRECTIVE_REGEX = re.compile(r"^\s*(['\"])use server\1\s*;?\s*$", re.MULTILINE)


def _strip_outer_markdown_fence(content: str) -> str:
    stripped = content.strip()
    if not (stripped.startswith("```") and stripped.endswith("```")):
        return content

    lines = stripped.splitlines()
    if len(lines) < 2:
        return content
    if not lines[0].startswith("```"):
        return content
    if lines[-1].strip() != "```":
        return content

    inner = "\n".join(lines[1:-1]).strip("\n")
    return f"{inner}\n" if inner else ""


def _has_use_client_directive(content: str) -> bool:
    head = "\n".join(content.splitlines()[:10])
    return _USE_CLIENT_DIRECTIVE_REGEX.search(head) is not None


def _needs_use_client_directive(content: str) -> bool:
    # If the file declares "use server", never inject "use client".
    head = "\n".join(content.splitlines()[:10])
    if _USE_SERVER_DIRECTIVE_REGEX.search(head) is not None:
        return False

    if _has_use_client_directive(content):
        return False

    if _CLIENT_HOOK_REGEX.search(content) is not None:
        return True

    if _CLIENT_EVENT_HANDLER_REGEX.search(content) is not None:
        return True

    for token in ("window.", "document.", "localStorage", "sessionStorage", "navigator.", "location."):
        if token in content:
            return True

    return False


def _inject_use_client_directive(content: str) -> str:
    if _has_use_client_directive(content):
        return content
    return f"'use client';\n\n{content}"


def _apply_deterministic_prebuild_fixes(candidate: Candidate) -> None:
    """Apply safe, deterministic code fixes before attempting a Next.js build."""
    added_use_client = False
    stripped_fences = False

    for f in candidate.files:
        original = f.content
        content = _strip_outer_markdown_fence(original)
        if content != original:
            stripped_fences = True

        if f.path in ("app/page.tsx", "app/page.jsx") and _needs_use_client_directive(content):
            content = _inject_use_client_directive(content)
            added_use_client = True

        if content != original:
            f.content = content

    if added_use_client:
        log_info(f"Candidate {candidate.id}: Preflight added 'use client' directive (client-only usage detected)")
    if stripped_fences and not added_use_client:
        log_info(f"Candidate {candidate.id}: Preflight stripped outer markdown fences from file content")


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
_node_modules_lock = asyncio.Lock()

# Global semaphore to limit concurrent builds (npm/next build is CPU + IO heavy)
_build_semaphore: asyncio.Semaphore | None = None


def _get_build_semaphore(config: Config) -> asyncio.Semaphore:
    global _build_semaphore
    if _build_semaphore is None:
        _build_semaphore = asyncio.Semaphore(max(1, int(config.budget.concurrency_build)))
    return _build_semaphore


async def setup_node_modules_cache(config: Config) -> Path:
    """Set up cached node_modules directory.

    Args:
        config: Application configuration

    Returns:
        Path to cached node_modules
    """
    global _node_modules_cache

    async with _node_modules_lock:
        if _node_modules_cache and _node_modules_cache.exists():
            return _node_modules_cache

        # Create cache directory
        cache_dir = config.out_path / "cache" / "node_modules_template"
        ensure_dir(cache_dir)

        # Cache invalidation: if template deps change, rebuild node_modules.
        # This prevents subtle "works on my machine" bugs when package-lock updates.
        template_lock = config.template_path / "package-lock.json"
        template_pkg = config.template_path / "package.json"
        hash_source = template_lock if template_lock.exists() else template_pkg
        template_hash = hashlib.sha256(hash_source.read_bytes()).hexdigest()
        hash_file = cache_dir / ".titan_template_deps_hash"

        node_modules_dir = cache_dir / "node_modules"
        if node_modules_dir.exists():
            cached_hash = None
            try:
                cached_hash = hash_file.read_text(encoding="utf-8").strip()
            except Exception:
                cached_hash = None

            if cached_hash == template_hash:
                _node_modules_cache = cache_dir
                log_info("Using cached node_modules")
                return cache_dir

            log_warning("Template dependencies changed; rebuilding cached node_modules")
            try:
                shutil.rmtree(node_modules_dir)
            except Exception as e:
                log_warning(f"Failed to remove cached node_modules: {e}")

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

        try:
            hash_file.write_text(template_hash, encoding="utf-8")
        except Exception as e:
            log_warning(f"Failed to write deps hash file: {e}")

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

    _apply_deterministic_prebuild_fixes(candidate)

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

        build_sem = _get_build_semaphore(config)
        async with build_sem:
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
