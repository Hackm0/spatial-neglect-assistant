// ===== State =====
let currentStream  = null;
let fpsInterval    = null;
let frameCount     = 0;
let lastFpsTime    = performance.now();
let ws             = null;
let detectionOn    = false;
let sendingFrame   = false;          // throttle: only one in-flight frame
const TARGET_FPS   = 10;            // frames sent to server per second
let lastSendTime   = 0;

// ===== DOM refs =====
const video         = document.getElementById('localVideo');
const canvas        = document.getElementById('detectionCanvas');
const ctx           = canvas.getContext('2d');
const startBtn      = document.getElementById('startBtn');
const stopBtn       = document.getElementById('stopBtn');
const overlay       = document.getElementById('video-overlay');
const streamInfo    = document.getElementById('stream-info');
const resLabel      = document.getElementById('resolution-label');
const fpsLabel      = document.getElementById('fps-label');
const statusBadge   = document.getElementById('status-badge');
const statusText    = document.getElementById('status-text');
const videoWrapper  = document.querySelector('.video-wrapper');
const wsBadge       = document.getElementById('ws-badge');
const wsText        = document.getElementById('ws-text');
const detectToggle  = document.getElementById('detectToggle');

// ===== Hidden capture canvas =====
const captureCanvas = document.createElement('canvas');
const captureCtx    = captureCanvas.getContext('2d');

// ===== Helpers =====
function setStatus(state) {
  statusBadge.className = 'badge badge-' + state;
  const labels = { idle: 'Inactif', live: 'EN DIRECT', error: 'Erreur' };
  statusText.textContent = labels[state] ?? state;
}

function setWsStatus(state) {
  const labels = { idle: 'Déconnecté', live: 'Connecté', error: 'Erreur serveur' };
  wsBadge.className = 'badge badge-' + state;
  wsText.textContent = labels[state] ?? state;
}

function getConstraints() {
  return {
    video: {
      facingMode: { ideal: 'environment' },
      width:  { ideal: 1280 },
      height: { ideal: 720 },
    },
    audio: false,
  };
}

// ===== FPS counter (for display) =====
function startFpsCounter() {
  frameCount  = 0;
  lastFpsTime = performance.now();
  fpsInterval = setInterval(() => {
    const elapsed = (performance.now() - lastFpsTime) / 1000;
    fpsLabel.textContent = Math.round(frameCount / elapsed) + ' fps';
    frameCount  = 0;
    lastFpsTime = performance.now();
  }, 1000);
  (function countFrame() {
    if (!currentStream) return;
    frameCount++;
    requestAnimationFrame(countFrame);
  })();
}

function stopFpsCounter() {
  clearInterval(fpsInterval);
  fpsInterval = null;
  fpsLabel.textContent = '– fps';
}

// ===== WebSocket =====
function connectWebSocket() {
  if (ws && ws.readyState <= WebSocket.OPEN) return;
  ws = new WebSocket('ws://localhost:8000/ws');
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    setWsStatus('live');
    detectToggle.disabled = false;
  };

  ws.onmessage = (evt) => {
    sendingFrame = false;            // ready for next frame
    try {
      const data = JSON.parse(evt.data);
      drawDetections(data.detections || []);
    } catch (e) { /* ignore parse errors */ }
  };

  ws.onerror = () => setWsStatus('error');

  ws.onclose = () => {
    setWsStatus('idle');
    detectToggle.disabled = true;
    detectToggle.textContent = 'Détection OFF';
    detectToggle.classList.remove('detect-on');
    detectionOn = false;
    clearCanvas();
    // Reconnect after 3 s if stream is still active
    if (currentStream) setTimeout(connectWebSocket, 3000);
  };
}

function disconnectWebSocket() {
  if (ws) { ws.close(); ws = null; }
  setWsStatus('idle');
  detectToggle.disabled = true;
  detectToggle.textContent = 'Détection OFF';
  detectToggle.classList.remove('detect-on');
  detectionOn = false;
}

// ===== Detection toggle =====
function toggleDetection() {
  detectionOn = !detectionOn;
  if (detectionOn) {
    detectToggle.textContent = 'Détection ON';
    detectToggle.classList.add('detect-on');
    scheduleFrameLoop();
  } else {
    detectToggle.textContent = 'Détection OFF';
    detectToggle.classList.remove('detect-on');
    clearCanvas();
  }
}

// ===== Frame capture & send =====
function scheduleFrameLoop() {
  if (!detectionOn || !currentStream) return;
  requestAnimationFrame(frameLoop);
}

function frameLoop(ts) {
  if (!detectionOn || !currentStream) return;
  requestAnimationFrame(frameLoop);

  // Throttle to TARGET_FPS
  if (ts - lastSendTime < 1000 / TARGET_FPS) return;
  if (sendingFrame) return;        // previous frame still processing
  if (!ws || ws.readyState !== WebSocket.OPEN) return;

  lastSendTime = ts;
  sendingFrame = true;

  // Match canvas size to video display size
  const vw = video.videoWidth  || 640;
  const vh = video.videoHeight || 480;
  captureCanvas.width  = vw;
  captureCanvas.height = vh;
  captureCtx.drawImage(video, 0, 0, vw, vh);

  captureCanvas.toBlob((blob) => {
    if (!blob || !ws || ws.readyState !== WebSocket.OPEN) {
      sendingFrame = false;
      return;
    }
    blob.arrayBuffer().then(buf => ws.send(buf));
  }, 'image/jpeg', 0.7);
}

// ===== Draw bounding boxes =====
const FONT_SIZE = 13;

function drawDetections(detections) {
  // Size canvas to match displayed video element
  canvas.width  = video.clientWidth;
  canvas.height = video.clientHeight;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  const vw = video.videoWidth  || 640;
  const vh = video.videoHeight || 480;
  const scaleX = canvas.width  / vw;
  const scaleY = canvas.height / vh;

  for (const det of detections) {
    const [x1, y1, x2, y2] = det.box;
    const [r, g, b]  = det.colour;
    const colStr     = `rgb(${r},${g},${b})`;
    const colAlpha   = `rgba(${r},${g},${b},0.15)`;

    const sx1 = x1 * scaleX, sy1 = y1 * scaleY;
    const sx2 = x2 * scaleX, sy2 = y2 * scaleY;
    const sw  = sx2 - sx1,   sh  = sy2 - sy1;

    // Fill
    ctx.fillStyle = colAlpha;
    ctx.fillRect(sx1, sy1, sw, sh);

    // Border
    ctx.strokeStyle = colStr;
    ctx.lineWidth   = 2;
    ctx.strokeRect(sx1, sy1, sw, sh);

    // Label background
    const label  = `${det.label} ${Math.round(det.confidence * 100)}%`;
    ctx.font     = `600 ${FONT_SIZE}px Inter, sans-serif`;
    const tw     = ctx.measureText(label).width;
    const th     = FONT_SIZE + 6;

    ctx.fillStyle = colStr;
    ctx.fillRect(sx1 - 1, sy1 - th, tw + 12, th);

    // Label text
    ctx.fillStyle = '#fff';
    ctx.fillText(label, sx1 + 5, sy1 - 6);
  }
}

function clearCanvas() {
  canvas.width  = video.clientWidth  || canvas.width;
  canvas.height = video.clientHeight || canvas.height;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
}

// ===== Camera stream =====
async function startStream() {
  try {
    statusText.textContent = 'Chargement…';
    const stream = await navigator.mediaDevices.getUserMedia(getConstraints());
    currentStream   = stream;
    video.srcObject = stream;

    const track    = stream.getVideoTracks()[0];
    const settings = track.getSettings();
    resLabel.textContent = `${settings.width ?? '?'}×${settings.height ?? '?'}`;

    await video.play();

    overlay.classList.add('hidden');
    streamInfo.classList.remove('hidden');
    videoWrapper.classList.add('streaming');
    startBtn.classList.add('hidden');
    stopBtn.classList.remove('hidden');
    setStatus('live');
    startFpsCounter();
    connectWebSocket();
  } catch (err) {
    console.error(err);
    setStatus('error');
    showOverlayError(err);
  }
}

function stopStream() {
  if (currentStream) {
    currentStream.getTracks().forEach(t => t.stop());
    currentStream = null;
  }
  video.srcObject = null;
  stopFpsCounter();
  disconnectWebSocket();
  clearCanvas();

  overlay.classList.remove('hidden');
  overlay.querySelector('p').textContent = 'La caméra n\'est pas active';
  overlay.querySelector('svg').style.color = '';
  streamInfo.classList.add('hidden');
  videoWrapper.classList.remove('streaming');
  startBtn.classList.remove('hidden');
  stopBtn.classList.add('hidden');
  resLabel.textContent = '–';
  setStatus('idle');
}

// ===== Error overlay =====
function showOverlayError(err) {
  overlay.classList.remove('hidden');
  const p   = overlay.querySelector('p');
  const svg = overlay.querySelector('svg');
  svg.style.color = 'var(--danger)';
  if      (err.name === 'NotAllowedError')  p.textContent = 'Permission caméra refusée.';
  else if (err.name === 'NotFoundError')    p.textContent = 'Aucune caméra détectée.';
  else if (err.name === 'NotReadableError') p.textContent = 'Caméra déjà utilisée.';
  else                                       p.textContent = `Erreur : ${err.message}`;
}

// ===== Browser support check =====
window.addEventListener('DOMContentLoaded', () => {
  if (!navigator.mediaDevices?.getUserMedia) {
    startBtn.disabled = true;
    startBtn.textContent = 'Navigateur non supporté';
    setStatus('error');
    showOverlayError({ message: 'getUserMedia non supporté.' });
  }
});
