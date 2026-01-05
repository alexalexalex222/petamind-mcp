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


def test_eval_patch_offline_writes_summaries(tmp_path: Path, monkeypatch) -> None:
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
        return await mcp_code_server._titan_code_eval_patch_impl(
            repo_path=str(repo),
            patches=[{"path": "hello.txt", "patch": "@@ -1,1 +1,1 @@\n-hello\n+hello world\n"}],
            test_command="true",
            vision_mode="auto",
        )

    result = anyio.run(run)
    assert result["passes_all_gates"] is True
    run_dir = Path(result["run_dir"])
    cand_dir = Path(result["candidate_dir"])
    assert (run_dir / "run_summary.json").exists()
    assert (cand_dir / "candidate_summary.json").exists()
