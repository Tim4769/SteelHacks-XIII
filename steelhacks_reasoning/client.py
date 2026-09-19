"""Small NVIDIA OpenAI-compatible transport abstraction."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import requests
from dotenv import load_dotenv


class ModelClientError(RuntimeError):
    """Base class for safe model-client failures."""


class ModelTimeout(ModelClientError):
    """The configured model call exceeded its timeout."""


class UpstreamUnavailable(ModelClientError):
    """The configured model endpoint was unavailable or rejected the request."""


class ConfigurationError(ModelClientError):
    """Required model configuration is missing."""


class ModelClient(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str:
        """Return the assistant message content as text."""


@dataclass(frozen=True)
class NvidiaSettings:
    api_key: str
    base_url: str
    model: str
    connect_timeout_seconds: float = 5.0
    first_read_timeout_seconds: float = 12.0
    retry_read_timeout_seconds: float = 15.0
    max_output_tokens: int = 256

    @classmethod
    def from_environment(cls) -> NvidiaSettings:
        repo_root = Path(__file__).resolve().parents[1]
        load_dotenv(repo_root / ".env", override=False)
        api_key = os.getenv("NVIDIA_API_KEY", "")
        base_url = os.getenv("NVIDIA_BASE_URL", "")
        model = os.getenv("NVIDIA_MODEL", "")
        if not api_key or not base_url or not model:
            raise ConfigurationError("NVIDIA model configuration is incomplete.")
        return cls(api_key=api_key, base_url=base_url, model=model)


class NvidiaNemotronClient:
    """Calls NVIDIA's OpenAI-compatible chat-completions endpoint."""

    def __init__(
        self,
        settings: NvidiaSettings | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self._settings = settings or NvidiaSettings.from_environment()
        self._session = session or requests.Session()

    def complete(self, messages: list[dict[str, str]]) -> str:
        # NVIDIA documents this field as OpenAI SDK `extra_body`. Because this
        # client sends the wire request directly, its contents are merged into
        # the top-level JSON body exactly as the SDK would send them.
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
        payload = {
            "model": self._settings.model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": self._settings.max_output_tokens,
            "stream": False,
            **extra_body,
        }
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        response = None
        read_timeouts = (
            self._settings.first_read_timeout_seconds,
            self._settings.retry_read_timeout_seconds,
        )
        for attempt, read_timeout in enumerate(read_timeouts):
            try:
                response = self._session.post(
                    f"{self._settings.base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=(
                        self._settings.connect_timeout_seconds,
                        read_timeout,
                    ),
                )
                response.raise_for_status()
                break
            except requests.Timeout as exc:
                if attempt + 1 == len(read_timeouts):
                    raise ModelTimeout("The model request timed out.") from exc
            except requests.RequestException as exc:
                raise UpstreamUnavailable("The model service is unavailable.") from exc

        if response is None:
            raise ModelTimeout("The model request timed out.")

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise UpstreamUnavailable("The model service returned an unusable response.") from exc
        if not isinstance(content, str) or not content.strip():
            raise UpstreamUnavailable("The model service returned an empty response.")
        return content
