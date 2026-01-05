from titan_factory.mcp_code_server import CandidateResult, _select_winner


def _cand(
    *,
    index: int,
    ok: bool,
    applied: bool,
    test_ok: bool,
    lint_ok: bool,
    vision_ok: bool,
    vision_score: float | None,
    adds: int = 1,
    deletes: int = 1,
) -> CandidateResult:
    return CandidateResult(
        index=index,
        temperature=0.5,
        ok=ok,
        applied=applied,
        test_ok=test_ok,
        lint_ok=lint_ok,
        vision_ok=vision_ok,
        vision_score=vision_score,
        adds=adds,
        deletes=deletes,
        fix_rounds=0,
        patch="diff --git a/x b/x\n@@ -1 +1 @@\n-x\n+y\n",
        notes=[],
        error=None if ok else "failed",
    )


def test_select_winner_returns_none_when_no_candidate_passes() -> None:
    results = [
        _cand(index=0, ok=True, applied=True, test_ok=True, lint_ok=True, vision_ok=False, vision_score=7.9),
        _cand(index=1, ok=True, applied=True, test_ok=True, lint_ok=True, vision_ok=False, vision_score=7.5),
    ]
    assert _select_winner(results, allow_best_effort=False) is None


def test_select_winner_best_effort_prefers_higher_vision_score() -> None:
    results = [
        _cand(index=0, ok=True, applied=True, test_ok=True, lint_ok=True, vision_ok=False, vision_score=7.9),
        _cand(index=1, ok=True, applied=True, test_ok=True, lint_ok=True, vision_ok=False, vision_score=8.1),
    ]
    winner = _select_winner(results, allow_best_effort=True)
    assert winner is not None
    assert winner.index == 1


def test_select_winner_prefers_passing_candidate_over_best_effort() -> None:
    results = [
        _cand(index=0, ok=True, applied=True, test_ok=True, lint_ok=True, vision_ok=False, vision_score=9.9),
        _cand(index=1, ok=True, applied=True, test_ok=True, lint_ok=True, vision_ok=True, vision_score=8.0),
    ]
    winner = _select_winner(results, allow_best_effort=True)
    assert winner is not None
    assert winner.index == 1


def test_select_winner_prefers_fewer_weak_sections_when_otherwise_tied() -> None:
    a = _cand(index=0, ok=True, applied=True, test_ok=True, lint_ok=True, vision_ok=True, vision_score=8.5)
    b = _cand(index=1, ok=True, applied=True, test_ok=True, lint_ok=True, vision_ok=True, vision_score=8.5)
    a.creativity_eval_ok = True
    a.creativity_weak = 2
    a.creativity_min = 0.4
    a.creativity_avg = 0.6
    b.creativity_eval_ok = True
    b.creativity_weak = 0
    b.creativity_min = 0.8
    b.creativity_avg = 0.85

    winner = _select_winner([a, b], allow_best_effort=False)
    assert winner is not None
    assert winner.index == 1


def test_select_winner_prefers_candidate_with_creativity_eval_when_enabled() -> None:
    a = _cand(index=0, ok=True, applied=True, test_ok=True, lint_ok=True, vision_ok=True, vision_score=8.5)
    b = _cand(index=1, ok=True, applied=True, test_ok=True, lint_ok=True, vision_ok=True, vision_score=8.5)
    a.creativity_eval_ok = True
    a.creativity_weak = 0
    a.creativity_min = 0.8
    a.creativity_avg = 0.85
    # b has no creativity eval (default False)
    b.creativity_weak = 0
    b.creativity_min = 0.9
    b.creativity_avg = 0.9

    winner = _select_winner([a, b], allow_best_effort=False)
    assert winner is not None
    assert winner.index == 0
