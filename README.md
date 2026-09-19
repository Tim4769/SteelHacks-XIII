# SteelHacks XIII - interrogation-risk voice prototype

This repository contains a 24-hour hackathon MVP that connects browser audio,
ElevenLabs speech-to-text and text-to-speech, an interrogation-risk analysis
contract, and a reference interface.

The prototype does **not** make legal conclusions. Its outputs require human
review.

## What works now

- One-speaker continuous browser capture with manual officer/suspect role.
- Automatic turn finalization after about 1.3 seconds of silence.
- Echo cancellation plus capture suppression while spoken alerts play.
- Runtime MIME selection with `MediaRecorder.isTypeSupported()`.
- Backend-only ElevenLabs Scribe v2 and Flash v2.5 adapters.
- Session-based Nemotron request and structured concern response contracts.
- Exact-evidence, session, speaker, and concern-ID validation.
- Mock STT, mock risk analysis, and mock audio chime for a no-key demo.
- Warning audio only for new `concern_id` values.
- Distinct no-concern, insufficient-context, analysis-failure, and TTS-failure states.
- Reset protection, late-response ignoring, idempotent turn retries, and text fallback.

## Quick start in mock mode

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload --env-file .env
```

Open <http://127.0.0.1:8000>. Localhost is a secure browser context for
microphone access.

Click **Start live session** once, speak naturally, and pause when a turn is
complete. The browser automatically closes the segment, transcribes and
analyzes it, then continues listening. **Finalize turn now** is available for
noisy rooms. **Stop live session** ends continuous capture.

The VAD thresholds are intentionally defined at the top of
`app/static/app.js` so the team can tune the silence delay and microphone
sensitivity during venue testing. While a warning is synthesized and played,
the current recording segment is discarded and capture resumes after a short
echo-recovery delay.

Mock recording returns the configured `MOCK_STT_TEXT`. The typed fallback is
the fastest way to test negative and alternate examples.

Run tests:

```bash
pytest -q
```

## Enable ElevenLabs

Copy `.env.example` to `.env` and set:

```dotenv
AUDIO_PROVIDER_MODE=elevenlabs
ELEVENLABS_API_KEY=your_key_here
ELEVENLABS_VOICE_ID=your_voice_id_here
```

Then start with `uvicorn app.main:app --reload --env-file .env`. The browser
never receives either value. `voice=default` is an application alias mapped to
the backend voice ID.

## Connect the Nemotron representative's service

### Direct NVIDIA API

For the hosted NVIDIA Nemotron API, add these values to `.env`:

```dotenv
ANALYSIS_PROVIDER_MODE=nvidia
NEMOTRON_API_URL=https://integrate.api.nvidia.com/v1
NEMOTRON_API_KEY=your_private_nvidia_key
NEMOTRON_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
```

The backend converts the session/turn contract into a chat-completions request,
requires structured concern JSON, and then applies the same exact-evidence and
session validation used for a team-hosted service.

### Team-hosted contract adapter

The remote service must implement the request and response in
[`docs/integration-contract.md`](docs/integration-contract.md). Add these values
to `.env`:

```dotenv
ANALYSIS_PROVIDER_MODE=remote
NEMOTRON_API_URL=https://their-service.example/api/analyze
NEMOTRON_API_KEY=optional_key_if_required
```

The backend rejects non-verbatim evidence, unknown turn references, mismatched
speakers, duplicate concern IDs, or a mismatched session ID.

## Inputs still needed from the team

1. **ElevenLabs owner/account:** development API key and approved voice ID.
2. **Nemotron representative:** reachable endpoint, authentication method,
   final category taxonomy, and confirmation of the exact response schema.
3. **Interface representative:** confirmation that the reference state machine,
   manual role selector, and reset semantics will be carried into the final UI.
4. **Product/legal reviewer:** approved warning copy, human-review language, and
   rules for storing or deleting recordings and transcripts.

## Demo script

1. Start in mock mode and select **Officer**.
2. Record one short turn. The mock transcript is:
   `If you confess, I can make sure you go home tonight.`
3. Confirm the exact quote, category, explanation, and one spoken/chime alert.
4. Submit the same session again and confirm the concern does not replay.
5. Reset the session and confirm old results do not reappear.
6. Use typed fallback with `Where were you yesterday afternoon?` and confirm
   **No concern detected** with no audio.
7. Force an analysis endpoint failure in remote mode and confirm the interface
   shows **Analysis unavailable**, not a negative result.
