"""Google Gemini provider using native Vertex AI Gemini API."""

import asyncio
import base64
from typing import Any

import google.auth
import google.auth.transport.requests
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from titan_factory.config import Config

from .base import CompletionResponse, LLMProvider, Message, ProviderFactory


class GeminiProvider(LLMProvider):
    """Provider for Google Gemini models on Vertex AI.

    Uses Gemini's native API format (not OpenAI-compatible).
    Required for vision capabilities with Gemini 3 Pro.
    """

    def __init__(self, config: Config) -> None:
        """Initialize Gemini provider.

        Args:
            config: Application configuration
        """
        self.config = config
        self.project = config.google_project
        self.location = config.google_region
        self._credentials = None
        self._token = None
        self._token_expiry = 0.0
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "gemini"

    def _get_endpoint(self, model: str) -> str:
        """Get Gemini API endpoint for a model.

        Args:
            model: Model name (e.g., 'gemini-3-pro-preview')

        Returns:
            Full endpoint URL
        """
        return (
            f"https://{self.location}-aiplatform.googleapis.com/v1/"
            f"projects/{self.project}/locations/{self.location}/"
            f"publishers/google/models/{model}:generateContent"
        )

    async def _get_token(self) -> str:
        """Get a valid access token.

        Returns:
            Access token string
        """
        async with self._lock:
            import time

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
        """Make a text completion request to Gemini.

        Args:
            messages: Chat messages
            model: Model identifier
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature
            **kwargs: Additional options

        Returns:
            Completion response
        """
        token = await self._get_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        # Convert messages to Gemini format
        contents = []
        system_instruction = None

        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content
            else:
                role = "user" if msg.role == "user" else "model"
                contents.append({
                    "role": role,
                    "parts": [{"text": msg.content}],
                })

        payload = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }

        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
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
                    f"Gemini API error {response.status_code}: {error_text[:500]}"
                )

            data = response.json()

            # Extract response from Gemini format
            candidates = data.get("candidates", [])
            if not candidates:
                raise RuntimeError("No candidates in Gemini response")

            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            text = "".join(p.get("text", "") for p in parts)

            return CompletionResponse(
                content=text,
                model=model,
                usage=data.get("usageMetadata"),
                finish_reason=candidates[0].get("finishReason"),
                raw_response=data,
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
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
        """Make a vision completion request to Gemini.

        Gemini natively supports multimodal input with inline images.

        Args:
            messages: Chat messages
            model: Vision model identifier (e.g., 'gemini-3-pro-preview')
            images: List of image bytes (PNG/JPEG)
            max_tokens: Maximum tokens
            temperature: Sampling temperature
            **kwargs: Additional options

        Returns:
            Completion response
        """
        token = await self._get_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        # Build parts with images and text
        parts = []

        # Add images as inline_data
        for img_bytes in images:
            b64 = base64.b64encode(img_bytes).decode()
            parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": b64,
                }
            })

        # Extract text from messages
        system_instruction = None
        user_text = ""

        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content
            elif msg.role == "user" and isinstance(msg.content, str):
                user_text = msg.content

        # Add text after images
        if user_text:
            parts.append({"text": user_text})

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": parts,
                }
            ],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }

        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
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
                    f"Gemini Vision API error {response.status_code}: {error_text[:500]}"
                )

            data = response.json()

            candidates = data.get("candidates", [])
            if not candidates:
                raise RuntimeError("No candidates in Gemini vision response")

            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            text = "".join(p.get("text", "") for p in parts)

            return CompletionResponse(
                content=text,
                model=model,
                usage=data.get("usageMetadata"),
                finish_reason=candidates[0].get("finishReason"),
                raw_response=data,
            )


# Register provider
ProviderFactory.register("gemini", GeminiProvider)
