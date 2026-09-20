from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .config import settings
from .contracts import (
    AnalysisRequest,
    AnalysisResponse,
    HealthResponse,
    SynthesisRequest,
    TranscriptResponse,
    validate_analysis_evidence,
)
from .providers import (
    AudioResult,
    ProviderError,
    analyze_turns,
    synthesize_speech,
    transcribe_audio,
)

app = FastAPI(
    title="SteelHacks Interrogation-Risk Voice Prototype",
    version="0.1.0",
)

SUPPORTED_AUDIO_TYPES = {
    "audio/webm",
    "audio/mp4",
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
}

_transcript_cache: dict[str, tuple[str, TranscriptResponse]] = {}
_synthesis_cache: dict[str, tuple[str, AudioResult]] = {}
_analysis_locks: dict[str, asyncio.Lock] = {}


@app.middleware("http")
async def prevent_stale_interface_assets(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in {"/", "/index.html", "/app.js", "/styles.css"}:
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def _error(status: int, provider_error: ProviderError) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={
            "code": provider_error.code,
            "message": provider_error.message,
            "retryable": provider_error.retryable,
        },
    )


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    readiness = settings.readiness()
    return HealthResponse(
        status="ok",
        audio_provider_mode=settings.audio_provider_mode,
        analysis_provider_mode=settings.analysis_provider_mode,
        elevenlabs_ready=readiness["elevenlabs_ready"],
        nemotron_ready=readiness["nemotron_ready"],
        disclaimer=(
            "Hackathon prototype only. It does not make legal conclusions and "
            "requires human review."
        ),
    )


@app.post("/api/audio/transcribe", response_model=TranscriptResponse)
async def transcribe(
    file: UploadFile = File(...),
    turn_id: str = Form(..., min_length=1, max_length=100),
    duration_ms: int | None = Form(default=None, ge=0),
) -> TranscriptResponse:
    content_type = (file.content_type or "").split(";", 1)[0].lower()
    if content_type not in SUPPORTED_AUDIO_TYPES:
        raise HTTPException(
            status_code=415,
            detail={
                "code": "AUDIO_TYPE_UNSUPPORTED",
                "message": f"Unsupported audio type: {content_type or 'unknown'}.",
                "retryable": False,
            },
        )
    audio = await file.read(settings.audio_max_bytes + 1)
    if not audio:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "AUDIO_EMPTY",
                "message": "The recording is empty.",
                "retryable": False,
            },
        )
    if len(audio) > settings.audio_max_bytes:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "AUDIO_TOO_LARGE",
                "message": "The recording is larger than the hackathon limit.",
                "retryable": False,
            },
        )
    if duration_ms is not None and duration_ms > settings.audio_max_seconds * 1000:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "AUDIO_TOO_LONG",
                "message": (
                    f"Keep recordings under {settings.audio_max_seconds} seconds."
                ),
                "retryable": False,
            },
        )

    digest = hashlib.sha256(audio).hexdigest()
    cached = _transcript_cache.get(turn_id)
    if cached:
        previous_digest, previous_response = cached
        if previous_digest != digest:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "TURN_ID_CONFLICT",
                    "message": "The turn ID was already used for different audio.",
                    "retryable": False,
                },
            )
        return previous_response

    try:
        result = await transcribe_audio(
            settings=settings,
            audio=audio,
            filename=file.filename or "recording",
            content_type=content_type,
            turn_id=turn_id,
            duration_ms=duration_ms,
        )
    except ProviderError as exc:
        status = 503 if exc.retryable or "CONFIG" in exc.code else 422
        raise _error(status, exc) from exc

    _transcript_cache[turn_id] = (digest, result)
    return result


@app.post("/api/analyze", response_model=AnalysisResponse)
async def analyze(request: AnalysisRequest) -> AnalysisResponse:
    lock = _analysis_locks.setdefault(request.session_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ANALYSIS_IN_FLIGHT",
                "message": "An analysis request is already running for this session.",
                "retryable": True,
            },
        )

    async with lock:
        try:
            response = await analyze_turns(settings=settings, request=request)
            return validate_analysis_evidence(request, response)
        except ProviderError as exc:
            status = 503 if exc.retryable or "CONFIG" in exc.code else 422
            raise _error(status, exc) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "ANALYSIS_INVALID",
                    "message": str(exc),
                    "retryable": True,
                },
            ) from exc


@app.post("/api/audio/synthesize")
async def synthesize(request: SynthesisRequest) -> Response:
    digest = hashlib.sha256(request.text.encode("utf-8")).hexdigest()
    cached = _synthesis_cache.get(request.concern_id)
    if cached:
        previous_digest, previous_audio = cached
        if previous_digest != digest:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CONCERN_ID_CONFLICT",
                    "message": "The concern ID was already used with different alert text.",
                    "retryable": False,
                },
            )
        result = previous_audio
    else:
        try:
            result = await synthesize_speech(
                settings=settings,
                text=request.text,
                voice_alias=request.voice,
            )
        except ProviderError as exc:
            status = 503 if exc.retryable or "CONFIG" in exc.code else 422
            raise _error(status, exc) from exc
        _synthesis_cache[request.concern_id] = (digest, result)

    return Response(
        content=result.data,
        media_type=result.content_type,
        headers={
            "Cache-Control": "no-store",
            "X-Concern-ID": request.concern_id,
            "X-Audio-Provider-Mode": settings.audio_provider_mode,
        },
    )


STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
