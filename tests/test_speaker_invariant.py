from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from steelhacks_reasoning.analyzer import DialogueAnalyzer
from steelhacks_reasoning.models import (
    AnalysisStatus,
    AnalyzeResponse,
    Concern,
    ConcernCategory,
    Evidence,
    Speaker,
)
from steelhacks_reasoning.streamlit_adapter import (
    accept_analysis_response,
    build_analyze_payload,
)


class NoNetwork:
    def __init__(self, output: str | None = None) -> None:
        self.output = output
        self.calls = 0

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        if self.output is None:
            raise AssertionError("NVIDIA must not be called")
        return self.output


def payload(
    turns: list[dict[str, Any]], request_id: str = "r1", sequence: int = 1
) -> dict[str, Any]:
    return {
        "session_id": "speaker-session",
        "request_id": request_id,
        "sequence_number": sequence,
        "context_turns": [],
        "new_turns": turns,
    }


def turn(turn_id: str, speaker: str, text: str, timestamp: int = 0) -> dict[str, Any]:
    return {
        "turn_id": turn_id,
        "speaker": speaker,
        "text": text,
        "timestamp_ms": timestamp,
    }


@pytest.mark.parametrize(
    ("speaker", "text"),
    [
        ("suspect", "If I confess, can I go home?"),
        ("suspect", "Are you threatening to arrest my sister?"),
        ("suspect", "I killed Donald."),
        ("narrator", "The detective promised him release for a confession."),
        ("witness", "The detective promised him release for a confession."),
        ("unknown", "If you confess, you can go home."),
    ],
)
def test_non_officer_turn_never_generates_concern(speaker: str, text: str) -> None:
    client = NoNetwork()
    response = DialogueAnalyzer(client).analyze_dialogue(payload([turn("t1", speaker, text)]))
    assert response.status == "no_concern_detected"
    assert response.concerns == []
    assert client.calls == 0


def test_counsel_request_then_officer_question_flags_only_officer() -> None:
    analyzer = DialogueAnalyzer(NoNetwork())
    request = payload(
        [
            turn("s1", "suspect", "I want to speak to my lawyer. I don't want to answer."),
            turn("o1", "officer", "We'll deal with that later. Did you hurt Donald?", 1000),
        ]
    )
    response = analyzer.analyze_dialogue(request)
    concern = next(
        item for item in response.concerns if item.category == ConcernCategory.COUNSEL_QUESTIONING
    )
    assert concern.evidence[0].turn_id == "o1"
    assert concern.evidence[0].speaker == Speaker.OFFICER
    assert concern.evidence[0].quote == request["new_turns"][1]["text"]


def test_counsel_request_then_compliant_stop_is_no_concern() -> None:
    response = DialogueAnalyzer(NoNetwork()).analyze_dialogue(
        payload(
            [
                turn("s1", "suspect", "I want a lawyer."),
                turn(
                    "o1",
                    "officer",
                    "You've asked for a lawyer. I'm stopping the interview now. "
                    "We'll arrange for you to speak with counsel.",
                ),
            ]
        )
    )
    assert response.status == "no_concern_detected"
    assert response.concerns == []


@pytest.mark.parametrize(
    "officer_text",
    [
        "I will stop questioning you now.",
        "We will arrange for you to speak with counsel.",
        "You do not have to answer any more questions.",
        "I won't ask you about the case until counsel is present.",
    ],
)
def test_each_compliant_officer_response_produces_no_concern(
    officer_text: str,
) -> None:
    response = DialogueAnalyzer(NoNetwork()).analyze_dialogue(
        payload(
            [
                turn("s1", "suspect", "I want a lawyer."),
                turn("o1", "officer", officer_text),
            ]
        )
    )
    assert response.status == "no_concern_detected"
    assert response.concerns == []


def test_counsel_state_persists_across_same_session_requests() -> None:
    analyzer = DialogueAnalyzer(NoNetwork())
    first = analyzer.analyze_dialogue(
        payload([turn("s1", "suspect", "I need an attorney.")], "r1", 1)
    )
    second = analyzer.analyze_dialogue(
        payload([turn("o1", "officer", "Explain those marks on your hands.")], "r2", 2)
    )
    assert first.status == "no_concern_detected"
    assert second.concerns[0].category == ConcernCategory.COUNSEL_QUESTIONING
    assert second.concerns[0].evidence[0].speaker == Speaker.OFFICER


def test_reset_session_clears_persisted_counsel_state() -> None:
    analyzer = DialogueAnalyzer(NoNetwork())
    analyzer.analyze_dialogue(payload([turn("s1", "suspect", "I need an attorney.")], "r1", 1))
    analyzer.reset_session("speaker-session")
    response = analyzer.analyze_dialogue(
        payload([turn("o1", "officer", "Explain those marks on your hands.")], "r2", 2)
    )
    assert response.status == "no_concern_detected"
    assert response.concerns == []


def test_model_non_officer_evidence_is_rejected_without_repair_or_alert() -> None:
    raw = json.dumps(
        {
            "status": "concern_detected",
            "concerns": [
                {
                    "category": "benefit_conditioned_on_confession",
                    "evidence": [{"turn_id": "s1", "start_char": 0, "end_char": 12}],
                    "explanation": "Potential benefit.",
                    "alert_text": "Review this statement.",
                }
            ],
        }
    )
    client = NoNetwork(raw)
    response = DialogueAnalyzer(client).analyze_dialogue(
        {
            "session_id": "model-speaker",
            "request_id": "r1",
            "sequence_number": 1,
            "context_turns": [turn("s1", "suspect", "If I confess, can I go home?")],
            "new_turns": [turn("o1", "officer", "We should discuss your statement.")],
        }
    )
    assert response.status == "no_concern_detected"
    assert response.concerns == []
    assert response.detection_source == "local_fallback"
    assert client.calls == 1


def test_backend_derives_officer_metadata_from_source_turn() -> None:
    text = "We should discuss your statement."
    raw = json.dumps(
        {
            "status": "concern_detected",
            "concerns": [
                {
                    "category": "benefit_conditioned_on_confession",
                    "evidence": [{"turn_id": "o1", "start_char": 0, "end_char": len(text)}],
                    "explanation": "Potential benefit.",
                    "alert_text": "Review this statement.",
                }
            ],
        }
    )
    response = DialogueAnalyzer(NoNetwork(raw)).analyze_dialogue(
        payload([turn("o1", "officer", text, 4321)])
    )
    evidence = response.concerns[0].evidence[0]
    assert evidence.speaker == Speaker.OFFICER
    assert evidence.timestamp_ms == 4321
    assert evidence.quote == text


def test_exact_long_scenario_returns_only_officer_evidence() -> None:
    turns = json.loads((Path(__file__).parent / "fixtures/counsel_scenario.json").read_text())
    response = DialogueAnalyzer(NoNetwork()).analyze_dialogue(payload(turns))
    by_turn = {
        evidence.turn_id: concern.category.value
        for concern in response.concerns
        for evidence in concern.evidence
    }
    assert by_turn["c2"] == "questioning_after_counsel_request"
    assert any(
        concern.category == ConcernCategory.BENEFIT and concern.evidence[0].turn_id == "c4"
        for concern in response.concerns
    )
    assert any(
        concern.category == ConcernCategory.THREAT and concern.evidence[0].turn_id == "c6"
        for concern in response.concerns
    )
    assert by_turn["c8"] == "questioning_after_counsel_request"
    assert all(
        evidence.speaker == Speaker.OFFICER
        for concern in response.concerns
        for evidence in concern.evidence
    )
    assert not {"c1", "c3", "c5", "c7", "c9", "c11", "c13"} & set(by_turn)


def test_adapter_preserves_speaker_fields() -> None:
    context = [turn("s1", "suspect", "I want a lawyer.")]
    new = [turn("o1", "officer", "What happened?", 1000)]
    built = build_analyze_payload(
        session_id="s",
        request_id="r",
        sequence_number=1,
        context_turns=context,
        new_turns=new,
    )
    assert built["context_turns"][0]["speaker"] == "suspect"
    assert built["new_turns"][0]["speaker"] == "officer"


def test_adapter_excludes_non_officer_alert_and_keeps_card_audio_id_aligned() -> None:
    invalid = Concern.model_construct(
        concern_id="suspect-concern",
        category=ConcernCategory.BENEFIT,
        evidence=[
            Evidence(
                turn_id="s1",
                speaker=Speaker.SUSPECT,
                start_char=0,
                end_char=4,
                quote="Test",
                timestamp_ms=0,
            )
        ],
        explanation="Invalid suspect concern.",
        alert_text="Must not play.",
    )
    response = AnalyzeResponse.model_construct(
        session_id="s",
        request_id="r",
        sequence_number=1,
        analyzed_turn_ids=["s1"],
        status=AnalysisStatus.CONCERN,
        concerns=[invalid],
        error=None,
        detection_source="nemotron",
        technical_warning=None,
    )
    rejected = accept_analysis_response(
        response,
        active_session_id="s",
        latest_sequence_number=0,
        displayed_concern_ids=set(),
        played_concern_ids=set(),
    )
    assert rejected.new_concerns == ()
    assert rejected.alert_texts_to_play == ()
    assert rejected.alert_concern_ids == ()

    valid_text = "Confess or I will add another charge."
    valid = DialogueAnalyzer(NoNetwork()).analyze_dialogue(
        payload([turn("o1", "officer", valid_text)])
    )
    accepted = accept_analysis_response(
        valid,
        active_session_id="speaker-session",
        latest_sequence_number=0,
        displayed_concern_ids=set(),
        played_concern_ids=set(),
    )
    assert accepted.new_concerns[0].concern_id == accepted.alert_concern_ids[0]


def test_officer_labeled_counsel_request_emits_attribution_warning_without_relabeling() -> None:
    response = DialogueAnalyzer(NoNetwork()).analyze_dialogue(
        payload([turn("o1", "officer", "I want to speak to my lawyer.")])
    )
    assert response.status == "no_concern_detected"
    assert response.concerns == []
    assert "SPEAKER_ATTRIBUTION_SUSPECTED" in (response.technical_warning or "")
