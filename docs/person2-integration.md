# Person 2 reasoning integration

The Person 2 module accepts a self-contained batch of finalized dialogue turns, asks NVIDIA
Nemotron for a narrow classification, validates every evidence reference against the submitted
text, and returns an `AnalyzeResponse` for Person 3. Results identify potential concerns for human
review and are not legal conclusions.

## Setup

Use Python 3.11 or newer. From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
# Contributors running checks also install: python -m pip install -e '.[dev]'
```

The repository-root `.env` is local and ignored by Git. It must define these names:

```dotenv
NVIDIA_API_KEY=
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_MODEL=
```

Do not place any of these values in Streamlit browser code. The module loads local values at the
model-client boundary and sends the API key only in the server-side NVIDIA authorization header.

The NVIDIA request disables thinking with
`chat_template_kwargs.enable_thinking=false`, uses temperature `0`, limits output to 256 tokens,
and does not stream. To contain intermittent hosted-endpoint stalls, one logical model call may make
one initial attempt with a 12-second read timeout and at most one retry with a 15-second read
timeout. A successful response is never retried, and authentication, invalid-request,
configuration, and other non-transient failures are not retried. If both transient attempts time
out, analysis returns `MODEL_TIMEOUT`; it never returns a no-concern result for a technical failure.

## Entry point

There is no server start command in this vertical slice. Person 3 imports the module directly:

```python
from steelhacks_reasoning import analyze_dialogue

result = analyze_dialogue(payload)
result_dict = result.model_dump(mode="json")
```

The core entry point is:

```python
analyze_dialogue(payload: AnalyzeRequest | Mapping[str, Any]) -> AnalyzeResponse
```

For deterministic tests or explicit dependency injection, construct
`DialogueAnalyzer(model_client)` and call its `analyze_dialogue` method. The core package does not
import Streamlit and can later be wrapped by `POST /api/analyze` without rewriting its logic.

## Request example

```json
{
  "session_id": "demo-001",
  "request_id": "req-003",
  "sequence_number": 3,
  "context_turns": [
    {
      "turn_id": "t1",
      "speaker": "officer",
      "text": "Where were you last night?",
      "timestamp_ms": 0
    }
  ],
  "new_turns": [
    {
      "turn_id": "t2",
      "speaker": "officer",
      "text": "If you confess, I can make sure you go home tonight.",
      "timestamp_ms": 5000
    }
  ]
}
```

Allowed speakers are `officer`, `suspect`, and `unknown`. `new_turns` must contain at least one
turn. IDs and text must be nonblank, sequence numbers and timestamps must be nonnegative, and turn
IDs must be unique across both arrays. The direct-call request size limit is 256 KB.

## Response example

```json
{
  "session_id": "demo-001",
  "request_id": "req-003",
  "sequence_number": 3,
  "analyzed_turn_ids": ["t2"],
  "status": "concern_detected",
  "concerns": [
    {
      "concern_id": "concern_deterministic_hash",
      "category": "benefit_conditioned_on_confession",
      "evidence": [
        {
          "turn_id": "t2",
          "speaker": "officer",
          "start_char": 0,
          "end_char": 52,
          "quote": "If you confess, I can make sure you go home tonight.",
          "timestamp_ms": 5000
        }
      ],
      "explanation": "The statement may condition a release-related benefit on confessing.",
      "alert_text": "Potential inducement detected. Review the promise of release."
    }
  ],
  "error": null
}
```

The module supports only `benefit_conditioned_on_confession` and
`threat_conditioned_on_confession`. Technical failures return `status: "error"`; they are never
reported as `no_concern_detected`. Stable error codes are `INVALID_INPUT`, `INPUT_CONFLICT`,
`MODEL_TIMEOUT`, `MODEL_OUTPUT_INVALID`, `UPSTREAM_UNAVAILABLE`, and `CONFIGURATION_ERROR`.

## Person 3 adapter

`steelhacks_reasoning.streamlit_adapter` demonstrates the handoff without taking ownership of UI
state. Person 3 should:

1. Build a request with the active session, a unique request ID, the next sequence number, recent
   context, and newly finalized turns.
2. Call `analyze_dialogue` from server-side Streamlit code.
3. Ignore a response whose session does not match or whose sequence number is older than the most
   recently accepted response.
4. Deduplicate warning cards and spoken alerts using `concern_id`.
5. Highlight the backend-derived evidence offsets, display the qualified explanation, and send only
   previously unplayed `alert_text` values to Person 1.

Person 3’s current branch uses `benefit_for_confession` and `threat_for_confession`. Those names
must be migrated to `benefit_conditioned_on_confession` and
`threat_conditioned_on_confession`. `LEGACY_CATEGORY_MAP` exists only as a temporary display-data
migration aid; Person 2 never emits the old names.

## Minimal Streamlit integration

The following is the complete server-side control flow Person 3 needs. The surrounding transcript
and session state remain owned by Person 3.

```python
import uuid

import streamlit as st

from steelhacks_reasoning import analyze_dialogue
from steelhacks_reasoning.streamlit_adapter import accept_analysis_response


session_id = st.session_state.live_session["session_id"]
sequence_number = st.session_state.get("analysis_sequence", 0) + 1
request_id = f"req-{uuid.uuid4().hex}"

payload = {
    "session_id": session_id,
    "request_id": request_id,
    "sequence_number": sequence_number,
    "context_turns": st.session_state.live_session["turns"][-10:],
    "new_turns": newly_finalized_turns,
}

st.session_state.analysis_sequence = sequence_number
with st.spinner("Analyzing finalized dialogue..."):
    result = analyze_dialogue(payload)

decision = accept_analysis_response(
    result,
    active_session_id=st.session_state.live_session["session_id"],
    latest_sequence_number=st.session_state.get("accepted_analysis_sequence", -1),
    displayed_concern_ids=st.session_state.setdefault("displayed_concern_ids", set()),
    played_concern_ids=st.session_state.setdefault("played_concern_ids", set()),
)

if not decision.accepted:
    # Old session or older sequence: discard without changing UI or audio state.
    pass
elif result.status == "concern_detected":
    st.session_state.accepted_analysis_sequence = decision.latest_sequence_number
    for concern in decision.new_concerns:
        st.warning(f"{concern.alert_text}\n\n{concern.explanation}")
        st.session_state.displayed_concern_ids.add(concern.concern_id)
    for concern in decision.new_concerns:
        if concern.concern_id not in st.session_state.played_concern_ids:
            send_alert_text_to_person_1(concern.alert_text)
            st.session_state.played_concern_ids.add(concern.concern_id)
elif result.status == "no_concern_detected":
    st.session_state.accepted_analysis_sequence = decision.latest_sequence_number
    st.caption("No potential concern detected in the finalized turn.")
elif result.status == "insufficient_context":
    st.session_state.accepted_analysis_sequence = decision.latest_sequence_number
    st.info("More finalized dialogue is needed before analysis can complete.")
elif result.status == "error":
    # Never send alert audio after a technical error.
    st.error(result.error.message if result.error else "Analysis failed.")
    if result.error and result.error.code in {"MODEL_TIMEOUT", "UPSTREAM_UNAVAILABLE"}:
        if st.button("Retry analysis"):
            st.rerun()  # Reuse the same payload/request_id for an identical retry.
```

Before calling `analyze_dialogue`, reduce each turn to the contract fields `turn_id`, `speaker`,
`text`, and `timestamp_ms`; do not pass Streamlit-only display fields. Preserve the same request ID
and body for an identical retry. A changed body requires a new request ID.

## Validation commands

```bash
ruff format --check .
ruff check .
mypy steelhacks_reasoning
pytest
python scripts/run_real_evaluation.py
```

The real-model script uses only held-out synthetic dialogue. It reports per-case categories, JSON
and evidence validity, false positives, false negatives, and latency. Its small sample is not legal
validation.

## Known limitations

- The retry and turn-conflict cache is bounded and process-local. It does not survive restarts and is
  not shared between application instances; analysis correctness does not depend on it.
- This version has no HTTP wrapper and no cross-process request-size enforcement.
- Only the benefit and threat categories are supported. Counsel-request analysis is deferred.
- Speaker labels and finalized transcript accuracy remain Person 3 and Person 1 responsibilities.
- Model classification remains probabilistic even though schema and evidence checks are deterministic.
- The hosted NVIDIA trial endpoint has shown intermittent stalls even with thinking disabled. The
  bounded retry strategy caps a logical model call near 28 seconds, but cannot guarantee demo latency.
