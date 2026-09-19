from auditor.demo_data import live_demo_script
from auditor.state import (
    format_clock,
    format_timestamp,
    high_risk_count,
    ingest_audio,
    ingest_text_turn,
    init_state,
    reset_live_session,
    session_duration_seconds,
    start_stream,
    stop_stream,
)
from auditor.theme import boot_page

import streamlit as st

boot_page("Live Interrogation Monitor")
init_state()

session = st.session_state.live_session

st.title("Live Interrogation Monitor")
st.caption("Real-time custodial oversight and coercive technique detection.")

with st.sidebar:
    st.header("Control panel")
    if session["is_streaming"]:
        if st.button("Stop Stream", type="primary", width="stretch"):
            stop_stream()
            st.rerun()
        st.success("Live audio stream is active.")
    else:
        if st.button("Start Live Audio Stream", type="primary", width="stretch"):
            start_stream()
            st.rerun()
        st.caption("ElevenLabs STT will attach to this toggle.")

    st.divider()
    if st.button(
        "Play next demo turn",
        disabled=not session["is_streaming"],
            width="stretch",
    ):
        script = live_demo_script()
        if st.session_state.demo_cursor >= len(script):
            st.session_state.demo_cursor = 0
        piece = script[st.session_state.demo_cursor]
        st.session_state.demo_cursor += 1
        ingest_text_turn(piece["speaker"], piece["text"])
        st.rerun()
    st.caption("Stand-in for finalized ElevenLabs speech-to-text turns.")

    st.divider()
    uploaded = st.file_uploader(
        "Upload sample audio",
        type=["wav", "mp3"],
        help="Demonstration upload. Later this file is sent to ElevenLabs STT.",
    )
    if uploaded is not None and st.button("Transcribe uploaded audio", width="stretch"):
        ingest_audio(uploaded.name, uploaded.getvalue())
        st.rerun()

    st.divider()
    speaker = st.selectbox("Speaker tag for manual turn", ["officer", "suspect", "unknown"])
    manual = st.text_area("Paste a finalized transcript turn", height=90)
    if st.button("Submit turn", width="stretch"):
        if not session["is_streaming"]:
            start_stream()
        if manual.strip():
            ingest_text_turn(speaker, manual)
            st.rerun()

    st.divider()
    if st.button("Reset live session", width="stretch"):
        reset_live_session()
        st.rerun()


m1, m2 = st.columns(2)
m1.metric("Active Session Duration", format_clock(session_duration_seconds(session)))
m2.metric("High-Risk Alerts Triggered", high_risk_count(session))

left, right = st.columns([0.6, 0.4], gap="large")

with left:
    with st.container():
        st.subheader("Live timestamped transcript")
        feed = st.container(height=560)
        if not session["turns"]:
            feed.info("Start the stream, play demo turns, or upload audio.")
        else:
            for turn in reversed(session["turns"]):
                risk = turn.get("risk_level") or "none"
                css_class = "high" if turn.get("is_high_risk") else ("medium" if risk == "medium" else "")
                tag = "HIGH-RISK" if turn.get("is_high_risk") else risk.upper()
                feed.markdown(
                    f"""
                    <div class="transcript-line {css_class}">
                      <div class="meta">{format_timestamp(turn.get("timestamp_ms"))}
                        · <span class="speaker">{turn.get("speaker")}</span>
                        · {tag}</div>
                      <div>{turn.get("text")}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

with right:
    with st.container():
        st.subheader("Active alert & rights caution")
        alert = session.get("active_alert")
        if not alert or alert.get("status") not in {"high", "error"}:
            st.markdown(
                """
                <div class="alert-normal">
                  <strong>Status: Monitoring (Normal)</strong>
                  <p>No high-risk coercive technique is currently flagged.
                  Nemotron analysis runs on each finalized turn.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        elif alert.get("status") == "error":
            st.error(alert.get("reasoning") or "Analysis error")
        else:
            st.markdown(
                f"""
                <div class="alert-high">
                  <div>VIOLATION TYPE: <strong>{alert.get("violation_type")}</strong></div>
                  <p><strong>Reasoning:</strong> {alert.get("reasoning")}</p>
                  <div class="caution">AUTOMATED CAUTION TRIGGERED: You have the right to remain silent.</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        spoken = st.session_state.last_spoken_alert
        if spoken:
            st.caption("Audio safeguard prompt (ElevenLabs TTS stub)")
            st.write(spoken["alert_text"])
            if spoken.get("audio_bytes"):
                st.audio(spoken["audio_bytes"])
            else:
                st.info("Spoken alert queued. Connect ElevenLabs TTS to play this line aloud.")
