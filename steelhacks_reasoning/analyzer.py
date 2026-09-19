"""Retry-safe orchestration for the Person 2 reasoning vertical slice."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import ValidationError

from .client import (
    ConfigurationError,
    ModelClient,
    ModelTimeout,
    NvidiaNemotronClient,
    UpstreamUnavailable,
)
from .models import (
    AnalysisError,
    AnalysisStatus,
    AnalyzeRequest,
    AnalyzeResponse,
    ErrorCode,
)
from .prompt import build_messages, build_repair_messages
from .validation import (
    ModelOutputInvalid,
    parse_model_output,
    validate_model_output,
)

MAX_REQUEST_BYTES = 256_000


class DialogueAnalyzer:
    """Validates requests, calls Nemotron, validates evidence, and handles retries."""

    def __init__(
        self,
        client: ModelClient | None = None,
        cache_size: int = 256,
        timing_callback: Callable[[str, float], None] | None = None,
    ) -> None:
        self._client = client
        self._cache_size = cache_size
        self._timing_callback = timing_callback
        self._responses: OrderedDict[tuple[str, str], tuple[str, AnalyzeResponse]] = OrderedDict()
        self._turns: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._lock = threading.Lock()

    def analyze_dialogue(self, payload: AnalyzeRequest | Mapping[str, Any]) -> AnalyzeResponse:
        if isinstance(payload, AnalyzeRequest):
            request = payload
            encoded = request.model_dump_json()
            if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
                return _input_error(request.model_dump(), "Request exceeds the size limit.")
        else:
            try:
                encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                return _input_error(payload, "Request must be JSON-compatible.")
            if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
                return _input_error(payload, "Request exceeds the size limit.")
            try:
                request = AnalyzeRequest.model_validate(payload)
            except ValidationError:
                return _input_error(payload, "Request does not match the analysis schema.")

        fingerprint = _fingerprint(request.model_dump(mode="json"))
        conflict = self._check_cache_and_conflicts(request, fingerprint)
        if isinstance(conflict, AnalyzeResponse):
            return conflict

        try:
            client = self._client or NvidiaNemotronClient()
            raw = self._model_call("initial_model_call", client, build_messages(request))
            try:
                response = self._parse_and_validate(raw, request, repair=False)
            except ModelOutputInvalid as first_error:
                repaired = self._model_call(
                    "repair_model_call",
                    client,
                    build_repair_messages(request, raw, str(first_error)),
                )
                try:
                    response = self._parse_and_validate(repaired, request, repair=True)
                except ModelOutputInvalid:
                    response = _error_response(
                        request,
                        ErrorCode.MODEL_OUTPUT_INVALID,
                        "The model returned invalid structured output after one repair attempt.",
                        retryable=True,
                    )
        except ModelTimeout:
            response = _error_response(
                request, ErrorCode.MODEL_TIMEOUT, "The model request timed out.", retryable=True
            )
        except ConfigurationError:
            response = _error_response(
                request,
                ErrorCode.CONFIGURATION_ERROR,
                "The model configuration is incomplete.",
                retryable=False,
            )
        except UpstreamUnavailable:
            response = _error_response(
                request,
                ErrorCode.UPSTREAM_UNAVAILABLE,
                "The model service is unavailable.",
                retryable=True,
            )

        self._store_response(request, fingerprint, response)
        return response.model_copy(deep=True)

    def _model_call(self, stage: str, client: ModelClient, messages: list[dict[str, str]]) -> str:
        started = time.perf_counter()
        try:
            return client.complete(messages)
        finally:
            self._emit_timing(stage, time.perf_counter() - started)

    def _parse_and_validate(
        self, raw: str, request: AnalyzeRequest, *, repair: bool
    ) -> AnalyzeResponse:
        prefix = "repair_" if repair else ""
        started = time.perf_counter()
        try:
            parsed = parse_model_output(raw)
        finally:
            self._emit_timing(f"{prefix}json_parsing", time.perf_counter() - started)

        started = time.perf_counter()
        try:
            return validate_model_output(parsed, request)
        finally:
            self._emit_timing(f"{prefix}local_validation", time.perf_counter() - started)

    def _emit_timing(self, stage: str, elapsed_seconds: float) -> None:
        if self._timing_callback is None:
            return
        try:
            self._timing_callback(stage, elapsed_seconds)
        except Exception:
            # Diagnostics must never affect analysis or expose request content.
            return

    def _check_cache_and_conflicts(
        self, request: AnalyzeRequest, fingerprint: str
    ) -> AnalyzeResponse | None:
        key = (request.session_id, request.request_id)
        with self._lock:
            cached = self._responses.get(key)
            if cached:
                cached_fingerprint, response = cached
                if cached_fingerprint == fingerprint:
                    self._responses.move_to_end(key)
                    return response.model_copy(deep=True)
                return _error_response(
                    request,
                    ErrorCode.INPUT_CONFLICT,
                    "The request ID was reused with different content.",
                    retryable=False,
                )

            for turn in [*request.context_turns, *request.new_turns]:
                turn_key = (request.session_id, turn.turn_id)
                turn_fingerprint = _fingerprint(turn.model_dump(mode="json"))
                known = self._turns.get(turn_key)
                if known is not None and known != turn_fingerprint:
                    return _error_response(
                        request,
                        ErrorCode.INPUT_CONFLICT,
                        "A turn ID was reused with different content.",
                        retryable=False,
                    )

            for turn in [*request.context_turns, *request.new_turns]:
                turn_key = (request.session_id, turn.turn_id)
                self._turns[turn_key] = _fingerprint(turn.model_dump(mode="json"))
                self._turns.move_to_end(turn_key)
            while len(self._turns) > self._cache_size * 20:
                self._turns.popitem(last=False)
        return None

    def _store_response(
        self, request: AnalyzeRequest, fingerprint: str, response: AnalyzeResponse
    ) -> None:
        key = (request.session_id, request.request_id)
        with self._lock:
            self._responses[key] = (fingerprint, response.model_copy(deep=True))
            self._responses.move_to_end(key)
            while len(self._responses) > self._cache_size:
                self._responses.popitem(last=False)


_default_analyzer: DialogueAnalyzer | None = None
_default_lock = threading.Lock()


def analyze_dialogue(payload: AnalyzeRequest | Mapping[str, Any]) -> AnalyzeResponse:
    """Analyze one self-contained request through the process-local default analyzer."""
    global _default_analyzer
    if _default_analyzer is None:
        with _default_lock:
            if _default_analyzer is None:
                _default_analyzer = DialogueAnalyzer()
    return _default_analyzer.analyze_dialogue(payload)


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _input_error(payload: Mapping[str, Any], message: str) -> AnalyzeResponse:
    session_id = payload.get("session_id") if isinstance(payload, Mapping) else ""
    request_id = payload.get("request_id") if isinstance(payload, Mapping) else ""
    sequence = payload.get("sequence_number") if isinstance(payload, Mapping) else 0
    return AnalyzeResponse(
        session_id=session_id if isinstance(session_id, str) else "",
        request_id=request_id if isinstance(request_id, str) else "",
        sequence_number=sequence if type(sequence) is int and sequence >= 0 else 0,
        analyzed_turn_ids=[],
        status=AnalysisStatus.ERROR,
        concerns=[],
        error=AnalysisError(
            code=ErrorCode.INVALID_INPUT,
            message=message,
            retryable=False,
        ),
    )


def _error_response(
    request: AnalyzeRequest, code: ErrorCode, message: str, retryable: bool
) -> AnalyzeResponse:
    return AnalyzeResponse(
        session_id=request.session_id,
        request_id=request.request_id,
        sequence_number=request.sequence_number,
        analyzed_turn_ids=[turn.turn_id for turn in request.new_turns],
        status=AnalysisStatus.ERROR,
        concerns=[],
        error=AnalysisError(code=code, message=message, retryable=retryable),
    )
