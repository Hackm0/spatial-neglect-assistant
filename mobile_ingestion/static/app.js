const elements = {
  connectButton: document.getElementById("connect-button"),
  disconnectButton: document.getElementById("disconnect-button"),
  errorMessage: document.getElementById("error-message"),
  preview: document.getElementById("local-preview"),
  statusBadge: document.getElementById("status-badge"),
  statusDetail: document.getElementById("status-detail"),
  voiceStatusBadge: document.getElementById("voice-status-badge"),
  voiceStatusDetail: document.getElementById("voice-status-detail"),
  voiceLastCommand: document.getElementById("voice-last-command"),
  wakePhraseInput: document.getElementById("wake-phrase-input"),
  voiceIdleTimeoutInput: document.getElementById("voice-idle-timeout-input"),
  voiceSaveSettingsButton: document.getElementById("voice-save-settings-button"),
  voiceToggleButton: document.getElementById("voice-toggle-button"),
};

let localStream = null;
let peerConnection = null;
let statusInterval = null;
let speechRecognition = null;
let wakeWordListeningEnabled = false;
let awaitingVoiceCommand = false;
let commandDeadlineAt = 0;
let inactivityTimerId = null;

const COMMAND_CAPTURE_WINDOW_MS = 12000;
const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
const SETTINGS_STORAGE_KEY = "mobileIngestion.voiceSettings.v1";
const DEFAULT_WAKE_WORDS = Array.isArray(window.APP_CONFIG?.voiceWakePhrases)
  ? window.APP_CONFIG.voiceWakePhrases
  : ["ok jarvis", "okay jarvis"];
const DEFAULT_IDLE_TIMEOUT_SECONDS = Number(window.APP_CONFIG?.voiceIdleTimeoutSeconds || 180);

let activeWakeWords = DEFAULT_WAKE_WORDS;
let activeIdleTimeoutSeconds = DEFAULT_IDLE_TIMEOUT_SECONDS;

function setStatus(label, detail) {
  elements.statusBadge.textContent = label;
  elements.statusDetail.textContent = detail;
}

function showError(message) {
  elements.errorMessage.hidden = false;
  elements.errorMessage.textContent = message;
}

function clearError() {
  elements.errorMessage.hidden = true;
  elements.errorMessage.textContent = "";
}

function setBusy(isBusy) {
  elements.connectButton.disabled = isBusy;
  elements.disconnectButton.disabled = !isBusy;
}

function setVoiceState(label, detail, stateClass = "") {
  elements.voiceStatusBadge.textContent = label;
  elements.voiceStatusDetail.textContent = detail;
  elements.voiceStatusBadge.classList.remove("voice-state-listening", "voice-state-awaiting");
  if (stateClass) {
    elements.voiceStatusBadge.classList.add(stateClass);
  }
}

function setLastVoiceCommand(commandText) {
  elements.voiceLastCommand.textContent = `Derniere commande: ${commandText}`;
}

function normalizeSpeechText(text) {
  return text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function sanitizeWakeWords(rawWakeWords) {
  if (!Array.isArray(rawWakeWords)) {
    return ["ok jarvis", "okay jarvis"];
  }

  const normalizedWakeWords = rawWakeWords
    .map((item) => normalizeSpeechText(String(item || "")))
    .filter((item) => item.length > 0);

  return normalizedWakeWords.length > 0 ? [...new Set(normalizedWakeWords)] : ["ok jarvis", "okay jarvis"];
}

function sanitizeIdleTimeoutSeconds(rawValue) {
  const parsed = Number(rawValue);
  if (!Number.isFinite(parsed)) {
    return 180;
  }
  const rounded = Math.round(parsed);
  if (rounded < 5) {
    return 5;
  }
  if (rounded > 3600) {
    return 3600;
  }
  return rounded;
}

function parseWakeWordsInput(rawValue) {
  const parts = String(rawValue || "")
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
  return sanitizeWakeWords(parts);
}

function updateSettingsInputs() {
  elements.wakePhraseInput.value = activeWakeWords.join(", ");
  elements.voiceIdleTimeoutInput.value = String(activeIdleTimeoutSeconds);
}

function loadPersistedVoiceSettings() {
  try {
    const raw = window.localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (!raw) {
      activeWakeWords = sanitizeWakeWords(DEFAULT_WAKE_WORDS);
      activeIdleTimeoutSeconds = sanitizeIdleTimeoutSeconds(DEFAULT_IDLE_TIMEOUT_SECONDS);
      updateSettingsInputs();
      return;
    }

    const parsed = JSON.parse(raw);
    activeWakeWords = sanitizeWakeWords(parsed.wakeWords);
    activeIdleTimeoutSeconds = sanitizeIdleTimeoutSeconds(parsed.idleTimeoutSeconds);
    updateSettingsInputs();
  } catch (error) {
    console.error(error);
    activeWakeWords = sanitizeWakeWords(DEFAULT_WAKE_WORDS);
    activeIdleTimeoutSeconds = sanitizeIdleTimeoutSeconds(DEFAULT_IDLE_TIMEOUT_SECONDS);
    updateSettingsInputs();
  }
}

function persistVoiceSettings() {
  window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify({
    wakeWords: activeWakeWords,
    idleTimeoutSeconds: activeIdleTimeoutSeconds,
  }));
}

function applyVoiceSettings() {
  const nextWakeWords = parseWakeWordsInput(elements.wakePhraseInput.value);
  const nextIdleTimeoutSeconds = sanitizeIdleTimeoutSeconds(elements.voiceIdleTimeoutInput.value);

  activeWakeWords = nextWakeWords;
  activeIdleTimeoutSeconds = nextIdleTimeoutSeconds;
  updateSettingsInputs();
  persistVoiceSettings();

  setVoiceState(
    wakeWordListeningEnabled ? "Ecoute active" : "Desactivee",
    `Wake phrases: ${activeWakeWords.join(" / ")} | Auto-off: ${activeIdleTimeoutSeconds}s`,
    wakeWordListeningEnabled ? "voice-state-listening" : "",
  );

  if (wakeWordListeningEnabled) {
    restartInactivityTimer();
  }
}

function clearInactivityTimer() {
  if (inactivityTimerId !== null) {
    window.clearTimeout(inactivityTimerId);
    inactivityTimerId = null;
  }
}

function restartInactivityTimer() {
  clearInactivityTimer();
  if (!wakeWordListeningEnabled) {
    return;
  }
  inactivityTimerId = window.setTimeout(() => {
    stopWakeWordListening("Auto-off active apres inactivite.");
  }, activeIdleTimeoutSeconds * 1000);
}

function extractWakeWordPayload(normalizedText) {
  for (const wakeWord of activeWakeWords) {
    const position = normalizedText.indexOf(wakeWord);
    if (position !== -1) {
      const afterWake = normalizedText.slice(position + wakeWord.length).trim();
      return {
        matched: true,
        commandText: afterWake,
      };
    }
  }

  return {
    matched: false,
    commandText: "",
  };
}

function inferLocalVoiceIntent(commandText) {
  const normalized = normalizeSpeechText(commandText);

  if (!normalized) {
    return "unknown";
  }

  if (/\b(connect|reconnect|start|demarre|ouvre|open)\b/.test(normalized)) {
    return "connect";
  }

  if (/\b(disconnect|stop|arrete|close|shutdown|quit)\b/.test(normalized)) {
    return "disconnect";
  }

  if (/\b(status|statut|state|health)\b/.test(normalized)) {
    return "status";
  }

  return "server";
}

function beginAwaitingVoiceCommand() {
  awaitingVoiceCommand = true;
  commandDeadlineAt = Date.now() + COMMAND_CAPTURE_WINDOW_MS;
  restartInactivityTimer();
  setVoiceState(
    "En attente",
    "Wake word detecte. Prononcez votre commande maintenant.",
    "voice-state-awaiting",
  );
}

function resetAwaitingVoiceCommand() {
  awaitingVoiceCommand = false;
  commandDeadlineAt = 0;
  if (wakeWordListeningEnabled) {
    restartInactivityTimer();
    setVoiceState(
      "Ecoute active",
      "Dites \"Ok Jarvis\" pour demarrer une commande.",
      "voice-state-listening",
    );
  }
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
  const response = await fetch("/api/webrtc/status");
  const payload = await response.json();
  const detail = payload.active
    ? `Session ${payload.state}, etat pair: ${payload.connectionState}.`
    : payload.error || "Aucune session active.";
  setStatus(payload.state, detail);
}

function startStatusPolling() {
  stopStatusPolling();
  statusInterval = window.setInterval(() => {
    fetchStatus().catch(() => {
      showError("Impossible de joindre le serveur pour recuperer le statut.");
    });
  }, 2000);
}

function stopStatusPolling() {
  if (statusInterval !== null) {
    window.clearInterval(statusInterval);
    statusInterval = null;
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
    localStream = await requestLocalStream();
    elements.preview.srcObject = localStream;

    peerConnection = buildPeerConnection();
    localStream.getTracks().forEach((track) => {
      peerConnection.addTrack(track, localStream);
    });

    const offer = await peerConnection.createOffer();
    await peerConnection.setLocalDescription(offer);
    await waitForIceGatheringComplete(peerConnection);

    setStatus("Negociation", "Envoi de l'offre WebRTC au serveur...");
    const response = await fetch("/api/webrtc/offer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(peerConnection.localDescription),
    });

    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "La negociation WebRTC a echoue.");
    }

    await peerConnection.setRemoteDescription(payload);
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

  if (peerConnection) {
    peerConnection.getSenders().forEach((sender) => sender.track?.stop());
    peerConnection.close();
    peerConnection = null;
  }

  if (localStream) {
    localStream.getTracks().forEach((track) => track.stop());
    localStream = null;
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

async function executeVoiceCommand(commandText) {
  const cleaned = commandText.trim();
  if (!cleaned) {
    setVoiceState(
      "En attente",
      "Commande vide. Dites \"Ok Jarvis\" puis une commande claire.",
      "voice-state-awaiting",
    );
    return;
  }

  setLastVoiceCommand(cleaned);
  const localIntent = inferLocalVoiceIntent(cleaned);

  try {
    if (localIntent === "connect") {
      await connect();
      setVoiceState(
        "Commande executee",
        "Connexion lancee depuis la commande vocale.",
        "voice-state-listening",
      );
      return;
    }

    if (localIntent === "disconnect") {
      await disconnect();
      setVoiceState(
        "Commande executee",
        "Deconnexion lancee depuis la commande vocale.",
        "voice-state-listening",
      );
      return;
    }

    if (localIntent === "status") {
      await fetchStatus();
      setVoiceState(
        "Commande executee",
        "Statut serveur mis a jour.",
        "voice-state-listening",
      );
      return;
    }

    const response = await fetch("/api/webrtc/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        command: cleaned,
        payload: {
          text: cleaned,
          source: "voice",
          final: true,
        },
      }),
    });

    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Erreur lors de l'execution de la commande vocale.");
    }

    if (payload.command === "unknown") {
      setVoiceState(
        "Non reconnu",
        "Commande non reconnue. Reessayez apres \"Ok Jarvis\".",
        "voice-state-listening",
      );
      return;
    }

    setVoiceState(
      "Commande executee",
      `Action: ${payload.command}`,
      "voice-state-listening",
    );
  } catch (error) {
    console.error(error);
    setVoiceState(
      "Erreur",
      error.message || "La commande vocale a echoue.",
      "voice-state-listening",
    );
  }
}

function handleSpeechResult(event) {
  restartInactivityTimer();
  for (let i = event.resultIndex; i < event.results.length; i += 1) {
    const result = event.results[i];
    if (!result.isFinal) {
      continue;
    }

    const transcript = result[0]?.transcript || "";
    const normalized = normalizeSpeechText(transcript);
    if (!normalized) {
      continue;
    }

    if (awaitingVoiceCommand && commandDeadlineAt > 0 && Date.now() > commandDeadlineAt) {
      resetAwaitingVoiceCommand();
    }

    if (!awaitingVoiceCommand) {
      const wakeDetection = extractWakeWordPayload(normalized);
      if (!wakeDetection.matched) {
        continue;
      }

      beginAwaitingVoiceCommand();

      if (wakeDetection.commandText) {
        executeVoiceCommand(wakeDetection.commandText).finally(() => {
          resetAwaitingVoiceCommand();
        });
      }
      continue;
    }

    executeVoiceCommand(normalized).finally(() => {
      resetAwaitingVoiceCommand();
    });
  }
}

function ensureSpeechRecognition() {
  if (!SpeechRecognitionCtor) {
    setVoiceState(
      "Indisponible",
      "La reconnaissance vocale n'est pas supportee sur ce navigateur.",
    );
    elements.voiceToggleButton.disabled = true;
    return false;
  }

  if (!speechRecognition) {
    speechRecognition = new SpeechRecognitionCtor();
    speechRecognition.continuous = true;
    speechRecognition.interimResults = false;
    speechRecognition.lang = "fr-FR";
    speechRecognition.addEventListener("result", handleSpeechResult);
    speechRecognition.addEventListener("error", (event) => {
      setVoiceState(
        "Erreur",
        `Reconnaissance vocale indisponible (${event.error}).`,
      );
    });
    speechRecognition.addEventListener("end", () => {
      if (wakeWordListeningEnabled) {
        try {
          speechRecognition.start();
        } catch (error) {
          console.error(error);
        }
      }
    });
  }

  return true;
}

function startWakeWordListening() {
  if (!ensureSpeechRecognition()) {
    return;
  }

  wakeWordListeningEnabled = true;
  resetAwaitingVoiceCommand();
  elements.voiceToggleButton.textContent = "Desactiver l'ecoute vocale";
  restartInactivityTimer();
  setVoiceState(
    "Ecoute active",
    `Dites \"${activeWakeWords[0]}\" pour demarrer une commande. Auto-off ${activeIdleTimeoutSeconds}s.`,
    "voice-state-listening",
  );

  try {
    speechRecognition.start();
  } catch (error) {
    console.error(error);
  }
}

function stopWakeWordListening(detail = "Cliquez pour reactiver l'ecoute wake-word.") {
  wakeWordListeningEnabled = false;
  resetAwaitingVoiceCommand();
  clearInactivityTimer();
  elements.voiceToggleButton.textContent = "Activer l'ecoute vocale";
  setVoiceState(
    "Desactivee",
    detail,
  );

  if (speechRecognition) {
    speechRecognition.stop();
  }
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

elements.voiceToggleButton.addEventListener("click", () => {
  if (wakeWordListeningEnabled) {
    stopWakeWordListening();
    return;
  }

  startWakeWordListening();
});

elements.voiceSaveSettingsButton.addEventListener("click", () => {
  applyVoiceSettings();
});

window.addEventListener("beforeunload", () => {
  stopStatusPolling();
  wakeWordListeningEnabled = false;
  clearInactivityTimer();
  if (speechRecognition) {
    speechRecognition.stop();
  }
  if (peerConnection) {
    peerConnection.close();
  }
  if (localStream) {
    localStream.getTracks().forEach((track) => track.stop());
  }
});

fetchStatus().catch(() => {
  setStatus("Serveur", "Le serveur est pret, mais le statut initial n'a pas pu etre lu.");
});

if (!SpeechRecognitionCtor) {
  setVoiceState(
    "Indisponible",
    "La reconnaissance vocale n'est pas supportee sur ce navigateur.",
  );
  elements.voiceToggleButton.disabled = true;
  elements.voiceSaveSettingsButton.disabled = true;
}

loadPersistedVoiceSettings();
