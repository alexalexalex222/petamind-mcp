"""Tests for exporter training format."""

import json

from titan_factory.exporter import format_training_example
from titan_factory.schema import GeneratedFile, UISpec


def _sample_ui_spec() -> UISpec:
    return UISpec.model_validate(
        {
            "niche": {"id": "martial_arts_bold", "vertical": "martial_arts", "pattern": "bold"},
            "page_type": "landing",
            "brand": {
                "name": "Iron Fist Dojo",
                "mood": "dark",
                "accent": "teal",
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
                "testimonials": [
                    {"name": "John D.", "text": "Life changing experience!"},
                    {"name": "Maya C.", "text": "Incredible coaches and community."},
                ],
                "faq": [
                    {"q": "What should I wear?", "a": "Comfortable athletic clothing."},
                    {"q": "Do you offer a trial?", "a": "Yes, your first class is free."},
                    {"q": "Is it beginner friendly?", "a": "Absolutely — we start from fundamentals."},
                ],
            },
            "layout": {
                "sections": [{"id": "hero", "must_include": ["headline", "cta"], "optional": False}],
                "navigation": "minimal",
                "notes": "Focus on visual impact",
            },
        }
    )


def test_format_training_example_preserves_think_and_outputs_training_schema():
    ui_spec = _sample_ui_spec()
    files = [
        GeneratedFile(path="app/page.tsx", content="export default function Page(){return null;}")
    ]

    raw_response = (
        "<think>Short reasoning.</think>\n"
        "{\"files\":[{\"path\":\"app/page.tsx\",\"content\":\"...\"}],\"notes\":[]}"
    )

    ex = format_training_example(
        task_prompt="Create a landing page...",
        ui_spec=ui_spec,
        files=files,
        raw_response=raw_response,
    )

    assistant = ex["messages"][2]["content"]
    assert assistant.startswith("<think>")

    json_part = assistant.split("</think>", 1)[1].strip()
    payload = json.loads(json_part)

    assert set(payload.keys()) == {"ui_spec", "files"}
    assert payload["ui_spec"]["brand"]["name"] == "Iron Fist Dojo"
    assert payload["files"][0]["path"] == "app/page.tsx"


def test_format_training_example_fallback_think_when_missing():
    ui_spec = _sample_ui_spec()
    files = [GeneratedFile(path="app/page.tsx", content="export default function Page(){return null;}")]

    ex = format_training_example(
        task_prompt="Create a landing page...",
        ui_spec=ui_spec,
        files=files,
        raw_response=None,
    )

    assistant = ex["messages"][2]["content"]
    assert assistant.startswith("<think>")
    assert "\"ui_spec\"" in assistant
    assert "\"files\"" in assistant
