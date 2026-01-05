import anyio
from pathlib import Path

from titan_factory import mcp_code_server


def test_derive_auto_context_queries_prefers_long_terms_and_skips_stopwords() -> None:
    q = mcp_code_server._derive_auto_context_queries(
        "Fix the broken payment webhook handler in src/payments/webhook.py",
        max_queries=5,
    )
    assert "broken" in q
    assert "payment" in q
    assert "webhook" in q
    assert "handler" in q
    assert "the" not in q


def test_auto_context_files_uses_rg_when_available(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("def foo():\n    return 1\n", encoding="utf-8")

    async def fake_run_command(cmd: str, cwd=None, timeout_ms=120000, capture_output=True):
        if cmd.startswith("command -v rg"):
            return 0, "/usr/bin/rg\n", ""
        if cmd.startswith("rg -l"):
            # Include a traversal path to ensure filtering works.
            return 0, "src/foo.py\n../oops.py\n", ""
        return 1, "", ""

    monkeypatch.setattr(mcp_code_server, "run_command", fake_run_command)

    async def run():
        return await mcp_code_server._auto_context_files(repo_root=repo, queries=["foo"], max_files=10)

    files = anyio.run(run)
    assert files == ["src/foo.py"]


def test_maybe_symlink_reuse_dirs_creates_symlink(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    worktree.mkdir()

    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "x.txt").write_text("x", encoding="utf-8")

    created = mcp_code_server._maybe_symlink_reuse_dirs(
        repo_root=repo, worktree=worktree, reuse_dirs=["node_modules"]
    )
    assert created == ["node_modules"]
    assert (worktree / "node_modules").is_symlink()


def test_build_context_blob_truncates_total_chars(tmp_path: Path) -> None:
    repo = tmp_path
    (repo / "a.txt").write_text("a" * 50, encoding="utf-8")
    (repo / "b.txt").write_text("b" * 50, encoding="utf-8")

    blob = mcp_code_server._build_context_blob(
        repo_root=repo,
        context_files=["a.txt", "b.txt"],
        max_file_chars=10_000,
        max_total_chars=80,
    )
    assert "a.txt" in blob
    assert "…(context truncated)…" in blob


def test_infer_test_command_prefers_pnpm_when_lock_present(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    (repo / "package.json").write_text("{}", encoding="utf-8")
    (repo / "pnpm-lock.yaml").write_text("lock", encoding="utf-8")

    async def fake_run_command(cmd: str, cwd=None, timeout_ms=120000, capture_output=True):
        if cmd.startswith("command -v "):
            if "pnpm" in cmd:
                return 0, "/usr/bin/pnpm\n", ""
            if "npm" in cmd:
                return 0, "/usr/bin/npm\n", ""
            if "yarn" in cmd:
                return 1, "", ""
        return 1, "", ""

    monkeypatch.setattr(mcp_code_server, "run_command", fake_run_command)

    async def run():
        return await mcp_code_server._infer_test_command(repo)

    cmd, reason = anyio.run(run)
    assert cmd == "pnpm test"
    assert "pnpm" in reason.lower()


def test_infer_test_command_uses_pytest_when_python_signals(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")

    async def fake_run_command(cmd: str, cwd=None, timeout_ms=120000, capture_output=True):
        if cmd.startswith("command -v "):
            if "pytest" in cmd:
                return 0, "/usr/bin/pytest\n", ""
        return 1, "", ""

    monkeypatch.setattr(mcp_code_server, "run_command", fake_run_command)

    async def run():
        return await mcp_code_server._infer_test_command(repo)

    cmd, reason = anyio.run(run)
    assert cmd == "pytest -q"
    assert "python" in reason.lower()


def test_infer_test_command_defaults_to_true_when_unknown_repo(tmp_path: Path) -> None:
    async def run():
        return await mcp_code_server._infer_test_command(tmp_path)

    cmd, reason = anyio.run(run)
    assert cmd == "true"
    assert "skipping" in reason.lower()


def test_pick_preview_port_caps_attempts_to_stride(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def fake_find_available_port(start: int = 3000, max_attempts: int = 100) -> int:
        calls.append((start, max_attempts))
        return start + 1

    monkeypatch.setattr(mcp_code_server, "find_available_port", fake_find_available_port)
    monkeypatch.setenv("TITAN_MCP_PORT_STRIDE", "25")
    monkeypatch.setenv("TITAN_MCP_PORT_ATTEMPTS", "1000")  # should be capped to stride

    port = mcp_code_server._pick_preview_port(idx=2, port_start_base=3000)
    assert port == 3051
    assert calls == [(3050, 25)]
