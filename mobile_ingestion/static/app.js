const MAX_PROTOCOL_LOG_LINES = 500;
const SERVO_MIN_ANGLE = 0;
const SERVO_MAX_ANGLE = 180;
const NEUTRAL_SERVO_ANGLE = 90;
const DEFAULT_MAX_VIDEO_FPS = 5;
const SESSION_TOKEN_HEADER = "X-Session-Token";
const SESSION_ROLE_SENDER = "sender";
const SESSION_ROLE_SPECTATOR = "spectator";
const VOICE_STATUS_POLL_INTERVAL_MS = 2000;
const VOICE_EVENT_RECONNECT_DELAY_MS = 1500;
const OBJECT_SEARCH_STATUS_POLL_INTERVAL_MS = 2000;
const OBJECT_SEARCH_EVENT_RECONNECT_DELAY_MS = 1500;
const MODE_STATUS_POLL_INTERVAL_MS = 2000;
const MODE_EVENT_RECONNECT_DELAY_MS = 1500;

const elements = {
  connectButton: document.getElementById("connect-button"),
  disconnectButton: document.getElementById("disconnect-button"),
  roleSelect: document.getElementById("role-select"),
  errorMessage: document.getElementById("error-message"),
  preview: document.getElementById("local-preview"),
  statusBadge: document.getElementById("status-badge"),
  statusDetail: document.getElementById("status-detail"),
  activeModeBadge: document.getElementById("active-mode-badge"),
  activeModeDetail: document.getElementById("active-mode-detail"),
  voiceDebugToggleButton: document.getElementById("voice-debug-toggle-button"),
  voiceDebugPanel: document.getElementById("voice-debug-panel"),
  voiceDebugBadge: document.getElementById("voice-debug-badge"),
  voiceErrorMessage: document.getElementById("voice-error-message"),
  voiceAvailabilityDetail: document.getElementById("voice-availability-detail"),
  voiceSessionDetail: document.getElementById("voice-session-detail"),
  voiceModeState: document.getElementById("voice-mode-state"),
  voiceTransportDetail: document.getElementById("voice-transport-detail"),
  voiceDroppedChunks: document.getElementById("voice-dropped-chunks"),
  voiceLastTranscriptAt: document.getElementById("voice-last-transcript-at"),
  voiceLastWakeWord: document.getElementById("voice-last-wake-word"),
  voiceLastEntryId: document.getElementById("voice-last-entry-id"),
  voiceTranscriptLog: document.getElementById("voice-transcript-log"),
  objectSearchBadge: document.getElementById("object-search-badge"),
  objectSearchDetail: document.getElementById("object-search-detail"),
  objectSearchTarget: document.getElementById("object-search-target"),
    objectSearchModelDetailRow: document.getElementById(
      "object-search-model-detail-row")
      || document.getElementById("object-search-model-detail")?.closest("p"),
  objectSearchModelDetail: document.getElementById("object-search-model-detail"),
    objectSearchModelControls: document.getElementById("object-search-model-controls")
      || document.querySelector(".object-search-model-controls"),
  objectSearchVisionModelSelect: document.getElementById(
      "object-search-vision-model-select"),
  objectSearchModelUpdateStatus: document.getElementById(
      "object-search-model-update-status"),
  objectSearchErrorMessage: document.getElementById("object-search-error-message"),
  debugToggleButton: document.getElementById("debug-toggle-button"),
  arduinoDebugPanel: document.getElementById("arduino-debug-panel"),
  arduinoDebugBadge: document.getElementById("arduino-debug-badge"),
  arduinoErrorMessage: document.getElementById("arduino-error-message"),
  arduinoPortSelect: document.getElementById("arduino-port-select"),
  arduinoRefreshPortsButton: document.getElementById(
      "arduino-refresh-ports-button"),
  arduinoConnectButton: document.getElementById("arduino-connect-button"),
  arduinoDisconnectButton: document.getElementById("arduino-disconnect-button"),
  arduinoAvailabilityDetail: document.getElementById(
      "arduino-availability-detail"),
  arduinoSelectedPort: document.getElementById("arduino-selected-port"),
  arduinoKeepaliveStatus: document.getElementById("arduino-keepalive-status"),
  arduinoConnectionDetail: document.getElementById(
      "arduino-connection-detail"),
  arduinoTxCount: document.getElementById("arduino-tx-count"),
  arduinoRxCount: document.getElementById("arduino-rx-count"),
  arduinoInvalidCount: document.getElementById("arduino-invalid-count"),
  arduinoLastRx: document.getElementById("arduino-last-rx"),
  arduinoDistance: document.getElementById("arduino-distance"),
  arduinoDistanceFlags: document.getElementById("arduino-distance-flags"),
  arduinoAccelX: document.getElementById("arduino-accel-x"),
  arduinoAccelY: document.getElementById("arduino-accel-y"),
  arduinoAccelZ: document.getElementById("arduino-accel-z"),
  arduinoJoystickX: document.getElementById("arduino-joystick-x"),
  arduinoJoystickY: document.getElementById("arduino-joystick-y"),
  arduinoJoystickButton: document.getElementById("arduino-joystick-button"),
  arduinoServoRange: document.getElementById("arduino-servo-range"),
  arduinoServoNumber: document.getElementById("arduino-servo-number"),
  arduinoVibrationToggle: document.getElementById("arduino-vibration-toggle"),
  arduinoCenterButton: document.getElementById("arduino-center-button"),
  arduinoAllStopButton: document.getElementById("arduino-all-stop-button"),
  arduinoProtocolLog: document.getElementById("arduino-protocol-log"),
};

const state = {
  localStream: null,
  remoteStream: null,
  peerConnection: null,
  roomStatus: null,
  requestedRole: SESSION_ROLE_SENDER,
  connectedRole: null,
  sessionToken: null,
  statusInterval: null,
  voice: {
    debugVisible: false,
    eventSource: null,
    statusInterval: null,
    reconnectTimeout: null,
    streamConnected: false,
    status: null,
    audioContext: null,
    transcriptEntries: [],
    lastTranscriptReceivedAt: null,
    lastWakeWord: null,
  },
  objectSearch: {
    eventSource: null,
    statusInterval: null,
    reconnectTimeout: null,
    status: null,
    modelUpdateInFlight: false,
  },
  mode: {
    eventSource: null,
    statusInterval: null,
    reconnectTimeout: null,
    status: null,
  },
  arduino: {
    debugVisible: false,
    eventSource: null,
    commandSyncTimeout: null,
    commandDirty: false,
    commandInFlight: false,
    pendingCommandAck: null,
    status: null,
    selectedPort: "",
    command: {
      servoAngleDegrees: NEUTRAL_SERVO_ANGLE,
      vibrationEnabled: false,
    },
    protocolLogLines: [],
  },
};

function setStatus(label, detail) {
  elements.statusBadge.textContent = label;
  elements.statusDetail.textContent = detail;
}

function showMessage(element, message) {
  element.hidden = false;
  element.textContent = message;
}

function clearMessage(element) {
  element.hidden = true;
  element.textContent = "";
}

function showError(message) {
  showMessage(elements.errorMessage, message);
}

function clearError() {
  clearMessage(elements.errorMessage);
}

function showVoiceError(message) {
  showMessage(elements.voiceErrorMessage, message);
}

function clearVoiceError() {
  clearMessage(elements.voiceErrorMessage);
}

function showObjectSearchError(message) {
  showMessage(elements.objectSearchErrorMessage, message);
}

function clearObjectSearchError() {
  clearMessage(elements.objectSearchErrorMessage);
}

function showArduinoError(message) {
  showMessage(elements.arduinoErrorMessage, message);
}

function clearArduinoError() {
  clearMessage(elements.arduinoErrorMessage);
}

function setBusy(isBusy) {
  elements.connectButton.disabled = isBusy;
  elements.disconnectButton.disabled = !isBusy;
  elements.roleSelect.disabled = isBusy;
}

function selectedRole() {
  return elements.roleSelect.value || SESSION_ROLE_SENDER;
}

function hasSenderPrivileges() {
  return state.connectedRole === SESSION_ROLE_SENDER
    && typeof state.sessionToken === "string"
    && state.sessionToken.length > 0;
}

function isSpectatorSession() {
  return state.connectedRole === SESSION_ROLE_SPECTATOR;
}

function isSpectatorView() {
  const activeRole = state.connectedRole || state.requestedRole || selectedRole();
  return activeRole === SESSION_ROLE_SPECTATOR;
}

function buildFetchOptions(options = {}) {
  const {
    includeSessionToken = false,
    headers: rawHeaders,
    ...fetchOptions
  } = options;
  const headers = new Headers(rawHeaders || {});
  if (includeSessionToken && state.sessionToken) {
    headers.set(SESSION_TOKEN_HEADER, state.sessionToken);
  }
  return {
    ...fetchOptions,
    headers,
  };
}

function clampServoAngle(value) {
  const numericValue = Number(value);
  if (Number.isNaN(numericValue)) {
    return state.arduino.command.servoAngleDegrees;
  }
  return Math.min(SERVO_MAX_ANGLE, Math.max(SERVO_MIN_ANGLE, numericValue));
}

function formatTimestamp(timestamp) {
  if (typeof timestamp !== "number") {
    return "--";
  }
  const date = new Date(timestamp * 1000);
  const milliseconds = String(date.getMilliseconds()).padStart(3, "0");
  return `${date.toLocaleTimeString("fr-CA", { hour12: false })}.${milliseconds}`;
}

function formatIsoTimestamp(timestamp) {
  if (typeof timestamp !== "string" || !timestamp) {
    return "--";
  }
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return "--";
  }
  return `${date.toLocaleTimeString("fr-CA", { hour12: false })}.${String(date.getMilliseconds()).padStart(3, "0")}`;
}

function getConfiguredVideoMaxFps() {
  const configuredValue = Number(window.APP_CONFIG?.videoMaxFps);
  if (!Number.isFinite(configuredValue) || configuredValue <= 0) {
    return DEFAULT_MAX_VIDEO_FPS;
  }
  return configuredValue;
}

function buildVideoConstraints() {
  const maxFps = getConfiguredVideoMaxFps();
  return {
    facingMode: { ideal: "environment" },
    width: { ideal: 1280 },
    height: { ideal: 720 },
    frameRate: {
      ideal: maxFps,
      max: maxFps,
    },
  };
}

async function enforceVideoTrackConstraints(stream) {
  const [videoTrack] = stream.getVideoTracks();
  if (!videoTrack || typeof videoTrack.applyConstraints !== "function") {
    return;
  }

  try {
    await videoTrack.applyConstraints({
      frameRate: getConfiguredVideoMaxFps(),
    });
  } catch (error) {
    console.warn("Impossible de limiter la cadence video.", error);
  }
}

function formatAxis(value, unit, isValid) {
  if (!isValid) {
    return `0 ${unit} (invalid)`;
  }
  return `${value} ${unit}`;
}

function formatProtocolLogLine(frame) {
  const sequence = frame.sequence == null
    ? "--"
    : Number(frame.sequence).toString(16).toUpperCase().padStart(2, "0");
  const typeLabel = frame.messageType == null
    ? "--"
    : `0x${Number(frame.messageType).toString(16).toUpperCase().padStart(2, "0")}`;
  let line = `${formatTimestamp(frame.timestamp)} | ${frame.direction.toUpperCase().padEnd(6, " ")} | type=${typeLabel.padEnd(4, " ")} | seq=${sequence} | ${frame.status}`;
  if (frame.hexString) {
    line += ` | ${frame.hexString}`;
  }
  return line;
}

function renderProtocolLog() {
  elements.arduinoProtocolLog.textContent = state.arduino.protocolLogLines.join("\n");
  elements.arduinoProtocolLog.scrollTop = elements.arduinoProtocolLog.scrollHeight;
}

function setProtocolLogLines(frames) {
  state.arduino.protocolLogLines = frames
      .slice(-MAX_PROTOCOL_LOG_LINES)
      .map((frame) => formatProtocolLogLine(frame));
  renderProtocolLog();
}

function appendProtocolLogLine(frame) {
  state.arduino.protocolLogLines.push(formatProtocolLogLine(frame));
  if (state.arduino.protocolLogLines.length > MAX_PROTOCOL_LOG_LINES) {
    state.arduino.protocolLogLines.splice(
        0,
        state.arduino.protocolLogLines.length - MAX_PROTOCOL_LOG_LINES,
    );
  }
  renderProtocolLog();
}

function updateVoiceDebugVisibility() {
  elements.voiceDebugPanel.hidden = !state.voice.debugVisible;
  elements.voiceDebugToggleButton.textContent = state.voice.debugVisible
    ? "Masquer le voice debug"
    : "Activer le voice debug";
}

function shouldMaintainVoiceMonitoring() {
  return state.peerConnection !== null || state.localStream !== null;
}

function shouldMaintainObjectSearchMonitoring() {
  return state.peerConnection !== null || state.localStream !== null;
}

function getConfiguredObjectSearchVisionModels() {
  const configuredModels = window.APP_CONFIG?.objectSearchVisionModels;
  if (!Array.isArray(configuredModels)) {
    return [];
  }
  return configuredModels.filter((model) => typeof model === "string" && model);
}

function setObjectSearchModelSelectorEnabled(isEnabled) {
  elements.objectSearchVisionModelSelect.disabled = !isEnabled;
}

function showObjectSearchModelUpdateStatus(message) {
  showMessage(elements.objectSearchModelUpdateStatus, message);
}

function clearObjectSearchModelUpdateStatus() {
  clearMessage(elements.objectSearchModelUpdateStatus);
}

function updateProtectedControls() {
  const canControl = hasSenderPrivileges();
  const spectatorView = isSpectatorView();

  if (elements.objectSearchModelDetailRow) {
    elements.objectSearchModelDetailRow.hidden = spectatorView;
    elements.objectSearchModelDetailRow.style.display = spectatorView ? "none" : "";
  }
  if (elements.objectSearchModelControls) {
    elements.objectSearchModelControls.hidden = spectatorView;
    elements.objectSearchModelControls.style.display = spectatorView ? "none" : "";
  }
  setObjectSearchModelSelectorEnabled(
      canControl
      && !spectatorView
      && getConfiguredObjectSearchVisionModels().length > 0
      && !state.objectSearch.modelUpdateInFlight,
  );
  updateArduinoDebugVisibility();
  if (state.arduino.status) {
    applyArduinoStatus(state.arduino.status, { forceCommandSync: true });
  } else {
    setArduinoManualControlsEnabled(false);
    elements.arduinoConnectButton.disabled = !canControl;
    elements.arduinoDisconnectButton.disabled = !canControl;
  }
}

function syncObjectSearchVisionModelSelector(status) {
  const selectedModel = status?.selectedVisionModel
    || window.APP_CONFIG?.objectSearchVisionModel
    || "";
  if (!selectedModel) {
    return;
  }
  elements.objectSearchVisionModelSelect.value = selectedModel;
}

function renderObjectSearchVisionModelOptions() {
  const models = getConfiguredObjectSearchVisionModels();
  elements.objectSearchVisionModelSelect.innerHTML = "";

  models.forEach((model) => {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = model;
    elements.objectSearchVisionModelSelect.append(option);
  });

  syncObjectSearchVisionModelSelector(state.objectSearch.status);
  updateProtectedControls();
}

function setObjectSearchBadge(label, stateName) {
  elements.objectSearchBadge.textContent = label;
  elements.objectSearchBadge.dataset.state = stateName;
}

function buildObjectSearchDetail(status) {
  if (!status) {
    return "Connecte le flux mobile pour activer la recherche d'objet.";
  }
  if (!status.available) {
    return status.error || "La recherche d'objet est indisponible sur le serveur.";
  }
  const hasPendingTarget = Boolean(status.targetLabel) && !status.detected;
  const modelPreparing = status.active
    && !status.modelReady
    && (status.modelState === "loading" || status.modelState === "pending");
  if (modelPreparing && !hasPendingTarget) {
    return "Préparation du modèle vision...";
  }
  if (hasPendingTarget && !status.modelReady) {
    return status.targetLabel
      ? `Le modèle vision se prépare pour chercher « ${status.targetLabel} ».`
      : "Le modèle vision se prépare pour la recherche d'objet.";
  }
  if (hasPendingTarget) {
    return status.targetLabel
      ? `Je cherche « ${status.targetLabel} » dans le champ de la caméra.`
      : "Recherche d'objet en cours.";
  }
  if (status.state === "awaiting_request") {
    return "Quelle cible dois-je chercher ?";
  }
  if (status.state === "resolving_target") {
    return "Analyse de la demande vocale...";
  }
  if (status.state === "found") {
    return status.targetLabel
      ? `« ${status.targetLabel} » est visible dans le champ de la caméra.`
      : "Objet détecté dans le champ de la caméra.";
  }
  if (status.state === "error") {
    return status.error || "La recherche d'objet a rencontré une erreur.";
  }
  if (status.active) {
    return "Dites « jarvis », puis demandez l'objet à trouver.";
  }
  return "Connecte le flux mobile pour activer la recherche d'objet.";
}

function buildObjectSearchModelDetail(status) {
  if (!status) {
    return "--";
  }
  if (typeof status.modelDetail === "string" && status.modelDetail) {
    return status.modelDetail;
  }
  if (status.modelReady) {
    return "Le modèle vision est prêt.";
  }
  if (status.modelState === "loading") {
    return "Téléchargement / chargement du modèle vision en cours...";
  }
  if (status.modelState === "unavailable") {
    return "Le modèle vision est indisponible.";
  }
  if (status.modelState === "error") {
    return "Le modèle vision a rencontré une erreur.";
  }
  return "Le modèle vision n'est pas encore chargé.";
}

function applyObjectSearchStatus(status) {
  state.objectSearch.status = status;
  if (typeof status.selectedVisionModel === "string" && status.selectedVisionModel) {
    window.APP_CONFIG.objectSearchVisionModel = status.selectedVisionModel;
  }
  elements.objectSearchTarget.textContent = status.targetLabel || "--";
  elements.objectSearchDetail.textContent = buildObjectSearchDetail(status);
  elements.objectSearchModelDetail.textContent = buildObjectSearchModelDetail(status);
  syncObjectSearchVisionModelSelector(status);
  updateProtectedControls();
  const hasPendingTarget = Boolean(status.targetLabel) && !status.detected;

  let badgeLabel = "Inactif";
  let badgeState = status.state || "idle";
  if (!status.available) {
    badgeLabel = "Indisponible";
    badgeState = "unavailable";
  } else if (status.detected || status.state === "found") {
    badgeLabel = "Détecté";
    badgeState = "found";
  } else if (status.active && !status.modelReady
      && (status.modelState === "loading" || status.modelState === "pending")) {
    badgeLabel = "Chargement";
    badgeState = "loading";
  } else if (hasPendingTarget) {
    badgeLabel = "Recherche";
    badgeState = "searching";
  } else if (status.state === "awaiting_request") {
    badgeLabel = "À l'écoute";
  } else if (status.state === "resolving_target") {
    badgeLabel = "Analyse";
  } else if (status.state === "error") {
    badgeLabel = "Erreur";
  } else if (status.active && status.modelReady) {
    badgeLabel = "Prêt";
    badgeState = "ready";
  }
  setObjectSearchBadge(badgeLabel, badgeState);

  if (status.error && (status.state === "error" || !status.available)) {
    showObjectSearchError(status.error);
  } else {
    clearObjectSearchError();
  }
}

async function fetchObjectSearchStatus() {
  const payload = await requestJson("/api/object-search/status");
  applyObjectSearchStatus(payload);
}

async function updateObjectSearchVisionModel(model) {
  if (!hasSenderPrivileges()) {
    showObjectSearchError(
        "Connecte-toi comme sender pour changer le modele vision.",
    );
    elements.objectSearchVisionModelSelect.value = (
      state.objectSearch.status?.selectedVisionModel
      || window.APP_CONFIG?.objectSearchVisionModel
      || ""
    );
    return;
  }
  const fallbackModel = state.objectSearch.status?.selectedVisionModel
    || window.APP_CONFIG?.objectSearchVisionModel
    || "";
  state.objectSearch.modelUpdateInFlight = true;
  showObjectSearchModelUpdateStatus(`Application du modèle ${model}...`);
  setObjectSearchModelSelectorEnabled(false);
  clearObjectSearchError();

  try {
    const payload = await requestJson("/api/object-search/vision-model", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ model }),
      includeSessionToken: true,
    });
    state.objectSearch.modelUpdateInFlight = false;
    window.APP_CONFIG.objectSearchVisionModel = payload.selectedVisionModel || model;
    clearObjectSearchModelUpdateStatus();
    applyObjectSearchStatus(payload);
  } catch (error) {
    state.objectSearch.modelUpdateInFlight = false;
    elements.objectSearchVisionModelSelect.value = fallbackModel;
    showObjectSearchError(
        error.message || "Impossible de changer le modèle vision.",
    );
    showObjectSearchModelUpdateStatus(
        "Le changement de modèle a échoué.",
    );
    updateProtectedControls();
    throw error;
  }
}

function clearObjectSearchReconnectTimer() {
  if (state.objectSearch.reconnectTimeout !== null) {
    window.clearTimeout(state.objectSearch.reconnectTimeout);
    state.objectSearch.reconnectTimeout = null;
  }
}

function scheduleObjectSearchEventStreamReconnect() {
  if (state.objectSearch.reconnectTimeout !== null
      || !shouldMaintainObjectSearchMonitoring()) {
    return;
  }

  state.objectSearch.reconnectTimeout = window.setTimeout(() => {
    state.objectSearch.reconnectTimeout = null;
    if (!shouldMaintainObjectSearchMonitoring()) {
      return;
    }
    openObjectSearchEventStream();
  }, OBJECT_SEARCH_EVENT_RECONNECT_DELAY_MS);
}

function closeObjectSearchEventStream() {
  if (state.objectSearch.eventSource) {
    state.objectSearch.eventSource.close();
    state.objectSearch.eventSource = null;
  }
}

function openObjectSearchEventStream() {
  if (!shouldMaintainObjectSearchMonitoring()) {
    return;
  }

  closeObjectSearchEventStream();
  const eventSource = new EventSource("/api/object-search/events");
  state.objectSearch.eventSource = eventSource;

  eventSource.addEventListener("status", (event) => {
    const payload = JSON.parse(event.data);
    applyObjectSearchStatus(payload);
    clearObjectSearchReconnectTimer();
  });

  eventSource.addEventListener("error", () => {
    if (state.objectSearch.eventSource !== eventSource) {
      return;
    }
    closeObjectSearchEventStream();
    scheduleObjectSearchEventStreamReconnect();
    fetchObjectSearchStatus().catch((error) => {
      console.error(error);
    });
  });
}

function startObjectSearchStatusPolling() {
  stopObjectSearchStatusPolling();
  state.objectSearch.statusInterval = window.setInterval(() => {
    fetchObjectSearchStatus().catch((error) => {
      console.error(error);
      showObjectSearchError(
          "Impossible de récupérer le statut de recherche d'objet.",
      );
    });
  }, OBJECT_SEARCH_STATUS_POLL_INTERVAL_MS);
}

function stopObjectSearchStatusPolling() {
  if (state.objectSearch.statusInterval !== null) {
    window.clearInterval(state.objectSearch.statusInterval);
    state.objectSearch.statusInterval = null;
  }
}

function resetObjectSearchState() {
  clearObjectSearchReconnectTimer();
  state.objectSearch.status = null;
  state.objectSearch.modelUpdateInFlight = false;
  elements.objectSearchTarget.textContent = "--";
  elements.objectSearchModelDetail.textContent = "--";
  elements.objectSearchDetail.textContent =
    "Connecte le flux mobile pour activer la recherche d'objet.";
  setObjectSearchBadge("Inactif", "idle");
  syncObjectSearchVisionModelSelector(null);
  clearObjectSearchModelUpdateStatus();
  updateProtectedControls();
  clearObjectSearchError();
}

function getModeLabel(mode) {
  if (mode === "eating") {
    return "Eating";
  }
  if (mode === "object_search") {
    return "Object Search";
  }
  return "Idle";
}

function applyModeStatus(status) {
  state.mode.status = status;
  const mode = status?.mode || "idle";
  elements.activeModeBadge.textContent = getModeLabel(mode);
  elements.activeModeBadge.dataset.state = mode;
  elements.activeModeDetail.textContent = (
    status?.detail
    || (mode === "object_search"
      ? "Recherche d'objet en cours."
      : (mode === "eating" ? "Mode repas actif." : "Mode idle."))
  );
}

async function fetchModeStatus() {
  const payload = await requestJson("/api/mode/status");
  applyModeStatus(payload);
}

function clearModeReconnectTimer() {
  if (state.mode.reconnectTimeout !== null) {
    window.clearTimeout(state.mode.reconnectTimeout);
    state.mode.reconnectTimeout = null;
  }
}

function scheduleModeEventStreamReconnect() {
  if (state.mode.reconnectTimeout !== null) {
    return;
  }

  state.mode.reconnectTimeout = window.setTimeout(() => {
    state.mode.reconnectTimeout = null;
    openModeEventStream();
  }, MODE_EVENT_RECONNECT_DELAY_MS);
}

function closeModeEventStream() {
  if (state.mode.eventSource) {
    state.mode.eventSource.close();
    state.mode.eventSource = null;
  }
}

function openModeEventStream() {
  closeModeEventStream();
  const eventSource = new EventSource("/api/mode/events");
  state.mode.eventSource = eventSource;

  eventSource.addEventListener("status", (event) => {
    const payload = JSON.parse(event.data);
    applyModeStatus(payload);
    clearModeReconnectTimer();
  });

  eventSource.addEventListener("error", () => {
    if (state.mode.eventSource !== eventSource) {
      return;
    }
    closeModeEventStream();
    scheduleModeEventStreamReconnect();
    fetchModeStatus().catch((error) => {
      console.error(error);
    });
  });
}

function startModeStatusPolling() {
  stopModeStatusPolling();
  state.mode.statusInterval = window.setInterval(() => {
    fetchModeStatus().catch((error) => {
      console.error(error);
    });
  }, MODE_STATUS_POLL_INTERVAL_MS);
}

function stopModeStatusPolling() {
  if (state.mode.statusInterval !== null) {
    window.clearInterval(state.mode.statusInterval);
    state.mode.statusInterval = null;
  }
}

function renderVoiceTransportDetail() {
  let detail = "Inactif";
  if (state.voice.reconnectTimeout !== null) {
    detail = "Reconnexion SSE...";
  } else if (state.voice.streamConnected) {
    detail = "SSE + polling";
  } else if (state.voice.statusInterval !== null) {
    detail = "Polling secours";
  }
  elements.voiceTransportDetail.textContent = detail;
}

function recordVoiceTranscriptTimestamp(timestamp) {
  if (typeof timestamp !== "string" || !timestamp) {
    return;
  }
  state.voice.lastTranscriptReceivedAt = timestamp;
  elements.voiceLastTranscriptAt.textContent =
    formatIsoTimestamp(state.voice.lastTranscriptReceivedAt);
}

function extractLatestTranscriptTimestamp(entries) {
  let latestTimestamp = null;

  entries.forEach((entry) => {
    if (typeof entry?.receivedAt !== "string" || !entry.receivedAt) {
      return;
    }
    const parsedDate = new Date(entry.receivedAt);
    if (Number.isNaN(parsedDate.getTime())) {
      return;
    }
    if (latestTimestamp === null || parsedDate > new Date(latestTimestamp)) {
      latestTimestamp = entry.receivedAt;
    }
  });

  return latestTimestamp;
}

function renderVoiceTranscript() {
  const lines = state.voice.transcriptEntries.map((entry) => {
    const prefix = entry.isFinal ? "[final]" : "[live ]";
    return `${formatIsoTimestamp(entry.receivedAt)} ${prefix} ${entry.text}`;
  });
  elements.voiceTranscriptLog.textContent = lines.join("\n");
  elements.voiceTranscriptLog.scrollTop = elements.voiceTranscriptLog.scrollHeight;
}

function renderVoiceWakeWord() {
  const wakeWord = state.voice.lastWakeWord;
  elements.voiceLastWakeWord.textContent = wakeWord
    ? `${wakeWord.phrase} @ ${formatIsoTimestamp(wakeWord.receivedAt)}`
    : "--";
  elements.voiceLastEntryId.textContent = wakeWord?.entryId || "--";
}

function applyVoiceStatus(status) {
  state.voice.status = status;
  state.voice.transcriptEntries = Array.isArray(status.entries)
    ? [...status.entries]
    : [];
  state.voice.lastWakeWord = status.lastWakeWord || null;
  const latestTranscriptTimestamp = extractLatestTranscriptTimestamp(
      state.voice.transcriptEntries,
  );
  if (latestTranscriptTimestamp !== null) {
    recordVoiceTranscriptTimestamp(latestTranscriptTimestamp);
  }
  elements.voiceDebugBadge.textContent = status.active ? "Actif" : "Inactif";
  elements.voiceAvailabilityDetail.textContent = status.available
    ? "Prêt côté serveur"
    : (status.error || "Indisponible");
  elements.voiceSessionDetail.textContent = status.sessionId || "--";
  elements.voiceModeState.textContent = status.modeState || "idle";
  renderVoiceTransportDetail();
  elements.voiceDroppedChunks.textContent = String(status.droppedChunks || 0);
  if (status.error) {
    showVoiceError(status.error);
  } else {
    clearVoiceError();
  }
  renderVoiceWakeWord();
  renderVoiceTranscript();
}

function upsertVoiceTranscriptEntry(entry) {
  const currentIndex = state.voice.transcriptEntries.findIndex(
      (candidate) => candidate.entryId === entry.entryId,
  );
  if (currentIndex >= 0) {
    state.voice.transcriptEntries.splice(currentIndex, 1, entry);
  } else {
    state.voice.transcriptEntries.push(entry);
  }
  recordVoiceTranscriptTimestamp(entry.receivedAt);
  renderVoiceTranscript();
}

function ensureVoiceAudioContext() {
  if (state.voice.audioContext) {
    if (state.voice.audioContext.state === "suspended") {
      state.voice.audioContext.resume().catch((error) => {
        console.error(error);
      });
    }
    return;
  }

  const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextConstructor) {
    return;
  }
  state.voice.audioContext = new AudioContextConstructor();
  if (state.voice.audioContext.state === "suspended") {
    state.voice.audioContext.resume().catch((error) => {
      console.error(error);
    });
  }
}

function playWakeWordTone() {
  if (isSpectatorSession()) {
    return;
  }
  ensureVoiceAudioContext();
  if (!state.voice.audioContext) {
    return;
  }

  const audioContext = state.voice.audioContext;
  const startAt = audioContext.currentTime + 0.01;
  const oscillator = audioContext.createOscillator();
  const gainNode = audioContext.createGain();
  oscillator.type = "triangle";
  oscillator.frequency.setValueAtTime(740, startAt);
  oscillator.frequency.linearRampToValueAtTime(1040, startAt + 0.12);
  gainNode.gain.setValueAtTime(0.0001, startAt);
  gainNode.gain.linearRampToValueAtTime(0.12, startAt + 0.02);
  gainNode.gain.exponentialRampToValueAtTime(0.0001, startAt + 0.2);
  oscillator.connect(gainNode);
  gainNode.connect(audioContext.destination);
  oscillator.start(startAt);
  oscillator.stop(startAt + 0.22);
}

async function fetchVoiceStatus() {
  const payload = await requestJson("/api/voice/status");
  applyVoiceStatus(payload);
}

function clearVoiceReconnectTimer() {
  if (state.voice.reconnectTimeout !== null) {
    window.clearTimeout(state.voice.reconnectTimeout);
    state.voice.reconnectTimeout = null;
    renderVoiceTransportDetail();
  }
}

function scheduleVoiceEventStreamReconnect() {
  if (state.voice.reconnectTimeout !== null || !shouldMaintainVoiceMonitoring()) {
    return;
  }

  state.voice.reconnectTimeout = window.setTimeout(() => {
    state.voice.reconnectTimeout = null;
    renderVoiceTransportDetail();

    if (!shouldMaintainVoiceMonitoring()) {
      return;
    }
    openVoiceEventStream();
  }, VOICE_EVENT_RECONNECT_DELAY_MS);
  renderVoiceTransportDetail();
}

function closeVoiceEventStream() {
  if (state.voice.eventSource) {
    state.voice.eventSource.close();
    state.voice.eventSource = null;
  }
  state.voice.streamConnected = false;
  renderVoiceTransportDetail();
}

function openVoiceEventStream() {
  if (!shouldMaintainVoiceMonitoring()) {
    return;
  }

  closeVoiceEventStream();
  const eventSource = new EventSource("/api/voice/events");
  state.voice.eventSource = eventSource;

  eventSource.addEventListener("open", () => {
    if (state.voice.eventSource !== eventSource) {
      return;
    }
    state.voice.streamConnected = true;
    clearVoiceReconnectTimer();
    renderVoiceTransportDetail();
    if (!state.voice.status?.error) {
      clearVoiceError();
    }
  });

  eventSource.addEventListener("status", (event) => {
    const payload = JSON.parse(event.data);
    applyVoiceStatus(payload);
  });

  eventSource.addEventListener("transcript", (event) => {
    const payload = JSON.parse(event.data);
    upsertVoiceTranscriptEntry(payload);
  });

  eventSource.addEventListener("wake-word", (event) => {
    const payload = JSON.parse(event.data);
    state.voice.lastWakeWord = payload;
    renderVoiceWakeWord();
    playWakeWordTone();
  });

  eventSource.addEventListener("error", () => {
    if (state.voice.eventSource !== eventSource) {
      return;
    }

    closeVoiceEventStream();
    scheduleVoiceEventStreamReconnect();
    fetchVoiceStatus().catch((error) => {
      console.error(error);
    });

    if (state.voice.debugVisible) {
      showVoiceError("Le flux temps reel voix a ete interrompu. Reconnexion en cours.");
    }
  });
}

function startVoiceStatusPolling() {
  stopVoiceStatusPolling();
  state.voice.statusInterval = window.setInterval(() => {
    fetchVoiceStatus().catch((error) => {
      console.error(error);
      if (state.voice.debugVisible) {
        showVoiceError("Impossible de recuperer le statut voix en secours.");
      }
    });
  }, VOICE_STATUS_POLL_INTERVAL_MS);
  renderVoiceTransportDetail();
}

function stopVoiceStatusPolling() {
  if (state.voice.statusInterval !== null) {
    window.clearInterval(state.voice.statusInterval);
    state.voice.statusInterval = null;
    renderVoiceTransportDetail();
  }
}

function resetVoiceState() {
  clearVoiceReconnectTimer();
  state.voice.status = null;
  state.voice.transcriptEntries = [];
  state.voice.lastTranscriptReceivedAt = null;
  state.voice.lastWakeWord = null;
  state.voice.streamConnected = false;
  elements.voiceDebugBadge.textContent = "Inactif";
  elements.voiceAvailabilityDetail.textContent = "--";
  elements.voiceSessionDetail.textContent = "--";
  elements.voiceModeState.textContent = "idle";
  elements.voiceTransportDetail.textContent = "Inactif";
  elements.voiceDroppedChunks.textContent = "0";
  elements.voiceLastTranscriptAt.textContent = "--";
  clearVoiceError();
  renderVoiceWakeWord();
  renderVoiceTranscript();
}

function setArduinoManualControlsEnabled(isEnabled) {
  const controlsEnabled = isEnabled && hasSenderPrivileges();
  elements.arduinoServoRange.disabled = !controlsEnabled;
  elements.arduinoServoNumber.disabled = !controlsEnabled;
  elements.arduinoVibrationToggle.disabled = !controlsEnabled;
  elements.arduinoCenterButton.disabled = !controlsEnabled;
  elements.arduinoAllStopButton.disabled = !controlsEnabled;
}

function syncArduinoCommandInputs(command) {
  elements.arduinoServoRange.value = command.servoAngleDegrees.toFixed(1);
  elements.arduinoServoNumber.value = command.servoAngleDegrees.toFixed(1);
  elements.arduinoVibrationToggle.checked = command.vibrationEnabled;
}

function commandFromStatus(status) {
  const command = status.debugEnabled ? status.debugCommand : status.effectiveCommand;
  return {
    servoAngleDegrees: Number(command.servoAngleDegrees),
    vibrationEnabled: Boolean(command.vibrationEnabled),
  };
}

function commandsEqual(left, right) {
  const angleDelta = Math.abs(Number(left.servoAngleDegrees) - Number(right.servoAngleDegrees));
  return angleDelta < 0.1
    && Boolean(left.vibrationEnabled) === Boolean(right.vibrationEnabled);
}

function applyArduinoTelemetry(telemetry) {
  if (!telemetry) {
    elements.arduinoDistance.textContent = "--";
    elements.arduinoDistanceFlags.textContent = "--";
    elements.arduinoAccelX.textContent = "--";
    elements.arduinoAccelY.textContent = "--";
    elements.arduinoAccelZ.textContent = "--";
    elements.arduinoJoystickX.textContent = "--";
    elements.arduinoJoystickY.textContent = "--";
    elements.arduinoJoystickButton.textContent = "--";
    return;
  }

  elements.arduinoDistance.textContent = telemetry.distanceValid
    ? `${telemetry.distanceMm} mm`
    : "0 mm";
  elements.arduinoDistanceFlags.textContent =
    `valid: ${telemetry.distanceValid ? "yes" : "no"} | timeout: ${telemetry.distanceTimedOut ? "yes" : "no"}`;
  elements.arduinoAccelX.textContent = formatAxis(
      telemetry.accelXMg,
      "mg",
      telemetry.accelValid,
  );
  elements.arduinoAccelY.textContent = formatAxis(
      telemetry.accelYMg,
      "mg",
      telemetry.accelValid,
  );
  elements.arduinoAccelZ.textContent = formatAxis(
      telemetry.accelZMg,
      "mg",
      telemetry.accelValid,
  );
  elements.arduinoJoystickX.textContent = `${telemetry.joystickXPermille} permille`;
  elements.arduinoJoystickY.textContent = `${telemetry.joystickYPermille} permille`;
  elements.arduinoJoystickButton.textContent = telemetry.joystickButtonPressed
    ? "Pressed"
    : "Released";
}

function applyArduinoStatus(status, options = {}) {
  const { replaceLog = false, forceCommandSync = false } = options;
  state.arduino.status = status;
  state.arduino.selectedPort = status.selectedPort || state.arduino.selectedPort;
  const canControl = hasSenderPrivileges();

  elements.arduinoAvailabilityDetail.textContent = status.available
    ? "Prêt côté serveur"
    : status.detail;
  elements.arduinoSelectedPort.textContent = status.selectedPort || "--";
  elements.arduinoKeepaliveStatus.textContent = status.keepaliveActive ? "On" : "Off";
  elements.arduinoConnectionDetail.textContent = status.detail || "--";
  elements.arduinoTxCount.textContent = String(status.txCount);
  elements.arduinoRxCount.textContent = String(status.rxCount);
  elements.arduinoInvalidCount.textContent = String(status.invalidFrameCount);
  elements.arduinoLastRx.textContent = formatTimestamp(status.lastRxTimestamp);
  elements.arduinoDebugBadge.textContent = status.debugEnabled ? "Actif" : "Inactif";
  elements.arduinoConnectButton.disabled = !canControl || !status.available || status.connected;
  elements.arduinoDisconnectButton.disabled = !canControl || !status.connected;
  setArduinoManualControlsEnabled(status.connected);

  const serverCommand = commandFromStatus(status);
  if (state.arduino.pendingCommandAck
      && commandsEqual(serverCommand, state.arduino.pendingCommandAck)) {
    state.arduino.pendingCommandAck = null;
  }

  const waitingForCommandEcho = status.connected
    && state.arduino.pendingCommandAck
    && !commandsEqual(serverCommand, state.arduino.pendingCommandAck);
  const preserveLocalCommand = status.connected
    && !forceCommandSync
    && (state.arduino.commandDirty
        || state.arduino.commandInFlight
        || waitingForCommandEcho);
  if (!preserveLocalCommand) {
    state.arduino.command = serverCommand;
    syncArduinoCommandInputs(state.arduino.command);
    state.arduino.commandDirty = false;
  }

  if (!status.connected) {
    state.arduino.commandDirty = false;
    state.arduino.commandInFlight = false;
    state.arduino.pendingCommandAck = null;
  }
  applyArduinoTelemetry(status.latestTelemetry);

  if (replaceLog && Array.isArray(status.recentFrames)) {
    setProtocolLogLines(status.recentFrames);
  }
}

function renderArduinoPortOptions(ports) {
  const currentSelection = state.arduino.selectedPort || elements.arduinoPortSelect.value;
  elements.arduinoPortSelect.innerHTML = "";

  if (ports.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Aucun port detecte";
    elements.arduinoPortSelect.append(option);
    elements.arduinoPortSelect.disabled = true;
    state.arduino.selectedPort = "";
    return;
  }

  elements.arduinoPortSelect.disabled = false;
  ports.forEach((port) => {
    const option = document.createElement("option");
    option.value = port;
    option.textContent = port;
    if (port === currentSelection) {
      option.selected = true;
    }
    elements.arduinoPortSelect.append(option);
  });

  if (!elements.arduinoPortSelect.value) {
    elements.arduinoPortSelect.value = ports[0];
  }
  state.arduino.selectedPort = elements.arduinoPortSelect.value;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, buildFetchOptions(options));
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(payload?.error || "La requete a echoue.");
  }
  return payload;
}

async function requestLocalStream() {
  const preferredConstraints = {
    audio: {
      channelCount: { ideal: 1 },
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
    video: buildVideoConstraints(),
  };

  try {
    const stream = await navigator.mediaDevices.getUserMedia(preferredConstraints);
    await enforceVideoTrackConstraints(stream);
    return stream;
  } catch (error) {
    if (error.name !== "OverconstrainedError" && error.name !== "NotFoundError") {
      throw error;
    }
  }

  const fallbackStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: { ideal: 1 },
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
    video: {
      frameRate: {
        ideal: getConfiguredVideoMaxFps(),
        max: getConfiguredVideoMaxFps(),
      },
    },
  });
  await enforceVideoTrackConstraints(fallbackStream);
  return fallbackStream;
}

function buildPeerConnection(role) {
  const iceServers = (window.APP_CONFIG?.iceServers || []).map((url) => ({ urls: url }));
  const connection = new RTCPeerConnection({ iceServers });

  if (role === SESSION_ROLE_SPECTATOR) {
    state.remoteStream = new MediaStream();
    connection.addEventListener("track", (event) => {
      if (state.peerConnection !== connection || event.track.kind !== "video") {
        return;
      }
      const remoteStream = event.streams?.[0] || state.remoteStream;
      if (remoteStream !== state.remoteStream) {
        state.remoteStream = remoteStream;
      } else {
        state.remoteStream.addTrack(event.track);
      }
      elements.preview.srcObject = state.remoteStream;
      setStatus("Spectator", "La video du sender est affichee en direct.");
    });
  }

  connection.addEventListener("connectionstatechange", () => {
    if (state.peerConnection !== connection) {
      return;
    }
    const label = `WebRTC ${connection.connectionState}`;
    setStatus(label, "Le navigateur maintient la connexion avec le serveur.");
    if (["failed", "disconnected", "closed"].includes(connection.connectionState)) {
      setBusy(false);
      if (state.connectedRole === SESSION_ROLE_SPECTATOR) {
        window.setTimeout(() => {
          disconnect({ notifyServer: false, preserveStatus: true }).catch((error) => {
            console.error(error);
          });
          setStatus(
              "En attente",
              "Le flux du sender n'est plus disponible. Reconnecte-toi ou change de role.",
          );
        }, 0);
      }
    }
  });

  connection.addEventListener("iceconnectionstatechange", () => {
    if (connection.iceConnectionState === "failed") {
      showError("La connexion ICE a echoue. Verifie le Wi-Fi local ou le tunnel HTTPS.");
    }
  });

  return connection;
}

function waitForIceGatheringComplete(connection) {
  if (connection.iceGatheringState === "complete") {
    return Promise.resolve();
  }

  return new Promise((resolve) => {
    const handleStateChange = () => {
      if (connection.iceGatheringState === "complete") {
        connection.removeEventListener("icegatheringstatechange", handleStateChange);
        resolve();
      }
    };
    connection.addEventListener("icegatheringstatechange", handleStateChange);
  });
}

async function fetchStatus() {
  const payload = await requestJson("/api/webrtc/status");
  state.roomStatus = payload;

  if (state.connectedRole === SESSION_ROLE_SENDER) {
    setStatus(
        "Sender",
        payload.spectatorOccupied
          ? "Votre flux est en direct et un spectator est connecte."
          : "Votre flux est en direct. Le slot spectator est libre.",
    );
    return payload;
  }

  if (state.connectedRole === SESSION_ROLE_SPECTATOR) {
    setStatus(
        "Spectator",
        payload.senderVideoAvailable
          ? "Vous regardez la video du sender."
          : "En attente du flux video du sender.",
    );
    return payload;
  }

  if (payload.senderVideoAvailable && payload.spectatorOccupied) {
    setStatus("Salle pleine", "Un sender et un spectator sont deja connectes.");
    return payload;
  }
  if (payload.senderVideoAvailable) {
    setStatus("Sender actif", "Un sender diffuse deja. Le mode spectator est disponible.");
    return payload;
  }
  if (payload.senderOccupied) {
    setStatus(
        "Preparation",
        "Le sender se connecte. Attends que la video soit disponible pour rejoindre en spectator.",
    );
    return payload;
  }

  setStatus("En attente", "Aucune session active.");
  return payload;
}

function startStatusPolling() {
  stopStatusPolling();
  state.statusInterval = window.setInterval(() => {
    fetchStatus().catch(() => {
      showError("Impossible de joindre le serveur pour recuperer le statut.");
    });
  }, 2000);
}

function stopStatusPolling() {
  if (state.statusInterval !== null) {
    window.clearInterval(state.statusInterval);
    state.statusInterval = null;
  }
}

async function connect() {
  clearError();
  clearVoiceError();
  state.requestedRole = selectedRole();

  if (state.requestedRole === SESSION_ROLE_SENDER) {
    ensureVoiceAudioContext();

    if (!window.isSecureContext && window.location.hostname !== "localhost") {
      showError("Le navigateur mobile exige HTTPS pour ouvrir camera et micro.");
      return;
    }

    if (!navigator.mediaDevices?.getUserMedia) {
      showError("Ce navigateur ne supporte pas getUserMedia.");
      return;
    }
  }

  setBusy(true);
  setStatus(
      state.requestedRole === SESSION_ROLE_SENDER ? "Preparation" : "Spectator",
      state.requestedRole === SESSION_ROLE_SENDER
        ? "Demande des permissions camera et micro..."
        : "Connexion au flux video du sender...",
  );

  try {
    if (state.requestedRole === SESSION_ROLE_SENDER) {
      state.localStream = await requestLocalStream();
      elements.preview.srcObject = state.localStream;
    } else {
      state.localStream = null;
      state.remoteStream = new MediaStream();
      elements.preview.srcObject = null;
    }

    state.peerConnection = buildPeerConnection(state.requestedRole);
    if (state.requestedRole === SESSION_ROLE_SENDER) {
      state.localStream.getTracks().forEach((track) => {
        state.peerConnection.addTrack(track, state.localStream);
      });
    } else {
      state.peerConnection.addTransceiver("video", { direction: "recvonly" });
    }

    const offer = await state.peerConnection.createOffer();
    await state.peerConnection.setLocalDescription(offer);
    await waitForIceGatheringComplete(state.peerConnection);

    const localDescription = state.peerConnection.localDescription;
    if (!localDescription?.sdp || !localDescription?.type) {
      throw new Error("L'offre WebRTC locale est incomplète.");
    }

    setStatus("Negociation", "Envoi de l'offre WebRTC au serveur...");
    const payload = await requestJson("/api/webrtc/offer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sdp: localDescription.sdp,
        type: localDescription.type,
        role: state.requestedRole,
      }),
    });

    state.sessionToken = payload.sessionToken || null;
    state.connectedRole = payload.role || state.requestedRole;
    updateProtectedControls();
    await state.peerConnection.setRemoteDescription({
      sdp: payload.sdp,
      type: payload.type,
    });
    setStatus(
        state.connectedRole === SESSION_ROLE_SENDER ? "Sender" : "Spectator",
        state.connectedRole === SESSION_ROLE_SENDER
          ? "Votre camera et votre micro sont connectes au serveur."
          : "En attente de la video du sender...",
    );
    await Promise.all([
      fetchVoiceStatus(),
      fetchObjectSearchStatus(),
      fetchModeStatus(),
      fetchStatus(),
    ]);
    startVoiceStatusPolling();
    startObjectSearchStatusPolling();
    startModeStatusPolling();
    openVoiceEventStream();
    openObjectSearchEventStream();
    openModeEventStream();
    startStatusPolling();
  } catch (error) {
    console.error(error);
    await disconnect({ notifyServer: true, preserveStatus: true });
    showError(error.message || "La connexion a echoue.");
    setStatus(
        "Erreur",
        state.requestedRole === SESSION_ROLE_SENDER
          ? "Le flux n'a pas pu etre etabli."
          : "Le mode spectator n'a pas pu etre etabli.",
    );
  }
}

async function disconnect(options = {}) {
  const { notifyServer = true, preserveStatus = false } = options;
  const sessionToken = state.sessionToken;

  stopStatusPolling();
  stopVoiceStatusPolling();
  stopObjectSearchStatusPolling();
  stopModeStatusPolling();
  clearVoiceReconnectTimer();
  clearObjectSearchReconnectTimer();
  clearModeReconnectTimer();

  if (state.peerConnection) {
    const peerConnection = state.peerConnection;
    state.peerConnection = null;
    peerConnection.getSenders().forEach((sender) => sender.track?.stop());
    peerConnection.close();
  }

  if (state.localStream) {
    state.localStream.getTracks().forEach((track) => track.stop());
    state.localStream = null;
  }

  state.remoteStream = null;
  state.sessionToken = null;
  state.connectedRole = null;
  elements.preview.srcObject = null;
  setBusy(false);
  updateProtectedControls();

  if (notifyServer && sessionToken) {
    try {
      await fetch("/api/webrtc/session", buildFetchOptions({
        method: "DELETE",
        headers: {
          [SESSION_TOKEN_HEADER]: sessionToken,
        },
      }));
    } catch (error) {
      console.error(error);
    }
  }

  if (!preserveStatus) {
    await fetchStatus().catch(() => {
      setStatus("En attente", "Aucune session active.");
    });
  }

  closeVoiceEventStream();
  resetVoiceState();
  closeObjectSearchEventStream();
  resetObjectSearchState();
  closeModeEventStream();
  startModeStatusPolling();
  openModeEventStream();
  fetchModeStatus().catch((error) => {
    console.error(error);
  });
}

async function fetchArduinoStatus() {
  const payload = await requestJson("/api/arduino/status");
  applyArduinoStatus(payload, { replaceLog: true });
}

async function fetchArduinoPorts() {
  const payload = await requestJson("/api/arduino/ports");
  renderArduinoPortOptions(payload.ports || []);
}

function closeArduinoEventStream() {
  if (state.arduino.eventSource) {
    state.arduino.eventSource.close();
    state.arduino.eventSource = null;
  }
}

function openArduinoEventStream() {
  closeArduinoEventStream();
  const eventSource = new EventSource("/api/arduino/events");
  state.arduino.eventSource = eventSource;

  eventSource.addEventListener("status", (event) => {
    const payload = JSON.parse(event.data);
    applyArduinoStatus(payload);
  });

  eventSource.addEventListener("telemetry", (event) => {
    const payload = JSON.parse(event.data);
    applyArduinoTelemetry(payload);
  });

  eventSource.addEventListener("frame", (event) => {
    const payload = JSON.parse(event.data);
    appendProtocolLogLine(payload);
  });

  eventSource.addEventListener("error", () => {
    if (!state.arduino.debugVisible) {
      return;
    }
    showArduinoError("Le flux temps reel Arduino a ete interrompu.");
  });
}

async function setArduinoDebugMode(enabled) {
  if (!hasSenderPrivileges()) {
    throw new Error(
        "Connecte-toi comme sender pour modifier le mode debug Arduino.",
    );
  }
  const payload = await requestJson("/api/arduino/debug", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
    includeSessionToken: true,
  });
  applyArduinoStatus(payload);
}

async function connectArduino() {
  if (!hasSenderPrivileges()) {
    throw new Error("Connecte-toi comme sender pour controler l'Arduino.");
  }
  clearArduinoError();
  state.arduino.selectedPort = elements.arduinoPortSelect.value;
  const payload = await requestJson("/api/arduino/connection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ port: state.arduino.selectedPort }),
    includeSessionToken: true,
  });
  applyArduinoStatus(payload, { replaceLog: true });
}

async function disconnectArduino() {
  if (!hasSenderPrivileges()) {
    throw new Error("Connecte-toi comme sender pour controler l'Arduino.");
  }
  clearArduinoError();
  const payload = await requestJson("/api/arduino/connection", {
    method: "DELETE",
    includeSessionToken: true,
  });
  applyArduinoStatus(payload, { replaceLog: true });
}

async function sendArduinoCommand() {
  if (!hasSenderPrivileges() || !state.arduino.status?.connected) {
    return;
  }

  clearArduinoError();
  state.arduino.commandInFlight = true;
  const commandToSend = {
    servoAngleDegrees: state.arduino.command.servoAngleDegrees,
    vibrationEnabled: state.arduino.command.vibrationEnabled,
  };
  state.arduino.pendingCommandAck = { ...commandToSend };
  const commandEndpoint = state.arduino.status.debugEnabled
    ? "/api/arduino/debug/command"
    : "/api/arduino/command";
  try {
    const payload = await requestJson(commandEndpoint, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        servoAngleDegrees: commandToSend.servoAngleDegrees,
        vibrationEnabled: commandToSend.vibrationEnabled,
      }),
      includeSessionToken: true,
    });
    state.arduino.commandDirty = false;
    applyArduinoStatus(payload);
  } finally {
    state.arduino.commandInFlight = false;
  }
}

function scheduleArduinoCommandSync() {
  if (state.arduino.commandSyncTimeout !== null) {
    window.clearTimeout(state.arduino.commandSyncTimeout);
  }
  state.arduino.commandSyncTimeout = window.setTimeout(() => {
    state.arduino.commandSyncTimeout = null;
    sendArduinoCommand().catch((error) => {
      console.error(error);
      showArduinoError(error.message || "Le controle manuel a echoue.");
    });
  }, 100);
}

function updateArduinoDebugVisibility() {
  elements.arduinoDebugPanel.hidden = !state.arduino.debugVisible;
  elements.debugToggleButton.textContent = state.arduino.debugVisible
    ? "Masquer Arduino"
    : (hasSenderPrivileges()
      ? "Activer le debug Arduino"
      : "Voir Arduino (lecture seule)");
}

function toggleVoiceDebugPanel() {
  state.voice.debugVisible = !state.voice.debugVisible;
  updateVoiceDebugVisibility();
}

async function toggleArduinoDebugPanel() {
  if (!state.arduino.debugVisible) {
    state.arduino.debugVisible = true;
    updateArduinoDebugVisibility();
    clearArduinoError();

    try {
      await Promise.all([fetchArduinoStatus(), fetchArduinoPorts()]);
      openArduinoEventStream();
      if (hasSenderPrivileges()) {
        await setArduinoDebugMode(true);
      }
    } catch (error) {
      console.error(error);
      showArduinoError(error.message || "Impossible d'ouvrir le panneau Arduino.");
    }
    return;
  }

  closeArduinoEventStream();
  if (state.arduino.commandSyncTimeout !== null) {
    window.clearTimeout(state.arduino.commandSyncTimeout);
    state.arduino.commandSyncTimeout = null;
  }

  if (hasSenderPrivileges()) {
    try {
      await setArduinoDebugMode(false);
    } catch (error) {
      console.error(error);
    }
  }

  state.arduino.debugVisible = false;
  updateArduinoDebugVisibility();
  clearArduinoError();
}

function updateArduinoCommandState(command) {
  state.arduino.command = {
    servoAngleDegrees: clampServoAngle(command.servoAngleDegrees),
    vibrationEnabled: Boolean(command.vibrationEnabled),
  };
  // Keep local edits visible until the backend acknowledges the command.
  state.arduino.commandDirty = true;
  syncArduinoCommandInputs(state.arduino.command);
}

elements.connectButton.addEventListener("click", () => {
  connect().catch((error) => {
    console.error(error);
    showError("Une erreur inattendue est survenue.");
  });
});

elements.disconnectButton.addEventListener("click", () => {
  disconnect().catch((error) => {
    console.error(error);
    showError("La deconnexion a echoue.");
  });
});

elements.roleSelect.addEventListener("change", (event) => {
  state.requestedRole = event.target.value;
  clearError();
  updateProtectedControls();
});

elements.objectSearchVisionModelSelect.addEventListener("change", (event) => {
  updateObjectSearchVisionModel(event.target.value).catch((error) => {
    console.error(error);
  });
});

elements.voiceDebugToggleButton.addEventListener("click", () => {
  ensureVoiceAudioContext();
  toggleVoiceDebugPanel();
});

elements.debugToggleButton.addEventListener("click", () => {
  toggleArduinoDebugPanel().catch((error) => {
    console.error(error);
    showArduinoError("Impossible de changer le mode debug Arduino.");
  });
});

elements.arduinoRefreshPortsButton.addEventListener("click", () => {
  fetchArduinoPorts().catch((error) => {
    console.error(error);
    showArduinoError(error.message || "Impossible d'actualiser les ports serie.");
  });
});

elements.arduinoPortSelect.addEventListener("change", (event) => {
  state.arduino.selectedPort = event.target.value;
});

elements.arduinoConnectButton.addEventListener("click", () => {
  connectArduino().catch((error) => {
    console.error(error);
    showArduinoError(error.message || "La connexion Arduino a echoue.");
  });
});

elements.arduinoDisconnectButton.addEventListener("click", () => {
  disconnectArduino().catch((error) => {
    console.error(error);
    showArduinoError(error.message || "La deconnexion Arduino a echoue.");
  });
});

elements.arduinoServoRange.addEventListener("input", (event) => {
  updateArduinoCommandState({
    servoAngleDegrees: event.target.value,
    vibrationEnabled: state.arduino.command.vibrationEnabled,
  });
  scheduleArduinoCommandSync();
});

elements.arduinoServoNumber.addEventListener("change", (event) => {
  updateArduinoCommandState({
    servoAngleDegrees: event.target.value,
    vibrationEnabled: state.arduino.command.vibrationEnabled,
  });
  scheduleArduinoCommandSync();
});

elements.arduinoVibrationToggle.addEventListener("change", (event) => {
  updateArduinoCommandState({
    servoAngleDegrees: state.arduino.command.servoAngleDegrees,
    vibrationEnabled: event.target.checked,
  });
  scheduleArduinoCommandSync();
});

elements.arduinoCenterButton.addEventListener("click", () => {
  updateArduinoCommandState({
    servoAngleDegrees: NEUTRAL_SERVO_ANGLE,
    vibrationEnabled: state.arduino.command.vibrationEnabled,
  });
  sendArduinoCommand().catch((error) => {
    console.error(error);
    showArduinoError(error.message || "Impossible de centrer le servo.");
  });
});

elements.arduinoAllStopButton.addEventListener("click", () => {
  updateArduinoCommandState({
    servoAngleDegrees: NEUTRAL_SERVO_ANGLE,
    vibrationEnabled: false,
  });
  sendArduinoCommand().catch((error) => {
    console.error(error);
    showArduinoError(error.message || "Impossible d'envoyer le all stop.");
  });
});

window.addEventListener("beforeunload", () => {
  stopStatusPolling();
  stopVoiceStatusPolling();
  stopObjectSearchStatusPolling();
  stopModeStatusPolling();
  clearVoiceReconnectTimer();
  clearObjectSearchReconnectTimer();
  clearModeReconnectTimer();
  closeVoiceEventStream();
  closeObjectSearchEventStream();
  closeModeEventStream();
  closeArduinoEventStream();
  if (state.peerConnection) {
    state.peerConnection.close();
  }
  if (state.localStream) {
    state.localStream.getTracks().forEach((track) => track.stop());
  }
});

updateVoiceDebugVisibility();
state.requestedRole = selectedRole();
renderObjectSearchVisionModelOptions();
resetVoiceState();
resetObjectSearchState();
applyModeStatus({ mode: "idle", detail: "Mode idle." });
updateArduinoDebugVisibility();
setArduinoManualControlsEnabled(false);
applyArduinoTelemetry(null);
renderProtocolLog();
updateProtectedControls();

fetchStatus().catch(() => {
  setStatus("Serveur", "Le serveur est pret, mais le statut initial n'a pas pu etre lu.");
});

fetchVoiceStatus().catch(() => {
  elements.voiceAvailabilityDetail.textContent =
    "Le statut voix initial n'a pas pu etre lu.";
});

fetchObjectSearchStatus().catch(() => {
  elements.objectSearchDetail.textContent =
    "Le statut initial de recherche d'objet n'a pas pu etre lu.";
});

fetchModeStatus().catch(() => {
  elements.activeModeDetail.textContent =
    "Le statut initial du mode actif n'a pas pu etre lu.";
});
startModeStatusPolling();
openModeEventStream();
