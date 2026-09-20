from __future__ import annotations

from typing import Any

import requests

from steelhacks_reasoning.client import (
    ModelTimeout,
    NvidiaNemotronClient,
    NvidiaSettings,
    UpstreamUnavailable,
)


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": '{"status":"ok"}'}}]}


class RecordingSession:
    def __init__(self, outcomes: list[str] | None = None) -> None:
        self.outcomes = list(outcomes or ["success"])
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        outcome = self.outcomes.pop(0)
        if outcome == "timeout":
            raise requests.Timeout("synthetic timeout")
        if outcome == "upstream_error":
            raise requests.RequestException("synthetic non-retryable error")
        return FakeResponse()


def settings() -> NvidiaSettings:
    return NvidiaSettings(
        api_key="synthetic-test-key",
        base_url="https://example.invalid/v1",
        model="nvidia/nemotron-3.5-lightning-30b-a3b",
    )


def test_success_within_first_window_and_no_retry() -> None:
    session = RecordingSession()
    client = NvidiaNemotronClient(settings(), session=session)  # type: ignore[arg-type]
    client.complete([{"role": "user", "content": "Synthetic test"}])

    call = session.calls[0]
    body = call["json"]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_budget" not in body
    assert body["max_tokens"] == 256
    assert body["max_tokens"] <= 512
    assert body["temperature"] == 0.0
    assert "top_p" not in body
    assert body["stream"] is False
    assert call["timeout"] == (1.0, 2.0)
    assert len(session.calls) == 1


def test_timeout_is_not_retried() -> None:
    session = RecordingSession(["timeout", "success"])
    client = NvidiaNemotronClient(settings(), session=session)  # type: ignore[arg-type]
    try:
        client.complete([{"role": "user", "content": "Synthetic test"}])
    except ModelTimeout:
        pass
    else:
        raise AssertionError("Expected ModelTimeout")
    assert len(session.calls) == 1


def test_single_attempt_timeout_raises_model_timeout() -> None:
    session = RecordingSession(["timeout"])
    client = NvidiaNemotronClient(settings(), session=session)  # type: ignore[arg-type]
    try:
        client.complete([{"role": "user", "content": "Synthetic test"}])
    except ModelTimeout:
        pass
    else:
        raise AssertionError("Expected ModelTimeout")
    assert len(session.calls) == 1


def test_non_retryable_upstream_error_is_not_retried() -> None:
    session = RecordingSession(["upstream_error", "success"])
    client = NvidiaNemotronClient(settings(), session=session)  # type: ignore[arg-type]
    try:
        client.complete([{"role": "user", "content": "Synthetic test"}])
    except UpstreamUnavailable:
        pass
    else:
        raise AssertionError("Expected UpstreamUnavailable")
    assert len(session.calls) == 1
