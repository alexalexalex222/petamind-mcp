"""
CURRENT extract_json() IMPLEMENTATION
=====================================
This is the function that is failing to extract JSON from 75% of model responses.

Location: src/titan_factory/utils.py
"""

import json
import re
from typing import Any


def extract_json(text: str) -> dict[str, Any] | None:
    """Safely extract JSON from model response.

    Tries multiple strategies:
    1. Strip thinking blocks (<think>...</think>)
    2. Parse full string as JSON
    3. Look for ```json code blocks
    4. Find first { and last } and parse

    Args:
        text: Raw model response

    Returns:
        Parsed JSON dict or None if extraction fails
    """
    if text is None:
        return None

    text = text.strip()

    # Strategy 0: Strip <think>...</think> blocks (thinking models like Kimi K2)
    # These models output reasoning before JSON
    think_pattern = re.compile(r"<think>[\s\S]*?</think>\s*", re.IGNORECASE)
    text = think_pattern.sub("", text).strip()

    # Also handle unclosed <think> blocks (truncated responses)
    # Remove everything from <think> to the first { if think block wasn't closed
    if "<think>" in text.lower() and "</think>" not in text.lower():
        first_brace = text.find("{")
        if first_brace != -1:
            text = text[first_brace:]

    # Strategy 1: Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Extract from markdown code block
    code_block_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Strategy 3: Find first { and last }
    first_brace = text.find("{")
    last_brace = text.rfind("}")

    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace : last_brace + 1])
        except json.JSONDecodeError:
            pass

    # Strategy 4: Try to find array
    first_bracket = text.find("[")
    last_bracket = text.rfind("]")

    if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
        try:
            return json.loads(text[first_bracket : last_bracket + 1])
        except json.JSONDecodeError:
            pass

    return None


def extract_json_strict(text: str) -> dict[str, Any]:
    """Extract JSON, raising on failure.

    Args:
        text: Raw model response

    Returns:
        Parsed JSON dict

    Raises:
        ValueError: If JSON extraction fails
    """
    result = extract_json(text)
    if result is None:
        raise ValueError(f"Failed to extract JSON from response: {text[:500]}...")
    return result


# =============================================================================
# KNOWN ISSUES WITH CURRENT IMPLEMENTATION:
# =============================================================================
#
# 1. The regex `r"<think>[\s\S]*?</think>\s*"` uses non-greedy matching (*?)
#    This SHOULD work, but test it against the actual responses.
#
# 2. Strategy 2 (code block) uses `r"```(?:json)?\s*([\s\S]*?)```"`
#    - The ([\s\S]*?) is non-greedy - could this fail on large code blocks?
#    - Does it handle multiple code blocks correctly?
#
# 3. Strategy 3 finds first { and last } - but what if the JSON is truncated?
#    The last } might be inside a string, not the actual JSON end.
#
# 4. TRUNCATED JSON: If the model response hits max_tokens and truncates,
#    the JSON will be malformed. Current code has NO repair strategy.
#
# 5. Order of operations: Should we try code block BEFORE stripping think?
#    What if the think block contains JSON-like content?
#
# =============================================================================
