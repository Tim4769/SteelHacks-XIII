"""Simple local statement detector for the Person 2 reasoning module."""

from __future__ import annotations

import html
import time
from typing import Any
from uuid import uuid4

import streamlit as st

from demo_helpers import STATEMENT_EXAMPLES, build_statement_payload, evidence_parts
from steelhacks_reasoning import analyze_dialogue

CATEGORY_LABELS = {
    "benefit_conditioned_on_confession": "Benefit conditioned on confession",
    "threat_conditioned_on_confession": "Threat conditioned on confession",
    "third_party_threat_conditioned_on_confession": "Third-party threat conditioned on confession",
    "deprivation_conditioned_on_confession": "Deprivation conditioned on confession",
    "evidence_claim_used_as_pressure": "Evidence claim used as pressure",
    "minimization_used_to_elicit_admission": "Minimization used to elicit admission",
    "questioning_after_counsel_request": "Questioning after counsel request",
}


def initialize_state() -> None:
    defaults: dict[str, Any] = {
        "active_session_id": f"demo-{uuid4().hex}",
        "sequence_number": 1,
        "statement_input": "",
        "last_result": None,
        "last_latency": None,
        "last_payload": None,
        "displayed_concern_ids": set(),
        "played_alert_ids": set(),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def clear_result() -> None:
    st.session_state.last_result = None
    st.session_state.last_latency = None
    st.session_state.last_payload = None


def load_example(name: str) -> None:
    st.session_state.statement_input = STATEMENT_EXAMPLES[name]
    clear_result()


def make_payload(text: str) -> dict[str, Any]:
    return build_statement_payload(
        session_id=st.session_state.active_session_id,
        sequence_number=st.session_state.sequence_number,
        speaker="officer",
        text=text,
        request_id=f"req-{uuid4().hex}",
        turn_id=f"turn-{uuid4().hex}",
    )


def perform_analysis(payload: dict[str, Any]) -> None:
    with st.spinner("Analyzing with NVIDIA Nemotron…"):
        started = time.perf_counter()
        response = analyze_dialogue(payload)
        latency = time.perf_counter() - started
    st.session_state.last_result = response.model_dump(mode="json")
    st.session_state.last_latency = latency
    st.session_state.last_payload = payload
    st.session_state.sequence_number += 1
    for concern in response.concerns:
        st.session_state.displayed_concern_ids.add(concern.concern_id)


def analyze_current_statement() -> None:
    text = st.session_state.statement_input
    if not text.strip():
        st.error("Enter an interrogation statement before analysis.")
        return
    perform_analysis(make_payload(text))


def retry_last_statement() -> None:
    previous = st.session_state.last_payload
    if previous is None:
        return
    payload = build_statement_payload(
        session_id=st.session_state.active_session_id,
        sequence_number=st.session_state.sequence_number,
        speaker=previous["new_turns"][0]["speaker"],
        text=previous["new_turns"][0]["text"],
        request_id=f"req-{uuid4().hex}",
        turn_id=previous["new_turns"][0]["turn_id"],
    )
    perform_analysis(payload)


def render_highlighted_statement() -> None:
    response = st.session_state.last_result
    payload = st.session_state.last_payload
    if response is None or payload is None or response["status"] != "concern_detected":
        return
    turn = payload["new_turns"][0]
    ranges = sorted(
        (evidence["start_char"], evidence["end_char"])
        for concern in response["concerns"]
        for evidence in concern["evidence"]
        if evidence["turn_id"] == turn["turn_id"]
    )
    pieces: list[str] = []
    cursor = 0
    for start, end in ranges:
        if start < cursor:
            continue
        before, quote, _ = evidence_parts(turn["text"], start, end)
        pieces.extend((html.escape(before[cursor:]), f"<mark>{html.escape(quote)}</mark>"))
        cursor = end
    pieces.append(html.escape(turn["text"][cursor:]))
    st.markdown("**Evidence in submitted statement**")
    st.markdown("".join(pieces), unsafe_allow_html=True)


def render_result() -> None:
    response = st.session_state.last_result
    if response is None:
        return
    st.divider()
    if response["status"] == "concern_detected":
        st.error("Potential concern detected")
        render_highlighted_statement()
        for concern in response["concerns"]:
            st.markdown(f"### {CATEGORY_LABELS.get(concern['category'], concern['category'])}")
            for evidence in concern["evidence"]:
                st.markdown(f"**Exact supporting quote:** “{evidence['quote']}”")
                st.caption(
                    f"{evidence['speaker'].title()} · {evidence['timestamp_ms']} ms · "
                    f"{evidence['turn_id']}"
                )
            st.write(concern["explanation"])
            st.warning(concern["alert_text"])
            st.caption(f"Concern ID: {concern['concern_id']}")
    elif response["status"] == "no_concern_detected":
        st.success("No configured concern detected")
    elif response["status"] == "insufficient_context":
        st.warning("More dialogue is needed before the configured concerns can be assessed.")
    else:
        error = response["error"]
        st.error("The submitted request was invalid and was not classified.")
        st.write(f"**Error code:** {error['code']}")
        st.write(f"**Message:** {error['message']}")
        st.write(f"**Retryable:** {error['retryable']}")
        st.button("Retry", on_click=retry_last_statement, type="primary")
    source = response["detection_source"]
    source_label = "deterministic local classifier" if source == "local" else source
    st.caption(f"Detection source: {source_label}")
    if response.get("technical_warning"):
        st.warning(response["technical_warning"])
        st.button("Retry Nemotron", on_click=retry_last_statement)
    st.caption(f"Total latency: {st.session_state.last_latency:.3f} seconds")


st.set_page_config(page_title="Interrogation Review Monitor", page_icon="🔎", layout="centered")
initialize_state()
st.title("Interrogation Review Monitor")
st.info("Potential concerns for human review — not a legal conclusion.")

examples = st.columns(3)
for column, label, name in zip(
    examples,
    ("Load compliant example", "Load benefit example", "Load threat example"),
    ("compliant", "benefit", "threat"),
    strict=True,
):
    column.button(label, on_click=load_example, args=(name,), use_container_width=True)

st.text_area(
    "Enter an interrogation statement",
    key="statement_input",
    height=220,
    placeholder="Type or paste any interrogation statement here…",
    on_change=clear_result,
)
st.button("Analyze statement", on_click=analyze_current_statement, type="primary")
render_result()
