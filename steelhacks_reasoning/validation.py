"""Parse model JSON and validate all evidence against submitted turns."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from pydantic import ValidationError

from .models import (
    AnalysisStatus,
    AnalyzeRequest,
    AnalyzeResponse,
    Concern,
    Evidence,
    ModelAnalysis,
    ModelEvidence,
    Speaker,
    Turn,
)


class ModelOutputInvalid(ValueError):
    """The model response failed schema or evidence validation."""


_LEGAL_CONCLUSION = re.compile(
    r"\b(illegal|unlawful|inadmissible|misconduct|policy violation|rights violation)\b",
    re.IGNORECASE,
)


def parse_and_validate_model_output(raw: str, request: AnalyzeRequest) -> AnalyzeResponse:
    model_result = parse_model_output(raw)
    return validate_model_output(model_result, request)


def parse_model_output(raw: str) -> ModelAnalysis:
    """Parse JSON and validate only the model-facing schema."""
    try:
        data = json.loads(raw, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as exc:
        raise ModelOutputInvalid("model output is not valid JSON") from exc
    data = _normalize_compatible_metadata(data)
    try:
        model_result = ModelAnalysis.model_validate(data)
    except ValidationError as exc:
        locations = []
        for error in exc.errors(include_input=False, include_context=False):
            location = ".".join(str(part) for part in error["loc"])
            locations.append(f"{location}:{error['type']}")
        summary = ", ".join(locations[:8])
        raise ModelOutputInvalid(f"model output schema errors: {summary}") from exc
    return model_result


def _normalize_compatible_metadata(data: object) -> object:
    if not isinstance(data, dict):
        return data
    normalized = dict(data)
    if "no_concerns" in normalized:
        no_concerns = normalized.pop("no_concerns")
        status = normalized.get("status")
        concerns = normalized.get("concerns")
        expected = status != "concern_detected" and concerns == []
        if type(no_concerns) is not bool or no_concerns != expected:
            raise ModelOutputInvalid("model output contains conflicting no_concerns metadata")

    top_level_concern_fields = {
        key: normalized.pop(key) for key in ("explanation", "alert_text") if key in normalized
    }
    if top_level_concern_fields:
        concerns = normalized.get("concerns")
        if not isinstance(concerns, list) or len(concerns) != 1:
            raise ModelOutputInvalid("top-level concern metadata is ambiguous")
        concern = concerns[0]
        if not isinstance(concern, dict):
            raise ModelOutputInvalid("top-level concern metadata is ambiguous")
        normalized_concern = dict(concern)
        for key, value in top_level_concern_fields.items():
            existing = normalized_concern.get(key)
            if existing is not None and existing != value:
                raise ModelOutputInvalid("top-level concern metadata conflicts with the concern")
            normalized_concern[key] = value
        normalized["concerns"] = [normalized_concern]
    return normalized


def validate_model_output(model_result: ModelAnalysis, request: AnalyzeRequest) -> AnalyzeResponse:
    """Validate evidence against the original request and build the public response."""
    turns = {turn.turn_id: turn for turn in [*request.context_turns, *request.new_turns]}
    new_ids = {turn.turn_id for turn in request.new_turns}
    concerns: list[Concern] = []
    seen_ids: set[str] = set()

    for raw_concern in model_result.concerns:
        if _LEGAL_CONCLUSION.search(raw_concern.explanation):
            raise ModelOutputInvalid("explanation makes an unsupported legal conclusion")
        evidence = [_validate_evidence(item, turns) for item in raw_concern.evidence]
        if not any(item.turn_id in new_ids for item in evidence):
            raise ModelOutputInvalid("every concern must be triggered or completed by a new turn")
        concern_id = _concern_id(
            request.session_id,
            raw_concern.category.value,
            evidence,
            new_ids,
        )
        if concern_id in seen_ids:
            continue
        seen_ids.add(concern_id)
        concerns.append(
            Concern(
                concern_id=concern_id,
                category=raw_concern.category,
                evidence=evidence,
                explanation=raw_concern.explanation,
                alert_text=raw_concern.alert_text,
            )
        )

    status = AnalysisStatus(model_result.status)
    if status == AnalysisStatus.CONCERN and not concerns:
        raise ModelOutputInvalid("concern_detected did not produce a unique validated concern")
    return AnalyzeResponse(
        session_id=request.session_id,
        request_id=request.request_id,
        sequence_number=request.sequence_number,
        analyzed_turn_ids=[turn.turn_id for turn in request.new_turns],
        status=status,
        concerns=concerns,
        error=None,
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            if result[key] != value:
                raise ModelOutputInvalid("model output contains conflicting duplicate JSON keys")
            continue
        result[key] = value
    return result


def _validate_evidence(item: ModelEvidence, turns: Mapping[str, Turn]) -> Evidence:
    turn = turns.get(item.turn_id)
    if turn is None:
        raise ModelOutputInvalid("evidence references a nonexistent turn")
    if turn.speaker != Speaker.OFFICER:
        raise ModelOutputInvalid(
            "evidence for an officer-conduct concern must reference an officer turn"
        )

    text = turn.text
    if item.start_char is not None and item.end_char is not None:
        start = item.start_char
        end = item.end_char
        if start < 0 or end <= start or end > len(text):
            raise ModelOutputInvalid("evidence offsets are outside the submitted turn")
        quote = text[start:end]
    else:
        quote = item.quote or ""
        if not quote:
            raise ModelOutputInvalid("evidence quote is empty")
        start = text.find(quote)
        if start < 0:
            raise ModelOutputInvalid("evidence quote is not an exact submitted substring")
        end = start + len(quote)

    return Evidence(
        turn_id=item.turn_id,
        speaker=turn.speaker,
        start_char=start,
        end_char=end,
        quote=quote,
        timestamp_ms=turn.timestamp_ms,
    )


def _concern_id(
    session_id: str,
    category: str,
    evidence: list[Evidence],
    new_ids: set[str],
) -> str:
    identity = {
        "session_id": session_id,
        "category": category,
        "triggering_new_turn_ids": sorted(
            item.turn_id for item in evidence if item.turn_id in new_ids
        ),
        "evidence": sorted((item.turn_id, item.start_char, item.end_char) for item in evidence),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return f"concern_{digest}"
