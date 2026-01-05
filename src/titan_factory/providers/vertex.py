"""Vertex AI provider using OpenAI-compatible chat completions endpoint."""

import asyncio
import base64
from typing import Any

import google.auth
import google.auth.transport.requests
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from titan_factory.config import Config
from titan_factory.utils import log_warning

from .base import CompletionResponse, LLMProvider, Message, ProviderFactory


class VertexProvider(LLMProvider):
    """Provider for Google Vertex AI MaaS models.

    Uses the OpenAI-compatible chat completions endpoint.
    Authentication via Application Default Credentials.

    Supports both regional and global endpoints:
    - Regional: https://{region}-aiplatform.googleapis.com/...
    - Global: https://aiplatform.googleapis.com/.../locations/global/...
    """

    def __init__(self, config: Config) -> None:
        """Initialize Vertex provider.

        Args:
            config: Application configuration
        """
        self.config = config
        self.project = config.google_project
        self.region = config.google_region
        self._credentials = None
        self._token = None
        self._token_expiry = 0.0
        self._lock = asyncio.Lock()
        self._concurrency = asyncio.Semaphore(max(1, config.budget.concurrency_vertex))

    def _get_endpoint(self, model: str) -> str:
        """Build the endpoint URL for a model.

        MaaS models use 'global' region with no region prefix in domain.

        Args:
            model: Model identifier

        Returns:
            Endpoint URL
        """
        # MaaS models typically use global region
        # Check if model name suggests MaaS (ends with -maas or contains publisher/)
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

    @property
    def name(self) -> str:
        return "vertex"

    async def _get_token(self) -> str:
        """Get a valid access token.

        Returns:
            Access token string
        """
        async with self._lock:
            import time

            current_time = time.time()

            # Refresh if token expires within 60 seconds
            if self._token is None or current_time >= self._token_expiry - 60:
                # Get credentials in thread pool (blocking operation).
                #
                # NOTE: In real-world runs we've seen intermittent DNS failures resolving
                # oauth2.googleapis.com during token refresh. Because token refresh is a
                # shared dependency for *all* Vertex calls, we retry here to avoid
                # failing whole tasks due to transient network hiccups.
                loop = asyncio.get_running_loop()
                last_err: Exception | None = None

                for attempt in range(4):
                    try:
                        self._credentials, _ = await loop.run_in_executor(
                            None,
                            google.auth.default,
                            ["https://www.googleapis.com/auth/cloud-platform"],
                        )

                        # Refresh token
                        request = google.auth.transport.requests.Request()
                        await loop.run_in_executor(None, self._credentials.refresh, request)

                        self._token = self._credentials.token
                        # Token expires in ~1 hour, set expiry to 50 minutes
                        self._token_expiry = current_time + 3000
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e
                        if attempt < 3:
                            backoff_s = 1.5 * (2**attempt)
                            log_warning(
                                f"Vertex token refresh failed (attempt {attempt + 1}/4): {e}"
                            )
                            await asyncio.sleep(backoff_s)
                            continue
                        raise

                if last_err is not None:
                    raise last_err

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
        """Make a completion request to Vertex AI.

        Args:
            messages: Chat messages
            model: Model identifier (e.g., 'deepseek-ai/deepseek-v3.2-maas')
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature
            **kwargs: Additional options

        Returns:
            Completion response
        """
        async with self._concurrency:
            token = await self._get_token()

            # Vertex MaaS (OpenAI-compatible endpoint) enforces a hard output token limit.
            # Some upstream stages increase max_tokens on retry (e.g. *1.25 on truncation),
            # which can accidentally exceed the provider limit and hard-fail the request.
            #
            # Keep this clamp centralized so all callers remain safe.
            vertex_max_output_tokens = 65536
            if int(max_tokens) > vertex_max_output_tokens:
                log_warning(
                    f"VertexProvider: clamping max_tokens from {max_tokens} to {vertex_max_output_tokens} "
                    f"for model {model}"
                )
                max_tokens = vertex_max_output_tokens

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

                # Extract response
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

        Converts images to base64 and includes them in the message.

        Args:
            messages: Chat messages
            model: Vision model identifier
            images: List of image bytes
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
ProviderFactory.register("vertex", VertexProvider)
