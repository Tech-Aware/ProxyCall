# ProxyCall

ProxyCall est l'API FastAPI déployée sur Render pour gérer l'attribution de numéros proxy, le routage des appels Twilio et la synchronisation avec Google Sheets. Le dépôt a été allégé pour ne conserver que ce qui est nécessaire à l'exécution du service en production.

## Périmètre conservé après nettoyage

- **Routes API métiers** : `/orders`, `/clients`, `/pool`, `/confirmations`, `/twilio/voice` restent servies par FastAPI (voir `app/main.py` et `api/`).
- **Webhook Twilio** : les webhooks voix sont toujours routés via `api/twilio_webhook.py` et le démarrage se fait via `python -m app.run`.
- **Accès Sheets** : les services `repositories`/`services` continuent d'utiliser `gspread` et `google-auth` présents dans `requirements.txt`.
- **Sécurité** : le header `Authorization: Bearer <token>` reste exigé si `PROXYCALL_API_TOKEN` est défini côté serveur.

## Installation minimale (local)

1. Créez un environnement virtuel et installez les dépendances :
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Définissez les variables d'environnement attendues (Twilio, Google, URL publique).

## Lancement local de l'API

Le point d'entrée standard utilise `app.run`, qui normalise automatiquement la variable `PORT` (utile sur Render) et configure une journalisation détaillée :
```bash
PUBLIC_BASE_URL="https://exemple.local" \
TWILIO_ACCOUNT_SID="..." \
TWILIO_AUTH_TOKEN="..." \
GOOGLE_SHEET_NAME="NomDuSheet" \
GOOGLE_SERVICE_ACCOUNT_FILE="/chemin/vers/credentials.json" \
python -m app.run
```

> **Sécurité :** si `PROXYCALL_API_TOKEN` est défini, toutes les routes métier exigent un header `Authorization: Bearer <token>`.

## Déploiement Render

Le blueprint Render (`render.yaml`) reste la référence pour déployer l'API. Le guide détaillé est dans `docs/deploiement_render.md`.
