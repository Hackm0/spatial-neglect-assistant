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
- `ELEVENLABS_VOICE_ID` (optionnel, ex: `CwhRBWXzGAHq8TQ4Fs17`)
- `ELEVENLABS_AGENT_ID` (optionnel, ex: `agent_0501kmv6mmc1emgtcb159rjmny1j`; active l'appel direct a l'agent publie ElevenLabs cote navigateur)

### UI Color System (Clinical HUD)

Palette principale (fond camera + HUD):

- `--bg: #080b10`
- `--surface: rgba(8,12,18,0.72)`
- `--surface-strong: rgba(8,12,18,0.88)`
- `--border: rgba(255,255,255,0.07)`
- `--border-hi: rgba(255,255,255,0.18)`
- `--text-dim: #5a6270`
- `--text-mid: #8c97a8`
- `--text-hi: #dde3ec`
- `--text-white: #ffffff`

Couleurs semantiques:

- `--ready: #34d494` (etat connecte/sain)
- `--active: #5b9cf6` (streaming, ecoute, detection)
- `--warn: #f5a623` (degradation, auto-off actif)
- `--danger: #ff4d4d` (erreur, signal perdu)

Nuances utilitaires recommandees:

- `--ready-soft: rgba(52,212,148,0.18)`
- `--active-soft: rgba(91,156,246,0.18)`
- `--warn-soft: rgba(245,166,35,0.25)`
- `--danger-soft: rgba(255,77,77,0.16)`

Regles d'usage:

- Ne jamais utiliser de fond blanc pour les panneaux HUD.
- Ne jamais utiliser de violet dans les gradients et etats.
- Garder les textes de statut critiques avec contraste AA minimum (4.5:1).
- Etat nominal = discret; erreurs/deconnexion = fortement visibles.
