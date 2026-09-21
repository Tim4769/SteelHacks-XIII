# Person 2 demo runbook

This runbook prepares the Nemotron reasoning module for a synthetic SteelHacks demonstration. Never
use real sensitive interrogation data during the demo.

## Install dependencies

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Start the standalone reasoning demonstration with:

```bash
python -m pip install -e '.[demo]'
streamlit run demo_app.py
```

If Streamlit needs restarting, stop it with `Ctrl-C`, then run the same command again. Streamlit
session state resets when the process restarts, so start a new demo session.

## Verify configuration safely

This prints only `set` or `missing`, never values:

```bash
python - <<'PY'
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path.cwd() / ".env", override=True)
for name in ("NVIDIA_API_KEY", "NVIDIA_BASE_URL", "NVIDIA_MODEL"):
    print(f"{name}: {'set' if os.getenv(name) else 'missing'}")
PY
```

Confirm that the local file is ignored:

```bash
git check-ignore -q .env && echo ".env is ignored"
```

Never display the API key, `.env` contents, authorization headers, or NVIDIA model-page sample code
during the demonstration or in screenshots, logs, terminals, or recordings.

## Warm up NVIDIA before presenting

Run one synthetic compliant request shortly before the presentation:

```bash
python scripts/run_real_evaluation.py --cases normal_question
```

Proceed when it returns a schema-valid `no_concern_detected` result. A warm-up result is not a
guarantee that the hosted endpoint will remain available.

## Demonstration transcripts

Use only these synthetic lines.

### Compliant exchange

```text
Officer: Where were you last night?
Suspect: I was at home.
```

Expected: `no_concern_detected`, no warning card, and no alert audio.

### Benefit condition

```text
Officer: If you confess, I can make sure you go home tonight.
```

Expected: `concern_detected` with `benefit_conditioned_on_confession`, validated transcript
evidence, one warning card, and one new `alert_text` handed to Person 1.

### Threat condition

```text
Officer: Confess now or I will make sure the charges get worse.
```

Expected: `concern_detected` with `threat_conditioned_on_confession`, validated transcript evidence,
one warning card, and one new `alert_text` handed to Person 1.

## Handling hosted-endpoint failures

If analysis reports a timeout through `technical_warning`:

1. Keep the transcript and deterministic classification visible.
2. Show `detection_source: local_fallback` and the sanitized warning.
3. Offer a user-initiated retry with a new request ID and the same dialogue.

Do not label local fallback output as Nemotron output. Upstream, configuration, and invalid-model
output failures use the same conservative fallback for valid input. Invalid request data and
request-ID conflicts remain errors and must be corrected.
