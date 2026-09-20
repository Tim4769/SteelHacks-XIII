from __future__ import annotations

from pathlib import Path
from typing import Any

from streamlit.testing.v1 import AppTest

import steelhacks_reasoning
from steelhacks_reasoning.models import AnalysisStatus, AnalyzeResponse, DetectionSource


def app_path() -> str:
    return str(Path(__file__).resolve().parents[1] / "demo_app.py")


def no_concern(payload: dict[str, Any]) -> AnalyzeResponse:
    return AnalyzeResponse(
        session_id=payload["session_id"],
        request_id=payload["request_id"],
        sequence_number=payload["sequence_number"],
        analyzed_turn_ids=[payload["new_turns"][0]["turn_id"]],
        status=AnalysisStatus.NONE,
        concerns=[],
        error=None,
    )


def test_manual_statement_is_editable_and_only_analyzed_on_click(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []

    def fake_analyze(payload: dict[str, Any]) -> AnalyzeResponse:
        calls.append(payload)
        return no_concern(payload)

    monkeypatch.setattr(steelhacks_reasoning, "analyze_dialogue", fake_analyze)
    app = AppTest.from_file(app_path()).run(timeout=10)
    assert calls == []
    arbitrary = "  Any manually entered sentence remains exact.  "
    app.text_area(key="statement_input").input(arbitrary).run(timeout=10)
    assert calls == []
    assert app.text_area(key="statement_input").value == arbitrary
    assert len(app.selectbox) == 0
    next(button for button in app.button if button.label == "Analyze statement").click().run(
        timeout=10
    )
    assert len(calls) == 1
    assert calls[0]["new_turns"][0]["text"] == arbitrary
    assert calls[0]["new_turns"][0]["speaker"] == "officer"
    assert calls[0]["new_turns"][0]["timestamp_ms"] == 0
    app.text_area(key="statement_input").input("Replacement sentence.").run(timeout=10)
    assert app.text_area(key="statement_input").value == "Replacement sentence."
    assert len(calls) == 1


def test_empty_input_is_rejected_without_analysis(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        steelhacks_reasoning, "analyze_dialogue", lambda payload: calls.append(payload)
    )
    app = AppTest.from_file(app_path()).run(timeout=10)
    app.text_area(key="statement_input").input("   ").run(timeout=10)
    next(button for button in app.button if button.label == "Analyze statement").click().run(
        timeout=10
    )
    assert calls == []
    assert any("Enter an interrogation statement" in error.value for error in app.error)


def test_example_populates_but_does_not_lock_input() -> None:
    app = AppTest.from_file(app_path()).run(timeout=10)
    next(button for button in app.button if button.label == "Load benefit example").click().run(
        timeout=10
    )
    assert "If you confess" in app.text_area(key="statement_input").value
    app.text_area(key="statement_input").input("Edited example.").run(timeout=10)
    assert app.text_area(key="statement_input").value == "Edited example."


def test_technical_fallback_keeps_classification_and_warning(monkeypatch: Any) -> None:
    def fake_timeout(payload: dict[str, Any]) -> AnalyzeResponse:
        return AnalyzeResponse(
            session_id=payload["session_id"],
            request_id=payload["request_id"],
            sequence_number=payload["sequence_number"],
            analyzed_turn_ids=[payload["new_turns"][0]["turn_id"]],
            status=AnalysisStatus.NONE,
            concerns=[],
            error=None,
            detection_source=DetectionSource.LOCAL_FALLBACK,
            technical_warning="Nemotron timed out; deterministic fallback was used.",
        )

    monkeypatch.setattr(steelhacks_reasoning, "analyze_dialogue", fake_timeout)
    app = AppTest.from_file(app_path()).run(timeout=10)
    app.text_area(key="statement_input").input("Synthetic statement.").run(timeout=10)
    next(button for button in app.button if button.label == "Analyze statement").click().run(
        timeout=10
    )
    assert any(success.value == "No configured concern detected" for success in app.success)
    assert not any(error.value == "Analysis unavailable" for error in app.error)
    assert any("Nemotron timed out" in warning.value for warning in app.warning)
    assert any(button.label == "Retry Nemotron" for button in app.button)
