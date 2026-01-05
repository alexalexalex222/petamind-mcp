import pytest

from titan_factory.mcp_code_server import _section_creativity_metrics


def test_section_creativity_metrics_returns_empty_on_bad_input() -> None:
    strong, weak, avg, min_score = _section_creativity_metrics(
        None, min_confidence=0.6, min_score=0.7
    )
    assert strong == []
    assert weak == []
    assert avg is None
    assert min_score is None


def test_section_creativity_metrics_filters_by_confidence_and_threshold() -> None:
    report = {
        "sections": [
            {"label": "hero", "score": 0.8, "confidence": 0.9, "notes": "good"},
            {"label": "features", "score": 0.5, "confidence": 0.9, "notes": "plain"},
            # Low-confidence section should be ignored
            {"label": "pricing", "score": 0.1, "confidence": 0.2, "notes": "unclear"},
        ]
    }
    strong, weak, avg, min_score = _section_creativity_metrics(
        report, min_confidence=0.6, min_score=0.7
    )
    assert strong == ["hero"]
    assert weak == ["features"]
    assert avg == pytest.approx((0.8 + 0.5) / 2)
    assert min_score == pytest.approx(0.5)


def test_section_creativity_metrics_dedupes_and_excludes_strong_from_weak() -> None:
    report = {
        "sections": [
            {"label": "hero", "score": 0.8, "confidence": 0.9, "notes": "good"},
            {"label": "hero", "score": 0.6, "confidence": 0.9, "notes": "dup"},
            {"label": "features", "score": 0.1, "confidence": 0.9, "notes": "bad"},
        ]
    }
    strong, weak, _avg, _min_score = _section_creativity_metrics(
        report, min_confidence=0.6, min_score=0.7
    )
    assert strong == ["hero"]
    assert weak == ["features"]

