"""Analysis client: Josh's Nemotron module with local fallback, else heuristics."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Copied from Josh's Streamlit adapter so the UI still loads if analysis extras fail.
CATEGORY_LABELS = {
    "benefit_conditioned_on_confession": "Potential Inducement",
    "threat_conditioned_on_confession": "Potential Coercive Threat",
    "third_party_threat_conditioned_on_confession": "Potential Third-Party Threat",
    "deprivation_conditioned_on_confession": "Potential Conditional Deprivation",
    "evidence_claim_used_as_pressure": "Potential Evidence-Claim Pressure",
    "minimization_used_to_elicit_admission": "Potential Minimization Tactic",
    "questioning_after_counsel_request": "Potential Questioning After Counsel Request",
    "leading_question": "Leading Question",
    "benefit_for_confession": "False Promise of Leniency",
    "threat_for_confession": "Coercive Threat",
}

MEDIUM_CATEGORIES = {"leading_question", "minimization_used_to_elicit_admission"}

_analyzer = None


def _get_analyzer():
    global _analyzer
    if _analyzer is None:
        from steelhacks_reasoning import DialogueAnalyzer

        _analyzer = DialogueAnalyzer()
    return _analyzer


def _compact_turn(turn: dict[str, Any]) -> dict[str, Any]:
    speaker = turn.get("speaker") or "unknown"
    if speaker not in {"officer", "suspect", "unknown", "narrator", "witness"}:
        speaker = "unknown"
    return {
        "turn_id": turn["turn_id"],
        "speaker": speaker,
        "text": turn["text"],
        "timestamp_ms": turn.get("timestamp_ms"),
    }


def analyze_turns(
    session_id: str,
    new_turns: list[dict[str, Any]],
    context_turns: list[dict[str, Any]] | None = None,
    sequence_number: int = 1,
) -> dict[str, Any]:
    payload = {
        "session_id": session_id,
        "request_id": f"req-{uuid.uuid4().hex[:10]}",
        "sequence_number": max(0, int(sequence_number)),
        "context_turns": [_compact_turn(t) for t in (context_turns or [])],
        "new_turns": [_compact_turn(t) for t in new_turns],
    }
    try:
        response = _get_analyzer().analyze_dialogue(payload)
        data = response.model_dump(mode="json")
        err = data.get("error")
        if isinstance(err, dict):
            data["error"] = err.get("message")
        return data
    except Exception:
        return _heuristic_analyze({"session_id": session_id, "turns": payload["new_turns"]})


def reset_remote_session(session_id: str) -> None:
    if not session_id:
        return
    try:
        _get_analyzer().reset_session(session_id)
    except Exception:
        return


def _heuristic_analyze(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = payload["session_id"]
    concerns: list[dict[str, Any]] = []
    analyzed = []

    for turn in payload["turns"]:
        analyzed.append(turn["turn_id"])
        if turn.get("speaker") != "officer":
            continue
        text = (turn.get("text") or "").lower()
        category = None
        explanation = None
        alert_text = None

        if "lawyer" in text or "attorney" in text or "counsel" in text:
            category = "questioning_after_counsel_request"
            explanation = (
                "The officer continues substantive questioning after an attorney "
                "request is in play."
            )
            alert_text = "Potential questioning after counsel request. Review this turn."
        elif any(k in text for k in ("confess", "go home", "go easy", "help you out")):
            category = "benefit_conditioned_on_confession"
            explanation = (
                "The officer appears to offer a specific benefit in exchange for a confession."
            )
            alert_text = "Potential inducement detected. Review the promise of benefit."
        elif any(k in text for k in ("never see", "worse for you", "you'll regret", "stack charges")):
            category = "threat_conditioned_on_confession"
            explanation = "The officer uses a threat to pressure a confession."
            alert_text = "Potential coercive threat detected. Review this turn."
        elif text.strip().endswith("didn't you") or text.strip().startswith("you were"):
            category = "leading_question"
            explanation = (
                "The question supplies the expected answer rather than inviting a free account."
            )
            alert_text = "Leading question detected. Review for suggestibility."

        if category:
            concerns.append(
                {
                    "concern_id": f"{session_id}-{turn['turn_id']}-{category}",
                    "category": category,
                    "evidence": [
                        {
                            "turn_id": turn["turn_id"],
                            "speaker": turn["speaker"],
                            "quote": turn["text"],
                            "timestamp_ms": turn.get("timestamp_ms"),
                        }
                    ],
                    "explanation": explanation,
                    "alert_text": alert_text,
                }
            )

    status = "concern_detected" if concerns else "no_concern_detected"
    return {
        "session_id": session_id,
        "analyzed_turn_ids": analyzed,
        "status": status,
        "concerns": concerns,
        "error": None,
    }
