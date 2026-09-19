from auditor.state import export_session, format_timestamp, init_state
from auditor.theme import boot_page

import streamlit as st

boot_page("Interrogation Archives")
init_state()

st.title("Interrogation Archives & Compliance Logs")
st.caption("Search transcripts by keyword, timestamp, or technique type.")

query = st.text_input(
    "Search",
    placeholder='Try "leading questions", "coercion", "00:00:10", or "confess"',
)

archives = st.session_state.archives
labels = [item["label"] for item in archives]
selected_label = st.selectbox("Archived session", labels)
session = next(item for item in archives if item["label"] == selected_label)

f1, f2, f3 = st.columns(3)
show_all = f1.checkbox("Show All Lines", value=True)
show_high = f2.checkbox("Show High-Risk Only")
show_medium = f3.checkbox("Show Medium-Risk Only")

if show_high or show_medium:
    show_all = False

needle = (query or "").strip().lower()


def matches(turn: dict) -> bool:
    risk = turn.get("risk_level") or "none"
    if not show_all:
        if show_high and show_medium:
            if risk not in {"high", "medium"}:
                return False
        elif show_high and risk != "high":
            return False
        elif show_medium and risk != "medium":
            return False
        elif not show_high and not show_medium:
            pass

    if not needle:
        return True
    haystack = " ".join(
        [
            format_timestamp(turn.get("timestamp_ms")),
            str(turn.get("timestamp_ms") or ""),
            turn.get("speaker") or "",
            turn.get("text") or "",
            turn.get("violation_type") or "",
            turn.get("category") or "",
            turn.get("reasoning") or "",
            turn.get("risk_level") or "",
        ]
    ).lower()
    aliases = {
        "leading questions": "leading_question",
        "coercion": "threat_for_confession",
        "coercive": "threat_for_confession",
        "false promise": "benefit_for_confession",
        "leniency": "benefit_for_confession",
    }
    extra = aliases.get(needle, needle)
    return needle in haystack or extra in haystack


visible = [turn for turn in session["turns"] if matches(turn)]

st.subheader("Interactive timeline")
if not visible:
    st.info("No lines match the current search and filters.")
else:
    for turn in visible:
        css_class = "high" if turn.get("is_high_risk") else ("medium" if turn.get("risk_level") == "medium" else "")
        reasoning = turn.get("reasoning") or "No concern returned for this turn."
        technique = turn.get("violation_type") or (turn.get("risk_level") or "none").upper()
        st.markdown(
            f"""
            <div class="transcript-line {css_class}">
              <div class="meta">{format_timestamp(turn.get("timestamp_ms"))}
                · <span class="speaker">{turn.get("speaker")}</span>
                · {technique}
                · {(turn.get("risk_level") or "none").upper()}</div>
              <div>{turn.get("text")}</div>
              <div class="meta" style="margin-top:8px;">Nemotron: {reasoning}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.subheader("Export data")
st.caption("Downloads the full selected session log, including alerts and Nemotron reasoning.")
c1, c2 = st.columns(2)
c1.download_button(
    "Download JSON",
    data=export_session(session, "json"),
    file_name=f"{session['session_id']}.json",
    mime="application/json",
    width="stretch",
)
c2.download_button(
    "Download CSV",
    data=export_session(session, "csv"),
    file_name=f"{session['session_id']}.csv",
    mime="text/csv",
    width="stretch",
)
