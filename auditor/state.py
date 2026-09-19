"""Session-state helpers shared by all Streamlit pages."""

from __future__ import annotations

import csv
import io
import json
import time
import uuid
from datetime import datetime
from typing import Any

import streamlit as st

from auditor.demo_data import sample_archives
from auditor.elevenlabs import synthesize_alert, transcribe_audio
from auditor.nemotron import CATEGORY_LABELS, analyze_turns, reset_remote_session


def init_state() -> None:
    if "live_session" not in st.session_state:
        st.session_state.live_session = _empty_session("live-demo")
    if "archives" not in st.session_state:
        st.session_state.archives = sample_archives()
    if "played_concern_ids" not in st.session_state:
        st.session_state.played_concern_ids = set()
    if "last_spoken_alert" not in st.session_state:
        st.session_state.last_spoken_alert = None
    if "demo_cursor" not in st.session_state:
        st.session_state.demo_cursor = 0


def _empty_session(prefix: str) -> dict[str, Any]:
    return {
        "session_id": f"{prefix}-{uuid.uuid4().hex[:8]}",
        "label": "Live session (unsaved)",
        "started_at": None,
        "is_streaming": False,
        "turns": [],
        "active_alert": None,
        "turn_counter": 0,
    }


def format_clock(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def session_duration_seconds(session: dict[str, Any]) -> int:
    started = session.get("started_at")
    if not started:
        return 0
    return int(time.time() - started)


def high_risk_count(session: dict[str, Any]) -> int:
    return sum(1 for t in session.get("turns", []) if t.get("is_high_risk"))


def format_timestamp(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return "--:--:--"
    return format_clock(timestamp_ms // 1000)


def start_stream() -> None:
    session = st.session_state.live_session
    if not session.get("started_at"):
        reset_live_session()
        session = st.session_state.live_session
        session["started_at"] = time.time()
    session["is_streaming"] = True


def reset_live_session() -> None:
    old_id = st.session_state.live_session.get("session_id")
    reset_remote_session(old_id)
    st.session_state.live_session = _empty_session("live")
    st.session_state.demo_cursor = 0
    st.session_state.played_concern_ids = set()
    st.session_state.last_spoken_alert = None


def stop_stream(save_to_archive: bool = True) -> None:
    session = st.session_state.live_session
    session["is_streaming"] = False
    if save_to_archive and session["turns"]:
        archived = {
            "session_id": session["session_id"],
            "label": f"Live capture {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "turns": list(session["turns"]),
        }
        st.session_state.archives = [archived, *st.session_state.archives]


def ingest_text_turn(speaker: str, text: str) -> None:
    session = st.session_state.live_session
    session["turn_counter"] += 1
    started = session["started_at"] or time.time()
    timestamp_ms = int((time.time() - started) * 1000)
    turn = {
        "turn_id": f"t{session['turn_counter']}",
        "speaker": speaker,
        "text": text.strip(),
        "timestamp_ms": timestamp_ms,
        "risk_level": "none",
        "is_high_risk": False,
        "category": None,
        "violation_type": None,
        "reasoning": None,
        "alert_text": None,
        "concern_id": None,
    }
    apply_analysis(session, [turn])
    session["turns"].append(turn)


def ingest_audio(file_name: str, audio_bytes: bytes) -> None:
    start_stream()
    for piece in transcribe_audio(file_name, audio_bytes):
        ingest_text_turn(piece.get("speaker", "unknown"), piece.get("text", ""))


def apply_analysis(session: dict[str, Any], new_turns: list[dict[str, Any]]) -> None:
    result = analyze_turns(session["session_id"], new_turns)
    status = result.get("status")
    if result.get("error") or status == "error":
        session["active_alert"] = {
            "status": "error",
            "violation_type": "Analysis Error",
            "reasoning": result.get("error") or "Nemotron returned status=error.",
            "alert_text": "Analysis failed. Retry this turn when the Nemotron service is available.",
        }
        return

    if status == "insufficient_context":
        if not session.get("active_alert"):
            session["active_alert"] = None
        return

    by_id = {t["turn_id"]: t for t in new_turns}
    for concern in result.get("concerns") or []:
        evidence = (concern.get("evidence") or [{}])[0]
        turn = by_id.get(evidence.get("turn_id"))
        category = concern.get("category")
        risk_level = "medium" if category == "leading_question" else "high"
        if turn:
            turn["category"] = category
            turn["violation_type"] = CATEGORY_LABELS.get(category, category)
            turn["reasoning"] = concern.get("explanation")
            turn["alert_text"] = concern.get("alert_text")
            turn["concern_id"] = concern.get("concern_id")
            turn["risk_level"] = risk_level
            turn["is_high_risk"] = risk_level == "high"

        if risk_level == "high":
            session["active_alert"] = {
                "status": "high",
                "violation_type": CATEGORY_LABELS.get(category, category),
                "reasoning": concern.get("explanation"),
                "alert_text": concern.get("alert_text"),
                "concern_id": concern.get("concern_id"),
            }
            maybe_speak_alert(concern.get("concern_id"), concern.get("alert_text"))

    if result.get("status") == "no_concern_detected" and not session.get("active_alert"):
        session["active_alert"] = None


def maybe_speak_alert(concern_id: str | None, alert_text: str | None) -> None:
    if not concern_id or not alert_text:
        return
    if concern_id in st.session_state.played_concern_ids:
        return
    st.session_state.played_concern_ids.add(concern_id)
    audio_bytes = synthesize_alert(alert_text)
    st.session_state.last_spoken_alert = {
        "concern_id": concern_id,
        "alert_text": alert_text,
        "audio_bytes": audio_bytes,
    }


def export_session(session: dict[str, Any], fmt: str) -> bytes:
    rows = []
    for turn in session.get("turns", []):
        rows.append(
            {
                "session_id": session.get("session_id"),
                "timestamp": format_timestamp(turn.get("timestamp_ms")),
                "timestamp_ms": turn.get("timestamp_ms"),
                "speaker": turn.get("speaker"),
                "text": turn.get("text"),
                "risk_level": turn.get("risk_level"),
                "violation_type": turn.get("violation_type"),
                "category": turn.get("category"),
                "reasoning": turn.get("reasoning"),
                "alert_text": turn.get("alert_text"),
                "concern_id": turn.get("concern_id"),
            }
        )
    if fmt == "json":
        payload = {
            "session_id": session.get("session_id"),
            "label": session.get("label"),
            "turns": rows,
        }
        return json.dumps(payload, indent=2).encode("utf-8")

    buffer = io.StringIO()
    fieldnames = [
        "session_id",
        "timestamp",
        "timestamp_ms",
        "speaker",
        "text",
        "risk_level",
        "violation_type",
        "category",
        "reasoning",
        "alert_text",
        "concern_id",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")
