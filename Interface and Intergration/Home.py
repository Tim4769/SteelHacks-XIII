from auditor.state import init_state
from auditor.theme import boot_page

import streamlit as st

boot_page("Custodial Oversight Auditor")
init_state()

st.title("Custodial Oversight Auditor")
st.caption("Real-time interrogation monitoring and searchable compliance archives.")

st.write(
    "Open live monitoring to stream audio and surface high-risk alerts, "
    "or open archives to search past sessions. Transcripts live in session "
    "state so they persist while you move between pages."
)

left, right = st.columns(2, gap="large")

with left:
    with st.container(border=True):
        st.subheader("Live Interrogation Monitor")
        st.write(
            "Record officer and suspect audio on two inputs, transcribe "
            "each turn, and surface high-risk rights cautions."
        )
        if st.button("Go to live monitor", type="primary", width="stretch"):
            st.switch_page("pages/2_Live_Interrogation_Monitor.py")

with right:
    with st.container(border=True):
        st.subheader("Past Interrogation Archives")
        st.write(
            "Search prior sessions by keyword, timestamp, or technique. "
            "Filter risk levels and export JSON or CSV for review."
        )
        if st.button("Go to archives", width="stretch"):
            st.switch_page("pages/3_Past_Interrogation_Archives.py")
