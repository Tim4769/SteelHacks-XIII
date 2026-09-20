from __future__ import annotations

from typing import Any

import pytest

from demo_helpers import (
    PRESET_SCENARIOS,
    STATEMENT_EXAMPLES,
    build_preset_payload,
    build_statement_payload,
    compare_expected,
    custom_example,
    evidence_parts,
    new_custom_turn,
    new_request_id,
    reset_session_state,
)


def test_preset_construction_is_complete_and_independent() -> None:
    payload = build_preset_payload("Benefit conditioned on confession")
    assert payload["sequence_number"] == 1
    assert payload["new_turns"][0]["turn_id"] == "benefit-t1"
    payload["new_turns"][0]["text"] = "changed"
    assert (
        PRESET_SCENARIOS["Benefit conditioned on confession"]["new_turns"][0]["text"] != "changed"
    )


def test_statement_payload_preserves_arbitrary_text() -> None:
    text = "  Arbitrary manually entered text.  "
    payload = build_statement_payload(
        session_id="session",
        sequence_number=4,
        speaker="suspect",
        text=text,
        request_id="request",
        turn_id="turn",
    )
    assert payload["new_turns"] == [
        {
            "turn_id": "turn",
            "speaker": "suspect",
            "text": text,
            "timestamp_ms": 0,
        }
    ]
    assert STATEMENT_EXAMPLES["benefit"].startswith("If you confess")


def test_expected_result_comparison() -> None:
    passed, label = compare_expected(
        "Compliant question", {"status": "no_concern_detected", "concerns": []}
    )
    assert passed is True
    assert label == "Pass"


def test_technical_error_is_not_classified() -> None:
    passed, label = compare_expected("Compliant question", {"status": "error", "concerns": []})
    assert passed is None
    assert label == "Technical failure — classification not evaluated"


def test_evidence_parts_and_bounds() -> None:
    assert evidence_parts("abcdef", 1, 4) == ("a", "bcd", "ef")
    with pytest.raises(ValueError):
        evidence_parts("abcdef", -1, 4)
    with pytest.raises(ValueError):
        evidence_parts("abcdef", 2, 7)


def test_retry_request_id_generation() -> None:
    assert new_request_id("retry", lambda: "fixed") == "retry-fixed"


def test_custom_examples_are_editable_copies() -> None:
    turns = custom_example("compliant")
    turns[0]["text"] = "Changed"
    assert custom_example("compliant")[0]["text"] == "Where were you yesterday evening?"


def test_new_custom_turn_has_stable_id_and_next_timestamp() -> None:
    turn = new_custom_turn(custom_example("compliant"), lambda: "fixed")
    assert turn == {
        "turn_id": "custom-fixed",
        "speaker": "unknown",
        "text": "",
        "timestamp_ms": 6000,
    }


def test_reset_session_state() -> None:
    state: dict[str, Any] = {
        "custom_turns": [{"turn_id": "old"}],
        "last_result": {"status": "old"},
    }
    reset_session_state(state, lambda: "fresh")
    assert state["active_session_id"] == "demo-fresh"
    assert state["sequence_number"] == 1
    assert len(state["custom_turns"]) == 2
    assert state["displayed_concern_ids"] == set()
    assert state["played_alert_ids"] == set()
    assert state["last_result"] is None
