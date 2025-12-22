"""Tests for prompt generation."""

import pytest

from titan_factory.config import load_config
from titan_factory.promptgen import (
    NICHE_DEFINITIONS,
    generate_niches,
    generate_task_prompt,
    generate_tasks,
)
from titan_factory.schema import NicheDefinition, PageType


class TestNicheGeneration:
    """Tests for niche generation."""

    def test_generates_100_niches(self):
        """Test that exactly 100 niches are defined."""
        assert len(NICHE_DEFINITIONS) == 100

    def test_unique_niche_ids(self):
        """Test all niche IDs are unique."""
        niches = generate_niches()
        ids = [n.id for n in niches]

        assert len(ids) == len(set(ids))

    def test_niche_structure(self):
        """Test niches have required fields."""
        niches = generate_niches()

        for niche in niches:
            assert niche.id
            assert niche.vertical
            assert niche.pattern
            assert niche.description

    def test_niche_verticals_diverse(self):
        """Test verticals are diverse."""
        niches = generate_niches()
        verticals = set(n.vertical for n in niches)

        # Should have many different verticals
        assert len(verticals) >= 30


class TestTaskGeneration:
    """Tests for task generation."""

    @pytest.fixture
    def config(self):
        """Load test config."""
        return load_config()

    def test_generates_correct_count(self, config):
        """Test task count matches configuration."""
        tasks = list(generate_tasks(config))

        # Prompt generation creates only the "base" page types (edit tasks are created later),
        # but it now supports emitting multiple tasks per page type per niche by varying seed.
        expected = config.pipeline.total_niches * config.pipeline.tasks_per_niche
        assert len(tasks) == expected

    def test_task_ids_unique(self, config):
        """Test all task IDs are unique."""
        tasks = list(generate_tasks(config))
        ids = [t.id for t in tasks]

        assert len(ids) == len(set(ids))

    def test_task_ids_deterministic(self, config):
        """Test task IDs are deterministic across runs."""
        tasks1 = list(generate_tasks(config))
        tasks2 = list(generate_tasks(config))

        ids1 = [t.id for t in tasks1]
        ids2 = [t.id for t in tasks2]

        assert ids1 == ids2

    def test_all_page_types_covered(self, config):
        """Test all page types are generated."""
        tasks = list(generate_tasks(config))
        page_types = set(t.page_type for t in tasks)

        # Should cover all base page types (edit tasks are created later)
        for pt in PageType:
            if pt == PageType.EDIT:
                continue
            assert pt in page_types

        assert PageType.EDIT not in page_types

    def test_edit_tasks_present(self, config):
        """Test promptgen does not emit placeholder edit tasks."""
        tasks = list(generate_tasks(config))
        edit_count = sum(1 for t in tasks if t.is_edit)

        # Edit tasks require real code_old and are generated dynamically later.
        assert edit_count == 0


class TestPromptGeneration:
    """Tests for individual prompt generation."""

    @pytest.fixture
    def sample_niche(self):
        """Create a sample niche for testing."""
        return NicheDefinition(
            id="martial_arts_bold",
            vertical="martial_arts",
            pattern="bold",
            description="Martial arts gyms with powerful, dynamic presence",
        )

    def test_landing_prompt(self, sample_niche):
        """Test landing page prompt structure."""
        prompt = generate_task_prompt(sample_niche, PageType.LANDING, seed=42)

        assert "landing page" in prompt.lower()
        assert "martial" in prompt.lower()
        assert "No UI libraries" in prompt

    def test_directory_prompt(self, sample_niche):
        """Test directory page prompt structure."""
        prompt = generate_task_prompt(sample_niche, PageType.DIRECTORY_HOME, seed=42)

        assert "directory" in prompt.lower()
        assert "search" in prompt.lower() or "filter" in prompt.lower()
        assert "No UI libraries" in prompt

    def test_dashboard_prompt(self, sample_niche):
        """Test dashboard prompt structure."""
        prompt = generate_task_prompt(sample_niche, PageType.ADMIN_DASHBOARD, seed=42)

        assert "dashboard" in prompt.lower()
        assert "No UI libraries" in prompt

    def test_edit_prompt(self, sample_niche):
        """Test edit task prompt structure."""
        prompt = generate_task_prompt(
            sample_niche,
            PageType.EDIT,
            seed=42,
            is_edit=True,
            code_old="const x = 1;",
        )

        assert "Refactor" in prompt or "edit" in prompt.lower()
        assert "const x = 1" in prompt
        assert "No UI libraries" in prompt

    def test_different_seeds_vary_content(self, sample_niche):
        """Test different seeds produce different prompts."""
        prompt1 = generate_task_prompt(sample_niche, PageType.LANDING, seed=1)
        prompt2 = generate_task_prompt(sample_niche, PageType.LANDING, seed=2)

        # Prompts should be different (different city, style, etc.)
        assert prompt1 != prompt2

    def test_same_seed_same_content(self, sample_niche):
        """Test same seed produces same prompt."""
        prompt1 = generate_task_prompt(sample_niche, PageType.LANDING, seed=42)
        prompt2 = generate_task_prompt(sample_niche, PageType.LANDING, seed=42)

        assert prompt1 == prompt2
