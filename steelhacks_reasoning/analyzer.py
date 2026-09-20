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
from .corpus import CORPUS_VERSION
from .local_classifier import (
    CLASSIFIER_VERSION,
    classify_locally_decision,
    looks_like_mislabeled_counsel_request,
    normalize_text,
)
from .models import (
    TAXONOMY_VERSION,
    AnalysisError,
    AnalysisStatus,
    AnalyzeRequest,
    AnalyzeResponse,
    Concern,
    DetectionSource,
    ErrorCode,
)
from .prompt import build_messages
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
        self._responses: OrderedDict[tuple[str, ...], AnalyzeResponse] = OrderedDict()
        self._request_fingerprints: OrderedDict[tuple[str, str, str], str] = OrderedDict()
        self._turns: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._counsel_states: dict[str, tuple[int, bool]] = {}
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

        fingerprint = _request_fingerprint(request)
        conflict = self._check_cache_and_conflicts(request, fingerprint)
        if isinstance(conflict, AnalyzeResponse):
            return conflict

        started = time.perf_counter()
        try:
            with self._lock:
                counsel_requested = self._counsel_states.get(request.session_id, (-1, False))[1]
            local_decision = classify_locally_decision(request, counsel_requested)
        finally:
            self._emit_timing("local_classification", time.perf_counter() - started)
        self._update_counsel_state(request, local_decision.counsel_requested)
        local_response = local_decision.response
        attribution_warning = looks_like_mislabeled_counsel_request(request)
        if local_decision.decisive:
            response = _with_attribution_warning(local_response, attribution_warning)
            if response.technical_warning is None:
                self._store_response(request, fingerprint, response)
            return response.model_copy(deep=True)

        try:
            client = self._client or NvidiaNemotronClient()
            raw = self._model_call("initial_model_call", client, build_messages(request))
            response = self._parse_and_validate(raw, request, repair=False)
            response = _combine_responses(response, local_response)
        except ModelOutputInvalid:
            response = _fallback_response(
                local_response,
                "Nemotron returned invalid structured output; deterministic fallback was used.",
            )
        except ModelTimeout:
            response = _fallback_response(
                local_response,
                "Nemotron timed out; deterministic fallback was used.",
            )
        except ConfigurationError:
            response = _fallback_response(
                local_response,
                "Nemotron is not configured; deterministic fallback was used.",
            )
        except UpstreamUnavailable:
            response = _fallback_response(
                local_response,
                "Nemotron was unavailable; deterministic fallback was used.",
            )

        response = _with_attribution_warning(response, attribution_warning)

        if response.technical_warning is None:
            self._store_response(request, fingerprint, response)
        return response.model_copy(deep=True)

    def _update_counsel_state(self, request: AnalyzeRequest, counsel_requested: bool) -> None:
        with self._lock:
            known_sequence, _ = self._counsel_states.get(request.session_id, (-1, False))
            if request.sequence_number >= known_sequence:
                self._counsel_states[request.session_id] = (
                    request.sequence_number,
                    counsel_requested,
                )

    def reset_session(self, session_id: str) -> None:
        """Clear process-local retry, turn, response, and counsel state for a session."""
        with self._lock:
            self._counsel_states.pop(session_id, None)
            self._responses = OrderedDict(
                (key, value) for key, value in self._responses.items() if key[0] != session_id
            )
            self._request_fingerprints = OrderedDict(
                (key, value)
                for key, value in self._request_fingerprints.items()
                if key[0] != session_id
            )
            self._turns = OrderedDict(
                (key, value) for key, value in self._turns.items() if key[0] != session_id
            )

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
        key = _response_cache_key(request, fingerprint)
        request_key = (request.session_id, request.request_id, TAXONOMY_VERSION)
        with self._lock:
            cached = self._responses.get(key)
            if cached:
                self._responses.move_to_end(key)
                return cached.model_copy(deep=True)
            known_fingerprint = self._request_fingerprints.get(request_key)
            if known_fingerprint is not None and known_fingerprint != fingerprint:
                return _error_response(
                    request,
                    ErrorCode.INPUT_CONFLICT,
                    "The request ID was reused with different content.",
                    retryable=False,
                )
            self._request_fingerprints[request_key] = fingerprint
            self._request_fingerprints.move_to_end(request_key)
            while len(self._request_fingerprints) > self._cache_size:
                self._request_fingerprints.popitem(last=False)

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
        key = _response_cache_key(request, fingerprint)
        with self._lock:
            self._responses[key] = response.model_copy(deep=True)
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


def _response_cache_key(request: AnalyzeRequest, fingerprint: str | None = None) -> tuple[str, ...]:
    turns = [*request.context_turns, *request.new_turns]
    speakers = json.dumps([turn.speaker.value for turn in turns], separators=(",", ":"))
    normalized = json.dumps([normalize_text(turn.text) for turn in turns], separators=(",", ":"))
    return (
        request.session_id,
        request.request_id,
        speakers,
        normalized,
        TAXONOMY_VERSION,
        CLASSIFIER_VERSION,
        CORPUS_VERSION,
        fingerprint or _request_fingerprint(request),
    )


def _request_fingerprint(request: AnalyzeRequest) -> str:
    turns = [*request.context_turns, *request.new_turns]
    identity = {
        "request": request.model_dump(mode="json"),
        "normalized_turns": [
            {
                "turn_id": turn.turn_id,
                "speaker": turn.speaker.value,
                "text": normalize_text(turn.text),
            }
            for turn in turns
        ],
        "taxonomy_version": TAXONOMY_VERSION,
        "classifier_version": CLASSIFIER_VERSION,
        "corpus_version": CORPUS_VERSION,
    }
    return _fingerprint(identity)


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


def _fallback_response(local: AnalyzeResponse, warning: str) -> AnalyzeResponse:
    return local.model_copy(
        update={
            "detection_source": DetectionSource.LOCAL_FALLBACK,
            "technical_warning": warning,
        },
        deep=True,
    )


def _with_attribution_warning(
    response: AnalyzeResponse, attribution_suspected: bool
) -> AnalyzeResponse:
    if not attribution_suspected:
        return response
    warning = (
        "SPEAKER_ATTRIBUTION_SUSPECTED: officer-labeled text resembles an explicit "
        "first-person request for counsel; verify the upstream speaker label."
    )
    if response.technical_warning:
        warning = f"{response.technical_warning} {warning}"
    return response.model_copy(update={"technical_warning": warning}, deep=True)


def _combine_responses(model: AnalyzeResponse, local: AnalyzeResponse) -> AnalyzeResponse:
    if model.status == AnalysisStatus.INSUFFICIENT:
        return _fallback_response(
            local,
            "Nemotron returned insufficient context; deterministic classification was used.",
        )
    merged: list[Concern] = list(model.concerns)
    keys = {
        (
            concern.category,
            tuple((item.turn_id, item.start_char, item.end_char) for item in concern.evidence),
        )
        for concern in merged
    }
    for concern in local.concerns:
        key = (
            concern.category,
            tuple((item.turn_id, item.start_char, item.end_char) for item in concern.evidence),
        )
        if key not in keys:
            merged.append(concern)
            keys.add(key)
    status = AnalysisStatus.CONCERN if merged else AnalysisStatus.NONE
    source = DetectionSource.HYBRID if local.concerns else DetectionSource.NEMOTRON
    return model.model_copy(
        update={
            "status": status,
            "concerns": merged,
            "detection_source": source,
            "technical_warning": None,
        },
        deep=True,
    )
