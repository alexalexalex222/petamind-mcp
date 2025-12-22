"""
SCHEMA DEFINITIONS
==================
Pydantic models for the pipeline.

Location: src/titan_factory/schema.py
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# === Expected Output Schema ===

class GeneratedFile(BaseModel):
    """A generated file."""
    path: str = Field(..., description="File path relative to project root")
    content: str = Field(..., description="File content")


class UIGenOutput(BaseModel):
    """Output from UI generator - THIS IS WHAT extract_json MUST RETURN."""
    files: list[GeneratedFile] = Field(..., description="Generated files")
    notes: list[str] = Field(default_factory=list, description="Brief notes about implementation")


def validate_uigen_output(data: dict) -> UIGenOutput:
    """Validate UI generator output.

    Args:
        data: Raw JSON dict (from extract_json)

    Returns:
        Validated UIGenOutput

    Raises:
        ValidationError: If validation fails
    """
    return UIGenOutput.model_validate(data)


# =============================================================================
# EXPECTED JSON STRUCTURE:
# =============================================================================
#
# {
#   "files": [
#     {
#       "path": "app/page.tsx",
#       "content": "'use client';\n\nimport React from 'react';\n..."
#     }
#   ],
#   "notes": [
#     "Complete Next.js App Router landing page",
#     "Dark theme with teal accents"
#   ]
# }
#
# IMPORTANT:
# - "files" is REQUIRED and must be a list
# - "notes" is optional (defaults to empty list)
# - Each file must have "path" and "content" strings
#
# =============================================================================


# === Candidate Status ===

class CandidateStatus(str, Enum):
    """Candidate processing status."""
    PENDING = "pending"
    GENERATED = "generated"      # JSON extracted successfully
    BUILD_FAILED = "build_failed"
    BUILD_PASSED = "build_passed"
    RENDERED = "rendered"
    SCORED = "scored"
    SELECTED = "selected"
    DISCARDED = "discarded"      # <-- THIS IS SET WHEN extract_json FAILS


class Candidate(BaseModel):
    """A generated candidate."""
    id: str
    task_id: str
    generator_model: str
    variant_index: int
    status: CandidateStatus = CandidateStatus.PENDING
    files: list[GeneratedFile] = Field(default_factory=list)
    error: str | None = None

    # Raw model response - includes <think> blocks for training reasoning
    raw_generator_response: str | None = Field(
        default=None,
        description="Full generator response including <think> reasoning blocks"
    )


# =============================================================================
# WHY raw_generator_response EXISTS:
# =============================================================================
#
# The user SPECIFICALLY requested that we preserve BOTH:
# 1. The <think> reasoning blocks (for training reasoning capability)
# 2. The actual JSON output (for the generated code)
#
# This is why we store raw_generator_response BEFORE extraction.
# The training data should include the full response with thinking.
#
# =============================================================================
