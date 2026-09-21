"""Nemotron analysis client matching Person 2's integration plan.

Person 3 POSTs finalized turns to POST /api/analyze and renders concerns.
Person 2 hosts Nemotron; this UI never holds the NVIDIA key.

Until NEMOTRON_ANALYZE_URL is set, analyze_turns() returns the same JSON
shape using a local heuristic so the interface can be demoed.
"""

from __future__ import annotations

from typing import Any

CATEGORY_LABELS = {
    "benefit_for_confession": "False Promise of Leniency",
    "threat_for_confession": "Coercive Threat",
    "questioning_after_counsel_request": "Questioning After Counsel Request",
    "leading_question": "Leading Question",
}

# Person 2's hosted URL (not localhost on another laptop).
NEMOTRON_ANALYZE_URL = None  # e.g. "https://team-backend.example/api/analyze"
NEMOTRON_RESET_URL = None  # e.g. "https://team-backend.example/api/reset-session"


def build_analyze_payload(session_id: str, turns: list[dict[str, Any]]) -> dict[str, Any]:
    """Request body for Person 2 POST /api/analyze."""
    return {
        "session_id": session_id,
        "turns": [
            {
                "turn_id": t["turn_id"],
                "speaker": t["speaker"],
                "text": t["text"],
                "timestamp_ms": t.get("timestamp_ms"),
            }
            for t in turns
        ],
    }


def analyze_turns(session_id: str, new_turns: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze newly finalized turns.

    Later:
        import requests
        response = requests.post(NEMOTRON_ANALYZE_URL, json=payload, timeout=30)
        return response.json()
    """
    payload = build_analyze_payload(session_id, new_turns)
    if NEMOTRON_ANALYZE_URL:
        raise NotImplementedError("Set NEMOTRON_ANALYZE_URL and POST to Person 2.")
    return _heuristic_analyze(payload)


def reset_remote_session(session_id: str) -> None:
    """Person 2 POST /api/reset-session. No-op until the backend is hosted."""
    _ = session_id
    if NEMOTRON_RESET_URL:
        raise NotImplementedError("Set NEMOTRON_RESET_URL and POST {session_id}.")


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
            category = "benefit_for_confession"
            explanation = (
                "The officer appears to offer a specific benefit in exchange for a confession."
            )
            alert_text = "Potential inducement detected. Review the promise of benefit."
        elif any(k in text for k in ("never see", "worse for you", "you'll regret", "stack charges")):
            category = "threat_for_confession"
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
