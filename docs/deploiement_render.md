# Déploiement sur Render

Ce guide résume la mise en production de ProxyCall sur Render (API FastAPI).

## 1. Schéma global
- **Backend** : service web Render exécutant `python -m app.run`. Il expose les routes `/orders`, `/clients`, `/pool`, `/confirmations` et `/twilio/voice` pour la logique métier et les webhooks Twilio.
- **Secrets** : les clés sensibles (Twilio, Google) restent dans le dashboard Render et sont injectées en variables d'environnement ou fichiers secrets.

## 2. Blueprint Render (`render.yaml`)
Le fichier `render.yaml` à la racine définit un service web Python sur un plan **pro** Render en région **frankfurt** pour disposer de ressources accrues :
- Installation via `pip install -r requirements.txt`.
- Lancement via `python -m app.run` (le module gère la normalisation de `$PORT` exposé par Render et journalise toute correction appliquée).
- Variables d'environnement attendues : `PUBLIC_BASE_URL` (ou `RENDER_EXTERNAL_URL`), identifiants Twilio, paramètres de pool (`TWILIO_PHONE_COUNTRY`, `TWILIO_NUMBER_TYPE`, `TWILIO_POOL_SIZE`) et Google (`GOOGLE_SHEET_NAME`, `GOOGLE_SERVICE_ACCOUNT_FILE`).
- Le secret JSON Google peut être chargé comme *secret file* et monté à l'emplacement `/etc/secrets/google-credentials.json` pour rester hors du dépôt.

Pour déployer :
1. Connectez le dépôt GitHub à Render et sélectionnez le blueprint `render.yaml`.
2. Renseignez les variables marquées `sync: false` dans le dashboard (ou via un groupe d'environnement) et uploadez le fichier JSON de service Google en secret file.
3. Render fournira `RENDER_EXTERNAL_URL` : définissez `PUBLIC_BASE_URL` sur cette valeur si vous souhaitez figer l'URL (sinon le code la prendra par défaut).

## 3. Bonnes pratiques
- Ne commitez jamais les secrets : utilisez le dashboard Render pour les variables et secret files.
- Limitez l'accès à l'URL publique via un token (`PROXYCALL_API_TOKEN`) ou les ACL Render si nécessaire.
- Les journaux API masquent les numéros (`mask_phone`) et les identifiants pour éviter les fuites en production.
