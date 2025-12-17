# Déploiement sur Render

Ce guide résume la mise en production de ProxyCall sur Render (API FastAPI) ainsi que la préparation de la CLI pour consommer ce backend hébergé.

## 1. Schéma global
- **Backend** : service web Render exécutant `uvicorn app.main:app`. Il expose les routes `/orders` et `/twilio/voice` pour la logique métier et les webhooks Twilio.
- **CLI** : utilisable par n'importe quel utilisateur. Elle lit un fichier `.env.render` local pour obtenir l'URL Render et les clés nécessaires pour envoyer des requêtes HTTP ou piloter Twilio/Google Sheets.
- **Secrets** : les clés sensibles (Twilio, Google) restent dans le dashboard Render et sont injectées en variables d'environnement ou fichiers secrets.

## 2. Blueprint Render (`render.yaml`)
Le fichier `render.yaml` à la racine définit un service web Python :
- Installation via `pip install -r requirements.txt`.
- Lancement via `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (Render expose automatiquement `$PORT`).
- Variables d'environnement attendues : `PUBLIC_BASE_URL` (ou `RENDER_EXTERNAL_URL`), identifiants Twilio, paramètres de pool (`TWILIO_PHONE_COUNTRY`, `TWILIO_NUMBER_TYPE`, `TWILIO_POOL_SIZE`) et Google (`GOOGLE_SHEET_NAME`, `GOOGLE_SERVICE_ACCOUNT_FILE`).
- Le secret JSON Google peut être chargé comme *secret file* et monté à l'emplacement `/etc/secrets/google-credentials.json` pour rester hors du dépôt.

Pour déployer :
1. Connectez le dépôt GitHub à Render et sélectionnez le blueprint `render.yaml`.
2. Renseignez les variables marquées `sync: false` dans le dashboard (ou via un groupe d'environnement) et uploadez le fichier JSON de service Google en secret file.
3. Render fournira `RENDER_EXTERNAL_URL` : définissez `PUBLIC_BASE_URL` sur cette valeur si vous souhaitez figer l'URL (sinon le code la prendra par défaut).

## 3. Préparer la CLI pour Render
1. Copiez `.env.render.example` en `.env.render` et complétez :
   - `PUBLIC_BASE_URL` : URL Render publique (ex. `https://proxycall.onrender.com`).
   - `PROXYCALL_API_TOKEN` : si vous protégez l'API par un header ou une auth personnalisée.
   - Clés Twilio (`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, etc.) pour les actions LIVE.
   - Paramètres Google (`GOOGLE_SHEET_NAME`, `GOOGLE_SERVICE_ACCOUNT_FILE`).
2. La CLI charge automatiquement `.env.render` puis `.env`, avec redaction des logs (Rich) et messages d'erreur détaillés.
3. Chaque utilisateur conserve son `.env.render` local : sans les clés valides, aucune requête sensible ne peut être effectuée.

## 4. Sécurité et bonnes pratiques
- Ne commitez jamais les secrets : utilisez le dashboard Render pour les variables et secret files.
- Limitez l'accès à l'URL publique via un token (`PROXYCALL_API_TOKEN`) ou les ACL Render si nécessaire.
- Les journaux API masquent les numéros (`mask_phone`) et les identifiants pour éviter les fuites en production.
