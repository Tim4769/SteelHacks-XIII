"""ElevenLabs STT/TTS used by the Streamlit auditor (Person 1 pipeline).

Reads AUDIO_PROVIDER_MODE from the repo .env. mock mode needs no credentials.
elevenlabs mode calls Scribe v2 and TTS with ELEVENLABS_API_KEY.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / ".env", override=False)

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"


def _audio_mode() -> str:
    return os.getenv("AUDIO_PROVIDER_MODE", "elevenlabs").strip().lower()


def stt_enabled() -> bool:
    return _audio_mode() == "elevenlabs" and bool(os.getenv("ELEVENLABS_API_KEY"))


def tts_enabled() -> bool:
    return stt_enabled() and bool(os.getenv("ELEVENLABS_VOICE_ID"))


def _guess_content_type(file_name: str, content_type: str | None) -> str:
    if content_type:
        return content_type.split(";", 1)[0].lower()
    name = (file_name or "").lower()
    if name.endswith(".wav"):
        return "audio/wav"
    if name.endswith(".mp3"):
        return "audio/mpeg"
    if name.endswith(".m4a") or name.endswith(".mp4"):
        return "audio/mp4"
    if name.endswith(".ogg"):
        return "audio/ogg"
    return "audio/webm"


def transcribe_audio(
    file_name: str,
    audio_bytes: bytes,
    speaker: str = "officer",
    content_type: str | None = None,
) -> list[dict[str, Any]]:
    """Return finalized turns. Speaker is assigned by the interface (two-device tagging)."""
    if not audio_bytes:
        return []
    role = speaker if speaker in {"officer", "suspect", "unknown"} else "unknown"

    if not stt_enabled():
        mock = os.getenv(
            "MOCK_STT_TEXT",
            "If you confess, I can make sure you go home tonight.",
        ).strip()
        if role == "suspect":
            mock = os.getenv("MOCK_STT_SUSPECT_TEXT", "I was at home.").strip()
        return [{"speaker": role, "text": mock}]

    mime = _guess_content_type(file_name, content_type)
    headers = {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}
    files = {"file": (file_name or "recording.webm", audio_bytes, mime)}
    form = {
        "model_id": os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2"),
        "tag_audio_events": "false",
    }
    try:
        response = httpx.post(STT_URL, headers=headers, files=files, data=form, timeout=60)
    except httpx.TimeoutException as exc:
        raise RuntimeError("Transcription timed out.") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError("Transcription is unavailable.") from exc

    if response.status_code >= 400:
        raise RuntimeError("The audio could not be transcribed.")

    payload = response.json()
    transcript = str(payload.get("text", "")).strip()
    if not transcript:
        raise RuntimeError("No speech was detected.")
    return [{"speaker": role, "text": transcript}]


def synthesize_alert(alert_text: str) -> bytes | None:
    if not alert_text or not tts_enabled():
        return None
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    try:
        response = httpx.post(
            url,
            headers={
                "xi-api-key": os.environ["ELEVENLABS_API_KEY"],
                "Content-Type": "application/json",
            },
            params={"output_format": "mp3_44100_128"},
            json={
                "text": alert_text,
                "model_id": os.getenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5"),
            },
            timeout=60,
        )
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    return response.content

