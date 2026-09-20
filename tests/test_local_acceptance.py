from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any

import pytest

from steelhacks_reasoning.analyzer import DialogueAnalyzer, _response_cache_key
from steelhacks_reasoning.local_classifier import classify_locally
from steelhacks_reasoning.models import AnalyzeRequest

CASES: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "fixtures/local_acceptance_cases.json").read_text()
)


class NoNetworkClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        raise AssertionError("strong local acceptance cases must not call NVIDIA")


def request(case: dict[str, Any], request_id: str = "request-1") -> AnalyzeRequest:
    return AnalyzeRequest.model_validate(
        {
            "session_id": "local-acceptance",
            "request_id": request_id,
            "sequence_number": 1,
            "context_turns": [],
            "new_turns": [
                {
                    "turn_id": f"turn-{case['id']}",
                    "speaker": "officer",
                    "text": case["text"],
                    "timestamp_ms": 0,
                }
            ],
        }
    )


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_fixture_driven_local_acceptance(case: dict[str, Any]) -> None:
    client = NoNetworkClient()
    analyzer = DialogueAnalyzer(client)
    started = time.perf_counter()
    first = analyzer.analyze_dialogue(request(case))
    elapsed = time.perf_counter() - started
    second = DialogueAnalyzer(NoNetworkClient()).analyze_dialogue(request(case))

    expected = case["category"]
    assert first.status == ("concern_detected" if expected else "no_concern_detected")
    assert first.detection_source == "local"
    assert first.error is None
    assert first.technical_warning is None
    assert elapsed < 1.0
    assert client.calls == 0
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    if expected:
        assert [concern.category.value for concern in first.concerns] == [expected]
        evidence = first.concerns[0].evidence[0]
        assert evidence.quote in case["text"]
        assert case["text"][evidence.start_char : evidence.end_char] == evidence.quote
        assert first.concerns[0].concern_id == second.concerns[0].concern_id
    else:
        assert first.concerns == []


def test_offline_acceptance_latency_distribution() -> None:
    durations: list[float] = []
    for case in CASES:
        started = time.perf_counter()
        DialogueAnalyzer(NoNetworkClient()).analyze_dialogue(request(case))
        durations.append(time.perf_counter() - started)
    assert statistics.median(durations) < 0.5
    assert max(durations) < 1.0


def test_cache_key_contains_version_component() -> None:
    key = _response_cache_key(request(CASES[0]))
    assert len(key) == 8
    assert key[2] == '["officer"]'
    assert "your sister" in key[3]
    assert all(key[index] for index in (4, 5, 6))


def test_old_cached_negative_cannot_override_current_local_concern() -> None:
    negative_case = {"id": "old", "text": "Where were you last night?", "category": None}
    positive_case = CASES[0]
    analyzer = DialogueAnalyzer(NoNetworkClient())
    old_response = classify_locally(request(negative_case, request_id="shared"))
    analyzer._responses[("local-acceptance", "shared", "old-classifier-version")] = old_response
    current = analyzer.analyze_dialogue(request(positive_case, request_id="shared"))
    assert current.status == "concern_detected"
    assert current.concerns[0].category.value == positive_case["category"]
    assert current.detection_source == "local"
