# Déploiement sur Render

Ce guide résume la mise en production de ProxyCall sur Render (API FastAPI) ainsi que la préparation de la CLI pour consommer ce backend hébergé.

## 1. Schéma global
- **Backend** : service web Render exécutant `python -m app.run`. Il expose les routes `/orders` et `/twilio/voice` pour la logique métier et les webhooks Twilio.
- **CLI** : utilisable par n'importe quel utilisateur. Elle lit un fichier `.env.render` local pour obtenir l'URL Render (et éventuellement un token d'accès) puis envoie des requêtes HTTP au backend.
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

## 3. Préparer la CLI pour Render
1. Copiez `.env.render.example` en `.env.render` et complétez le strict minimum :
   - `PUBLIC_BASE_URL` : URL Render publique (ex. `https://proxycall.onrender.com`).
   - `PROXYCALL_API_TOKEN` : uniquement si vous protégez l'API par un header ou une auth personnalisée.
2. Lancez la CLI **sans argument** (ou avec `--render` si vous souhaitez l'expliciter) : le mode Render est choisi automatiquement et enverra toutes les commandes (`create-client`, `pool-list`, etc.) vers l'API hébergée via `httpx`.
3. La CLI charge automatiquement `.env.render` puis `.env`, avec redaction des logs (Rich) et messages d'erreur détaillés. Les erreurs réseau/HTTP sont remontées avec le code status et le détail JSON renvoyé par l'API Render.
4. Les secrets Twilio/Google restent sur Render : la CLI n'en a pas besoin pour appeler les endpoints.

📌 **Mode Live (dev uniquement)**
- Activez-le avec `--live` pour travailler contre vos comptes Twilio/Google locaux.
- Les variables `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `PUBLIC_BASE_URL`, `GOOGLE_SHEET_NAME` et `GOOGLE_SERVICE_ACCOUNT_FILE` doivent être présentes **et** le fichier de compte de service doit être accessible. Sinon, la CLI stoppe immédiatement avec un message listant les clés manquantes ou le fichier introuvable.

## 4. Sécurité et bonnes pratiques
- Ne commitez jamais les secrets : utilisez le dashboard Render pour les variables et secret files.
- Limitez l'accès à l'URL publique via un token (`PROXYCALL_API_TOKEN`) ou les ACL Render si nécessaire.
- Les journaux API masquent les numéros (`mask_phone`) et les identifiants pour éviter les fuites en production.
