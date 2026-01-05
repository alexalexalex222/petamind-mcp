import subprocess
from pathlib import Path

import anyio

from titan_factory import mcp_code_server


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_titan_code_solve_end_to_end_offline(tmp_path: Path, monkeypatch) -> None:
    # Create a tiny git repo we can run worktrees against.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.txt").write_text("hello\n", encoding="utf-8")

    _git(repo, "init")
    _git(repo, "add", "hello.txt")
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Keep run artifacts inside the temp dir.
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TITAN_MCP_OUT_DIR", str(out_dir))

    # Force a deterministic offline "LLM" response.
    async def fake_call_llm_json(
        *,
        provider_name: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ):
        _ = (provider_name, model, system_prompt, user_prompt, temperature, max_tokens)
        return {
            "patches": [
                {
                    "path": "hello.txt",
                    "patch": "@@ -1,1 +1,1 @@\n-hello\n+hello world\n",
                }
            ],
            "notes": ["offline stub patch"],
        }

    monkeypatch.setattr(mcp_code_server, "_call_llm_json", fake_call_llm_json)

    async def fake_capture_diff_screenshots(*, diff_text: str, out_dir: Path, timeout_ms: int):
        _ = (diff_text, timeout_ms)
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / "diff.png"
        p.write_bytes(b"fake")
        return [p]

    async def fake_vision_eval(
        *,
        images,
        goal,
        threshold,
        provider_name,
        model,
        min_confidence,
        kind,
    ):
        _ = (images, goal, threshold, provider_name, model, min_confidence, kind)
        return {
            "broken": {"broken": False, "confidence": 1.0, "reasons": []},
            "score": {"score": 10.0},
        }

    monkeypatch.setattr(mcp_code_server, "_capture_diff_screenshots", fake_capture_diff_screenshots)
    monkeypatch.setattr(mcp_code_server, "_vision_eval", fake_vision_eval)

    async def run():
        return await mcp_code_server.titan_code_solve(
            repo_path=str(repo),
            goal="Change hello to hello world",
            planning_mode="off",
            auto_context_mode="off",
            max_candidates=1,
            candidate_concurrency=1,
            max_fix_rounds=0,
            vision_mode="auto",
            section_creativity_mode="off",
            apply_to_repo=False,
            allow_nonpassing_winner=False,
        )

    result = anyio.run(run)

    assert result["test_command_inferred"] is True
    assert result["test_command"] == "true"
    assert "skipping" in str(result["test_command_inferred_reason"] or "").lower()

    winner = result["winner"]
    assert winner is not None
    assert "hello world" in winner["patch"]

    # Ensure we didn't accidentally apply changes to the original repo.
    assert (repo / "hello.txt").read_text(encoding="utf-8") == "hello\n"

    # Ensure artifacts got written.
    run_dir = Path(result["run_dir"])
    assert run_dir.exists()
    assert (run_dir / "run_summary.json").exists()
