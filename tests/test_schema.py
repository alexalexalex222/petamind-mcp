"""Tests for schema validation."""

import pytest
from pydantic import ValidationError

from titan_factory.schema import (
    Accent,
    JudgeScore,
    Mood,
    PageType,
    validate_judge_score,
    validate_ui_spec,
    validate_uigen_output,
)


class TestUISpec:
    """Tests for UISpec validation."""

    def test_valid_ui_spec(self):
        """Test valid UI spec parses correctly."""
        data = {
            "niche": {"id": "martial_arts_bold", "vertical": "martial_arts", "pattern": "bold"},
            "page_type": "landing",
            "brand": {
                "name": "Iron Fist Dojo",
                "mood": "dark",
                "accent": "blue",
                "style_keywords": ["premium", "bold", "modern"],
                "radius": "medium",
                "density": "balanced",
            },
            "cta": {"primary": "Start Training", "secondary": "Learn More"},
            "content": {
                "business_name": "Iron Fist Dojo",
                "city": "Austin",
                "offer": "Transform your body and mind",
                "audience": "Adults seeking martial arts training",
                "highlights": ["Expert instructors", "Modern facility", "Flexible schedules"],
                "testimonials": [{"name": "John D.", "text": "Life changing experience!"}],
                "faq": [{"q": "What should I wear?", "a": "Comfortable athletic clothing."}],
            },
            "layout": {
                "sections": [
                    {"id": "hero", "must_include": ["headline", "cta"], "optional": False},
                    {"id": "features", "optional": True},
                ],
                "navigation": "minimal",
                "notes": "Focus on visual impact",
            },
        }

        spec = validate_ui_spec(data)

        assert spec.niche.id == "martial_arts_bold"
        assert spec.page_type == PageType.LANDING
        assert spec.brand.mood == Mood.DARK
        assert spec.brand.accent == Accent.BLUE
        assert len(spec.content.highlights) == 3

    def test_invalid_page_type(self):
        """Test invalid page type raises error."""
        data = {
            "niche": {"id": "test", "vertical": "test", "pattern": "test"},
            "page_type": "invalid_type",
            "brand": {
                "name": "Test",
                "mood": "dark",
                "accent": "blue",
                "style_keywords": ["modern"],
            },
            "cta": {"primary": "Click"},
            "content": {
                "business_name": "Test",
                "city": "Austin",
                "offer": "Test",
                "audience": "Everyone",
                "highlights": ["One"],
            },
            "layout": {"sections": [], "navigation": "standard"},
        }

        with pytest.raises(ValidationError):
            validate_ui_spec(data)

    def test_invalid_mood(self):
        """Test invalid mood raises error."""
        data = {
            "niche": {"id": "test", "vertical": "test", "pattern": "test"},
            "page_type": "landing",
            "brand": {
                "name": "Test",
                "mood": "purple",  # Invalid
                "accent": "blue",
                "style_keywords": ["modern"],
            },
            "cta": {"primary": "Click"},
            "content": {
                "business_name": "Test",
                "city": "Austin",
                "offer": "Test",
                "audience": "Everyone",
                "highlights": ["One"],
            },
            "layout": {"sections": [], "navigation": "standard"},
        }

        with pytest.raises(ValidationError):
            validate_ui_spec(data)

    def test_edit_task_spec(self):
        """Test edit task spec parsing."""
        data = {
            "niche": {"id": "test", "vertical": "test", "pattern": "test"},
            "page_type": "edit",
            "brand": {
                "name": "Test",
                "mood": "dark",
                "accent": "blue",
                "style_keywords": ["modern"],
            },
            "cta": {"primary": "Save"},
            "content": {
                "business_name": "Test",
                "city": "Austin",
                "offer": "Test",
                "audience": "Everyone",
                "highlights": ["One"],
            },
            "layout": {"sections": [], "navigation": "standard"},
            "edit_task": {
                "enabled": True,
                "instructions": "Add dark mode toggle",
                "code_old": "const x = 1;",
            },
        }

        spec = validate_ui_spec(data)

        assert spec.page_type == PageType.EDIT
        assert spec.edit_task.enabled is True
        assert "dark mode" in spec.edit_task.instructions


class TestUIGenOutput:
    """Tests for UI generator output validation."""

    def test_valid_output(self):
        """Test valid output parses correctly."""
        data = {
            "files": [
                {"path": "app/page.tsx", "content": "export default function Page() {}"},
                {"path": "app/globals.css", "content": "@tailwind base;"},
            ],
            "notes": ["Used grid layout", "Added responsive breakpoints"],
        }

        output = validate_uigen_output(data)

        assert len(output.files) == 2
        assert output.files[0].path == "app/page.tsx"
        assert len(output.notes) == 2

    def test_empty_files_fails(self):
        """Test empty files array fails validation."""
        data = {"files": [], "notes": []}

        with pytest.raises(ValidationError):
            validate_uigen_output(data)


class TestJudgeScore:
    """Tests for judge score validation."""

    def test_valid_score(self):
        """Test valid score parses correctly."""
        data = {
            "score": 8.5,
            "pass": True,
            "issues": ["Minor spacing issue"],
            "highlights": ["Excellent typography"],
            "fix_suggestions": ["Increase padding"],
        }

        score = validate_judge_score(data)

        assert score.score == 8.5
        assert score.passing is True
        assert len(score.issues) == 1

    def test_score_out_of_range(self):
        """Test score outside 0-10 fails."""
        data = {"score": 15, "pass": True}

        with pytest.raises(ValidationError):
            validate_judge_score(data)

    def test_alternative_field_name(self):
        """Test 'passing' field name works."""
        data = {"score": 7.0, "passing": False, "issues": ["Test"]}

        # Direct model validation should work
        score = JudgeScore.model_validate(data)
        assert score.passing is False
