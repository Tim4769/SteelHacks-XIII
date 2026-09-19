"""ElevenLabs speech-to-text and text-to-speech stubs (Person 1).

Live audio and uploaded files should become finalized transcript turns,
then this UI sends those turns to Nemotron. Spoken alerts use alert_text
from Person 2. Replace the stub functions when ElevenLabs keys are available.
"""

from __future__ import annotations

from typing import Any

ELEVENLABS_STT_ENABLED = False
ELEVENLABS_TTS_ENABLED = False


def transcribe_audio(file_name: str, audio_bytes: bytes) -> list[dict[str, Any]]:
    """Turn recorded or uploaded audio into finalized dialogue turns.

    Later: send audio_bytes to ElevenLabs Speech-to-Text and map speakers.
    """
    _ = audio_bytes
    return [
        {
            "speaker": "officer",
            "text": f"[ElevenLabs STT pending] Processed demo audio from {file_name}.",
        },
        {
            "speaker": "officer",
            "text": "If you confess, I can make sure you go home tonight.",
        },
    ]


def synthesize_alert(alert_text: str) -> bytes | None:
    """Return playable audio for a Nemotron alert_text.

    Later: call ElevenLabs Text-to-Speech and return mp3/wav bytes for st.audio.
    """
    _ = alert_text
    if not ELEVENLABS_TTS_ENABLED:
        return None
    return None
