"""Validated request and response contracts for dialogue analysis."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

NonEmptyString = Annotated[str, Field(min_length=1, max_length=200)]
TranscriptText = Annotated[str, Field(min_length=1, max_length=20_000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Speaker(StrEnum):
    OFFICER = "officer"
    SUSPECT = "suspect"
    UNKNOWN = "unknown"


class ConcernCategory(StrEnum):
    BENEFIT = "benefit_conditioned_on_confession"
    THREAT = "threat_conditioned_on_confession"


class AnalysisStatus(StrEnum):
    CONCERN = "concern_detected"
    NONE = "no_concern_detected"
    INSUFFICIENT = "insufficient_context"
    ERROR = "error"


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    INPUT_CONFLICT = "INPUT_CONFLICT"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"


class Turn(StrictModel):
    turn_id: NonEmptyString
    speaker: Speaker
    text: TranscriptText
    timestamp_ms: StrictInt | StrictFloat | None = None

    @field_validator("turn_id", "text")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("timestamp_ms")
    @classmethod
    def valid_timestamp(cls, value: int | float | None) -> int | float | None:
        if isinstance(value, bool) or (value is not None and value < 0):
            raise ValueError("timestamp_ms must be nonnegative or null")
        return value


class AnalyzeRequest(StrictModel):
    session_id: NonEmptyString
    request_id: NonEmptyString
    sequence_number: StrictInt = Field(ge=0)
    context_turns: list[Turn] = Field(default_factory=list, max_length=100)
    new_turns: list[Turn] = Field(min_length=1, max_length=50)

    @field_validator("session_id", "request_id")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def unique_turn_ids(self) -> AnalyzeRequest:
        ids = [turn.turn_id for turn in [*self.context_turns, *self.new_turns]]
        if len(ids) != len(set(ids)):
            raise ValueError("turn IDs must be unique across context_turns and new_turns")
        return self


class Evidence(StrictModel):
    turn_id: str
    speaker: Speaker
    start_char: int
    end_char: int
    quote: str
    timestamp_ms: int | float | None


class Concern(StrictModel):
    concern_id: str
    category: ConcernCategory
    evidence: list[Evidence]
    explanation: str
    alert_text: str


class AnalysisError(StrictModel):
    code: ErrorCode
    message: str
    retryable: bool


class AnalyzeResponse(StrictModel):
    session_id: str
    request_id: str
    sequence_number: int
    analyzed_turn_ids: list[str]
    status: AnalysisStatus
    concerns: list[Concern]
    error: AnalysisError | None

    @model_validator(mode="after")
    def consistent_status(self) -> AnalyzeResponse:
        if self.status == AnalysisStatus.ERROR:
            if self.error is None or self.concerns:
                raise ValueError("error responses require error details and no concerns")
        elif self.error is not None:
            raise ValueError("non-error responses cannot include error details")
        if self.status == AnalysisStatus.CONCERN and not self.concerns:
            raise ValueError("concern_detected requires at least one concern")
        if self.status != AnalysisStatus.CONCERN and self.concerns:
            raise ValueError("only concern_detected may include concerns")
        return self


class ModelEvidence(StrictModel):
    turn_id: str
    start_char: StrictInt | None = None
    end_char: StrictInt | None = None
    quote: str | None = None

    @model_validator(mode="after")
    def offsets_or_quote(self) -> ModelEvidence:
        has_start = self.start_char is not None
        has_end = self.end_char is not None
        if has_start != has_end:
            raise ValueError("start_char and end_char must be supplied together")
        if not has_start and self.quote is None:
            raise ValueError("evidence requires offsets or an exact quote")
        return self


class ModelConcern(StrictModel):
    category: ConcernCategory
    evidence: list[ModelEvidence] = Field(min_length=1, max_length=10)
    explanation: Annotated[str, Field(min_length=1, max_length=400)]
    alert_text: Annotated[str, Field(min_length=1, max_length=240)]


class ModelAnalysis(StrictModel):
    status: Literal["concern_detected", "no_concern_detected", "insufficient_context"]
    concerns: list[ModelConcern] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def consistent_status(self) -> ModelAnalysis:
        if self.status == "concern_detected" and not self.concerns:
            raise ValueError("concern_detected requires concerns")
        if self.status != "concern_detected" and self.concerns:
            raise ValueError("non-concern status must have no concerns")
        return self
