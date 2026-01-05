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


def test_candidate_failure_writes_error_and_traceback_files(tmp_path: Path, monkeypatch) -> None:
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

    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TITAN_MCP_OUT_DIR", str(out_dir))

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
        raise RuntimeError("boom")

    monkeypatch.setattr(mcp_code_server, "_call_llm_json", fake_call_llm_json)

    async def run():
        return await mcp_code_server.titan_code_solve(
            repo_path=str(repo),
            goal="This should fail",
            planning_mode="off",
            auto_context_mode="off",
            max_candidates=1,
            candidate_concurrency=1,
            max_fix_rounds=0,
            vision_mode="auto",
            section_creativity_mode="off",
            apply_to_repo=False,
            allow_nonpassing_winner=True,  # still returns best effort candidate
        )

    result = anyio.run(run)

    assert result["run_dir"]
    assert result["candidates"]
    cand0 = result["candidates"][0]
    assert cand0["ok"] is False
    assert "boom" in (cand0["error"] or "")

    cand_dir = Path(cand0["candidate_dir"])
    assert (cand_dir / "error.txt").exists()
    assert "boom" in (cand_dir / "error.txt").read_text(encoding="utf-8", errors="replace")
    assert (cand_dir / "traceback.txt").exists()
    assert (cand_dir / "candidate_summary.json").exists()

    run_dir = Path(result["run_dir"])
    assert (run_dir / "run_summary.json").exists()
