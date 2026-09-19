# Voice Action Agent

A SteelHacks project that turns spoken requests into decisions, system actions, and natural spoken responses.

**Status:** Concept and build planning. The application use case and implementation stack are still being finalized. This workspace currently contains planning material; a runnable application has not yet been added.

## Overview

The user speaks naturally. Speech-to-text converts their words into a transcript, NVIDIA Nemotron interprets the situation and selects an action, and the application executes that action. ElevenLabs then turns the response into speech.

Nemotron acts as the decision and routing layer. Its structured output tells the application which supported action to take, allowing the system to do useful work through a voice interface.

## How it works

```text
User speaks
    ↓
Speech-to-text produces a transcript
    ↓
Nemotron interprets intent and selects an action
    ↓
The application validates and executes the selected action
    ↓
The action result determines the response
    ↓
ElevenLabs generates speech
    ↓
The user hears the result
```

The application will support a small, predefined set of actions. It should ask for clarification when a request is unclear and report an action's actual outcome before claiming it succeeded.

## Example interaction

The following is an illustrative scenario; it does not commit the project to an emergency-response application.

**User:** “I think someone just fell over in the hallway.”

Nemotron could return a compact routing result:

```json
{
  "intent": "possible_emergency",
  "action": "create_alert",
  "arguments": {
    "location": "hallway",
    "summary": "The user reports that someone may have fallen."
  }
}
```

For this example, the application could create an on-screen alert. After successful execution, ElevenLabs could speak:

> “I've added an alert to the dashboard about a possible fall in the hallway.”

This example describes a proposed dashboard action. Contacting another person or an emergency service would require a separate integration.

## Planned components

| Component | Responsibility |
|---|---|
| Voice input | Capture the user's speech |
| Speech-to-text | Produce the transcript; ElevenLabs Scribe is a candidate |
| NVIDIA Nemotron | Interpret intent, classify the situation, and select a supported action |
| Action handler | Validate the structured result, run the action, and return its outcome |
| ElevenLabs text-to-speech | Speak a response based on the outcome |
| Interface | Show the transcript, selected action, progress, and result |

## Hackathon scope

Our first version will focus on one application and one or two useful actions. The initial goal is a complete interaction that works reliably from voice input through action execution to spoken output.

- Choose the specific user and problem the agent will help with.
- Build the transcript-to-action flow before connecting microphone input.
- Add spoken responses and a simple interface.
- Test successful actions, unclear requests, and failed actions.
- Keep a working demo and a backup recording ready for presentation.

Continuous listening is a stretch goal. A record-then-process interaction is an acceptable first version.

**Latency target:** Aim to begin responding within roughly 1–3 seconds after the user finishes speaking for simple actions. This is a design goal, not a measured result; actual timing will depend on transcription, model inference, action execution, speech generation, and network conditions.

## Sponsor tracks

**NVIDIA Nemotron — Beyond the Chatbot:** Nemotron makes structured decisions and routes requests to actions, giving its output a direct role in application behavior.

**ElevenLabs — Out Loud:** Speech is central to both input and output, allowing users to interact naturally without typing or reading a long response.

## Team responsibilities

| Person | Owns |
|---|---|
| 1 — Audio | Speech input, transcription, and ElevenLabs spoken output |
| 2 — Reasoning | Nemotron instructions, structured results, and conversation context |
| 3 — Interface and integration | App interface, action handler, and connecting the components |
| 4 — Product and testing | Use-case definition, test scenarios, demo, and submission |

We will connect the components regularly throughout development. Our submission target is **10 a.m. on September 20, 2026**, ahead of the **11 a.m. deadline**, in Pittsburgh time.

## Development setup

Installation and run instructions will be added when the implementation stack is selected and the first runnable version is available. Keep service API keys in local environment variables and out of the repository.

Starting documentation:

- [NVIDIA Nemotron API](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-nano-30b-a3b)
- [ElevenLabs speech-to-text](https://elevenlabs.io/docs/overview/capabilities/speech-to-text)
- [ElevenLabs text-to-speech quickstart](https://elevenlabs.io/docs/eleven-api/quickstart)
