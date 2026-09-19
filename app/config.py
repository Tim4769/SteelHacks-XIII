from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class Settings:
    audio_provider_mode: str
    analysis_provider_mode: str
    elevenlabs_api_key: str | None
    elevenlabs_voice_id: str | None
    elevenlabs_stt_model: str
    elevenlabs_tts_model: str
    nemotron_api_url: str | None
    nemotron_api_key: str | None
    nemotron_model: str | None
    audio_max_seconds: int
    audio_max_bytes: int
    mock_stt_text: str

    @classmethod
    def from_environment(cls) -> "Settings":
        audio_mode = os.getenv("AUDIO_PROVIDER_MODE", "mock").strip().lower()
        analysis_mode = os.getenv("ANALYSIS_PROVIDER_MODE", "mock").strip().lower()
        if audio_mode not in {"mock", "elevenlabs"}:
            raise ValueError("AUDIO_PROVIDER_MODE must be mock or elevenlabs")
        if analysis_mode not in {"mock", "remote", "nvidia"}:
            raise ValueError(
                "ANALYSIS_PROVIDER_MODE must be mock, remote, or nvidia"
            )

        return cls(
            audio_provider_mode=audio_mode,
            analysis_provider_mode=analysis_mode,
            elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY") or None,
            elevenlabs_voice_id=os.getenv("ELEVENLABS_VOICE_ID") or None,
            elevenlabs_stt_model=os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2"),
            elevenlabs_tts_model=os.getenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5"),
            nemotron_api_url=os.getenv("NEMOTRON_API_URL") or None,
            nemotron_api_key=os.getenv("NEMOTRON_API_KEY") or None,
            nemotron_model=os.getenv("NEMOTRON_MODEL") or None,
            audio_max_seconds=_positive_int("AUDIO_MAX_SECONDS", 30),
            audio_max_bytes=_positive_int("AUDIO_MAX_BYTES", 30_000_000),
            mock_stt_text=os.getenv(
                "MOCK_STT_TEXT",
                "If you confess, I can make sure you go home tonight.",
            ).strip(),
        )

    def readiness(self) -> dict[str, bool]:
        return {
            "elevenlabs_ready": bool(
                self.audio_provider_mode == "elevenlabs"
                and self.elevenlabs_api_key
                and self.elevenlabs_voice_id
            ),
            "nemotron_ready": bool(
                (
                    self.analysis_provider_mode == "remote"
                    and self.nemotron_api_url
                )
                or (
                    self.analysis_provider_mode == "nvidia"
                    and self.nemotron_api_url
                    and self.nemotron_api_key
                    and self.nemotron_model
                )
            ),
        }


settings = Settings.from_environment()
