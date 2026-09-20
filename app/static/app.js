const VAD = Object.freeze({
  speechThreshold: 0.035,
  silenceThreshold: 0.02,
  endOfTurnSilenceMs: 1300,
  minimumSpeechMs: 300,
  maximumSegmentMs: 30000,
  idleRotationMs: 20000,
  alertRecoveryMs: 450,
  sampleEveryMs: 50,
});

const AudioContextClass = window.AudioContext || window.webkitAudioContext;

const state = {
  sessionId: null,
  sessionStartedAt: null,
  sessionRole: null,
  turns: [],
  seenConcernIds: new Set(),
  mediaStream: null,
  currentSegment: null,
  audioContext: null,
  audioSource: null,
  analyser: null,
  waveform: null,
  vadHandle: null,
  liveSession: false,
  captureSuppressed: false,
  speechActive: false,
  speechStartedAt: null,
  speechLoudMs: 0,
  silenceStartedAt: null,
  timerHandle: null,
  activeAbortController: null,
  activeAudio: null,
  activeAudioResolver: null,
  analysisQueue: Promise.resolve(),
  queuedTurns: 0,
};

const el = Object.fromEntries(
  [
    "resetButton", "stateLabel", "providerLabel", "securityWarning",
    "speakerSelect", "recordButton", "stopButton", "cancelButton",
    "recordingIndicator", "recordingText", "timer", "voiceMeterFill",
    "textFallback", "analyzeTextButton", "sessionId", "turnList",
    "analysisSummary", "analysisMetadata", "errorPanel", "concernList", "stopAudioButton",
  ].map((id) => [id, document.getElementById(id)])
);

function newId(prefix) {
  const value = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  return `${prefix}-${value}`;
}

function setUiState(label) {
  el.stateLabel.textContent = label;
}

function setIndicator(mode) {
  el.recordingIndicator.classList.remove("live", "listening", "paused");
  if (mode) el.recordingIndicator.classList.add(mode);
}

function renderControls() {
  const supported = Boolean(navigator.mediaDevices && window.MediaRecorder);
  el.recordButton.disabled = state.liveSession || !supported;
  el.stopButton.disabled = !state.liveSession;
  el.cancelButton.disabled = !state.liveSession || state.captureSuppressed;
  el.speakerSelect.disabled = state.liveSession;
  el.analyzeTextButton.disabled = state.queuedTurns > 0;
  el.recordButton.textContent = "Start live session";
}

function stopCurrentAudio() {
  if (state.activeAudio) {
    state.activeAudio.pause();
    state.activeAudio.currentTime = 0;
    if (state.activeAudio.dataset.url) URL.revokeObjectURL(state.activeAudio.dataset.url);
    state.activeAudio = null;
  }
  if (state.activeAudioResolver) {
    state.activeAudioResolver();
    state.activeAudioResolver = null;
  }
  el.stopAudioButton.disabled = true;
}

function resetVadState() {
  state.speechActive = false;
  state.speechStartedAt = null;
  state.speechLoudMs = 0;
  state.silenceStartedAt = null;
  el.voiceMeterFill.style.width = "0%";
}

function stopMediaTracks() {
  if (state.mediaStream) state.mediaStream.getTracks().forEach((track) => track.stop());
  state.mediaStream = null;
}

function teardownAudioAnalysis() {
  clearInterval(state.vadHandle);
  state.vadHandle = null;
  state.audioSource?.disconnect();
  state.audioSource = null;
  state.analyser = null;
  state.waveform = null;
  if (state.audioContext && state.audioContext.state !== "closed") {
    state.audioContext.close().catch(() => {});
  }
  state.audioContext = null;
}

function stopCurrentSegment(mode = "discard") {
  const segment = state.currentSegment;
  if (!segment || segment.recorder.state === "inactive") return;
  segment.stopMode = mode;
  segment.durationMs = Date.now() - segment.startedAt;
  segment.recorder.stop();
}

function stopLiveSession({ label = "Stopped", preserveAudio = false } = {}) {
  state.liveSession = false;
  state.captureSuppressed = false;
  stopCurrentSegment("discard");
  teardownAudioAnalysis();
  stopMediaTracks();
  clearInterval(state.timerHandle);
  state.timerHandle = null;
  if (!preserveAudio) stopCurrentAudio();
  resetVadState();
  setIndicator(null);
  el.recordingText.textContent = "Ready to start";
  el.timer.textContent = "00:00";
  renderControls();
  setUiState(label);
}

function resetSession() {
  state.activeAbortController?.abort();
  state.activeAbortController = null;
  stopLiveSession({ label: "Idle" });
  state.sessionId = newId("session");
  state.sessionStartedAt = Date.now();
  state.sessionRole = null;
  state.turns = [];
  state.seenConcernIds.clear();
  state.analysisQueue = Promise.resolve();
  state.queuedTurns = 0;
  el.sessionId.textContent = state.sessionId;
  el.turnList.innerHTML = '<li class="empty-state">No turns recorded yet.</li>';
  el.concernList.innerHTML = "";
  el.errorPanel.classList.add("hidden");
  el.analysisSummary.textContent = "Waiting for a finalized transcript.";
  el.analysisMetadata.textContent = "";
  el.analysisMetadata.classList.add("hidden");
  renderControls();
}

function preferredMimeType() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

function updateTimer() {
  if (!state.liveSession) return;
  const elapsed = Date.now() - state.sessionStartedAt;
  const minutes = Math.floor(elapsed / 60000);
  const seconds = Math.floor((elapsed % 60000) / 1000);
  el.timer.textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function createSegmentRecorder() {
  if (!state.liveSession || state.captureSuppressed || !state.mediaStream || state.currentSegment) return;
  const mimeType = preferredMimeType();
  const recorder = mimeType
    ? new MediaRecorder(state.mediaStream, { mimeType })
    : new MediaRecorder(state.mediaStream);
  const segment = {
    recorder,
    chunks: [],
    startedAt: Date.now(),
    stopMode: "discard",
    durationMs: 0,
    turnId: newId("turn"),
    speaker: state.sessionRole,
    timestampMs: Date.now() - state.sessionStartedAt,
  };
  state.currentSegment = segment;
  recorder.addEventListener("dataavailable", (event) => {
    if (event.data.size > 0) segment.chunks.push(event.data);
  });
  recorder.addEventListener("stop", () => handleStoppedSegment(segment));
  recorder.start(250);
}

function handleStoppedSegment(segment) {
  if (state.currentSegment === segment) state.currentSegment = null;
  const type = segment.recorder.mimeType || segment.chunks[0]?.type || "audio/webm";
  const blob = new Blob(segment.chunks, { type });
  if (state.liveSession && !state.captureSuppressed) createSegmentRecorder();
  if (segment.stopMode === "finalize" && blob.size > 0) {
    enqueueRecordedTurn(blob, segment);
  }
}

function setupAudioAnalysis() {
  state.audioContext = new AudioContextClass();
  state.audioSource = state.audioContext.createMediaStreamSource(state.mediaStream);
  state.analyser = state.audioContext.createAnalyser();
  state.analyser.fftSize = 2048;
  state.waveform = new Float32Array(state.analyser.fftSize);
  state.audioSource.connect(state.analyser);
  state.vadHandle = setInterval(sampleVoiceActivity, VAD.sampleEveryMs);
}

function rootMeanSquare(values) {
  let total = 0;
  for (const value of values) total += value * value;
  return Math.sqrt(total / values.length);
}

function sampleVoiceActivity() {
  if (!state.liveSession || state.captureSuppressed || !state.analyser) return;
  state.analyser.getFloatTimeDomainData(state.waveform);
  const volume = rootMeanSquare(state.waveform);
  el.voiceMeterFill.style.width = `${Math.min(100, Math.round(volume * 900))}%`;
  const now = Date.now();
  const segmentAge = state.currentSegment ? now - state.currentSegment.startedAt : 0;

  if (!state.speechActive && volume >= VAD.speechThreshold) {
    state.speechActive = true;
    state.speechStartedAt = now;
    state.speechLoudMs = VAD.sampleEveryMs;
    state.silenceStartedAt = null;
    if (state.currentSegment) {
      state.currentSegment.timestampMs = now - state.sessionStartedAt;
    }
    setIndicator("live");
    el.recordingText.textContent = "Speech detected";
    setUiState("Listening / speech detected");
    return;
  }

  if (state.speechActive) {
    if (volume > VAD.silenceThreshold) {
      state.speechLoudMs += VAD.sampleEveryMs;
    }
    if (volume <= VAD.silenceThreshold) {
      state.silenceStartedAt ??= now;
      const silenceMs = now - state.silenceStartedAt;
      if (
        silenceMs >= VAD.endOfTurnSilenceMs
        && state.speechLoudMs >= VAD.minimumSpeechMs
      ) {
        finalizeCurrentTurn("Pause detected");
        return;
      }
    } else {
      state.silenceStartedAt = null;
    }
    if (segmentAge >= VAD.maximumSegmentMs) {
      finalizeCurrentTurn("Maximum turn length reached");
    }
  } else if (segmentAge >= VAD.idleRotationMs) {
    stopCurrentSegment("discard");
  }
}

function finalizeCurrentTurn(reason = "Turn finalized", force = false) {
  if (!state.liveSession || state.captureSuppressed || !state.currentSegment) return;
  const hadSpeech = state.speechActive;
  resetVadState();
  if (!hadSpeech && !force) {
    el.recordingText.textContent = "Listening for speech";
    return;
  }
  setIndicator("listening");
  el.recordingText.textContent = `${reason}; continuing to listen`;
  setUiState("Turn queued for transcription");
  stopCurrentSegment("finalize");
}

async function startLiveSession() {
  if (!window.isSecureContext) {
    showError("MIC_REQUIRES_SECURE_CONTEXT", "Use HTTPS or localhost for microphone access.", false);
    return;
  }
  if (state.liveSession) return;
  stopCurrentAudio();
  el.errorPanel.classList.add("hidden");
  try {
    state.mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
    });
    state.sessionRole = el.speakerSelect.value;
    state.sessionStartedAt = Date.now();
    state.liveSession = true;
    state.captureSuppressed = false;
    resetVadState();
    setupAudioAnalysis();
    await state.audioContext.resume();
    createSegmentRecorder();
    state.timerHandle = setInterval(updateTimer, 250);
    setIndicator("listening");
    el.recordingText.textContent = `Listening for ${state.sessionRole} speech`;
    setUiState("Listening");
    renderControls();
  } catch (_) {
    stopLiveSession({ label: "Microphone unavailable" });
    showError("MIC_PERMISSION_DENIED", "Microphone access is unavailable. Enable it and retry.", true);
  }
}

async function apiJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail || {};
    throw Object.assign(new Error(detail.message || "Request failed."), {
      code: detail.code || "REQUEST_FAILED",
      retryable: Boolean(detail.retryable),
    });
  }
  return payload;
}

function enqueueRecordedTurn(blob, segment) {
  const sessionAtQueue = state.sessionId;
  state.queuedTurns += 1;
  renderControls();
  state.analysisQueue = state.analysisQueue
    .then(async () => {
      if (state.sessionId !== sessionAtQueue) return;
      await transcribeAndAnalyze(blob, segment, sessionAtQueue);
    })
    .catch((error) => {
      if (error.name !== "AbortError") showError(error.code, error.message, error.retryable);
    })
    .finally(() => {
      state.queuedTurns = Math.max(0, state.queuedTurns - 1);
      renderControls();
      if (state.liveSession && !state.captureSuppressed) {
        setIndicator("listening");
        el.recordingText.textContent = `Listening for ${state.sessionRole} speech`;
        setUiState("Listening");
      }
    });
}

async function transcribeAndAnalyze(blob, segment, sessionAtStart) {
  setUiState("Transcribing");
  const form = new FormData();
  const extension = blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm";
  form.append("file", blob, `recording.${extension}`);
  form.append("turn_id", segment.turnId);
  form.append("duration_ms", String(segment.durationMs));
  const transcript = await apiJson("/api/audio/transcribe", { method: "POST", body: form });
  if (state.sessionId !== sessionAtStart) return;
  await addTurnAndAnalyze({
    turn_id: transcript.turn_id,
    speaker: segment.speaker,
    text: transcript.transcript,
    timestamp_ms: segment.timestampMs,
  });
}

async function analyzeTypedTurn() {
  const text = el.textFallback.value.trim();
  if (!text) return showError("TEXT_EMPTY", "Enter one complete speaker turn.", false);
  const turn = {
    turn_id: newId("turn"),
    speaker: el.speakerSelect.value,
    text,
    timestamp_ms: Date.now() - state.sessionStartedAt,
  };
  el.textFallback.value = "";
  state.queuedTurns += 1;
  renderControls();
  try {
    await state.analysisQueue;
    await addTurnAndAnalyze(turn);
  } catch (error) {
    showError(error.code, error.message, error.retryable);
  } finally {
    state.queuedTurns = Math.max(0, state.queuedTurns - 1);
    renderControls();
  }
}

function renderTurns() {
  el.turnList.innerHTML = "";
  state.turns.forEach((turn) => {
    const item = document.createElement("li");
    const role = document.createElement("span");
    role.className = "turn-role";
    role.textContent = turn.speaker;
    const text = document.createElement("div");
    text.textContent = turn.text;
    item.append(role, text);
    el.turnList.append(item);
  });
}

async function addTurnAndAnalyze(turn) {
  const sessionAtStart = state.sessionId;
  state.turns.push(turn);
  renderTurns();
  setUiState("Analyzing");
  el.analysisSummary.textContent = "Checking the finalized session turns...";
  el.errorPanel.classList.add("hidden");
  const controller = new AbortController();
  state.activeAbortController = controller;
  let result;
  try {
    result = await apiJson("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionAtStart, turns: state.turns }),
      signal: controller.signal,
    });
  } finally {
    if (state.activeAbortController === controller) state.activeAbortController = null;
  }
  if (state.sessionId !== sessionAtStart) return;
  await renderAnalysis(result, sessionAtStart);
}

async function renderAnalysis(result, sessionAtStart) {
  const metadata = [];
  if (result.detection_source) metadata.push(`Detection source: ${result.detection_source}`);
  if (result.technical_warning) metadata.push(result.technical_warning);
  el.analysisMetadata.textContent = metadata.join(" — ");
  el.analysisMetadata.classList.toggle("hidden", metadata.length === 0);
  if (result.status === "insufficient_context") {
    el.analysisSummary.textContent = "Insufficient context. No audio alert was generated.";
    setUiState("Insufficient context");
    return;
  }
  if (!result.concerns.length) {
    el.analysisSummary.textContent = "No concern detected. No audio alert was generated.";
    el.concernList.innerHTML = '<div class="success-empty">No concern detected in the submitted turns.</div>';
    setUiState("No concern detected");
    return;
  }

  const newConcerns = result.concerns.filter((concern) => !state.seenConcernIds.has(concern.concern_id));
  el.analysisSummary.textContent = `${result.concerns.length} concern(s) returned; ${newConcerns.length} new.`;
  el.concernList.innerHTML = "";
  result.concerns.forEach((concern) => {
    const card = document.createElement("article");
    card.className = "concern";
    const title = document.createElement("h3");
    title.textContent = concern.category.replaceAll("_", " ");
    const explanation = document.createElement("p");
    explanation.textContent = concern.explanation;
    card.append(title, explanation);
    concern.evidence.forEach((evidence) => {
      const quote = document.createElement("blockquote");
      quote.className = "evidence";
      quote.textContent = `“${evidence.quote}” — ${evidence.speaker}, ${evidence.turn_id}`;
      card.append(quote);
    });
    el.concernList.append(card);
  });

  for (const concern of newConcerns) {
    if (state.sessionId !== sessionAtStart) return;
    state.seenConcernIds.add(concern.concern_id);
    await speakConcern(concern, sessionAtStart);
  }
  setUiState("Review required");
}

function suspendCaptureForAlert() {
  if (!state.liveSession) return;
  state.captureSuppressed = true;
  resetVadState();
  stopCurrentSegment("discard");
  setIndicator("paused");
  el.recordingText.textContent = "Capture paused during spoken warning";
  renderControls();
}

async function resumeCaptureAfterAlert(sessionAtStart) {
  await new Promise((resolve) => setTimeout(resolve, VAD.alertRecoveryMs));
  if (!state.liveSession || state.sessionId !== sessionAtStart) return;
  state.captureSuppressed = false;
  resetVadState();
  createSegmentRecorder();
  setIndicator("listening");
  el.recordingText.textContent = `Listening for ${state.sessionRole} speech`;
  setUiState("Listening");
  renderControls();
}

async function speakConcern(concern, sessionAtStart) {
  suspendCaptureForAlert();
  setUiState("Synthesizing alert");
  try {
    const response = await fetch("/api/audio/synthesize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ concern_id: concern.concern_id, text: concern.alert_text, voice: "default" }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const detail = payload.detail || {};
      throw Object.assign(new Error(detail.message || "Audio alert is unavailable."), {
        code: detail.code || "TTS_UNAVAILABLE",
        retryable: Boolean(detail.retryable),
      });
    }
    const blob = await response.blob();
    if (state.sessionId !== sessionAtStart) return;
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.dataset.url = url;
    state.activeAudio = audio;
    el.stopAudioButton.disabled = false;
    setUiState("Speaking alert / capture paused");
    await audio.play();
    await new Promise((resolve) => {
      state.activeAudioResolver = resolve;
      audio.addEventListener("ended", resolve, { once: true });
      audio.addEventListener("error", resolve, { once: true });
    });
    stopCurrentAudio();
  } catch (error) {
    showAudioError(concern, error, sessionAtStart);
  } finally {
    await resumeCaptureAfterAlert(sessionAtStart);
  }
}

function showAudioError(concern, error, sessionAtStart) {
  el.errorPanel.classList.remove("hidden");
  el.errorPanel.innerHTML = "";
  const message = document.createElement("span");
  message.textContent = `${error.message} The validated warning remains visible. `;
  const retry = document.createElement("button");
  retry.className = "secondary";
  retry.textContent = "Retry audio only";
  retry.addEventListener("click", async () => {
    retry.disabled = true;
    el.errorPanel.classList.add("hidden");
    await speakConcern(concern, sessionAtStart);
  });
  el.errorPanel.append(message, retry);
  el.analysisSummary.textContent = "Concern detected. Spoken alert unavailable; analysis was not rerun.";
  setUiState("Warning ready / audio unavailable");
}

function showError(code, message, retryable) {
  el.errorPanel.classList.remove("hidden");
  el.errorPanel.textContent = `${message}${retryable ? " You can retry." : ""} (${code})`;
  el.analysisSummary.textContent = "Analysis or audio unavailable. No risk conclusion was inferred.";
  setUiState("Unavailable");
}

async function loadHealth() {
  try {
    const health = await apiJson("/api/health");
    el.providerLabel.textContent = `Audio: ${health.audio_provider_mode} / Analysis: ${health.analysis_provider_mode}`;
  } catch (_) {
    el.providerLabel.textContent = "Backend unavailable";
  }
}

el.recordButton.addEventListener("click", startLiveSession);
el.stopButton.addEventListener("click", () => stopLiveSession({ label: "Live session stopped" }));
el.cancelButton.addEventListener("click", () => finalizeCurrentTurn("Manually finalized", true));
el.analyzeTextButton.addEventListener("click", analyzeTypedTurn);
el.resetButton.addEventListener("click", resetSession);
el.stopAudioButton.addEventListener("click", stopCurrentAudio);

if (!window.isSecureContext) {
  el.securityWarning.textContent = "Microphone recording requires HTTPS or localhost. Typed fallback remains available.";
  el.securityWarning.classList.remove("hidden");
}
if (!navigator.mediaDevices || !window.MediaRecorder || !AudioContextClass) {
  el.securityWarning.textContent = "This browser cannot run continuous audio capture. Use the typed fallback.";
  el.securityWarning.classList.remove("hidden");
}

resetSession();
loadHealth();
