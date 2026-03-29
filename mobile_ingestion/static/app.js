const STORAGE_KEY = "mobileIngestion.voiceSettings.v1";

const elements = {
  orb: document.getElementById("orb"),
  title: document.getElementById("status-title"),
  detail: document.getElementById("status-detail"),
  preview: document.getElementById("local-preview"),
  wakePhraseInput: document.getElementById("wake-phrase-input"),
  idleTimeoutInput: document.getElementById("voice-idle-timeout-input"),
  saveSettingsButton: document.getElementById("voice-save-settings-button"),
  toggleVoiceButton: document.getElementById("voice-toggle-button"),
  voiceStatusBadge: document.getElementById("voice-status-badge"),
  voiceStatusDetail: document.getElementById("voice-status-detail"),
  voiceLastCommand: document.getElementById("voice-last-command"),
};

const bootstrapConfig = window.mobileIngestionConfig || {};
const showVoiceControls = Boolean(bootstrapConfig.showVoiceControls);
const elevenlabsAgentId = typeof bootstrapConfig.elevenlabsAgentId === "string"
  ? bootstrapConfig.elevenlabsAgentId.trim()
  : "";
const useBrowserElevenLabsAgent = Boolean(elevenlabsAgentId) && !showVoiceControls;
const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;

let speechRecognition = null;
let listening = false;
let inactivityTimer = null;
let audioContext = null;
let pendingAudio = null;
let unlockHandlersInstalled = false;
let awaitingCommand = false;
let awaitingCommandTimer = null;
let elevenLabsConversation = null;
let elevenLabsSdkPromise = null;

const defaultSettings = {
  wakePhrases: Array.isArray(bootstrapConfig.voiceWakePhrases)
    ? bootstrapConfig.voiceWakePhrases
    : ["ok jarvis", "okay jarvis"],
  idleTimeoutSeconds: Number.isFinite(Number(bootstrapConfig.voiceIdleTimeoutSeconds))
    ? Math.max(1, Number(bootstrapConfig.voiceIdleTimeoutSeconds))
    : 180,
};

let settings = { ...defaultSettings };

function setOrbState(state) {
  if (!elements.orb) {
    return;
  }
  elements.orb.className = "orb";
  if (state) {
    elements.orb.classList.add(`state-${state}`);
  }
}

function updateStatus(title, detail, state = "idle") {
  if (elements.title) {
    elements.title.textContent = title;
  }
  if (elements.detail) {
    elements.detail.textContent = detail;
  }
  setOrbState(state);
}

function updateVoiceBadge() {
  if (!elements.voiceStatusBadge || !elements.voiceStatusDetail) {
    return;
  }
  if (listening) {
    elements.voiceStatusBadge.textContent = "Ecoute active";
    elements.voiceStatusDetail.textContent = showVoiceControls
      ? `Phrases: ${settings.wakePhrases.join(", ")} | Auto-off: ${settings.idleTimeoutSeconds}s`
      : `Auto-off: ${settings.idleTimeoutSeconds}s`;
    setOrbState("listening");
  } else {
    elements.voiceStatusBadge.textContent = "Desactivee";
    elements.voiceStatusDetail.textContent = showVoiceControls
      ? `Auto-off: ${settings.idleTimeoutSeconds}s | Phrases: ${settings.wakePhrases.join(", ")}`
      : `Auto-off: ${settings.idleTimeoutSeconds}s`;
    setOrbState("idle");
  }
}

function installAudioUnlockHandlers() {
  if (unlockHandlersInstalled) {
    return;
  }
  const unlockOnGesture = () => {
    void unlockAudioPlayback();
  };
  window.addEventListener("pointerdown", unlockOnGesture, { passive: true });
  window.addEventListener("touchstart", unlockOnGesture, { passive: true });
  window.addEventListener("click", unlockOnGesture, { passive: true });
  unlockHandlersInstalled = true;
}

async function unlockAudioPlayback() {
  try {
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
    if (AudioContextCtor && !audioContext) {
      audioContext = new AudioContextCtor();
    }
    if (audioContext && audioContext.state === "suspended") {
      await audioContext.resume();
    }

    if (pendingAudio) {
      const audioToPlay = pendingAudio;
      pendingAudio = null;
      await audioToPlay.play();
      await new Promise((resolve) => {
        audioToPlay.onended = resolve;
        audioToPlay.onerror = resolve;
      });
      if (listening) {
        updateStatus("Pret", "En ecoute", "listening");
      } else {
        updateStatus("Pret", "Audio active", "idle");
      }
      updateVoiceBadge();
    }
  } catch (error) {
    console.debug("Audio unlock still blocked", error);
  }
}

function loadSettings() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return;
    }
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed.wakePhrases) && parsed.wakePhrases.length > 0) {
      settings.wakePhrases = parsed.wakePhrases.map((item) => String(item).toLowerCase());
    }
    if (Number.isFinite(Number(parsed.idleTimeoutSeconds))) {
      settings.idleTimeoutSeconds = Math.max(1, Number(parsed.idleTimeoutSeconds));
    }
  } catch (error) {
    console.warn("Unable to load voice settings", error);
  }
}

function saveSettings() {
  if (elements.wakePhraseInput) {
    const phrases = elements.wakePhraseInput.value
      .split(",")
      .map((item) => item.trim().toLowerCase())
      .filter(Boolean);
    settings.wakePhrases = phrases.length > 0 ? phrases : [...defaultSettings.wakePhrases];
  }
  if (elements.idleTimeoutInput) {
    const idle = Number(elements.idleTimeoutInput.value);
    settings.idleTimeoutSeconds = Number.isFinite(idle) ? Math.max(1, idle) : defaultSettings.idleTimeoutSeconds;
  }

  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  syncInputs();
  updateVoiceBadge();
}

function syncInputs() {
  if (elements.wakePhraseInput) {
    elements.wakePhraseInput.value = settings.wakePhrases.join(", ");
  }
  if (elements.idleTimeoutInput) {
    elements.idleTimeoutInput.value = String(settings.idleTimeoutSeconds);
  }
}

function clearInactivityTimer() {
  if (inactivityTimer) {
    window.clearTimeout(inactivityTimer);
    inactivityTimer = null;
  }
}

function clearAwaitingCommand() {
  awaitingCommand = false;
  if (awaitingCommandTimer) {
    window.clearTimeout(awaitingCommandTimer);
    awaitingCommandTimer = null;
  }
}

function armAwaitingCommand() {
  clearAwaitingCommand();
  awaitingCommand = true;
  updateStatus("Pret", "Je vous ecoute, dites votre commande", "listening");
  void speakWakeAcknowledgement();
  if (elements.voiceLastCommand) {
    elements.voiceLastCommand.textContent = "En attente de commande...";
  }
  awaitingCommandTimer = window.setTimeout(() => {
    clearAwaitingCommand();
    if (listening) {
      updateStatus("Pret", "En ecoute", "listening");
    }
  }, 8000);
}

async function speakWakeAcknowledgement() {
  if (useBrowserElevenLabsAgent) {
    updateStatus("Pret", "Agent Jarvis actif", "listening");
    return;
  }
  try {
    const response = await fetch("/api/webrtc/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        forceAgent: true,
        text: "Reponds exactement: Oui, je t'ecoute.",
      }),
    });
    const data = await response.json();
    if (data.command !== "agent_response" || !data.result || !data.result.audioData) {
      return;
    }

    const audio = new Audio("data:audio/mp3;base64," + data.result.audioData);
    try {
      await audio.play();
      await new Promise((resolve) => {
        audio.onended = resolve;
        audio.onerror = resolve;
      });
    } catch (error) {
      pendingAudio = audio;
      updateStatus("Activer le Son", "Click requis par le navigateur", "error");
      if (elements.voiceStatusDetail) {
        elements.voiceStatusDetail.textContent = "Activer le Son (Click requis par le navigateur)";
      }
    }
  } catch (error) {
    console.debug("Wake acknowledgement via agent unavailable", error);
  }
}

function updateFromConvaiMessage(message) {
  if (!message || typeof message !== "object") {
    return;
  }
  if (message.type === "agent_response") {
    const text = typeof message.text === "string" ? message.text : "Reponse de Jarvis";
    updateStatus("Reponse de Jarvis", text, "speaking");
  }
}

async function getElevenLabsConversation() {
  if (!useBrowserElevenLabsAgent) {
    return null;
  }
  if (elevenLabsConversation) {
    return elevenLabsConversation;
  }
  if (!elevenLabsSdkPromise) {
    elevenLabsSdkPromise = import("https://cdn.jsdelivr.net/npm/@elevenlabs/client/+esm");
  }

  const module = await elevenLabsSdkPromise;
  const Conversation = module && module.Conversation;
  if (!Conversation || typeof Conversation.startSession !== "function") {
    throw new Error("ElevenLabs SDK indisponible");
  }

  elevenLabsConversation = await Conversation.startSession({
    agentId: elevenlabsAgentId,
    onConnect: () => {
      updateStatus("Pret", "Connecte a l'agent Jarvis", "listening");
    },
    onDisconnect: () => {
      elevenLabsConversation = null;
      if (listening) {
        updateStatus("Pret", "Session agent fermee, reconnexion automatique", "listening");
      }
    },
    onMessage: (message) => {
      updateFromConvaiMessage(message);
    },
    onError: (message) => {
      const detail = typeof message === "string" && message.trim()
        ? message
        : "Erreur agent ElevenLabs";
      updateStatus("Erreur Agent", detail, "error");
    },
  });

  return elevenLabsConversation;
}

async function sendToElevenLabsBrowserAgent(commandText) {
  const conversation = await getElevenLabsConversation();
  if (!conversation || typeof conversation.sendUserMessage !== "function") {
    throw new Error("Session agent indisponible");
  }
  conversation.sendUserMessage(commandText);
}

function scheduleAutoOff() {
  clearInactivityTimer();
  if (!listening) {
    return;
  }
  inactivityTimer = window.setTimeout(() => {
    stopListening("Auto-off apres inactivite");
  }, settings.idleTimeoutSeconds * 1000);
}

function stopListening(reason) {
  listening = false;
  clearInactivityTimer();
  clearAwaitingCommand();
  if (speechRecognition) {
    try {
      speechRecognition.stop();
    } catch (error) {
      console.debug("Unable to stop recognition", error);
    }
  }
  updateStatus("Pret", reason, "idle");
  updateVoiceBadge();
}

function hasWakePhrase(text) {
  const normalized = text.toLowerCase();
  return settings.wakePhrases.some((phrase) => normalized.includes(phrase));
}

function isVisualLocatorQuery(text) {
  const normalized = String(text || "").toLowerCase();
  const asksLocation = /(where|find|locate|spot|ou\s+est|ou\s+sont|trouve|trouver|reper)/.test(normalized);
  const likelyStatusCommand = /(status|state|health|ping|connect|disconnect)/.test(normalized);
  return asksLocation && !likelyStatusCommand;
}

async function sendCommand(commandText) {
  if (!commandText) {
    return;
  }
  clearAwaitingCommand();

  if (elements.voiceLastCommand) {
    elements.voiceLastCommand.textContent = commandText;
  }
  // Pause recognition briefly so it doesn't hear itself speak
  if (speechRecognition) {
    try { speechRecognition.stop(); } catch(e){}
  }
  updateStatus("Traitement", `Commande: ${commandText}`, "speaking");

  try {
    const wantsVisualLocator = isVisualLocatorQuery(commandText);

    if (useBrowserElevenLabsAgent && !wantsVisualLocator) {
      await sendToElevenLabsBrowserAgent(commandText);
      updateStatus("Agent Jarvis", "Commande transmise a ElevenLabs", "speaking");
      return;
    }

    const res = await fetch("/api/webrtc/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ 
        command: commandText,
        text: commandText,
        forceAgent: !wantsVisualLocator,
        forceLocator: wantsVisualLocator,
        payload: { text: commandText, source: "voice", final: true }
      }),
    });
    
    const data = await res.json();
    const resultText = data && data.result && typeof data.result.text === "string"
      ? data.result.text
      : "Reponse indisponible";
    if (data.command === "agent_response") {
      updateStatus("Reponse de Jarvis", resultText, "speaking");
      if (data.result && data.result.audioData) {
        const audio = new Audio("data:audio/mp3;base64," + data.result.audioData);
        try {
          await audio.play();
          await new Promise((resolve) => {
            audio.onended = resolve;
            audio.onerror = resolve;
          });
        } catch (error) {
          // Browser requires explicit user interaction before playing assistant audio.
          pendingAudio = audio;
          updateStatus("Activer le Son", "Click requis par le navigateur", "error");
          if (elements.voiceStatusDetail) {
            elements.voiceStatusDetail.textContent = "Activer le Son (Click requis par le navigateur)";
          }
          return;
        }
      }
    } else if (data.command === "agent_error") {
      updateStatus("Erreur Agent", resultText, "error");
    }
  } catch (error) {
    console.warn("Command request failed", error);
  } finally {
    if (listening) {
      updateStatus("Pret", "En ecoute", "listening");
      if (speechRecognition) {
        try { speechRecognition.start(); } catch(e){}
      }
    }
  }
}

function extractCommand(transcript) {
  const normalized = transcript.trim();
  if (!normalized) {
    return "";
  }
  const lower = normalized.toLowerCase();
  for (const phrase of settings.wakePhrases) {
    const index = lower.indexOf(phrase.toLowerCase());
    if (index >= 0) {
      return normalized.slice(index + phrase.length).trim();
    }
  }
  return "";
}

function setupSpeechRecognition() {
  if (!SpeechRecognitionCtor) {
    updateStatus("Non supporte", "Votre navigateur ne supporte pas SpeechRecognition", "error");
    return;
  }

  speechRecognition = new SpeechRecognitionCtor();
  speechRecognition.continuous = true;
  speechRecognition.interimResults = false;
  speechRecognition.lang = "fr-FR";

  speechRecognition.addEventListener("result", (event) => {
    scheduleAutoOff();
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      if (!event.results[i].isFinal) {
        continue;
      }
      const transcript = String(event.results[i][0].transcript || "").trim();
      if (!transcript) {
        continue;
      }
      if (hasWakePhrase(transcript)) {
        const commandText = extractCommand(transcript);
        if (commandText) {
          void sendCommand(commandText);
        } else {
          armAwaitingCommand();
        }
        continue;
      }

      if (awaitingCommand) {
        void sendCommand(transcript);
      }
    }
  });

  speechRecognition.addEventListener("end", () => {
    if (!listening) {
      return;
    }
    try {
      speechRecognition.start();
    } catch (error) {
      console.debug("Unable to restart recognition", error);
    }
  });
}

async function initializeMedia() {
  if (!elements.preview || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: true,
      video: { facingMode: { ideal: "environment" } },
    });
    elements.preview.srcObject = stream;
  } catch (error) {
    console.warn("Media access denied or unavailable", error);
  }
}

function startListening() {
  if (!speechRecognition) {
    setupSpeechRecognition();
  }
  if (!speechRecognition) {
    return;
  }

  listening = true;
  updateStatus("Pret", "En ecoute", "listening");
  updateVoiceBadge();
  scheduleAutoOff();

  try {
    speechRecognition.start();
  } catch (error) {
    console.debug("Unable to start recognition", error);
  }
}

function toggleListening() {
  if (listening) {
    updateStatus("Pret", "En ecoute", "listening");
    scheduleAutoOff();
    return;
  }
  startListening();
}

function init() {
  loadSettings();
  syncInputs();
  updateStatus("Pret", "Initialisation terminee", "idle");
  updateVoiceBadge();
  installAudioUnlockHandlers();
  void initializeMedia();
  setupSpeechRecognition();

  if (elements.saveSettingsButton) {
    elements.saveSettingsButton.addEventListener("click", saveSettings);
  }
  if (elements.toggleVoiceButton) {
    elements.toggleVoiceButton.addEventListener("click", toggleListening);
  }

  // Hands-free mode: start listening automatically for end users.
  startListening();
}

window.addEventListener("DOMContentLoaded", init);
