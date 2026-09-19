const state = {
  sessionId: null,
  turns: [],
  seenConcernIds: new Set(),
  mediaRecorder: null,
  mediaStream: null,
  chunks: [],
  recordingStartedAt: null,
  timerHandle: null,
  activeAbortController: null,
  activeAudio: null,
  busy: false,
  cancelledRecording: false,
};

const el = Object.fromEntries(
  [
    "resetButton", "stateLabel", "providerLabel", "securityWarning",
    "speakerSelect", "recordButton", "stopButton", "cancelButton",
    "recordingIndicator", "recordingText", "timer", "textFallback",
    "analyzeTextButton", "sessionId", "turnList", "analysisSummary",
    "errorPanel", "concernList", "stopAudioButton",
  ].map((id) => [id, document.getElementById(id)])
);

function newId(prefix) {
  const value = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  return `${prefix}-${value}`;
}

function setUiState(label) {
  el.stateLabel.textContent = label;
}

function setBusy(busy) {
  state.busy = busy;
  el.recordButton.disabled = busy || !navigator.mediaDevices;
  el.analyzeTextButton.disabled = busy;
  el.speakerSelect.disabled = busy || Boolean(state.mediaRecorder);
}

function stopCurrentAudio() {
  if (state.activeAudio) {
    state.activeAudio.pause();
    state.activeAudio.currentTime = 0;
    if (state.activeAudio.dataset.url) URL.revokeObjectURL(state.activeAudio.dataset.url);
    state.activeAudio = null;
  }
  el.stopAudioButton.disabled = true;
}

function stopMediaTracks() {
  if (state.mediaStream) state.mediaStream.getTracks().forEach((track) => track.stop());
  state.mediaStream = null;
  state.mediaRecorder = null;
}

function resetSession() {
  state.activeAbortController?.abort();
  state.activeAbortController = null;
  if (state.mediaRecorder?.state === "recording") state.mediaRecorder.stop();
  stopMediaTracks();
  stopCurrentAudio();
  clearInterval(state.timerHandle);
  state.sessionId = newId("session");
  state.turns = [];
  state.seenConcernIds.clear();
  state.busy = false;
  state.cancelledRecording = false;
  el.sessionId.textContent = state.sessionId;
  el.turnList.innerHTML = '<li class="empty-state">No turns recorded yet.</li>';
  el.concernList.innerHTML = "";
  el.errorPanel.classList.add("hidden");
  el.analysisSummary.textContent = "Waiting for a finalized transcript.";
  el.recordingIndicator.classList.remove("live");
  el.recordingText.textContent = "Ready to record";
  el.timer.textContent = "00:00";
  el.stopButton.disabled = true;
  el.cancelButton.disabled = true;
  setBusy(false);
  setUiState("Idle");
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
  const elapsed = Date.now() - state.recordingStartedAt;
  const seconds = Math.floor(elapsed / 1000);
  el.timer.textContent = `00:${String(seconds).padStart(2, "0")}`;
  if (seconds >= 30 && state.mediaRecorder?.state === "recording") stopRecording();
}

async function startRecording() {
  if (!window.isSecureContext) {
    showError("MIC_REQUIRES_SECURE_CONTEXT", "Use HTTPS or localhost for microphone access.", false);
    return;
  }
  stopCurrentAudio();
  state.cancelledRecording = false;
  const turnId = newId("turn");
  try {
    state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = preferredMimeType();
    state.chunks = [];
    state.mediaRecorder = mimeType
      ? new MediaRecorder(state.mediaStream, { mimeType })
      : new MediaRecorder(state.mediaStream);
    state.mediaRecorder.addEventListener("dataavailable", (event) => {
      if (event.data.size > 0) state.chunks.push(event.data);
    });
    state.mediaRecorder.addEventListener("stop", () => finalizeRecording(turnId));
    state.mediaRecorder.start();
    state.recordingStartedAt = Date.now();
    state.timerHandle = setInterval(updateTimer, 250);
    el.recordingIndicator.classList.add("live");
    el.recordingText.textContent = `Recording ${el.speakerSelect.value}`;
    el.recordButton.disabled = true;
    el.stopButton.disabled = false;
    el.cancelButton.disabled = false;
    el.speakerSelect.disabled = true;
    setUiState("Recording");
  } catch (error) {
    stopMediaTracks();
    showError("MIC_PERMISSION_DENIED", "Microphone access is unavailable. Enable it and retry.", true);
  }
}

function stopRecording() {
  if (state.mediaRecorder?.state === "recording") state.mediaRecorder.stop();
  el.stopButton.disabled = true;
  el.cancelButton.disabled = true;
}

function cancelRecording() {
  state.cancelledRecording = true;
  stopRecording();
}

async function finalizeRecording(turnId) {
  clearInterval(state.timerHandle);
  const durationMs = Date.now() - state.recordingStartedAt;
  const mimeType = state.mediaRecorder?.mimeType || state.chunks[0]?.type || "audio/webm";
  const blob = new Blob(state.chunks, { type: mimeType });
  stopMediaTracks();
  el.recordingIndicator.classList.remove("live");
  el.recordingText.textContent = "Ready to record";
  el.speakerSelect.disabled = false;
  if (state.cancelledRecording) {
    setBusy(false);
    setUiState("Cancelled");
    return;
  }
  await transcribeAndAnalyze(blob, turnId, durationMs);
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

async function transcribeAndAnalyze(blob, turnId, durationMs) {
  const sessionAtStart = state.sessionId;
  setBusy(true);
  setUiState("Transcribing");
  const form = new FormData();
  const extension = blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm";
  form.append("file", blob, `recording.${extension}`);
  form.append("turn_id", turnId);
  form.append("duration_ms", String(durationMs));
  try {
    const transcript = await apiJson("/api/audio/transcribe", { method: "POST", body: form });
    if (state.sessionId !== sessionAtStart) return;
    await addTurnAndAnalyze({
      turn_id: transcript.turn_id,
      speaker: el.speakerSelect.value,
      text: transcript.transcript,
      timestamp_ms: null,
    });
  } catch (error) {
    if (error.name !== "AbortError") showError(error.code, error.message, error.retryable);
  } finally {
    if (state.sessionId === sessionAtStart) setBusy(false);
  }
}

async function analyzeTypedTurn() {
  const text = el.textFallback.value.trim();
  if (!text) return showError("TEXT_EMPTY", "Enter one complete speaker turn.", false);
  const turn = {
    turn_id: newId("turn"),
    speaker: el.speakerSelect.value,
    text,
    timestamp_ms: null,
  };
  el.textFallback.value = "";
  setBusy(true);
  try { await addTurnAndAnalyze(turn); }
  catch (error) { showError(error.code, error.message, error.retryable); }
  finally { setBusy(false); }
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

  const newConcerns = result.concerns.filter((c) => !state.seenConcernIds.has(c.concern_id));
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

async function speakConcern(concern, sessionAtStart) {
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
    setUiState("Speaking alert");
    await audio.play();
    await new Promise((resolve) => {
      audio.addEventListener("ended", resolve, { once: true });
      audio.addEventListener("error", resolve, { once: true });
    });
    stopCurrentAudio();
  } catch (error) {
    showAudioError(concern, error, sessionAtStart);
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

el.recordButton.addEventListener("click", startRecording);
el.stopButton.addEventListener("click", stopRecording);
el.cancelButton.addEventListener("click", cancelRecording);
el.analyzeTextButton.addEventListener("click", analyzeTypedTurn);
el.resetButton.addEventListener("click", resetSession);
el.stopAudioButton.addEventListener("click", stopCurrentAudio);

if (!window.isSecureContext) {
  el.securityWarning.textContent = "Microphone recording requires HTTPS or localhost. Typed fallback remains available.";
  el.securityWarning.classList.remove("hidden");
}
if (!navigator.mediaDevices || !window.MediaRecorder) {
  el.securityWarning.textContent = "This browser cannot record audio with MediaRecorder. Use the typed fallback.";
  el.securityWarning.classList.remove("hidden");
}

resetSession();
loadHealth();
