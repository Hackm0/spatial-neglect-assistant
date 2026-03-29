const MAX_PROTOCOL_LOG_LINES = 500;
const SERVO_MIN_ANGLE = 0;
const SERVO_MAX_ANGLE = 180;
const NEUTRAL_SERVO_ANGLE = 90;

const elements = {
  connectButton: document.getElementById("connect-button"),
  disconnectButton: document.getElementById("disconnect-button"),
  errorMessage: document.getElementById("error-message"),
  preview: document.getElementById("local-preview"),
  statusBadge: document.getElementById("status-badge"),
  statusDetail: document.getElementById("status-detail"),
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
  peerConnection: null,
  statusInterval: null,
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

function showArduinoError(message) {
  showMessage(elements.arduinoErrorMessage, message);
}

function clearArduinoError() {
  clearMessage(elements.arduinoErrorMessage);
}

function setBusy(isBusy) {
  elements.connectButton.disabled = isBusy;
  elements.disconnectButton.disabled = !isBusy;
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

function setArduinoManualControlsEnabled(isEnabled) {
  elements.arduinoServoRange.disabled = !isEnabled;
  elements.arduinoServoNumber.disabled = !isEnabled;
  elements.arduinoVibrationToggle.disabled = !isEnabled;
  elements.arduinoCenterButton.disabled = !isEnabled;
  elements.arduinoAllStopButton.disabled = !isEnabled;
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
  elements.arduinoConnectButton.disabled = !status.available || status.connected;
  elements.arduinoDisconnectButton.disabled = !status.connected;
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
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(payload?.error || "La requete a echoue.");
  }
  return payload;
}

async function requestLocalStream() {
  const preferredConstraints = {
    audio: true,
    video: {
      facingMode: { ideal: "environment" },
      width: { ideal: 1280 },
      height: { ideal: 720 },
    },
  };

  try {
    return await navigator.mediaDevices.getUserMedia(preferredConstraints);
  } catch (error) {
    if (error.name !== "OverconstrainedError" && error.name !== "NotFoundError") {
      throw error;
    }
  }

  return navigator.mediaDevices.getUserMedia({
    audio: true,
    video: true,
  });
}

function buildPeerConnection() {
  const iceServers = (window.APP_CONFIG?.iceServers || []).map((url) => ({ urls: url }));
  const connection = new RTCPeerConnection({ iceServers });

  connection.addEventListener("connectionstatechange", () => {
    const label = `WebRTC ${connection.connectionState}`;
    setStatus(label, "Le navigateur maintient la connexion avec le serveur.");
    if (["failed", "disconnected", "closed"].includes(connection.connectionState)) {
      setBusy(false);
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
  const detail = payload.active
    ? `Session ${payload.state}, etat pair: ${payload.connectionState}.`
    : payload.error || "Aucune session active.";
  setStatus(payload.state, detail);
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

  if (!window.isSecureContext && window.location.hostname !== "localhost") {
    showError("Le navigateur mobile exige HTTPS pour ouvrir camera et micro.");
    return;
  }

  if (!navigator.mediaDevices?.getUserMedia) {
    showError("Ce navigateur ne supporte pas getUserMedia.");
    return;
  }

  setBusy(true);
  setStatus("Preparation", "Demande des permissions camera et micro...");

  try {
    state.localStream = await requestLocalStream();
    elements.preview.srcObject = state.localStream;

    state.peerConnection = buildPeerConnection();
    state.localStream.getTracks().forEach((track) => {
      state.peerConnection.addTrack(track, state.localStream);
    });

    const offer = await state.peerConnection.createOffer();
    await state.peerConnection.setLocalDescription(offer);
    await waitForIceGatheringComplete(state.peerConnection);

    setStatus("Negociation", "Envoi de l'offre WebRTC au serveur...");
    const payload = await requestJson("/api/webrtc/offer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.peerConnection.localDescription),
    });

    await state.peerConnection.setRemoteDescription(payload);
    setStatus("Streaming", "Le flux mobile est connecte au serveur.");
    startStatusPolling();
  } catch (error) {
    console.error(error);
    await disconnect({ notifyServer: true, preserveStatus: true });
    showError(error.message || "La connexion a echoue.");
    setStatus("Erreur", "Le flux n'a pas pu etre etabli.");
  }
}

async function disconnect(options = {}) {
  const { notifyServer = true, preserveStatus = false } = options;

  stopStatusPolling();

  if (state.peerConnection) {
    state.peerConnection.getSenders().forEach((sender) => sender.track?.stop());
    state.peerConnection.close();
    state.peerConnection = null;
  }

  if (state.localStream) {
    state.localStream.getTracks().forEach((track) => track.stop());
    state.localStream = null;
  }

  elements.preview.srcObject = null;
  setBusy(false);

  if (notifyServer) {
    try {
      await fetch("/api/webrtc/session", { method: "DELETE" });
    } catch (error) {
      console.error(error);
    }
  }

  if (!preserveStatus) {
    setStatus("En attente", "Aucune session active.");
  }
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
  const payload = await requestJson("/api/arduino/debug", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  applyArduinoStatus(payload);
}

async function connectArduino() {
  clearArduinoError();
  state.arduino.selectedPort = elements.arduinoPortSelect.value;
  const payload = await requestJson("/api/arduino/connection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ port: state.arduino.selectedPort }),
  });
  applyArduinoStatus(payload, { replaceLog: true });
}

async function disconnectArduino() {
  clearArduinoError();
  const payload = await requestJson("/api/arduino/connection", {
    method: "DELETE",
  });
  applyArduinoStatus(payload, { replaceLog: true });
}

async function sendArduinoCommand() {
  if (!state.arduino.status?.connected) {
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
    ? "Masquer le debug Arduino"
    : "Activer le debug Arduino";
}

async function toggleArduinoDebugPanel() {
  if (!state.arduino.debugVisible) {
    state.arduino.debugVisible = true;
    updateArduinoDebugVisibility();
    clearArduinoError();

    try {
      await Promise.all([fetchArduinoStatus(), fetchArduinoPorts()]);
      openArduinoEventStream();
      await setArduinoDebugMode(true);
    } catch (error) {
      console.error(error);
      showArduinoError(error.message || "Impossible d'activer le debug Arduino.");
    }
    return;
  }

  closeArduinoEventStream();
  if (state.arduino.commandSyncTimeout !== null) {
    window.clearTimeout(state.arduino.commandSyncTimeout);
    state.arduino.commandSyncTimeout = null;
  }

  try {
    await setArduinoDebugMode(false);
  } catch (error) {
    console.error(error);
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
  closeArduinoEventStream();
  if (state.peerConnection) {
    state.peerConnection.close();
  }
  if (state.localStream) {
    state.localStream.getTracks().forEach((track) => track.stop());
  }
});

updateArduinoDebugVisibility();
setArduinoManualControlsEnabled(false);
applyArduinoTelemetry(null);
renderProtocolLog();

fetchStatus().catch(() => {
  setStatus("Serveur", "Le serveur est pret, mais le statut initial n'a pas pu etre lu.");
});
