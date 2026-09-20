from __future__ import annotations

import asyncio
import io
import json
import math
import struct
import wave
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from steelhacks_reasoning import analyze_dialogue as analyze_with_team_reasoning
from steelhacks_reasoning.models import AnalysisStatus as TeamAnalysisStatus

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


RIGHTS_REMINDER = (
    "Rights reminder for a United States custodial interrogation: You have the right "
    "to remain silent. Anything you say may be used against you in court. You have "
    "the right to speak with an attorney. This prototype is not legal advice."
)


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
        raise ProviderError("STT_TIMEOUT", "Transcription timed out.", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(
            "STT_UNAVAILABLE", "Transcription is unavailable.", retryable=True
        ) from exc

    if response.status_code == 429:
        raise ProviderError("STT_RATE_LIMITED", "Transcription capacity is busy.", retryable=True)
    if response.status_code in {401, 403}:
        raise ProviderError("STT_AUTH_FAILED", "Transcription credentials were rejected.")
    if response.status_code >= 500:
        raise ProviderError("STT_UNAVAILABLE", "Transcription is unavailable.", retryable=True)
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
                    "The officer statement appears to connect a confession "
                    "with a threatened consequence."
                )
                alert_text = (
                    "Potential threat linked to a confession detected. "
                    "Review the highlighted statement."
                )

        if category:
            concerns.append(
                Concern(
                    concern_id=_mock_concern_id(request.session_id, turn.turn_id, category),
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


NVIDIA_SYSTEM_PROMPT = """Classify supplied interrogation turns. Return JSON only:
{"decisions":[{"turn_id":"copied ID","category":"one allowed value"}]}

Allowed category values:
- benefit_for_confession: an officer connects confessing with release, leniency,
  going home, or another promised benefit.
- threat_for_confession: an officer connects confessing or refusing to confess with
  threatened harm, punishment, or another adverse consequence.
- insufficient_context: the text is too incomplete to classify.

Examples:
- "If you confess, I can make sure you go home tonight." => benefit_for_confession
- "Confess, or I will make this much worse for you." => threat_for_confession
- "Where were you yesterday afternoon?" => no decision; return {"decisions":[]}

Return decisions only for listed concerns or genuinely insufficient context. If there
are no listed concerns, return {"decisions":[]}. Copy every included turn_id exactly.
Do not return Markdown, explanations, evidence, commentary, or reasoning."""


def _nvidia_chat_url(base_url: str) -> str:
    """Accept either NVIDIA's /v1 base URL or its full chat endpoint."""
    parsed = urlsplit(base_url.strip())
    path = parsed.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        if path.endswith("/v1"):
            path = f"{path}/chat/completions"
        else:
            path = f"{path}/v1/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def _nvidia_payload(request: AnalysisRequest, model: str) -> dict[str, object]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": NVIDIA_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Analyze this session and return the required JSON object:\n"
                    f"{request.model_dump_json()}"
                ),
            },
        ],
        "temperature": 0,
        "top_p": 1,
        "max_tokens": 400,
        "stream": False,
        # The hosted model enables long-form reasoning by default. It is not
        # needed for this narrow JSON classifier and can exceed demo latency.
        "chat_template_kwargs": {"enable_thinking": False},
    }


def _extract_json_object(text: str) -> dict[str, object]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline >= 0:
            cleaned = cleaned[first_newline + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].rstrip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Nemotron response did not contain a JSON object")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("Nemotron response JSON must be an object")
    return value


def _parse_nvidia_response(
    payload: dict[str, object], request: AnalysisRequest
) -> AnalysisResponse:
    try:
        choices = payload["choices"]
        if not isinstance(choices, list) or not choices:
            raise ValueError("NVIDIA response did not include a choice")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ValueError("NVIDIA response choice was invalid")
        message = choice["message"]
        if not isinstance(message, dict):
            raise ValueError("NVIDIA response message was invalid")
        content = message["content"]
        if not isinstance(content, str):
            raise ValueError("NVIDIA response content was not text")
        result = _extract_json_object(content)
        decisions = result.get("decisions")
        if not isinstance(decisions, list):
            raise ValueError("Nemotron response did not include decisions")

        turns = {turn.turn_id: turn for turn in request.turns}
        seen_turn_ids: set[str] = set()
        concerns: list[Concern] = []
        insufficient_count = 0
        for decision in decisions:
            if not isinstance(decision, dict):
                raise ValueError("Nemotron decision was invalid")
            turn_id = decision.get("turn_id")
            category = decision.get("category")
            if not isinstance(turn_id, str) or turn_id not in turns:
                raise ValueError("Nemotron decision referenced an unknown turn")
            if turn_id in seen_turn_ids:
                raise ValueError("Nemotron returned duplicate turn decisions")
            seen_turn_ids.add(turn_id)
            if category not in {
                "benefit_for_confession",
                "threat_for_confession",
                "none",
                "insufficient_context",
            }:
                raise ValueError("Nemotron returned an unknown category")
            if category == "insufficient_context":
                insufficient_count += 1
                continue
            if category == "none":
                continue

            turn = turns[turn_id]
            if turn.speaker != Speaker.officer:
                raise ValueError("Nemotron flagged a non-officer turn")
            if category == "benefit_for_confession":
                explanation = (
                    "The officer statement appears to connect a confession with a promised benefit."
                )
                alert_text = (
                    "Potential inducement detected. Review the promise connected to a confession."
                )
            else:
                explanation = (
                    "The officer statement appears to connect a confession "
                    "with a threatened consequence."
                )
                alert_text = (
                    "Potential threat linked to a confession detected. "
                    "Review the highlighted statement."
                )
            concerns.append(
                Concern(
                    concern_id=_mock_concern_id(request.session_id, turn.turn_id, category),
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

        status = (
            AnalysisStatus.insufficient_context
            if insufficient_count > 0 and not concerns
            else AnalysisStatus.ok
        )
        return AnalysisResponse(
            session_id=request.session_id,
            status=status,
            concerns=concerns,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProviderError(
            "ANALYSIS_INVALID",
            "Nemotron returned an invalid structured response.",
            retryable=True,
        ) from exc


async def _analyze_with_nvidia(*, settings: Settings, request: AnalysisRequest) -> AnalysisResponse:
    if not (settings.nemotron_api_url and settings.nemotron_api_key and settings.nemotron_model):
        raise ProviderError(
            "ANALYSIS_CONFIG_MISSING",
            "NVIDIA Nemotron URL, API key, and model are required.",
        )

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                _nvidia_chat_url(settings.nemotron_api_url),
                headers={
                    "Authorization": f"Bearer {settings.nemotron_api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=_nvidia_payload(request, settings.nemotron_model),
            )
    except httpx.TimeoutException as exc:
        raise ProviderError("ANALYSIS_TIMEOUT", "Analysis timed out.", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(
            "ANALYSIS_UNAVAILABLE", "Analysis is unavailable.", retryable=True
        ) from exc

    if response.status_code == 429:
        raise ProviderError("ANALYSIS_RATE_LIMITED", "Analysis capacity is busy.", retryable=True)
    if response.status_code in {401, 403}:
        raise ProviderError("ANALYSIS_AUTH_FAILED", "NVIDIA credentials were rejected.")
    if response.status_code >= 500:
        raise ProviderError("ANALYSIS_UNAVAILABLE", "Analysis is unavailable.", retryable=True)
    if response.status_code >= 400:
        raise ProviderError("ANALYSIS_REJECTED", "NVIDIA rejected the analysis request.")
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ProviderError(
            "ANALYSIS_INVALID",
            "NVIDIA returned a non-JSON response.",
            retryable=True,
        ) from exc
    if not isinstance(payload, dict):
        raise ProviderError(
            "ANALYSIS_INVALID",
            "NVIDIA returned an invalid response.",
            retryable=True,
        )
    return _parse_nvidia_response(payload, request)


async def _analyze_with_team_module(request: AnalysisRequest) -> AnalysisResponse:
    """Adapt the Person 2 reasoning contract to the existing browser API."""
    newest = request.turns[-1]
    payload = {
        "session_id": request.session_id,
        "request_id": f"analysis-{len(request.turns)}-{newest.turn_id}",
        "sequence_number": len(request.turns),
        "context_turns": [turn.model_dump(mode="json") for turn in request.turns[:-1]],
        "new_turns": [newest.model_dump(mode="json")],
    }
    result = await asyncio.to_thread(analyze_with_team_reasoning, payload)
    if result.status == TeamAnalysisStatus.ERROR:
        detail = result.error
        raise ProviderError(
            detail.code.value if detail else "ANALYSIS_INVALID",
            detail.message if detail else "The reasoning module rejected the request.",
            retryable=detail.retryable if detail else False,
        )

    concerns = [
        Concern(
            concern_id=concern.concern_id,
            category=concern.category.value,
            explanation=concern.explanation,
            alert_text=concern.alert_text,
            evidence=[
                Evidence(
                    quote=evidence.quote,
                    turn_id=evidence.turn_id,
                    speaker=Speaker(evidence.speaker.value),
                    timestamp_ms=(
                        round(evidence.timestamp_ms) if evidence.timestamp_ms is not None else None
                    ),
                )
                for evidence in concern.evidence
            ],
        )
        for concern in result.concerns
    ]
    status = (
        AnalysisStatus.insufficient_context
        if result.status == TeamAnalysisStatus.INSUFFICIENT
        else AnalysisStatus.ok
    )
    return AnalysisResponse(
        session_id=request.session_id,
        status=status,
        concerns=concerns,
        detection_source=result.detection_source.value,
        technical_warning=result.technical_warning,
    )


def _with_rights_reminder(response: AnalysisResponse) -> AnalysisResponse:
    """Add one consistent spoken reminder to every detected concern."""
    concerns = [
        concern
        if RIGHTS_REMINDER in concern.alert_text
        else concern.model_copy(
            update={"alert_text": f"{concern.alert_text.rstrip()} {RIGHTS_REMINDER}"}
        )
        for concern in response.concerns
    ]
    return response.model_copy(update={"concerns": concerns})


async def analyze_turns(*, settings: Settings, request: AnalysisRequest) -> AnalysisResponse:
    if settings.analysis_provider_mode == "mock":
        return _with_rights_reminder(mock_analysis(request))

    if settings.analysis_provider_mode == "nvidia":
        return _with_rights_reminder(await _analyze_with_team_module(request))

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
        raise ProviderError("ANALYSIS_TIMEOUT", "Analysis timed out.", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(
            "ANALYSIS_UNAVAILABLE", "Analysis is unavailable.", retryable=True
        ) from exc

    if response.status_code == 429:
        raise ProviderError("ANALYSIS_RATE_LIMITED", "Analysis capacity is busy.", retryable=True)
    if response.status_code >= 500:
        raise ProviderError("ANALYSIS_UNAVAILABLE", "Analysis is unavailable.", retryable=True)
    if response.status_code >= 400:
        raise ProviderError("ANALYSIS_REJECTED", "Analysis rejected the request.")
    try:
        return _with_rights_reminder(AnalysisResponse.model_validate(response.json()))
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


async def synthesize_speech(*, settings: Settings, text: str, voice_alias: str) -> AudioResult:
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

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}"
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
        raise ProviderError("TTS_UNAVAILABLE", "Speech is unavailable.", retryable=True) from exc

    if response.status_code == 429:
        raise ProviderError("TTS_RATE_LIMITED", "Speech capacity is busy.", retryable=True)
    if response.status_code in {401, 403}:
        raise ProviderError("TTS_AUTH_FAILED", "Speech credentials were rejected.")
    if response.status_code >= 500:
        raise ProviderError("TTS_UNAVAILABLE", "Speech is unavailable.", retryable=True)
    if response.status_code >= 400:
        raise ProviderError("TTS_REJECTED", "The warning could not be spoken.")
    return AudioResult(response.content, "audio/mpeg")
