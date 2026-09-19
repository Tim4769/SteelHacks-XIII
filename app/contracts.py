from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Speaker(str, Enum):
    officer = "officer"
    suspect = "suspect"
    unknown = "unknown"


class TranscriptResponse(StrictModel):
    turn_id: str = Field(min_length=1, max_length=100)
    transcript: str = Field(min_length=1, max_length=20_000)
    language_code: str | None = Field(default=None, max_length=20)
    duration_ms: int | None = Field(default=None, ge=0)
    status: str = "ok"


class DialogueTurn(StrictModel):
    turn_id: str = Field(min_length=1, max_length=100)
    speaker: Speaker
    text: str = Field(min_length=1, max_length=20_000)
    timestamp_ms: int | None = Field(default=None, ge=0)


class AnalysisRequest(StrictModel):
    session_id: str = Field(min_length=1, max_length=100)
    turns: list[DialogueTurn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_turn_ids(self) -> "AnalysisRequest":
        ids = [turn.turn_id for turn in self.turns]
        if len(ids) != len(set(ids)):
            raise ValueError("turn_id values must be unique within a session")
        return self


class Evidence(StrictModel):
    quote: str = Field(min_length=1, max_length=5_000)
    turn_id: str = Field(min_length=1, max_length=100)
    speaker: Speaker
    timestamp_ms: int | None = Field(default=None, ge=0)


class Concern(StrictModel):
    concern_id: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=120)
    explanation: str = Field(min_length=1, max_length=2_000)
    alert_text: str = Field(min_length=1, max_length=600)
    evidence: list[Evidence] = Field(min_length=1, max_length=20)


class AnalysisStatus(str, Enum):
    ok = "ok"
    insufficient_context = "insufficient_context"


class AnalysisResponse(StrictModel):
    session_id: str = Field(min_length=1, max_length=100)
    status: AnalysisStatus
    concerns: list[Concern] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_concern_ids(self) -> "AnalysisResponse":
        ids = [concern.concern_id for concern in self.concerns]
        if len(ids) != len(set(ids)):
            raise ValueError("concern_id values must be unique")
        if self.status == AnalysisStatus.insufficient_context and self.concerns:
            raise ValueError("insufficient_context cannot include concerns")
        return self


class SynthesisRequest(StrictModel):
    concern_id: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=600)
    voice: str = Field(default="default", min_length=1, max_length=80)


class HealthResponse(StrictModel):
    status: str
    audio_provider_mode: str
    analysis_provider_mode: str
    elevenlabs_ready: bool
    nemotron_ready: bool
    disclaimer: str


def validate_analysis_evidence(
    request: AnalysisRequest, response: AnalysisResponse
) -> AnalysisResponse:
    if response.session_id != request.session_id:
        raise ValueError("analysis session_id does not match request")

    turns = {turn.turn_id: turn for turn in request.turns}
    for concern in response.concerns:
        for evidence in concern.evidence:
            turn = turns.get(evidence.turn_id)
            if turn is None:
                raise ValueError(
                    f"evidence references unknown turn_id {evidence.turn_id}"
                )
            if evidence.speaker != turn.speaker:
                raise ValueError(
                    f"evidence speaker does not match turn {evidence.turn_id}"
                )
            if evidence.quote not in turn.text:
                raise ValueError(
                    f"evidence quote is not verbatim in turn {evidence.turn_id}"
                )
    return response

