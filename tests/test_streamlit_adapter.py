from steelhacks_reasoning.models import AnalyzeResponse
from steelhacks_reasoning.streamlit_adapter import accept_analysis_response


def response(session: str = "s1", sequence: int = 2) -> AnalyzeResponse:
    return AnalyzeResponse.model_validate(
        {
            "session_id": session,
            "request_id": "r2",
            "sequence_number": sequence,
            "analyzed_turn_ids": ["t2"],
            "status": "concern_detected",
            "concerns": [
                {
                    "concern_id": "c1",
                    "category": "benefit_conditioned_on_confession",
                    "evidence": [
                        {
                            "turn_id": "t2",
                            "speaker": "officer",
                            "start_char": 0,
                            "end_char": 4,
                            "quote": "Test",
                            "timestamp_ms": 0,
                        }
                    ],
                    "explanation": "Potential conditional benefit.",
                    "alert_text": "Review the potential benefit.",
                }
            ],
            "error": None,
        }
    )


def test_adapter_rejects_old_session_and_sequence() -> None:
    old_session = accept_analysis_response(
        response(session="old"),
        active_session_id="current",
        latest_sequence_number=2,
        displayed_concern_ids=set(),
        played_concern_ids=set(),
    )
    old_sequence = accept_analysis_response(
        response(sequence=1),
        active_session_id="s1",
        latest_sequence_number=2,
        displayed_concern_ids=set(),
        played_concern_ids=set(),
    )
    assert old_session.accepted is False
    assert old_sequence.accepted is False


def test_adapter_deduplicates_cards_and_audio() -> None:
    decision = accept_analysis_response(
        response(),
        active_session_id="s1",
        latest_sequence_number=1,
        displayed_concern_ids={"c1"},
        played_concern_ids={"c1"},
    )
    assert decision.accepted is True
    assert decision.new_concerns == ()
    assert decision.alert_texts_to_play == ()
