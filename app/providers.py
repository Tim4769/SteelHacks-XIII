from __future__ import annotations

import io
import json
import math
import struct
import wave
from dataclasses import dataclass

import httpx

from .config import Settings
from .contracts import (
    AnalysisRequest,
    AnalysisResponse,
    AnalysisStatus,
    Concern,
    Evidence,
    Speaker,
    TranscriptResponse,
)


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class AudioResult:
    data: bytes
    content_type: str


async def transcribe_audio(
    *,
    settings: Settings,
    audio: bytes,
    filename: str,
    content_type: str,
    turn_id: str,
    duration_ms: int | None,
) -> TranscriptResponse:
    if settings.audio_provider_mode == "mock":
        if not settings.mock_stt_text:
            raise ProviderError("AUDIO_EMPTY", "No speech was detected.")
        return TranscriptResponse(
            turn_id=turn_id,
            transcript=settings.mock_stt_text,
            language_code="en",
            duration_ms=duration_ms,
        )

    if not settings.elevenlabs_api_key:
        raise ProviderError(
            "STT_CONFIG_MISSING",
            "ElevenLabs transcription is not configured.",
        )

    headers = {"xi-api-key": settings.elevenlabs_api_key}
    files = {"file": (filename, audio, content_type)}
    form = {
        "model_id": settings.elevenlabs_stt_model,
        "tag_audio_events": "false",
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.elevenlabs.io/v1/speech-to-text",
                headers=headers,
                files=files,
                data=form,
            )
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "STT_TIMEOUT", "Transcription timed out.", retryable=True
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(
            "STT_UNAVAILABLE", "Transcription is unavailable.", retryable=True
        ) from exc

    if response.status_code == 429:
        raise ProviderError(
            "STT_RATE_LIMITED", "Transcription capacity is busy.", retryable=True
        )
    if response.status_code in {401, 403}:
        raise ProviderError(
            "STT_AUTH_FAILED", "Transcription credentials were rejected."
        )
    if response.status_code >= 500:
        raise ProviderError(
            "STT_UNAVAILABLE", "Transcription is unavailable.", retryable=True
        )
    if response.status_code >= 400:
        raise ProviderError("STT_REJECTED", "The audio could not be transcribed.")

    payload = response.json()
    transcript = str(payload.get("text", "")).strip()
    if not transcript:
        raise ProviderError("AUDIO_EMPTY", "No speech was detected.")

    if duration_ms is None:
        word_ends = [
            float(word["end"])
            for word in payload.get("words", [])
            if isinstance(word, dict) and isinstance(word.get("end"), (int, float))
        ]
        duration_ms = round(max(word_ends) * 1000) if word_ends else None

    return TranscriptResponse(
        turn_id=turn_id,
        transcript=transcript,
        language_code=payload.get("language_code"),
        duration_ms=duration_ms,
    )


def _mock_concern_id(session_id: str, turn_id: str, category: str) -> str:
    return f"{session_id}-{turn_id}-{category}"


def mock_analysis(request: AnalysisRequest) -> AnalysisResponse:
    concerns: list[Concern] = []
    for turn in request.turns:
        lowered = turn.text.lower()
        category = None
        explanation = None
        alert_text = None

        if turn.speaker == Speaker.officer and "confess" in lowered:
            if any(phrase in lowered for phrase in ("go home", "release", "let you go")):
                category = "benefit_for_confession"
                explanation = (
                    "The officer statement appears to connect a confession with a promised benefit."
                )
                alert_text = (
                    "Potential inducement detected. Review the promise connected to a confession."
                )
            elif any(phrase in lowered for phrase in ("or else", "worse", "hurt", "threat")):
                category = "threat_for_confession"
                explanation = (
                    "The officer statement appears to connect a confession with a threatened consequence."
                )
                alert_text = (
                    "Potential threat linked to a confession detected. Review the highlighted statement."
                )

        if category:
            concerns.append(
                Concern(
                    concern_id=_mock_concern_id(
                        request.session_id, turn.turn_id, category
                    ),
                    category=category,
                    explanation=explanation,
                    alert_text=alert_text,
                    evidence=[
                        Evidence(
                            quote=turn.text,
                            turn_id=turn.turn_id,
                            speaker=turn.speaker,
                            timestamp_ms=turn.timestamp_ms,
                        )
                    ],
                )
            )

    return AnalysisResponse(
        session_id=request.session_id,
        status=AnalysisStatus.ok,
        concerns=concerns,
    )


async def analyze_turns(
    *, settings: Settings, request: AnalysisRequest
) -> AnalysisResponse:
    if settings.analysis_provider_mode == "mock":
        return mock_analysis(request)

    if not settings.nemotron_api_url:
        raise ProviderError(
            "ANALYSIS_CONFIG_MISSING",
            "The Nemotron analysis endpoint is not configured.",
        )

    headers = {"Content-Type": "application/json"}
    if settings.nemotron_api_key:
        headers["Authorization"] = f"Bearer {settings.nemotron_api_key}"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                settings.nemotron_api_url,
                headers=headers,
                content=request.model_dump_json(),
            )
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "ANALYSIS_TIMEOUT", "Analysis timed out.", retryable=True
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(
            "ANALYSIS_UNAVAILABLE", "Analysis is unavailable.", retryable=True
        ) from exc

    if response.status_code == 429:
        raise ProviderError(
            "ANALYSIS_RATE_LIMITED", "Analysis capacity is busy.", retryable=True
        )
    if response.status_code >= 500:
        raise ProviderError(
            "ANALYSIS_UNAVAILABLE", "Analysis is unavailable.", retryable=True
        )
    if response.status_code >= 400:
        raise ProviderError("ANALYSIS_REJECTED", "Analysis rejected the request.")
    try:
        return AnalysisResponse.model_validate(response.json())
    except (ValueError, json.JSONDecodeError) as exc:
        raise ProviderError(
            "ANALYSIS_INVALID",
            "Analysis returned an invalid response.",
            retryable=True,
        ) from exc


def _mock_chime() -> bytes:
    sample_rate = 16_000
    duration_seconds = 0.55
    frames = bytearray()
    for index in range(int(sample_rate * duration_seconds)):
        t = index / sample_rate
        envelope = max(0.0, 1.0 - t / duration_seconds)
        sample = int(9_000 * envelope * math.sin(2 * math.pi * 660 * t))
        frames.extend(struct.pack("<h", sample))

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(bytes(frames))
    return buffer.getvalue()


async def synthesize_speech(
    *, settings: Settings, text: str, voice_alias: str
) -> AudioResult:
    if settings.audio_provider_mode == "mock":
        return AudioResult(_mock_chime(), "audio/wav")

    if not settings.elevenlabs_api_key or not settings.elevenlabs_voice_id:
        raise ProviderError(
            "TTS_CONFIG_MISSING",
            "ElevenLabs speech synthesis is not configured.",
        )

    # The public alias keeps the provider voice ID out of the client contract.
    if voice_alias != "default":
        raise ProviderError("TTS_VOICE_UNKNOWN", "The requested voice is unavailable.")

    url = (
        "https://api.elevenlabs.io/v1/text-to-speech/"
        f"{settings.elevenlabs_voice_id}"
    )
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                url,
                headers={
                    "xi-api-key": settings.elevenlabs_api_key,
                    "Content-Type": "application/json",
                },
                params={"output_format": "mp3_44100_128"},
                json={"text": text, "model_id": settings.elevenlabs_tts_model},
            )
    except httpx.TimeoutException as exc:
        raise ProviderError("TTS_TIMEOUT", "Speech timed out.", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(
            "TTS_UNAVAILABLE", "Speech is unavailable.", retryable=True
        ) from exc

    if response.status_code == 429:
        raise ProviderError(
            "TTS_RATE_LIMITED", "Speech capacity is busy.", retryable=True
        )
    if response.status_code in {401, 403}:
        raise ProviderError("TTS_AUTH_FAILED", "Speech credentials were rejected.")
    if response.status_code >= 500:
        raise ProviderError(
            "TTS_UNAVAILABLE", "Speech is unavailable.", retryable=True
        )
    if response.status_code >= 400:
        raise ProviderError("TTS_REJECTED", "The warning could not be spoken.")
    return AudioResult(response.content, "audio/mpeg")

