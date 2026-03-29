# Spatial Neglect Assistant

## Mobile ingestion server

Le projet inclut maintenant une petite application Flask dédiée à l'ingestion
caméra + micro depuis un téléphone via WebRTC.

### Installation

```bash
python3 -m pip install -r requirements.txt
```

### Démarrage

```bash
cp .env.example .env
python3 -m mobile_ingestion
```

Le front est servi sur `/`. Depuis un téléphone, il faut utiliser un contexte
sécurisé pour ouvrir la caméra et le micro:

- `https://...` via un tunnel HTTPS
- ou `localhost` en développement local sur la même machine

### Variables utiles

- `OPENAI_API_KEY`
- `MOBILE_INGEST_HOST`
- `MOBILE_INGEST_PORT`
- `MOBILE_INGEST_DEBUG`
- `MOBILE_INGEST_ICE_SERVERS`
- `MOBILE_INGEST_ICE_TIMEOUT_SECONDS`
- `MOBILE_INGEST_SESSION_SHUTDOWN_TIMEOUT_SECONDS`
- `MOBILE_INGEST_VOICE_MODEL`
- `MOBILE_INGEST_VOICE_LANGUAGE`
- `MOBILE_INGEST_VOICE_PROMPT`
- `MOBILE_INGEST_VOICE_REALTIME_URL`
- `MOBILE_INGEST_VOICE_TRANSCRIPT_BUFFER_SIZE`
- `MOBILE_INGEST_VOICE_AUDIO_BUFFER_SECONDS`
- `MOBILE_INGEST_VOICE_WAKE_PHRASES`
- `MOBILE_INGEST_VOICE_WAKE_COOLDOWN_SECONDS`
- `MOBILE_INGEST_OBJECT_SEARCH_VISION_MODEL`
- `MOBILE_INGEST_OBJECT_SEARCH_DETECTION_INTERVAL_SECONDS`
- `MOBILE_INGEST_OBJECT_SEARCH_COMMAND_TIMEOUT_SECONDS`
- `MOBILE_INGEST_OBJECT_SEARCH_RESOLVER_MODEL`
