"""High-confidence local classification with exact original-text evidence."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass

from .models import (
    AnalysisStatus,
    AnalyzeRequest,
    AnalyzeResponse,
    Concern,
    ConcernCategory,
    DetectionSource,
    Evidence,
    Turn,
)

CLASSIFIER_VERSION = "4.0.0"

_COOPERATION = re.compile(
    r"\b(confess(?:ion)?|admit(?:ted|ting)?|take responsibility|cooperate|tell (?:us|me)|"
    r"give (?:us )?(?:a )?statement|sign|initial|approve|confirm|agree|acknowledge|"
    r"say you did it|tell the truth|help us|work with us)\b"
)
_CONDITION = re.compile(
    r"\b(if|unless|until|once|after|when|otherwise|or|refuse(?: to)?|will not|do not|"
    r"as soon as|the sooner|only after|and then|and (?:i|we) (?:will|can))\b"
)
_BENEFIT = re.compile(
    r"\b(help you (?:go home|get bail)?|go home tonight|release(?:d)?|let you go|lenien(?:cy|t)|"
    r"deal|make (?:this|things) easier|talk to the prosecutor|reducing? the charge|"
    r"good word|lower the charge|drop the charge|get bail|you can leave)\b"
)
_DIRECT_THREAT = re.compile(
    r"\b(add another charge|make (?:this|things) (?:much )?worse|maximum sentence|"
    r"stay here all night|you are going to jail|you will go to jail|lock you up|"
    r"punish(?:ment)?|throw the book|more charges?|serious charges?)\b"
)
_THIRD_PARTY = re.compile(
    r"\b(sister|brother|mother|father|mom|dad|parents?|wife|husband|spouse|partner|"
    r"girlfriend|boyfriend|child|children|son|daughter|family|friend|roommate|accomplice|"
    r"he|she|him|her|them)\b"
)
_THIRD_PARTY_ACTION = re.compile(
    r"\b(arrest(?:ed)?|charg(?:e|ed)|prosecute|detain(?:ed)?|hold|jail|"
    r"lock (?:them |him |her )?up|take into custody|investigate|deport|call immigration|"
    r"take (?:your |the )?children|remove (?:your |the )?children|contact child services|"
    r"child services .* take|lose custody|hurt|punish|go after|bring in|bringing .* in|"
    r"pick up|leave .* alone)\b"
)
_DEPRIVATION = re.compile(
    r"\b(sleep|rest|go to bed|food|eat|meal|water|drink|bathroom|toilet|restroom|"
    r"medication|medicine|blanket|warmth|break|end the interview|interview ends|"
    r"stop questioning|finish|be done|all go home)\b"
)
_EVIDENCE = re.compile(r"\b(dna|fingerprints?|video|camera|witness|evidence|recording|prints?)\b")
_EVIDENCE_ASSERTION = re.compile(
    r"\b(we have|we found|proves?|shows?|all over|caught|identified)\b"
)
_MINIMIZATION = re.compile(
    r"\b(just an accident|only an accident|no big deal|anyone (?:could|would)|"
    r"understandable|lost control|did not mean)\b"
)
_NEGATED_TACTIC = re.compile(
    r"\b(cannot|can not|will not|would not|did not|never|no)\s+"
    r"(?:promise|offer|threaten|guarantee|condition|require)\b"
)
_META = re.compile(
    r"\b(example|hypothetical|suppose|training|improper (?:statement|threat)|"
    r"someone said|he said|she said|the phrase|quoted?)\b"
)
_PROFANITY = re.compile(r"\b(fuck(?:ing|er)?|shit(?:ty)?|bullshit|damn|bitch|asshole)\b")
_SAFE_DIRECT = re.compile(
    r"^(?:you are (?:an idiot|lying)|i do not believe you|answer the question|"
    r"where were you last night|tell me what happened)[.!?]*$"
)
_SAFE_CONTEXT = re.compile(
    r"\b(may be interviewed because|have access to|would you like|whenever you need|"
    r"do not have to sign|whether or not|request counsel|already been lawfully charged)\b"
)
_TACTIC_SIGNAL = re.compile(
    r"\b(confess|confession|admit|admission|responsibility|cooperate|sign|statement|charge|"
    r"arrest|jail|"
    r"sleep|water|bathroom|medication|prosecutor|leniency|family|sister|brother)\b"
)


@dataclass(frozen=True)
class LocalDecision:
    response: AnalyzeResponse
    decisive: bool
    reason: str
    counsel_requested: bool


_COUNSEL_REQUEST = re.compile(
    r"\b(i want (?:to speak to |to talk to |a )?(?:my )?(?:lawyer|attorney|counsel)|"
    r"i need (?:a |an )?(?:lawyer|attorney|counsel)|can i (?:talk|speak) to (?:a |my )?"
    r"(?:lawyer|attorney|counsel)|i do not want to (?:talk|answer).*without (?:my )?"
    r"(?:lawyer|attorney|counsel)|i want counsel)\b"
)
_COUNSEL_COMPLIANT = re.compile(
    r"\b(stop(?:ping)? (?:the interview|questioning)|will stop questioning|arrange .*"
    r"(?:lawyer|attorney|counsel)|do not have to answer|will not ask .* until counsel|"
    r"counsel is present|lawyer is present|your lawyer is here)\b"
)
_SUSPECT_REINITIATION = re.compile(
    r"\b(i (?:want|would like) to (?:tell|talk)|i want to explain|i asked to speak to (?:the )?"
    r"detective again)\b"
)
_NARRATED_REINITIATION = re.compile(
    r"\b(asks? to speak with (?:the )?detective again|returned to speak|initiated .*"
    r"conversation)\b"
)
_SUBSTANTIVE_QUESTIONING = re.compile(
    r"\?|\b(explain|tell me|tell us|what happened|did you|help me understand|cooperate|"
    r"opportunity to help yourself|marks on your hands|serious charges?)\b"
)


def normalize_text(text: str) -> str:
    """Normalize for matching without changing evidence source text."""
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = normalized.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    contractions = {
        "won't": "will not",
        "wouldn't": "would not",
        "can't": "cannot",
        "don't": "do not",
        "doesn't": "does not",
        "we'll": "we will",
        "you'll": "you will",
        "i'll": "i will",
        "we've": "we have",
    }
    for original, replacement in contractions.items():
        normalized = normalized.replace(original, replacement)
    normalized = normalized.replace("confesion", "confession").replace(
        "responsiblity", "responsibility"
    )
    normalized = re.sub(r"[^a-z0-9'\s]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def classify_locally_decision(
    request: AnalyzeRequest, counsel_requested: bool = False
) -> LocalDecision:
    concerns: list[Concern] = []
    ambiguous = False
    reasons: list[str] = []
    new_ids = {turn.turn_id for turn in request.new_turns}
    for turn in [*request.context_turns, *request.new_turns]:
        normalized = normalize_text(turn.text)
        if turn.speaker.value == "suspect":
            if _COUNSEL_REQUEST.search(normalized):
                counsel_requested = True
            elif counsel_requested and _SUSPECT_REINITIATION.search(normalized):
                counsel_requested = False
            continue
        if turn.speaker.value == "narrator":
            if counsel_requested and _NARRATED_REINITIATION.search(normalized):
                counsel_requested = False
            continue
        if turn.speaker.value != "officer":
            continue
        if counsel_requested and _COUNSEL_COMPLIANT.search(normalized):
            counsel_requested = False
            continue
        if (
            counsel_requested
            and turn.turn_id in new_ids
            and _SUBSTANTIVE_QUESTIONING.search(normalized)
        ):
            concerns.append(_concern(request.session_id, turn, ConcernCategory.COUNSEL_QUESTIONING))
        if turn.turn_id not in new_ids:
            continue
        category, decisive, reason = _classify_turn(turn)
        reasons.append(reason)
        if category is not None:
            concerns.append(_concern(request.session_id, turn, category))
        elif not decisive:
            ambiguous = True
    if (
        request.context_turns
        and not concerns
        and any(_TACTIC_SIGNAL.search(normalize_text(turn.text)) for turn in request.context_turns)
    ):
        ambiguous = True
        reasons.append("context requires semantic combination")
    response = _response(request, concerns)
    if concerns:
        return LocalDecision(
            response, True, "high-confidence configured concern", counsel_requested
        )
    return LocalDecision(response, not ambiguous, "; ".join(reasons), counsel_requested)


def classify_locally(request: AnalyzeRequest) -> AnalyzeResponse:
    return classify_locally_decision(request).response


def looks_like_mislabeled_counsel_request(request: AnalyzeRequest) -> bool:
    """Warn without relabeling when officer-tagged text strongly resembles suspect speech."""
    return any(
        turn.speaker.value == "officer" and _COUNSEL_REQUEST.search(normalize_text(turn.text))
        for turn in request.new_turns
    )


def is_profanity_only(request: AnalyzeRequest) -> bool:
    return all(_is_profanity_or_insult(normalize_text(turn.text)) for turn in request.new_turns)


def _classify_turn(turn: Turn) -> tuple[ConcernCategory | None, bool, str]:
    if turn.speaker.value != "officer":
        return None, True, "non-officer statement"
    text = normalize_text(turn.text)
    if _META.search(text) or _NEGATED_TACTIC.search(text):
        return None, True, "quoted, hypothetical, or explicitly negated tactic"

    cooperation = bool(_COOPERATION.search(text))
    conditional = bool(_CONDITION.search(text))
    if (
        cooperation
        and conditional
        and _THIRD_PARTY.search(text)
        and _THIRD_PARTY_ACTION.search(text)
    ):
        return ConcernCategory.THIRD_PARTY_THREAT, True, "third-party adverse action leverage"
    if cooperation and conditional and _DEPRIVATION.search(text):
        return ConcernCategory.DEPRIVATION, True, "basic need or interview relief leverage"
    if cooperation and _MINIMIZATION.search(text):
        return ConcernCategory.MINIMIZATION, True, "minimization paired with admission request"
    if cooperation and _EVIDENCE.search(text) and _EVIDENCE_ASSERTION.search(text):
        return (
            ConcernCategory.EVIDENCE_PRESSURE,
            True,
            "evidence claim paired with admission request",
        )
    if cooperation and conditional and _BENEFIT.search(text):
        return ConcernCategory.BENEFIT, True, "benefit linked to cooperation"
    if cooperation and conditional and _DIRECT_THREAT.search(text):
        return ConcernCategory.THREAT, True, "direct adverse consequence linked to cooperation"

    if _is_profanity_or_insult(text) or _SAFE_DIRECT.fullmatch(text) or _SAFE_CONTEXT.search(text):
        return None, True, "ordinary question, disbelief, instruction, or insult"
    if not _TACTIC_SIGNAL.search(text):
        return None, True, "no configured tactic signal"
    return None, False, "tactic vocabulary present without a high-confidence pattern"


def _is_profanity_or_insult(text: str) -> bool:
    if _COOPERATION.search(text) or _CONDITION.search(text):
        return False
    cleaned = _PROFANITY.sub(" ", text)
    words = set(cleaned.split())
    allowed = {
        "you",
        "are",
        "a",
        "an",
        "the",
        "this",
        "that",
        "is",
        "really",
        "so",
        "very",
        "liar",
        "lying",
        "idiot",
        "shut",
        "up",
        "i",
        "do",
        "not",
        "believe",
    }
    return bool(_PROFANITY.search(text) or words <= allowed) and words <= allowed


def _response(request: AnalyzeRequest, concerns: list[Concern]) -> AnalyzeResponse:
    return AnalyzeResponse(
        session_id=request.session_id,
        request_id=request.request_id,
        sequence_number=request.sequence_number,
        analyzed_turn_ids=[turn.turn_id for turn in request.new_turns],
        status=AnalysisStatus.CONCERN if concerns else AnalysisStatus.NONE,
        concerns=concerns,
        error=None,
        detection_source=DetectionSource.LOCAL,
        technical_warning=None,
    )


def _concern(session_id: str, turn: Turn, category: ConcernCategory) -> Concern:
    evidence = Evidence(
        turn_id=turn.turn_id,
        speaker=turn.speaker,
        start_char=0,
        end_char=len(turn.text),
        quote=turn.text,
        timestamp_ms=turn.timestamp_ms,
    )
    identity = [CLASSIFIER_VERSION, session_id, category.value, turn.turn_id, 0, len(turn.text)]
    digest = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()[:20]
    label = category.value.replace("_", " ")
    return Concern(
        concern_id=f"concern_{digest}",
        category=category,
        evidence=[evidence],
        explanation=f"The statement may use {label} and should be reviewed by a person.",
        alert_text=f"Potential {label} detected for human review.",
    )
