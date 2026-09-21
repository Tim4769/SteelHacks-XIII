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
NEMOTRON_API_KEY=
NEMOTRON_API_URL=https://integrate.api.nvidia.com/v1
NEMOTRON_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
```

The original `NVIDIA_API_KEY`, `NVIDIA_BASE_URL`, and `NVIDIA_MODEL` names remain supported as
standalone aliases. Do not place any of these values in Streamlit browser code. The module loads local values at the
model-client boundary and sends the API key only in the server-side NVIDIA authorization header.

The analyzer is local-first. High-confidence configured concerns and obvious no-concern statements
return immediately with `detection_source: "local"` and do not call NVIDIA. Ambiguous statements
make one NVIDIA request with a 1-second connection timeout and 2-second read timeout. The request
disables thinking with `chat_template_kwargs.enable_thinking=false`, uses temperature `0`, limits
output to 256 tokens, and does not stream. There is no automatic retry or repair call. Model
failures return the conservative local classification with `detection_source: "local_fallback"`
and a sanitized `technical_warning`.

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

Allowed speakers are `officer`, `suspect`, `narrator`, `witness`, and `unknown`. `new_turns` must
contain at least one turn. IDs and text must be nonblank, sequence numbers and timestamps must be
nonnegative, and turn IDs must be unique across both arrays. The direct-call request size limit is
256 KB.

Only a submitted `speaker: "officer"` turn can be primary evidence for an officer-conduct
concern. Suspect, narrator, witness, and unknown turns provide context only. Evidence quote,
offsets, turn ID, speaker, and timestamp are all reconstructed from the same submitted officer
turn; model-provided speaker metadata is neither requested nor trusted. A non-officer evidence
candidate invalidates that model result and cannot produce a card or audio alert.

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
  "error": null,
  "detection_source": "hybrid",
  "technical_warning": null
}
```

Supported categories are `benefit_conditioned_on_confession`,
`threat_conditioned_on_confession`, `third_party_threat_conditioned_on_confession`,
`deprivation_conditioned_on_confession`, `evidence_claim_used_as_pressure`,
`minimization_used_to_elicit_admission`, and `questioning_after_counsel_request`. The detection
source is `local`, `nemotron`, `hybrid`, or `local_fallback`. Invalid input and request conflicts
still return `status: "error"`.

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
6. Preserve the actual speaker on every transcript turn. Do not infer or rewrite roles while
   constructing `context_turns` or `new_turns`.

An explicit suspect request for a lawyer activates process-local counsel-request context for that
session. Later substantive officer questioning is flagged with
`questioning_after_counsel_request`; the suspect request remains context and is never used as
primary evidence. An officer statement that clearly stops questioning or arranges counsel clears
the state. A documented suspect-initiated later interaction is treated conservatively under this
prototype policy and does not produce a definitive waiver or admissibility conclusion. Person 3
should continue sending sufficient context because process-local state does not survive a restart.
Call `DialogueAnalyzer.reset_session(session_id)` when explicitly resetting a session, or use a new
session ID with the module-level analyzer.

If an officer-labeled new turn strongly resembles a first-person request for counsel, the analyzer
returns the sanitized warning `SPEAKER_ATTRIBUTION_SUSPECTED`. It does not guess a replacement role;
Person 3 must correct the source transcript label.

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
    st.error(result.error.message if result.error else "The request was invalid.")

if result.technical_warning:
    st.warning(result.technical_warning)
    if st.button("Retry Nemotron"):
        st.rerun()
```

Before calling `analyze_dialogue`, reduce each turn to the contract fields `turn_id`, `speaker`,
`text`, and `timestamp_ms`; do not pass Streamlit-only display fields. A user-requested Nemotron
retry must use a new request ID with the same dialogue so it is not served from the idempotency
cache. A changed body also requires a new request ID.

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

- The response and turn-conflict caches are bounded and process-local. Response keys include the
  normalized text, speaker, taxonomy version, classifier version, and corpus version. Technical
  fallbacks are not cached.
- This version has no HTTP wrapper and no cross-process request-size enforcement.
- Counsel-request state is a conservative prototype policy. It does not decide waiver,
  reinitiation, admissibility, or any legal conclusion.
- Local matching is intentionally conservative and can miss nuanced tactics that Nemotron would
  detect. Always display the detection source and any technical warning.
- Speaker labels and finalized transcript accuracy remain Person 3 and Person 1 responsibilities.
- Model classification remains probabilistic even though schema and evidence checks are deterministic.
- The hosted NVIDIA endpoint may still be unavailable, but an ambiguous remote request has one
  bounded attempt and falls back locally after its 1-second connect/2-second read limits.
