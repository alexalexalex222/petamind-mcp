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


def test_apply_to_repo_skips_when_winner_is_best_effort(tmp_path: Path, monkeypatch) -> None:
    # Create a tiny repo where gates pass but vision fails.
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
        return {
            "patches": [
                {"path": "hello.txt", "patch": "@@ -1,1 +1,1 @@\n-hello\n+hello world\n"}
            ],
            "notes": ["stub patch"],
        }

    async def fake_capture_screenshots(*, url: str, out_dir: Path, viewports, timeout_ms: int):
        _ = (url, viewports, timeout_ms)
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / "desktop.png"
        p.write_bytes(b"fake")
        return [p]

    async def fake_vision_eval(*, images, goal, threshold, provider_name, model, min_confidence, kind):
        _ = (images, goal, threshold, provider_name, model, min_confidence, kind)
        # Not broken, but score below threshold => vision_ok False.
        return {"broken": {"broken": False, "confidence": 1.0}, "score": {"score": 0.0}}

    # Avoid real model + real browser + real Gemini.
    monkeypatch.setattr(mcp_code_server, "_call_llm_json", fake_call_llm_json)
    monkeypatch.setattr(mcp_code_server, "_capture_screenshots", fake_capture_screenshots)
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
            test_command="true",
            vision_mode="on",
            preview_command="python3 -m http.server {port}",
            preview_url="http://127.0.0.1:{port}/",
            preview_wait_timeout_s=10.0,
            section_creativity_mode="off",
            allow_nonpassing_winner=True,
            apply_to_repo=True,
        )

    result = anyio.run(run)
    assert result["winner"] is not None
    assert result["winner"]["passes_all_gates"] is False
    assert result["applied_to_repo"] is False
    assert result["apply_skipped_reason"]

    # Original repo should be unchanged (since apply was skipped).
    assert (repo / "hello.txt").read_text(encoding="utf-8") == "hello\n"
