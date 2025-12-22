"""Base provider interface for LLM APIs."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from titan_factory.config import Config


@dataclass
class Message:
    """A chat message."""

    role: Literal["system", "user", "assistant"]
    content: str | list[dict]  # str for text, list for vision (content blocks)

    def to_dict(self) -> dict:
        """Convert to API-compatible dict."""
        return {"role": self.role, "content": self.content}


@dataclass
class CompletionResponse:
    """Response from a completion request."""

    content: str
    model: str
    usage: dict[str, int] | None = None
    finish_reason: str | None = None
    raw_response: dict | None = None


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        model: str,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> CompletionResponse:
        """Make a completion request.

        Args:
            messages: Chat messages
            model: Model identifier
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature
            **kwargs: Additional provider-specific options

        Returns:
            Completion response
        """
        pass

    @abstractmethod
    async def complete_with_vision(
        self,
        messages: list[Message],
        model: str,
        images: list[bytes],
        max_tokens: int = 500,
        temperature: float = 0.1,
        **kwargs: Any,
    ) -> CompletionResponse:
        """Make a vision completion request.

        Args:
            messages: Chat messages (can include image placeholders)
            model: Vision model identifier
            images: List of image bytes (PNG/JPEG)
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature
            **kwargs: Additional options

        Returns:
            Completion response
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name."""
        pass


class ProviderFactory:
    """Factory for creating LLM providers."""

    _providers: dict[str, type[LLMProvider]] = {}
    _instances: dict[str, LLMProvider] = {}

    @classmethod
    def register(cls, name: str, provider_class: type[LLMProvider]) -> None:
        """Register a provider class.

        Args:
            name: Provider name
            provider_class: Provider class
        """
        cls._providers[name] = provider_class

    @classmethod
    def get(cls, name: str, config: Config) -> LLMProvider:
        """Get or create a provider instance.

        Args:
            name: Provider name
            config: Configuration

        Returns:
            Provider instance
        """
        if name not in cls._instances:
            if name not in cls._providers:
                raise ValueError(f"Unknown provider: {name}")
            cls._instances[name] = cls._providers[name](config)
        return cls._instances[name]

    @classmethod
    def clear(cls) -> None:
        """Clear cached instances."""
        cls._instances.clear()
