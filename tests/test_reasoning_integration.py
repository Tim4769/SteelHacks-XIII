import asyncio

from app.contracts import AnalysisRequest
from app.providers import _analyze_with_team_module


def test_counsel_request_flow_uses_only_officer_followup_as_evidence():
    request = AnalysisRequest.model_validate(
        {
            "session_id": "fastapi-counsel-regression",
            "turns": [
                {
                    "turn_id": "suspect-1",
                    "speaker": "suspect",
                    "text": "I want to speak to my lawyer. I don't want to answer.",
                    "timestamp_ms": 0,
                },
                {
                    "turn_id": "officer-1",
                    "speaker": "officer",
                    "text": "We will deal with that later. Did you hurt Donald?",
                    "timestamp_ms": 1000,
                },
            ],
        }
    )

    response = asyncio.run(_analyze_with_team_module(request))

    assert [item.category for item in response.concerns] == ["questioning_after_counsel_request"]
    evidence = response.concerns[0].evidence[0]
    assert evidence.turn_id == "officer-1"
    assert evidence.speaker.value == "officer"
    assert evidence.quote == "We will deal with that later. Did you hurt Donald?"


def test_combined_mislabeled_dialogue_is_not_called_a_confession_threat():
    request = AnalysisRequest.model_validate(
        {
            "session_id": "fastapi-speaker-warning-regression",
            "turns": [
                {
                    "turn_id": "combined-1",
                    "speaker": "officer",
                    "text": (
                        "I want to speak to my lawyer. I don't want to answer any more questions. "
                        "We will deal with that later. Did you hurt Donald?"
                    ),
                    "timestamp_ms": 0,
                }
            ],
        }
    )

    response = asyncio.run(_analyze_with_team_module(request))

    assert response.concerns == []
    assert "SPEAKER_ATTRIBUTION_SUSPECTED" in (response.technical_warning or "")
