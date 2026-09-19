"""Framework-neutral adapter showing Person 3's Streamlit integration boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import AnalyzeResponse, Concern

CATEGORY_LABELS = {
    "benefit_conditioned_on_confession": "Potential Inducement",
    "threat_conditioned_on_confession": "Potential Coercive Threat",
}

# Temporary migration aid for Person 3's current branch. New code must emit only
# the authoritative category names above.
LEGACY_CATEGORY_MAP = {
    "benefit_for_confession": "benefit_conditioned_on_confession",
    "threat_for_confession": "threat_conditioned_on_confession",
}


@dataclass(frozen=True)
class AdapterDecision:
    accepted: bool
    latest_sequence_number: int
    new_concerns: tuple[Concern, ...]
    alert_texts_to_play: tuple[str, ...]


def build_analyze_payload(
    *,
    session_id: str,
    request_id: str,
    sequence_number: int,
    context_turns: list[dict[str, Any]],
    new_turns: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the self-contained payload owned and ordered by Person 3."""
    return {
        "session_id": session_id,
        "request_id": request_id,
        "sequence_number": sequence_number,
        "context_turns": context_turns,
        "new_turns": new_turns,
    }


def accept_analysis_response(
    response: AnalyzeResponse,
    *,
    active_session_id: str,
    latest_sequence_number: int,
    displayed_concern_ids: set[str],
    played_concern_ids: set[str],
) -> AdapterDecision:
    """Reject stale results and select only new cards and unplayed alerts."""
    if response.session_id != active_session_id:
        return AdapterDecision(False, latest_sequence_number, (), ())
    if response.sequence_number < latest_sequence_number:
        return AdapterDecision(False, latest_sequence_number, (), ())

    new_concerns = tuple(
        concern for concern in response.concerns if concern.concern_id not in displayed_concern_ids
    )
    alert_texts = tuple(
        concern.alert_text
        for concern in new_concerns
        if concern.concern_id not in played_concern_ids
    )
    return AdapterDecision(
        True,
        max(latest_sequence_number, response.sequence_number),
        new_concerns,
        alert_texts,
    )
