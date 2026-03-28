"""
Spatial Detection – YOLOv8 WebSocket Server
FastAPI backend that receives JPEG frames from the browser via WebSocket,
runs YOLOv8 object detection, and returns detections as JSON.
"""

import cv2
import numpy as np
import base64, json, logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from ultralytics import YOLO

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("spatial-detection")

app = FastAPI(title="Spatial Detection API")

# Allow the frontend served on any localhost port
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load YOLOv8 nano model (downloads ~6 MB on first run)
logger.info("Loading YOLOv8n model...")
model = YOLO("yolov8n.pt")
logger.info("Model ready.")

# Colour palette – one colour per class id (cycles)
PALETTE = [
    (108,  99, 255),  # purple
    ( 61, 214, 140),  # green
    (255,  78, 106),  # red
    ( 52, 187, 255),  # blue
    (255, 193,  30),  # amber
    (255, 128,   0),  # orange
    (180,  60, 220),  # violet
    ( 30, 220, 180),  # teal
]


def colour_for(class_id: int):
    return PALETTE[class_id % len(PALETTE)]


def process_frame(jpeg_bytes: bytes) -> list:
    """Decode JPEG → run YOLO → return list of detections."""
    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        return []

    results = model(frame, verbose=False)[0]
    detections = []
    for box in results.boxes:
        xyxy  = box.xyxy[0].tolist()           # [x1, y1, x2, y2]
        conf  = float(box.conf[0])
        cls   = int(box.cls[0])
        label = model.names[cls]
        colour = colour_for(cls)
        detections.append({
            "label":      label,
            "confidence": round(conf, 3),
            "box":        [round(v) for v in xyxy],
            "colour":     colour,              # (R, G, B) tuple
        })
    return detections


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client = websocket.client
    logger.info(f"Client connected: {client}")
    try:
        while True:
            # Receive raw JPEG bytes from browser
            data = await websocket.receive_bytes()
            detections = process_frame(data)
            await websocket.send_text(json.dumps({"detections": detections}))
    except WebSocketDisconnect:
        logger.info(f"Client disconnected: {client}")
    except Exception as exc:
        logger.error(f"Error: {exc}")


@app.get("/health")
def health():
    return {"status": "ok", "model": "yolov8n"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
