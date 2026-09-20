from __future__ import annotations

import pytest

from steelhacks_reasoning.analyzer import DialogueAnalyzer
from steelhacks_reasoning.client import ModelTimeout
from steelhacks_reasoning.corpus import retrieve_examples
from steelhacks_reasoning.local_classifier import classify_locally, is_profanity_only
from steelhacks_reasoning.models import AnalyzeRequest, ConcernCategory


class NeverClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        raise AssertionError("model should not be called")


class TimeoutClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        raise ModelTimeout("synthetic timeout")


def request(text: str, speaker: str = "officer") -> AnalyzeRequest:
    return AnalyzeRequest.model_validate(
        {
            "session_id": "corpus-test",
            "request_id": f"request-{abs(hash((text, speaker)))}",
            "sequence_number": 1,
            "context_turns": [],
            "new_turns": [
                {
                    "turn_id": "turn-1",
                    "speaker": speaker,
                    "text": text,
                    "timestamp_ms": 0,
                }
            ],
        }
    )


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("Admit what you did and I'll recommend leniency.", ConcernCategory.BENEFIT),
        ("If you tell us you did it, I can help you get bail.", ConcernCategory.BENEFIT),
        (
            "Unless you confess, I will make sure you get the maximum sentence.",
            ConcernCategory.THREAT,
        ),
        ("Admit it or I'll throw the book at you.", ConcernCategory.THREAT),
        ("Confess or I will arrest your wife too.", ConcernCategory.THIRD_PARTY_THREAT),
        ("Unless you admit this, your mother will go to jail.", ConcernCategory.THIRD_PARTY_THREAT),
        ("You get no water until you confess.", ConcernCategory.DEPRIVATION),
        ("No bathroom break unless you admit what happened.", ConcernCategory.DEPRIVATION),
        ("We found your fingerprints, so admit it now.", ConcernCategory.EVIDENCE_PRESSURE),
        ("The video proves you did it; confess.", ConcernCategory.EVIDENCE_PRESSURE),
        ("It was just an accident, so admit you did it.", ConcernCategory.MINIMIZATION),
        ("Anyone could have lost control; tell me you did it.", ConcernCategory.MINIMIZATION),
    ],
)
def test_local_paraphrase_corpus(text: str, category: ConcernCategory) -> None:
    response = classify_locally(request(text))
    assert response.status == "concern_detected"
    assert response.concerns[0].category == category
    evidence = response.concerns[0].evidence[0]
    assert evidence.quote == text
    assert text[evidence.start_char : evidence.end_char] == evidence.quote


@pytest.mark.parametrize(
    "text",
    [
        "Where were you last night?",
        "You are a fucking liar.",
        "This is bullshit.",
        "I cannot promise you will go home if you confess.",
        "An improper example is: confess and I will let you go.",
        "Suppose an officer said confess or go to jail.",
        "The witness described a blue car.",
    ],
)
def test_local_negative_corpus(text: str) -> None:
    response = classify_locally(request(text))
    assert response.status == "no_concern_detected"
    assert response.concerns == []


def test_suspect_statement_does_not_create_officer_tactic() -> None:
    response = classify_locally(request("Confess or I will make this worse.", "suspect"))
    assert response.status == "no_concern_detected"


def test_profanity_only_skips_model_and_returns_stable_classification() -> None:
    client = NeverClient()
    req = request("You are a fucking liar.")
    assert is_profanity_only(req)
    response = DialogueAnalyzer(client).analyze_dialogue(req)
    assert response.status == "no_concern_detected"
    assert response.detection_source == "local"
    assert response.technical_warning is None
    assert client.calls == 0
    assert client.calls == 0


def test_positive_local_result_precedes_client_timeout() -> None:
    client = TimeoutClient()
    response = DialogueAnalyzer(client).analyze_dialogue(
        request("If you confess, I can help you get bail.")
    )
    assert response.status == "concern_detected"
    assert response.concerns[0].category == ConcernCategory.BENEFIT
    assert response.detection_source == "local"
    assert response.technical_warning is None


def test_retrieval_prefers_relevant_examples() -> None:
    examples = retrieve_examples(["Confess or your mother will go to jail."], limit=3)
    assert any(item.category == ConcernCategory.THIRD_PARTY_THREAT for item in examples)
