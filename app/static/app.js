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
const ARCHIVE_STORAGE_KEY = "steelhacks.interrogationArchives.v1";

function emptyCapture(role, meterId) {
  return {
    role,
    meterId,
    mediaStream: null,
    currentSegment: null,
    audioSource: null,
    analyser: null,
    waveform: null,
    speechActive: false,
    speechStartedAt: null,
    speechLoudMs: 0,
    silenceStartedAt: null,
  };
}

const state = {
  sessionId: null,
  sessionStartedAt: null,
  turns: [],
  concerns: [],
  alertTimeline: [],
  analysisMetadata: null,
  seenConcernIds: new Set(),
  captures: {
    suspect: emptyCapture("suspect", "suspectMeterFill"),
    officer: emptyCapture("officer", "officerMeterFill"),
  },
  audioContext: null,
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
  deviceChoicesInitialized: false,
};

const el = Object.fromEntries(
  [
    "resetButton", "stateLabel", "providerLabel", "securityWarning",
    "speakerSelect", "suspectDeviceSelect", "officerDeviceSelect", "refreshDevicesButton",
    "deviceStatus", "recordButton", "stopButton", "cancelButton",
    "recordingIndicator", "recordingText", "timer", "suspectMeterFill", "officerMeterFill",
    "textFallback", "analyzeTextButton", "sessionId", "turnList",
    "analysisSummary", "analysisMetadata", "errorPanel", "concernList", "stopAudioButton",
    "navToggle", "sideNav", "navBackdrop", "archiveSearch", "archiveSessionSelect",
    "downloadJsonButton", "downloadCsvButton", "archiveEmpty", "archiveDisplay",
    "archiveTitle", "archiveDate", "archiveTurnCount", "archiveConcernCount",
    "archiveTranscript", "archiveAlerts", "saveSessionDialog", "sessionNameInput",
    "saveSessionError", "saveSessionButton", "dontSaveSessionButton", "toast",
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
  const devicesChosen = Boolean(el.suspectDeviceSelect.value && el.officerDeviceSelect.value);
  el.recordButton.disabled = state.liveSession || !supported || !devicesChosen;
  el.stopButton.disabled = !state.liveSession;
  el.cancelButton.disabled = !state.liveSession || state.captureSuppressed;
  el.suspectDeviceSelect.disabled = state.liveSession;
  el.officerDeviceSelect.disabled = state.liveSession;
  el.refreshDevicesButton.disabled = state.liveSession;
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

function resetVadState(capture) {
  capture.speechActive = false;
  capture.speechStartedAt = null;
  capture.speechLoudMs = 0;
  capture.silenceStartedAt = null;
  el[capture.meterId].style.width = "0%";
}

function stopMediaTracks() {
  Object.values(state.captures).forEach((capture) => {
    capture.mediaStream?.getTracks().forEach((track) => track.stop());
    capture.mediaStream = null;
  });
}

function teardownAudioAnalysis() {
  clearInterval(state.vadHandle);
  state.vadHandle = null;
  Object.values(state.captures).forEach((capture) => {
    capture.audioSource?.disconnect();
    capture.audioSource = null;
    capture.analyser = null;
    capture.waveform = null;
  });
  if (state.audioContext && state.audioContext.state !== "closed") {
    state.audioContext.close().catch(() => {});
  }
  state.audioContext = null;
}

function stopCurrentSegment(capture, mode = "discard") {
  const segment = capture.currentSegment;
  if (!segment || segment.recorder.state === "inactive") return;
  segment.stopMode = mode;
  segment.durationMs = Date.now() - segment.startedAt;
  segment.recorder.stop();
}

function stopLiveSession({ label = "Stopped", preserveAudio = false } = {}) {
  state.liveSession = false;
  state.captureSuppressed = false;
  Object.values(state.captures).forEach((capture) => stopCurrentSegment(capture, "discard"));
  teardownAudioAnalysis();
  stopMediaTracks();
  clearInterval(state.timerHandle);
  state.timerHandle = null;
  if (!preserveAudio) stopCurrentAudio();
  Object.values(state.captures).forEach(resetVadState);
  setIndicator(null);
  el.recordingText.textContent = "Ready to start";
  el.timer.textContent = "00:00";
  updateSelectedDeviceStatus();
  renderControls();
  setUiState(label);
}

function resetSession() {
  state.activeAbortController?.abort();
  state.activeAbortController = null;
  stopLiveSession({ label: "Idle" });
  state.sessionId = newId("session");
  state.sessionStartedAt = Date.now();
  state.turns = [];
  state.concerns = [];
  state.alertTimeline = [];
  state.analysisMetadata = null;
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

function createSegmentRecorder(capture) {
  if (!state.liveSession || state.captureSuppressed || !capture.mediaStream || capture.currentSegment) return;
  const mimeType = preferredMimeType();
  const recorder = mimeType
    ? new MediaRecorder(capture.mediaStream, { mimeType })
    : new MediaRecorder(capture.mediaStream);
  const segment = {
    recorder,
    chunks: [],
    startedAt: Date.now(),
    stopMode: "discard",
    durationMs: 0,
    turnId: newId("turn"),
    speaker: capture.role,
    timestampMs: Date.now() - state.sessionStartedAt,
  };
  capture.currentSegment = segment;
  recorder.addEventListener("dataavailable", (event) => {
    if (event.data.size > 0) segment.chunks.push(event.data);
  });
  recorder.addEventListener("stop", () => handleStoppedSegment(capture, segment));
  recorder.start(250);
}

function handleStoppedSegment(capture, segment) {
  if (capture.currentSegment === segment) capture.currentSegment = null;
  const type = segment.recorder.mimeType || segment.chunks[0]?.type || "audio/webm";
  const blob = new Blob(segment.chunks, { type });
  if (state.liveSession && !state.captureSuppressed) createSegmentRecorder(capture);
  if (segment.stopMode === "finalize" && blob.size > 0) {
    enqueueRecordedTurn(blob, segment);
  }
}

function setupAudioAnalysis() {
  state.audioContext = new AudioContextClass();
  Object.values(state.captures).forEach((capture) => {
    capture.audioSource = state.audioContext.createMediaStreamSource(capture.mediaStream);
    capture.analyser = state.audioContext.createAnalyser();
    capture.analyser.fftSize = 2048;
    capture.waveform = new Float32Array(capture.analyser.fftSize);
    capture.audioSource.connect(capture.analyser);
  });
  state.vadHandle = setInterval(sampleVoiceActivity, VAD.sampleEveryMs);
}

function rootMeanSquare(values) {
  let total = 0;
  for (const value of values) total += value * value;
  return Math.sqrt(total / values.length);
}

function capitalize(value) {
  return `${value.charAt(0).toUpperCase()}${value.slice(1)}`;
}

function sampleVoiceActivity() {
  if (!state.liveSession || state.captureSuppressed) return;
  Object.values(state.captures).forEach(sampleCaptureVoiceActivity);
}

function sampleCaptureVoiceActivity(capture) {
  if (!capture.analyser) return;
  capture.analyser.getFloatTimeDomainData(capture.waveform);
  const volume = rootMeanSquare(capture.waveform);
  el[capture.meterId].style.width = `${Math.min(100, Math.round(volume * 900))}%`;
  const now = Date.now();
  const segmentAge = capture.currentSegment ? now - capture.currentSegment.startedAt : 0;

  if (!capture.speechActive && volume >= VAD.speechThreshold) {
    capture.speechActive = true;
    capture.speechStartedAt = now;
    capture.speechLoudMs = VAD.sampleEveryMs;
    capture.silenceStartedAt = null;
    if (capture.currentSegment) {
      capture.currentSegment.timestampMs = now - state.sessionStartedAt;
    }
    setIndicator("live");
    el.recordingText.textContent = `${capitalize(capture.role)} speech detected`;
    setUiState(`Listening / ${capture.role} speech detected`);
    return;
  }

  if (capture.speechActive) {
    if (volume > VAD.silenceThreshold) {
      capture.speechLoudMs += VAD.sampleEveryMs;
    }
    if (volume <= VAD.silenceThreshold) {
      capture.silenceStartedAt ??= now;
      const silenceMs = now - capture.silenceStartedAt;
      if (
        silenceMs >= VAD.endOfTurnSilenceMs
        && capture.speechLoudMs >= VAD.minimumSpeechMs
      ) {
        finalizeCurrentTurn(capture, "Pause detected");
        return;
      }
    } else {
      capture.silenceStartedAt = null;
    }
    if (segmentAge >= VAD.maximumSegmentMs) {
      finalizeCurrentTurn(capture, "Maximum turn length reached");
    }
  } else if (segmentAge >= VAD.idleRotationMs) {
    stopCurrentSegment(capture, "discard");
  }
}

function finalizeCurrentTurn(capture, reason = "Turn finalized", force = false) {
  if (!state.liveSession || state.captureSuppressed || !capture.currentSegment) return;
  const hadSpeech = capture.speechActive;
  resetVadState(capture);
  if (!hadSpeech && !force) {
    el.recordingText.textContent = "Listening for speech";
    return;
  }
  setIndicator("listening");
  el.recordingText.textContent = `${reason}; continuing to listen`;
  setUiState("Turn queued for transcription");
  stopCurrentSegment(capture, "finalize");
}

function audioConstraints(deviceId) {
  return {
    deviceId: { exact: deviceId },
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
    channelCount: 1,
  };
}

function displayDeviceName(device, index) {
  return device.label || `Microphone ${index + 1} (permission required for its name)`;
}

function pickSuggestedDevice(devices, role, otherDeviceId = "") {
  const preferredPattern = role === "suspect"
    ? /\biphone\b|\bphone\b|continuity/i
    : /macbook|built-in|internal/i;
  const preferred = devices.find((device) => {
    return device.deviceId !== otherDeviceId && preferredPattern.test(device.label);
  });
  const physicalFallback = role === "suspect"
    ? [...devices].reverse().find((device) => !/virtual/i.test(device.label) && device.deviceId !== otherDeviceId)
    : devices.find((device) => !/virtual/i.test(device.label) && device.deviceId !== otherDeviceId);
  return preferred
    || physicalFallback
    || devices.find((device) => device.deviceId !== otherDeviceId)
    || devices[0];
}

function populateDeviceSelect(select, devices, previousValue, suggestedValue) {
  select.innerHTML = "";
  devices.forEach((device, index) => {
    const option = document.createElement("option");
    option.value = device.deviceId;
    option.textContent = displayDeviceName(device, index);
    select.append(option);
  });
  if (devices.some((device) => device.deviceId === previousValue)) {
    select.value = previousValue;
  } else if (suggestedValue) {
    const suggestedIndex = devices.findIndex((device) => device.deviceId === suggestedValue);
    select.selectedIndex = suggestedIndex >= 0 ? suggestedIndex : 0;
  }
}

function selectRecommendedMappings() {
  const suspectOptions = [...el.suspectDeviceSelect.options];
  const officerOptions = [...el.officerDeviceSelect.options];
  const suspectIndex = suspectOptions.findIndex((option) => /\biphone\b|\bphone\b|continuity/i.test(option.textContent));
  const officerIndex = officerOptions.findIndex((option) => /macbook|built-in|internal/i.test(option.textContent));
  if (suspectIndex >= 0) el.suspectDeviceSelect.selectedIndex = suspectIndex;
  if (officerIndex >= 0) el.officerDeviceSelect.selectedIndex = officerIndex;
}

function updateSelectedDeviceStatus(prefix = "Selected mapping") {
  const suspectName = el.suspectDeviceSelect.selectedOptions[0]?.textContent;
  const officerName = el.officerDeviceSelect.selectedOptions[0]?.textContent;
  if (suspectName && officerName) {
    el.deviceStatus.textContent = `${prefix}: ${suspectName} = Suspect; ${officerName} = Officer.`;
  }
}

async function refreshMicrophoneDevices({ requestPermission = false, useRecommended = false } = {}) {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  let permissionStream = null;
  try {
    if (requestPermission) {
      permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    }
    const devices = (await navigator.mediaDevices.enumerateDevices())
      .filter((device) => device.kind === "audioinput" && device.deviceId !== "default");
    const microphones = devices.length
      ? devices
      : (await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === "audioinput");
    const preserveChoices = state.deviceChoicesInitialized && !useRecommended;
    const previousSuspect = preserveChoices ? el.suspectDeviceSelect.value : "";
    const previousOfficer = preserveChoices ? el.officerDeviceSelect.value : "";
    const suspectSuggestion = pickSuggestedDevice(microphones, "suspect");
    const officerSuggestion = pickSuggestedDevice(microphones, "officer", suspectSuggestion?.deviceId);
    populateDeviceSelect(el.suspectDeviceSelect, microphones, previousSuspect, suspectSuggestion?.deviceId);
    populateDeviceSelect(el.officerDeviceSelect, microphones, previousOfficer, officerSuggestion?.deviceId);
    if (useRecommended) selectRecommendedMappings();
    state.deviceChoicesInitialized = true;
    const named = microphones.filter((device) => device.label).length;
    if (microphones.length < 2) {
      el.deviceStatus.textContent = "Only one microphone is visible. Connect the iPhone, then press Find microphones again.";
    } else if (!named) {
      el.deviceStatus.textContent = "Microphones found. Press Find microphones and allow access to reveal their names.";
    } else {
      updateSelectedDeviceStatus(`${microphones.length} microphones found. Current mapping`);
    }
  } catch (_) {
    el.deviceStatus.textContent = "Microphone permission was not granted. Allow access, then try again.";
  } finally {
    permissionStream?.getTracks().forEach((track) => track.stop());
    renderControls();
  }
}

async function openRoleMicrophone(role, deviceId) {
  const capture = state.captures[role];
  capture.mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: audioConstraints(deviceId),
  });
  const [track] = capture.mediaStream.getAudioTracks();
  return track?.label || `${capitalize(role)} microphone`;
}

async function startLiveSession() {
  if (!window.isSecureContext) {
    showError("MIC_REQUIRES_SECURE_CONTEXT", "Use HTTPS or localhost for microphone access.", false);
    return;
  }
  if (state.liveSession) return;
  stopCurrentAudio();
  el.errorPanel.classList.add("hidden");
  const suspectDeviceId = el.suspectDeviceSelect.value;
  const officerDeviceId = el.officerDeviceSelect.value;
  if (!suspectDeviceId || !officerDeviceId) {
    showError("MIC_SELECTION_REQUIRED", "Choose both microphones first.", false);
    return;
  }
  if (suspectDeviceId === officerDeviceId) {
    showError("MIC_SELECTION_DUPLICATE", "Choose two different microphones so each person has a separate channel.", false);
    return;
  }
  try {
    const suspectLabel = await openRoleMicrophone("suspect", suspectDeviceId);
    const officerLabel = await openRoleMicrophone("officer", officerDeviceId);
    state.sessionStartedAt = Date.now();
    state.liveSession = true;
    state.captureSuppressed = false;
    Object.values(state.captures).forEach(resetVadState);
    setupAudioAnalysis();
    await state.audioContext.resume();
    Object.values(state.captures).forEach(createSegmentRecorder);
    state.timerHandle = setInterval(updateTimer, 250);
    setIndicator("listening");
    el.recordingText.textContent = "Listening to Suspect and Officer";
    el.deviceStatus.textContent = `Live: ${suspectLabel} = Suspect; ${officerLabel} = Officer.`;
    setUiState("Listening");
    renderControls();
  } catch (error) {
    stopLiveSession({ label: "Microphone unavailable" });
    const message = error.name === "NotReadableError"
      ? "The browser could not open both microphones at once. Close other audio apps or create a macOS Aggregate Device, then retry."
      : "One of the selected microphones is unavailable. Check permission and the two selections, then retry.";
    showError(error.name || "MIC_PERMISSION_DENIED", message, true);
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
        el.recordingText.textContent = "Listening to Suspect and Officer";
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
  const concernMap = new Map(state.concerns.map((concern) => [concern.concern_id, concern]));
  result.concerns.forEach((concern) => concernMap.set(concern.concern_id, structuredClone(concern)));
  state.concerns = [...concernMap.values()];
  state.analysisMetadata = {
    detection_source: result.detection_source || null,
    technical_warning: result.technical_warning || null,
  };
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
    state.alertTimeline.push({
      timestamp_ms: Math.max(0, Date.now() - state.sessionStartedAt),
      concern: structuredClone(concern),
    });
    await speakConcern(concern, sessionAtStart);
  }
  setUiState("Review required");
}

function suspendCaptureForAlert() {
  if (!state.liveSession) return;
  state.captureSuppressed = true;
  Object.values(state.captures).forEach((capture) => {
    resetVadState(capture);
    stopCurrentSegment(capture, "discard");
  });
  setIndicator("paused");
  el.recordingText.textContent = "Capture paused during spoken warning";
  renderControls();
}

async function resumeCaptureAfterAlert(sessionAtStart) {
  await new Promise((resolve) => setTimeout(resolve, VAD.alertRecoveryMs));
  if (!state.liveSession || state.sessionId !== sessionAtStart) return;
  state.captureSuppressed = false;
  Object.values(state.captures).forEach((capture) => {
    resetVadState(capture);
    createSegmentRecorder(capture);
  });
  setIndicator("listening");
  el.recordingText.textContent = "Listening to Suspect and Officer";
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

function navigateToPage(pageName) {
  document.querySelectorAll(".app-page").forEach((page) => {
    page.classList.toggle("active", page.dataset.page === pageName);
  });
  document.querySelectorAll(".nav-link").forEach((link) => {
    link.classList.toggle("active", link.dataset.page === pageName);
  });
  if (pageName === "archives") renderArchivePage();
  history.replaceState(null, "", `#${pageName}`);
  closeSideNav();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function toggleSideNav() {
  const open = !el.sideNav.classList.contains("open");
  el.sideNav.classList.toggle("open", open);
  el.navBackdrop.classList.toggle("hidden", !open);
  el.navToggle.setAttribute("aria-expanded", String(open));
  el.navToggle.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
}

function closeSideNav() {
  el.sideNav.classList.remove("open");
  el.navBackdrop.classList.add("hidden");
  el.navToggle.setAttribute("aria-expanded", "false");
  el.navToggle.setAttribute("aria-label", "Open navigation");
}

function loadArchives() {
  try {
    const value = JSON.parse(localStorage.getItem(ARCHIVE_STORAGE_KEY) || "[]");
    return Array.isArray(value) ? value : [];
  } catch (_) {
    return [];
  }
}

function storeArchives(archives) {
  localStorage.setItem(ARCHIVE_STORAGE_KEY, JSON.stringify(archives));
}

function formatElapsed(milliseconds) {
  const totalSeconds = Math.max(0, Math.floor((milliseconds || 0) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function concernTurnIds(session) {
  return new Set(session.concerns.flatMap((concern) => concern.evidence.map((item) => item.turn_id)));
}

function concernsForTurn(session, turnId) {
  return session.concerns.filter((concern) => concern.evidence.some((item) => item.turn_id === turnId));
}

function selectedArchive() {
  return loadArchives().find((archive) => archive.archive_id === el.archiveSessionSelect.value) || null;
}

function appendHighlightedText(container, value, query) {
  if (!query) {
    container.textContent = value;
    return;
  }
  const lowerValue = value.toLocaleLowerCase();
  const lowerQuery = query.toLocaleLowerCase();
  let start = 0;
  let index = lowerValue.indexOf(lowerQuery);
  while (index >= 0) {
    container.append(document.createTextNode(value.slice(start, index)));
    const mark = document.createElement("mark");
    mark.className = "search-highlight";
    mark.textContent = value.slice(index, index + query.length);
    container.append(mark);
    start = index + query.length;
    index = lowerValue.indexOf(lowerQuery, start);
  }
  container.append(document.createTextNode(value.slice(start)));
}

function renderArchiveTranscript(session, query, concernOnly) {
  el.archiveTranscript.innerHTML = "";
  const flaggedTurnIds = concernTurnIds(session);
  const visibleTurns = session.turns.filter((turn) => {
    const related = concernsForTurn(session, turn.turn_id);
    if (concernOnly && !flaggedTurnIds.has(turn.turn_id)) return false;
    if (!query) return true;
    const searchText = [turn.text, turn.speaker, ...related.flatMap((concern) => [concern.category, concern.explanation])].join(" ").toLocaleLowerCase();
    return searchText.includes(query.toLocaleLowerCase());
  });
  if (!visibleTurns.length) {
    el.archiveTranscript.innerHTML = '<li class="empty-state">No transcript lines match the current search and filter.</li>';
    return;
  }
  visibleTurns.forEach((turn) => {
    const item = document.createElement("li");
    if (flaggedTurnIds.has(turn.turn_id)) item.classList.add("concern-line");
    const role = document.createElement("span");
    role.className = "turn-role";
    role.textContent = turn.speaker;
    const time = document.createElement("span");
    time.className = "turn-time";
    time.textContent = formatElapsed(turn.timestamp_ms);
    const text = document.createElement("div");
    appendHighlightedText(text, turn.text, query);
    item.append(role, time, text);
    const categories = concernsForTurn(session, turn.turn_id).map((concern) => concern.category.replaceAll("_", " "));
    if (categories.length) {
      const badge = document.createElement("small");
      badge.textContent = `Concern: ${categories.join(", ")}`;
      item.append(badge);
    }
    el.archiveTranscript.append(item);
  });
}

function renderArchiveAlerts(session, query) {
  el.archiveAlerts.innerHTML = "";
  const alerts = session.alert_timeline.filter((entry) => {
    if (!query) return true;
    const concern = entry.concern;
    return [concern.category, concern.explanation, concern.alert_text]
      .join(" ").toLocaleLowerCase().includes(query.toLocaleLowerCase());
  });
  if (!alerts.length) {
    el.archiveAlerts.innerHTML = '<p class="empty-state">No alerts match the current search.</p>';
    return;
  }
  alerts.forEach((entry) => {
    const item = document.createElement("article");
    item.className = "archive-alert";
    const time = document.createElement("time");
    time.textContent = formatElapsed(entry.timestamp_ms);
    const title = document.createElement("strong");
    title.textContent = entry.concern.category.replaceAll("_", " ");
    const explanation = document.createElement("p");
    appendHighlightedText(explanation, entry.concern.explanation, query);
    item.append(time, title, explanation);
    el.archiveAlerts.append(item);
  });
}

function renderSelectedArchive() {
  const session = selectedArchive();
  const available = Boolean(session);
  el.archiveEmpty.classList.toggle("hidden", available);
  el.archiveDisplay.classList.toggle("hidden", !available);
  el.downloadJsonButton.disabled = !available;
  el.downloadCsvButton.disabled = !available;
  if (!session) return;
  el.archiveTitle.textContent = session.name;
  el.archiveDate.textContent = new Date(session.saved_at).toLocaleString();
  el.archiveTurnCount.textContent = String(session.turns.length);
  el.archiveConcernCount.textContent = String(session.concerns.length);
  const query = el.archiveSearch.value.trim();
  const concernOnly = document.querySelector('input[name="archiveLineFilter"]:checked')?.value === "concerns";
  renderArchiveTranscript(session, query, concernOnly);
  renderArchiveAlerts(session, query);
}

function renderArchivePage(preferredId = "") {
  const archives = loadArchives().sort((a, b) => new Date(b.saved_at) - new Date(a.saved_at));
  const previousId = preferredId || el.archiveSessionSelect.value;
  el.archiveSessionSelect.innerHTML = "";
  archives.forEach((archive) => {
    const option = document.createElement("option");
    option.value = archive.archive_id;
    option.textContent = `${archive.name} — ${new Date(archive.saved_at).toLocaleDateString()}`;
    el.archiveSessionSelect.append(option);
  });
  if (!archives.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No saved sessions";
    el.archiveSessionSelect.append(option);
  } else if (archives.some((archive) => archive.archive_id === previousId)) {
    el.archiveSessionSelect.value = previousId;
  }
  renderSelectedArchive();
}

function showToast(message) {
  el.toast.textContent = message;
  el.toast.classList.remove("hidden");
  setTimeout(() => el.toast.classList.add("hidden"), 2800);
}

async function stopAndOfferArchive() {
  Object.values(state.captures).forEach((capture) => {
    stopCurrentSegment(capture, capture.speechActive ? "finalize" : "discard");
  });
  stopLiveSession({ label: "Finalizing session" });
  await new Promise((resolve) => setTimeout(resolve, 350));
  await state.analysisQueue;
  setUiState("Live session stopped");
  const nextNumber = loadArchives().length + 1;
  el.sessionNameInput.value = `Session #${nextNumber}`;
  el.saveSessionError.classList.add("hidden");
  el.saveSessionDialog.showModal();
  el.sessionNameInput.select();
}

function saveCurrentSession() {
  const name = el.sessionNameInput.value.trim();
  if (!name) {
    el.saveSessionError.classList.remove("hidden");
    el.sessionNameInput.focus();
    return;
  }
  const archive = {
    schema_version: 1,
    archive_id: newId("archive"),
    name,
    session_id: state.sessionId,
    started_at: new Date(state.sessionStartedAt).toISOString(),
    saved_at: new Date().toISOString(),
    turns: structuredClone(state.turns),
    concerns: structuredClone(state.concerns),
    alert_timeline: structuredClone(state.alertTimeline),
    analysis_metadata: structuredClone(state.analysisMetadata),
  };
  try {
    const archives = loadArchives();
    archives.push(archive);
    storeArchives(archives);
    el.saveSessionDialog.close();
    resetSession();
    renderArchivePage(archive.archive_id);
    showToast(`Saved “${name}” to Past Interrogation Archives.`);
  } catch (_) {
    el.saveSessionError.textContent = "This browser could not save the session. Download it or clear browser storage and retry.";
    el.saveSessionError.classList.remove("hidden");
  }
}

function safeFilename(value) {
  return value.trim().replace(/[^a-z0-9_-]+/gi, "-").replace(/^-|-$/g, "").slice(0, 80) || "interrogation-session";
}

function downloadFile(filename, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 500);
}

function csvCell(value) {
  let text = value == null ? "" : String(value);
  if (/^[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

function sessionCsv(session) {
  const columns = ["record_type", "timestamp_ms", "speaker", "turn_id", "text", "concern_id", "concern_type", "explanation", "alert_text", "evidence_turn_ids", "evidence_quotes"];
  const rows = [columns];
  session.turns.forEach((turn) => rows.push(["transcript", turn.timestamp_ms, turn.speaker, turn.turn_id, turn.text, "", "", "", "", "", ""]));
  session.alert_timeline.forEach((entry) => rows.push([
    "alert", entry.timestamp_ms, "", "", "", entry.concern.concern_id,
    entry.concern.category, entry.concern.explanation, entry.concern.alert_text,
    entry.concern.evidence.map((item) => item.turn_id).join(" | "),
    entry.concern.evidence.map((item) => item.quote).join(" | "),
  ]));
  return rows.map((row) => row.map(csvCell).join(",")).join("\n");
}

function downloadSelectedArchive(format) {
  const session = selectedArchive();
  if (!session) return;
  const base = safeFilename(session.name);
  if (format === "json") {
    downloadFile(`${base}.json`, JSON.stringify(session, null, 2), "application/json");
  } else {
    downloadFile(`${base}.csv`, sessionCsv(session), "text/csv;charset=utf-8");
  }
}

el.recordButton.addEventListener("click", startLiveSession);
el.stopButton.addEventListener("click", stopAndOfferArchive);
el.cancelButton.addEventListener("click", () => {
  Object.values(state.captures).forEach((capture) => finalizeCurrentTurn(capture, "Manually finalized", true));
});
el.analyzeTextButton.addEventListener("click", analyzeTypedTurn);
el.resetButton.addEventListener("click", resetSession);
el.stopAudioButton.addEventListener("click", stopCurrentAudio);
el.refreshDevicesButton.addEventListener("click", () => refreshMicrophoneDevices({
  requestPermission: true,
  useRecommended: true,
}));
el.suspectDeviceSelect.addEventListener("change", () => {
  updateSelectedDeviceStatus();
  renderControls();
});
el.officerDeviceSelect.addEventListener("change", () => {
  updateSelectedDeviceStatus();
  renderControls();
});
navigator.mediaDevices?.addEventListener?.("devicechange", () => refreshMicrophoneDevices());
document.querySelectorAll(".nav-link").forEach((link) => {
  link.addEventListener("click", () => navigateToPage(link.dataset.page));
});
document.querySelectorAll("[data-go-page]").forEach((button) => {
  button.addEventListener("click", () => navigateToPage(button.dataset.goPage));
});
el.navToggle.addEventListener("click", toggleSideNav);
el.navBackdrop.addEventListener("click", closeSideNav);
el.archiveSessionSelect.addEventListener("change", renderSelectedArchive);
el.archiveSearch.addEventListener("input", renderSelectedArchive);
document.querySelectorAll('input[name="archiveLineFilter"]').forEach((radio) => {
  radio.addEventListener("change", renderSelectedArchive);
});
el.downloadJsonButton.addEventListener("click", () => downloadSelectedArchive("json"));
el.downloadCsvButton.addEventListener("click", () => downloadSelectedArchive("csv"));
el.saveSessionButton.addEventListener("click", saveCurrentSession);
el.dontSaveSessionButton.addEventListener("click", () => {
  el.saveSessionDialog.close();
  resetSession();
  showToast("Session was not saved.");
});
el.saveSessionDialog.addEventListener("cancel", () => resetSession());
el.sessionNameInput.addEventListener("input", () => el.saveSessionError.classList.add("hidden"));

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
const initialPage = ["home", "monitor", "archives"].includes(location.hash.slice(1))
  ? location.hash.slice(1)
  : "home";
navigateToPage(initialPage);
window.addEventListener("pageshow", () => {
  setTimeout(() => refreshMicrophoneDevices({ useRecommended: true }), 100);
}, { once: true });
