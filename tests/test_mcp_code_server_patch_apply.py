import pytest

from titan_factory.mcp_code_server import _apply_unified_diff_to_text


def test_apply_unified_diff_replaces_line() -> None:
    original = "alpha\nbravo\ncharlie\n"
    diff = "@@ -1,3 +1,3 @@\n alpha\n-bravo\n+BRAVO\n charlie\n"
    patched = _apply_unified_diff_to_text(original, diff)
    assert patched == "alpha\nBRAVO\ncharlie\n"


def test_apply_unified_diff_inserts_line() -> None:
    original = "one\nthree\n"
    diff = "@@ -1,2 +1,3 @@\n one\n+two\n three\n"
    patched = _apply_unified_diff_to_text(original, diff)
    assert patched == "one\ntwo\nthree\n"


def test_apply_unified_diff_creates_new_file_content() -> None:
    original = ""
    diff = "@@ -0,0 +1,2 @@\n+hello\n+world\n"
    patched = _apply_unified_diff_to_text(original, diff)
    assert patched == "hello\nworld"


def test_apply_unified_diff_uses_fuzzy_anchor_when_header_is_wrong() -> None:
    original = "a\nb\nc\nd\ne\n"
    # Header claims the hunk starts at old line 99, but the context clearly matches at the top.
    diff = "@@ -99,2 +99,2 @@\n a\n-b\n+B\n"
    patched = _apply_unified_diff_to_text(original, diff)
    assert patched == "a\nB\nc\nd\ne\n"


def test_apply_unified_diff_raises_on_context_mismatch() -> None:
    original = "x\ny\nz\n"
    diff = "@@ -1,3 +1,3 @@\n x\n-NOT_Y\n+Y\n z\n"
    with pytest.raises(ValueError):
        _apply_unified_diff_to_text(original, diff)
