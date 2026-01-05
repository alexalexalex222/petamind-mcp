from titan_factory.mcp_code_server import _playwright_install_hint


def test_playwright_install_hint_detects_missing_executable() -> None:
    msg = "browserType.launch: Executable doesn't exist at /home/x/.cache/ms-playwright/chromium-123/chrome"
    hint = _playwright_install_hint(RuntimeError(msg))
    assert hint is not None
    assert "playwright install chromium" in hint


def test_playwright_install_hint_returns_none_for_other_errors() -> None:
    hint = _playwright_install_hint(RuntimeError("some other failure"))
    assert hint is None
