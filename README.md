# Spatial Neglect Assistant

## Mobile ingestion server

Le projet inclut maintenant une petite application Flask dédiée à l'ingestion
caméra + micro depuis un téléphone via WebRTC.

### Installation

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

### Démarrage

```bash
python3 -m mobile_ingestion
```

Le front est servi sur `/`. Depuis un téléphone, il faut utiliser un contexte
sécurisé pour ouvrir la caméra et le micro:

- `https://...` via un tunnel HTTPS
- ou `localhost` en développement local sur la même machine

### Variables utiles

- `MOBILE_INGEST_HOST`
- `MOBILE_INGEST_PORT`
- `MOBILE_INGEST_DEBUG`
- `MOBILE_INGEST_ICE_SERVERS`
- `MOBILE_INGEST_ICE_TIMEOUT_SECONDS`
- `MOBILE_INGEST_SESSION_SHUTDOWN_TIMEOUT_SECONDS`
- `MOBILE_INGEST_VOICE_WAKE_PHRASES` (ex: `ok jarvis,okay jarvis`)
- `MOBILE_INGEST_VOICE_IDLE_TIMEOUT_SECONDS` (auto-off de l'ecoute vocale)
