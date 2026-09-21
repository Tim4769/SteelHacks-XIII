# Hackathon integration contract

This document is the shared boundary between Person 1 (audio), Person 2
(Nemotron analysis), and Person 3 (interface and orchestration).

## Ownership

- Person 1 owns browser recording support guidance, `/api/audio/transcribe`,
  ElevenLabs Scribe v2, `/api/audio/synthesize`, ElevenLabs TTS, and audio
  failure mapping.
- Person 2 owns interrogation-risk analysis, the approved concern taxonomy,
  evidence selection, and the Nemotron response.
- Person 3 owns `session_id`, `turn_id`, manual role selection, session turns,
  one-in-flight analysis, reset handling, result display, concern deduplication,
  and playback.

## Analysis request

```json
{
  "session_id": "demo-001",
  "turns": [
    {
      "turn_id": "t3",
      "speaker": "officer",
      "text": "If you confess, I can make sure you go home tonight.",
      "timestamp_ms": null
    }
  ]
}
```

`timestamp_ms` is the turn's position in a larger session. It is not the audio
clip duration. The hackathon MVP leaves it `null`.

## Analysis response

```json
{
  "session_id": "demo-001",
  "status": "ok",
  "concerns": [
    {
      "concern_id": "demo-001-t3-benefit_for_confession",
      "category": "benefit_for_confession",
      "explanation": "The statement appears to connect a confession with a promised benefit.",
      "alert_text": "Potential inducement detected. Review the promise connected to a confession.",
      "evidence": [
        {
          "quote": "If you confess, I can make sure you go home tonight.",
          "turn_id": "t3",
          "speaker": "officer",
          "timestamp_ms": null
        }
      ]
    }
  ]
}
```

The orchestrator must validate that every evidence quote appears verbatim in
the referenced turn and that the speaker matches. An invalid response is
`Analysis unavailable`, never `No concern detected`.

## Result semantics

- `status=ok` and `concerns=[]`: valid negative result. Show `No concern
  detected`; do not speak.
- `status=insufficient_context`: show `Insufficient context`; do not speak.
- HTTP or schema failure: show `Analysis unavailable`; do not infer a result.
- A new `concern_id`: display evidence and speak `alert_text` once.
- A repeated `concern_id`: keep the card and do not replay speech.
- TTS failure: preserve the warning card and retry TTS only.

## Deferred after the hackathon

Diarization, anonymous-speaker mapping, streaming STT/TTS, continuous listening,
barge-in, persistent session storage, and broad cross-browser support are out of
scope for the first demo.

