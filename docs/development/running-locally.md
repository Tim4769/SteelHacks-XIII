# Run Themis locally

The main demo is the FastAPI application in `app/`. Use Python 3.11 or newer and run the following commands from the repository root.

## No-key demo

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
python -m uvicorn app.main:app --reload --env-file .env
```

Open [localhost:8000](http://127.0.0.1:8000). The example environment defaults to mock audio and analysis providers. Mock recording returns a fixed sample transcript, and the audio response is a chime.

To try the interface without microphones:

1. Open **Live Interrogation Monitor** and expand **Typed transcript fallback**.
2. Select **Officer** and submit `Where were you yesterday afternoon?` for a no-concern example.
3. Submit `If you confess, I can make sure you go home tonight.` for an example concern with supporting evidence.
4. Use **Reset session** to clear the session before trying a new example.

For live capture, use **Find microphones** and assign separate physical microphones to the officer and suspect. **Start live session** begins capture; a pause of about 1.3 seconds finalizes a turn. The louder channel is selected for transcription. **Finalize turn now** helps in noisy rooms. **Stop live session** opens the save dialog; saved sessions are available in **Past Interrogation Archives**.

Saved transcripts and alerts use this browser's local storage. Clearing browser data can remove them; JSON and CSV exports provide a portable copy.

## ElevenLabs audio

Set these values in your local `.env`:

```dotenv
AUDIO_PROVIDER_MODE=elevenlabs
ELEVENLABS_API_KEY=your_key_here
ELEVENLABS_VOICE_ID=your_voice_id_here
```

The default models are `scribe_v2` for transcription and `eleven_flash_v2_5` for spoken alerts. Keep credentials in the ignored `.env`; the backend makes authenticated provider calls.

## NVIDIA reasoning

Set these values in the same `.env`:

```dotenv
ANALYSIS_PROVIDER_MODE=nvidia
NEMOTRON_API_URL=https://integrate.api.nvidia.com/v1
NEMOTRON_API_KEY=your_private_nvidia_key
NEMOTRON_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
```

Restart the server after configuration changes. The reasoning module uses local classifications for clear cases and calls the configured model for ambiguous dialogue. The interface exposes the detection source and technical warnings. A local fallback is not a live model result.

A separately hosted service can instead use `ANALYSIS_PROVIDER_MODE=remote` with its analyze endpoint in `NEMOTRON_API_URL`; see the [integration contract](integration-contract.md).

## Tests and additional demos

```bash
python -m pytest -q
```

The tests use synthetic inputs and mocked provider calls. They do not establish real-world legal accuracy or verify a live service account.

For the standalone reasoning demo:

```bash
python -m pip install -e '.[demo]'
streamlit run demo_app.py
```

For the earlier Streamlit interface prototype:

```bash
python -m pip install -r examples/streamlit/requirements.txt
cd examples/streamlit
streamlit run Home.py
```

That earlier interface includes simulated data and heuristic adapters. The FastAPI application is the main integrated demo.

[Back to Themis](../../README.md)
