"""
VERTEX AI PROVIDER
==================
Makes API calls to Vertex AI MaaS models.

Location: src/titan_factory/providers/vertex.py
"""

import asyncio
import base64
from typing import Any

import google.auth
import google.auth.transport.requests
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from titan_factory.config import Config
from .base import CompletionResponse, LLMProvider, Message, ProviderFactory


class VertexProvider(LLMProvider):
    """Provider for Google Vertex AI MaaS models."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.project = config.google_project
        self.region = config.google_region
        self._credentials = None
        self._token = None
        self._token_expiry = 0.0
        self._lock = asyncio.Lock()

    def _get_endpoint(self, model: str) -> str:
        """Build the endpoint URL for a model.

        MaaS models use 'global' region with no region prefix in domain.
        """
        is_maas = "-maas" in model.lower() or "/" in model

        if is_maas:
            # Global MaaS endpoint (no region prefix)
            return (
                f"https://aiplatform.googleapis.com/v1/projects/{self.project}"
                f"/locations/global/endpoints/openapi/chat/completions"
            )
        else:
            # Regional endpoint
            return (
                f"https://{self.region}-aiplatform.googleapis.com/v1/projects/{self.project}"
                f"/locations/{self.region}/endpoints/openapi/chat/completions"
            )

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
        """Make a completion request to Vertex AI.

        Args:
            messages: Chat messages
            model: Model identifier (e.g., 'moonshotai/kimi-k2-thinking-maas')
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature

        Returns:
            CompletionResponse with content, usage, finish_reason
        """
        token = await self._get_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": [m.to_dict() for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        endpoint = self._get_endpoint(model)
        timeout_s = self.config.pipeline.model_timeout_ms / 1000

        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.post(
                endpoint,
                headers=headers,
                json=payload,
            )

            if response.status_code != 200:
                error_text = response.text
                raise RuntimeError(
                    f"Vertex API error {response.status_code}: {error_text[:500]}"
                )

            data = response.json()

            choices = data.get("choices", [])
            if not choices:
                raise RuntimeError("No choices in Vertex response")

            choice = choices[0]
            message = choice.get("message", {})
            content = message.get("content", "")

            return CompletionResponse(
                content=content,
                model=model,
                usage=data.get("usage"),
                finish_reason=choice.get("finish_reason"),  # <-- CHECK THIS!
                raw_response=data,
            )


# Register provider
ProviderFactory.register("vertex", VertexProvider)


# =============================================================================
# CRITICAL OBSERVATION: finish_reason
# =============================================================================
#
# The response includes a `finish_reason` field. Possible values:
# - "stop" - Model finished naturally
# - "length" - Model hit max_tokens limit (TRUNCATED!)
# - "content_filter" - Content was filtered
#
# If finish_reason is "length", the JSON is likely TRUNCATED and invalid.
# We should check this BEFORE attempting JSON extraction!
#
# RECOMMENDATION: In uigen.py, add:
#   if response.finish_reason == "length":
#       log_warning(f"Response truncated at {max_tokens} tokens")
#       # Either skip extraction or attempt repair
#
# =============================================================================
