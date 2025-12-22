"""OpenRouter provider for LLM APIs."""

import asyncio
import base64
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from titan_factory.config import Config

from .base import CompletionResponse, LLMProvider, Message, ProviderFactory


class OpenRouterProvider(LLMProvider):
    """Provider for OpenRouter API.

    Uses OpenAI-compatible chat completions endpoint.
    """

    def __init__(self, config: Config) -> None:
        """Initialize OpenRouter provider.

        Args:
            config: Application configuration
        """
        self.config = config
        self.base_url = config.openrouter.base_url
        self.api_key = config.openrouter_api_key
        self._concurrency = asyncio.Semaphore(max(1, config.budget.concurrency_openrouter))

        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable not set")

    @property
    def name(self) -> str:
        return "openrouter"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def complete(
        self,
        messages: list[Message],
        model: str,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> CompletionResponse:
        """Make a completion request to OpenRouter.

        Args:
            messages: Chat messages
            model: Model identifier (e.g., 'mistralai/devstral-2512:free')
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature
            **kwargs: Additional options

        Returns:
            Completion response
        """
        async with self._concurrency:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://titan-factory.local",
                "X-Title": "TITAN Factory",
            }

            payload = {
                "model": model,
                "messages": [m.to_dict() for m in messages],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }

            timeout_s = self.config.pipeline.model_timeout_ms / 1000

            async with httpx.AsyncClient(timeout=timeout_s) as client:
                response = await client.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                )

                if response.status_code != 200:
                    error_text = response.text
                    raise RuntimeError(
                        f"OpenRouter API error {response.status_code}: {error_text[:500]}"
                    )

                data = response.json()

                # Handle OpenRouter-specific error format
                if "error" in data:
                    raise RuntimeError(f"OpenRouter error: {data['error']}")

                # Extract response
                choices = data.get("choices", [])
                if not choices:
                    raise RuntimeError("No choices in OpenRouter response")

                choice = choices[0]
                message = choice.get("message", {})
                content = message.get("content", "")

                return CompletionResponse(
                    content=content,
                    model=model,
                    usage=data.get("usage"),
                    finish_reason=choice.get("finish_reason"),
                    raw_response=data,
                )

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
            messages: Chat messages
            model: Vision model identifier
            images: List of image bytes (PNG/JPEG)
            max_tokens: Maximum tokens
            temperature: Sampling temperature
            **kwargs: Additional options

        Returns:
            Completion response
        """
        # Build content blocks with images
        content_blocks = []

        # Add images first
        for img_bytes in images:
            b64 = base64.b64encode(img_bytes).decode()
            content_blocks.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{b64}",
                },
            })

        # Add text from last user message
        text_content = ""
        for msg in reversed(messages):
            if msg.role == "user" and isinstance(msg.content, str):
                text_content = msg.content
                break

        if text_content:
            content_blocks.append({
                "type": "text",
                "text": text_content,
            })

        # Build new messages with vision content
        vision_messages = [
            m for m in messages if m.role == "system"  # Keep system messages
        ]
        vision_messages.append(Message(role="user", content=content_blocks))

        return await self.complete(
            messages=vision_messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )


# Register provider
ProviderFactory.register("openrouter", OpenRouterProvider)
