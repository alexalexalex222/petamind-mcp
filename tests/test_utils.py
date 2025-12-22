"""Tests for utility functions."""

import pytest

from titan_factory.utils import (
    estimate_tokens,
    extract_json,
    extract_json_strict,
    generate_candidate_id,
    generate_task_id,
    hash_prompt,
    truncate_text,
)


class TestJsonExtraction:
    """Tests for JSON extraction utilities."""

    def test_direct_json(self):
        """Test direct JSON parsing."""
        text = '{"key": "value", "num": 42}'
        result = extract_json(text)

        assert result == {"key": "value", "num": 42}

    def test_json_with_markdown(self):
        """Test JSON inside markdown code block."""
        text = """Here is the response:

```json
{"key": "value"}
```

That's all."""

        result = extract_json(text)
        assert result == {"key": "value"}

    def test_json_with_multiple_code_blocks(self):
        """Test extractor tries multiple fenced blocks until one parses."""
        text = """First attempt:

```json
{ not valid json }
```

Second attempt:
```json
{"key": "value"}
```
"""
        result = extract_json(text)
        assert result == {"key": "value"}

    def test_json_with_think_block(self):
        """Test extractor strips <think> blocks before parsing."""
        text = "<think>Reasoning here</think>\n{\"key\": \"value\"}"
        result = extract_json(text)
        assert result == {"key": "value"}

    def test_json_with_text_before(self):
        """Test JSON with text before it."""
        text = 'Some explanation here: {"key": "value"}'
        result = extract_json(text)

        assert result == {"key": "value"}

    def test_json_with_text_after(self):
        """Test JSON with text after it."""
        text = '{"key": "value"} and some more text'
        result = extract_json(text)

        assert result == {"key": "value"}

    def test_nested_json(self):
        """Test nested JSON parsing."""
        text = '{"outer": {"inner": "value"}}'
        result = extract_json(text)

        assert result == {"outer": {"inner": "value"}}

    def test_array_json(self):
        """Test array JSON parsing."""
        text = '[{"a": 1}, {"b": 2}]'
        result = extract_json(text)

        assert result == [{"a": 1}, {"b": 2}]

    def test_invalid_json(self):
        """Test invalid JSON returns None."""
        text = "This is not JSON at all"
        result = extract_json(text)

        assert result is None

    def test_empty_string(self):
        """Test empty string returns None."""
        result = extract_json("")
        assert result is None

    def test_strict_extraction_raises(self):
        """Test strict extraction raises on failure."""
        with pytest.raises(ValueError):
            extract_json_strict("not json")

    def test_strict_extraction_success(self):
        """Test strict extraction works on valid JSON."""
        result = extract_json_strict('{"key": "value"}')
        assert result == {"key": "value"}


class TestIdGeneration:
    """Tests for ID generation."""

    def test_task_id_deterministic(self):
        """Test task ID is deterministic."""
        id1 = generate_task_id("niche1", "landing", 123)
        id2 = generate_task_id("niche1", "landing", 123)

        assert id1 == id2
        assert len(id1) == 16

    def test_task_id_varies_by_input(self):
        """Test different inputs produce different IDs."""
        id1 = generate_task_id("niche1", "landing", 123)
        id2 = generate_task_id("niche2", "landing", 123)
        id3 = generate_task_id("niche1", "dashboard", 123)
        id4 = generate_task_id("niche1", "landing", 456)

        assert len({id1, id2, id3, id4}) == 4

    def test_candidate_id_deterministic(self):
        """Test candidate ID is deterministic."""
        id1 = generate_candidate_id("task1", "model1", 0)
        id2 = generate_candidate_id("task1", "model1", 0)

        assert id1 == id2
        assert len(id1) == 12

    def test_candidate_id_varies(self):
        """Test candidate IDs vary correctly."""
        id1 = generate_candidate_id("task1", "model1", 0)
        id2 = generate_candidate_id("task1", "model1", 1)
        id3 = generate_candidate_id("task1", "model2", 0)

        assert len({id1, id2, id3}) == 3


class TestPromptHashing:
    """Tests for prompt hashing."""

    def test_hash_deterministic(self):
        """Test hash is deterministic."""
        messages = [{"role": "user", "content": "Hello"}]
        params = {"max_tokens": 100}

        hash1 = hash_prompt(messages, params)
        hash2 = hash_prompt(messages, params)

        assert hash1 == hash2

    def test_hash_varies_by_content(self):
        """Test hash varies with content."""
        messages1 = [{"role": "user", "content": "Hello"}]
        messages2 = [{"role": "user", "content": "Goodbye"}]
        params = {"max_tokens": 100}

        hash1 = hash_prompt(messages1, params)
        hash2 = hash_prompt(messages2, params)

        assert hash1 != hash2


class TestTextUtils:
    """Tests for text utilities."""

    def test_truncate_short_text(self):
        """Test short text is not truncated."""
        text = "Hello world"
        result = truncate_text(text, max_length=100)

        assert result == text

    def test_truncate_long_text(self):
        """Test long text is truncated."""
        text = "a" * 100
        result = truncate_text(text, max_length=20)

        assert len(result) == 20
        assert result.endswith("...")

    def test_estimate_tokens(self):
        """Test token estimation."""
        text = "a" * 100
        result = estimate_tokens(text)

        assert result == 25  # 100 / 4
