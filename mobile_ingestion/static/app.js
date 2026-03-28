const elements = {
  connectButton: document.getElementById("connect-button"),
  disconnectButton: document.getElementById("disconnect-button"),
  errorMessage: document.getElementById("error-message"),
  preview: document.getElementById("local-preview"),
  statusBadge: document.getElementById("status-badge"),
  statusDetail: document.getElementById("status-detail"),
};

let localStream = null;
let peerConnection = null;
let statusInterval = null;

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
      showError("La connexion ICE a échoué. Vérifie le Wi-Fi local ou le tunnel HTTPS.");
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
    ? `Session ${payload.state}, état pair: ${payload.connectionState}.`
    : payload.error || "Aucune session active.";
  setStatus(payload.state, detail);
}

function startStatusPolling() {
  stopStatusPolling();
  statusInterval = window.setInterval(() => {
    fetchStatus().catch(() => {
      showError("Impossible de joindre le serveur pour récupérer le statut.");
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
    showError("Le navigateur mobile exige HTTPS pour ouvrir caméra et micro.");
    return;
  }

  if (!navigator.mediaDevices?.getUserMedia) {
    showError("Ce navigateur ne supporte pas getUserMedia.");
    return;
  }

  setBusy(true);
  setStatus("Préparation", "Demande des permissions caméra et micro...");

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

    setStatus("Négociation", "Envoi de l'offre WebRTC au serveur...");
    const response = await fetch("/api/webrtc/offer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(peerConnection.localDescription),
    });

    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "La négociation WebRTC a échoué.");
    }

    await peerConnection.setRemoteDescription(payload);
    setStatus("Streaming", "Le flux mobile est connecté au serveur.");
    startStatusPolling();
  } catch (error) {
    console.error(error);
    await disconnect({ notifyServer: true, preserveStatus: true });
    showError(error.message || "La connexion a échoué.");
    setStatus("Erreur", "Le flux n'a pas pu être établi.");
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

elements.connectButton.addEventListener("click", () => {
  connect().catch((error) => {
    console.error(error);
    showError("Une erreur inattendue est survenue.");
  });
});

elements.disconnectButton.addEventListener("click", () => {
  disconnect().catch((error) => {
    console.error(error);
    showError("La déconnexion a échoué.");
  });
});

window.addEventListener("beforeunload", () => {
  stopStatusPolling();
  if (peerConnection) {
    peerConnection.close();
  }
  if (localStream) {
    localStream.getTracks().forEach((track) => track.stop());
  }
});

fetchStatus().catch(() => {
  setStatus("Serveur", "Le serveur est prêt, mais le statut initial n'a pas pu être lu.");
});
