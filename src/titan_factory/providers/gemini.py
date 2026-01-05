"""Google Gemini provider supporting both Vertex AI and Google AI endpoints.

Supports two authentication modes:
1. API Key (GOOGLE_API_KEY or GEMINI_API_KEY) - Simple, uses generativelanguage.googleapis.com
2. ADC (Application Default Credentials) - Production, uses Vertex AI endpoint

API Key mode is used if GOOGLE_API_KEY/GEMINI_API_KEY is set, otherwise falls back to ADC.
"""

import asyncio
import base64
import os
import time
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from titan_factory.config import Config
from titan_factory.utils import log_warning

from .base import CompletionResponse, LLMProvider, Message, ProviderFactory


class GeminiProvider(LLMProvider):
    """Provider for Google Gemini models.

    Supports both Google AI (API key) and Vertex AI (ADC) endpoints.
    Uses Gemini's native API format for vision capabilities.
    """

    def __init__(self, config: Config) -> None:
        """Initialize Gemini provider.

        Args:
            config: Application configuration
        """
        self.config = config
        self.project = config.google_project
        self.location = config.google_region

        # Check for API key first (simpler auth)
        self.api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        self.force_adc = bool(getattr(config.vision_judge, "force_adc", False))
        if self.force_adc and self.api_key:
            log_warning(
                "GeminiProvider: force_adc=true; ignoring GOOGLE_API_KEY/GEMINI_API_KEY and using ADC/Vertex"
            )
        self.use_api_key = bool(self.api_key) and not self.force_adc

        # ADC credentials (fallback)
        self._credentials = None
        self._token = None
        self._token_expiry = 0.0
        self._lock = asyncio.Lock()
        self._concurrency = asyncio.Semaphore(max(1, config.budget.concurrency_gemini))
        self._rpm = max(1, int(getattr(config.budget, "requests_per_min_gemini", 20) or 20))
        self._min_interval_s = 60.0 / float(self._rpm)
        self._throttle_lock = asyncio.Lock()
        self._last_request_at = 0.0

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def auth_mode(self) -> str:
        """Return current authentication mode."""
        return "api_key" if self.use_api_key else "adc"

    def _get_endpoint(self, model: str) -> str:
        """Get Gemini API endpoint for a model.

        Args:
            model: Model name (e.g., 'gemini-2.0-flash', 'gemini-1.5-pro', 'gemini-3-pro-preview')

        Returns:
            Full endpoint URL (with API key query param if using API key mode)
        """
        if self.use_api_key:
            # Google AI endpoint (simpler)
            return (
                f"https://generativelanguage.googleapis.com/v1beta/"
                f"models/{model}:generateContent?key={self.api_key}"
            )
        else:
            # Gemini 3.x models require GLOBAL endpoint, not regional
            if model.startswith("gemini-3"):
                return (
                    f"https://aiplatform.googleapis.com/v1/"
                    f"projects/{self.project}/locations/global/"
                    f"publishers/google/models/{model}:generateContent"
                )
            else:
                # Vertex AI regional endpoint (for Gemini 2.x and earlier)
                return (
                    f"https://{self.location}-aiplatform.googleapis.com/v1/"
                    f"projects/{self.project}/locations/{self.location}/"
                    f"publishers/google/models/{model}:generateContent"
                )

    async def _throttle(self) -> None:
        # Simple spacing throttle to reduce 429s. Concurrency is already capped, but
        # Gemini quotas are often QPM-based and can still be exceeded by fast calls.
        if self._min_interval_s <= 0:
            return

        async with self._throttle_lock:
            now = time.monotonic()
            wait_s = (self._last_request_at + self._min_interval_s) - now
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            self._last_request_at = time.monotonic()

    async def _get_headers(self) -> dict[str, str]:
        """Get request headers based on auth mode.

        Returns:
            Headers dict
        """
        headers = {"Content-Type": "application/json"}

        if not self.use_api_key:
            # Need Bearer token for Vertex AI
            token = await self._get_token()
            headers["Authorization"] = f"Bearer {token}"

        return headers

    async def _get_token(self) -> str:
        """Get a valid access token for Vertex AI.

        Returns:
            Access token string

        Raises:
            RuntimeError: If ADC not configured
        """
        async with self._lock:
            import time

            current_time = time.time()

            if self._token is None or current_time >= self._token_expiry - 60:
                try:
                    import google.auth
                    import google.auth.transport.requests

                    loop = asyncio.get_running_loop()

                    last_err: Exception | None = None
                    for attempt in range(4):
                        try:
                            self._credentials, _ = await loop.run_in_executor(
                                None,
                                google.auth.default,
                                ["https://www.googleapis.com/auth/cloud-platform"],
                            )

                            request = google.auth.transport.requests.Request()
                            await loop.run_in_executor(None, self._credentials.refresh, request)

                            self._token = self._credentials.token
                            self._token_expiry = current_time + 3000
                            last_err = None
                            break
                        except Exception as e:
                            last_err = e
                            if attempt < 3:
                                backoff_s = 1.5 * (2**attempt)
                                log_warning(
                                    f"Gemini ADC token refresh failed (attempt {attempt + 1}/4): {e}"
                                )
                                await asyncio.sleep(backoff_s)
                                continue
                            raise

                    if last_err is not None:
                        raise last_err

                except Exception as e:
                    raise RuntimeError(
                        f"Failed to get ADC token. Run 'gcloud auth application-default login' "
                        f"or set GOOGLE_API_KEY env var. Error: {e}"
                    )

            return self._token

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
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
        async with self._concurrency:
            await self._throttle()
            headers = await self._get_headers()

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
                payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

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
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
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
            model: Vision model identifier (e.g., 'gemini-2.0-flash')
            images: List of image bytes (PNG/JPEG)
            max_tokens: Maximum tokens
            temperature: Sampling temperature
            **kwargs: Additional options

        Returns:
            Completion response
        """
        async with self._concurrency:
            await self._throttle()
            headers = await self._get_headers()

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
            text_parts: list[str] = []

            for msg in messages:
                if msg.role == "system":
                    system_instruction = msg.content
                elif msg.role in ("user", "assistant") and isinstance(msg.content, str):
                    text = msg.content.strip()
                    if text:
                        text_parts.append(text)

            # Add text after images
            if text_parts:
                # Preserve multi-message context (e.g., retry prompts).
                # Gemini supports multiple text parts in a single user turn.
                for t in text_parts[:8]:
                    parts.append({"text": t})

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
                payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

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
