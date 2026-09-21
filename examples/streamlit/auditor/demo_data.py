"""Sample archive sessions so Page 3 is reviewable before live recordings exist."""

from __future__ import annotations

from typing import Any


def sample_archives() -> list[dict[str, Any]]:
    return [
        {
            "session_id": "session-104",
            "label": "Session #104 - Case A49",
            "turns": [
                _turn("t1", "officer", "Where were you last night?", 0, "none"),
                _turn("t2", "suspect", "I was at home.", 5000, "none"),
                _turn(
                    "t3",
                    "officer",
                    "If you confess, I can make sure you go home tonight.",
                    10000,
                    "high",
                    category="benefit_for_confession",
                    violation="False Promise of Leniency",
                    reasoning="The officer offers release in exchange for a confession.",
                    alert_text="Potential inducement detected. Review the promise of release.",
                    concern_id="session-104-c1",
                ),
                _turn("t4", "suspect", "I want to think about that.", 16000, "none"),
            ],
        },
        {
            "session_id": "session-105",
            "label": "Session #105 - Case B12",
            "turns": [
                _turn("t1", "officer", "You were at the warehouse, didn't you?", 0, "medium",
                      category="leading_question",
                      violation="Leading Question",
                      reasoning="The question supplies the expected answer rather than inviting a free account.",
                      alert_text="Leading question detected. Review for suggestibility.",
                      concern_id="session-105-c1"),
                _turn("t2", "suspect", "I want a lawyer.", 8000, "none"),
                _turn(
                    "t3",
                    "officer",
                    "We can keep talking first. Tell me what happened after you called your lawyer.",
                    14000,
                    "high",
                    category="questioning_after_counsel_request",
                    violation="Questioning After Counsel Request",
                    reasoning="Substantive questioning continues after the suspect requested an attorney.",
                    alert_text="Potential questioning after counsel request. Review this turn.",
                    concern_id="session-105-c2",
                ),
                _turn(
                    "t4",
                    "officer",
                    "If you stay quiet it will be worse for you. We can stack charges.",
                    22000,
                    "high",
                    category="threat_for_confession",
                    violation="Coercive Threat",
                    reasoning="The officer uses a threat to pressure cooperation.",
                    alert_text="Potential coercive threat detected. Review this turn.",
                    concern_id="session-105-c3",
                ),
            ],
        },
    ]


def live_demo_script() -> list[dict[str, str]]:
    """Chronological turns used when the demo stream is started."""
    return [
        {"speaker": "officer", "text": "Where were you last night?"},
        {"speaker": "suspect", "text": "I was at home."},
        {"speaker": "officer", "text": "If you confess, I can make sure you go home tonight."},
        {"speaker": "suspect", "text": "I think I want a lawyer."},
        {"speaker": "officer", "text": "We can keep talking first. Tell me what happened after you called your lawyer."},
    ]


def _turn(
    turn_id: str,
    speaker: str,
    text: str,
    timestamp_ms: int,
    risk_level: str,
    category: str | None = None,
    violation: str | None = None,
    reasoning: str | None = None,
    alert_text: str | None = None,
    concern_id: str | None = None,
) -> dict[str, Any]:
    return {
        "turn_id": turn_id,
        "speaker": speaker,
        "text": text,
        "timestamp_ms": timestamp_ms,
        "risk_level": risk_level,
        "is_high_risk": risk_level == "high",
        "category": category,
        "violation_type": violation,
        "reasoning": reasoning,
        "alert_text": alert_text,
        "concern_id": concern_id,
    }
