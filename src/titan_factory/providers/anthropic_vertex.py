"""Anthropic Claude provider on Vertex AI (rawPredict)."""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

import google.auth
import google.auth.transport.requests
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from titan_factory.config import Config
from titan_factory.utils import log_warning

from .base import CompletionResponse, LLMProvider, Message, ProviderFactory


class AnthropicVertexProvider(LLMProvider):
    """Provider for Anthropic Claude models on Vertex AI.

    Uses the Vertex rawPredict endpoint with the Anthropic Messages API payload.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.project = config.google_project
        self.region = config.google_region or "global"
        self._credentials = None
        self._token = None
        self._token_expiry = 0.0
        self._lock = asyncio.Lock()
        self._concurrency = asyncio.Semaphore(max(1, config.budget.concurrency_vertex))

    @property
    def name(self) -> str:
        return "anthropic_vertex"

    def _get_endpoint(self, model: str) -> str:
        """Build the Vertex Anthropic endpoint for a model."""
        model_path = model.strip()
        if not model_path.startswith("publishers/"):
            model_path = f"publishers/anthropic/models/{model_path}"

        region = self.region or "global"
        if region != "global":
            # Anthropic models are typically served from global.
            log_warning(
                f"Anthropic Vertex models typically use region 'global'; "
                f"overriding configured region '{region}'"
            )
            region = "global"

        if region == "global":
            base = "https://aiplatform.googleapis.com"
        else:
            base = f"https://{region}-aiplatform.googleapis.com"

        return (
            f"{base}/v1/projects/{self.project}/locations/{region}/"
            f"{model_path}:rawPredict"
        )

    async def _get_token(self) -> str:
        """Get a valid access token."""
        async with self._lock:
            current_time = time.time()
            if self._token is None or current_time >= self._token_expiry - 60:
                loop = asyncio.get_event_loop()
                self._credentials, _ = await loop.run_in_executor(
                    None,
                    google.auth.default,
                    ["https://www.googleapis.com/auth/cloud-platform"],
                )
                request = google.auth.transport.requests.Request()
                await loop.run_in_executor(None, self._credentials.refresh, request)
                self._token = self._credentials.token
                self._token_expiry = current_time + 3000
            return self._token

    def _flatten_text_blocks(self, content: list[dict]) -> str:
        """Flatten OpenAI-style content blocks to plain text."""
        texts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    texts.append(str(text))
        return "\n".join(texts).strip()

    def _split_system_messages(self, messages: list[Message]) -> tuple[str, list[dict]]:
        """Extract system prompt and convert messages to Anthropic format."""
        system_parts: list[str] = []
        anthropic_messages: list[dict] = []

        for msg in messages:
            if msg.role == "system":
                if isinstance(msg.content, str):
                    system_parts.append(msg.content)
                elif isinstance(msg.content, list):
                    system_parts.append(self._flatten_text_blocks(msg.content))
                continue

            content: str | list[dict]
            if isinstance(msg.content, str):
                content = msg.content
            else:
                content = self._flatten_text_blocks(msg.content)

            anthropic_messages.append({
                "role": msg.role,
                "content": content,
            })

        system = "\n\n".join([p for p in system_parts if p.strip()]).strip()
        return system, anthropic_messages

    def _extract_payload(self, data: dict) -> dict:
        """Normalize rawPredict response to the model payload."""
        if isinstance(data, dict) and isinstance(data.get("predictions"), list):
            preds = data.get("predictions") or []
            if preds and isinstance(preds[0], dict):
                return preds[0]
        return data

    def _extract_text_from_payload(self, payload: dict) -> str:
        """Extract assistant text from Anthropic payload."""
        content_blocks = payload.get("content")
        if isinstance(content_blocks, list):
            parts: list[str] = []
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        parts.append(str(text))
            return "".join(parts).strip()

        if isinstance(content_blocks, str):
            return content_blocks.strip()

        return ""

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
        async with self._concurrency:
            token = await self._get_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            system, anthropic_messages = self._split_system_messages(messages)
            payload: dict[str, Any] = {
                "anthropic_version": "vertex-2023-10-16",
                "messages": anthropic_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if system:
                payload["system"] = system

            endpoint = self._get_endpoint(model)
            timeout_s = self.config.pipeline.model_timeout_ms / 1000

            async with httpx.AsyncClient(timeout=timeout_s) as client:
                response = await client.post(endpoint, headers=headers, json=payload)

            if response.status_code != 200:
                raise RuntimeError(
                    f"Anthropic Vertex API error {response.status_code}: "
                    f"{response.text[:500]}"
                )

            data = response.json()
            payload = self._extract_payload(data)
            content = self._extract_text_from_payload(payload)
            finish_reason = payload.get("stop_reason")

            return CompletionResponse(
                content=content,
                model=model,
                usage=payload.get("usage") or data.get("usage"),
                finish_reason=finish_reason,
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
        async with self._concurrency:
            token = await self._get_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            # Extract system prompt (if any)
            system, _ = self._split_system_messages(messages)

            # Get latest user text
            user_text = ""
            for msg in reversed(messages):
                if msg.role == "user" and isinstance(msg.content, str):
                    user_text = msg.content
                    break

            # Build Anthropic content blocks with images first
            content_blocks: list[dict] = []
            for img_bytes in images:
                b64 = base64.b64encode(img_bytes).decode("utf-8")
                content_blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64,
                    },
                })

            if user_text:
                content_blocks.append({
                    "type": "text",
                    "text": user_text,
                })

            anthropic_messages = [
                {
                    "role": "user",
                    "content": content_blocks,
                }
            ]

            payload: dict[str, Any] = {
                "anthropic_version": "vertex-2023-10-16",
                "messages": anthropic_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if system:
                payload["system"] = system

            endpoint = self._get_endpoint(model)
            timeout_s = self.config.pipeline.model_timeout_ms / 1000

            async with httpx.AsyncClient(timeout=timeout_s) as client:
                response = await client.post(endpoint, headers=headers, json=payload)

            if response.status_code != 200:
                raise RuntimeError(
                    f"Anthropic Vertex API error {response.status_code}: "
                    f"{response.text[:500]}"
                )

            data = response.json()
            payload = self._extract_payload(data)
            content = self._extract_text_from_payload(payload)
            finish_reason = payload.get("stop_reason")

            return CompletionResponse(
                content=content,
                model=model,
                usage=payload.get("usage") or data.get("usage"),
                finish_reason=finish_reason,
                raw_response=data,
            )


# Register provider
ProviderFactory.register("anthropic_vertex", AnthropicVertexProvider)
