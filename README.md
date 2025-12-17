# ProxyCall

Ce dépôt regroupe les services et intégrations permettant de gérer l'attribution de numéros proxy et le routage des appels. Les tests sont désormais centralisés dans le dossier `tests` et un script unique permet de lancer toute la suite.

## Installation rapide

1. Créez un environnement virtuel et installez les dépendances :
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Configurez les variables d'environnement nécessaires pour les services Twilio et Google Sheets lorsque vous souhaitez exécuter les tests live (voir ci-dessous).

## Exécution des tests

Le dossier `tests` contient trois catégories principales de scénarios, exécutables via `pytest`.

### 1. Tests unitaires et de service
- **Objectif :** valider la logique métier (routage d'appel, gestion des commandes, clients, interactions Twilio simulées, etc.).
- **Fichiers concernés :**
  - `tests/test_call_routing_service.py`
  - `tests/test_clients_service.py`
  - `tests/test_orders_service.py`
  - `tests/test_sheets_client_unit.py`
  - `tests/test_twilio_client_unit.py`
  - `tests/test_demo_scenarios.py`
- **Commande (PyCharm/terminal) :**
  ```bash
  python -m pytest tests/test_call_routing_service.py
  ```
  Ajustez le chemin vers le fichier ou utilisez `-k` pour cibler un test en particulier.

### 2. Tests démo hors-ligne
- **Objectif :** présenter les flux principaux sans appeler de services externes.
- **Fichiers concernés :**
  - `tests/client_repository_demo_test.py`
  - `tests/test_sheets_access_demo_test.py`
  - `tests/twilio_pools_demo_test.py`
- **Commande :**
  ```bash
  python -m pytest tests/client_repository_demo_test.py
  ```
  Ces tests peuvent être lancés sans configuration supplémentaire.

### 3. Tests live (intégrations réelles)
- **Objectif :** vérifier l'accès réel aux APIs Twilio et Google Sheets.
- **Fichiers concernés :**
  - `tests/client_repository_live_test.py`
  - `tests/test_sheets_access_live_test.py`
- **Activation :** par défaut ces tests sont ignorés. Pour les exécuter, définissez les variables requises :
  ```bash
  export PROXYCALL_RUN_LIVE=1
  export TWILIO_ACCOUNT_SID="..."
  export TWILIO_AUTH_TOKEN="..."
  export GOOGLE_SERVICE_ACCOUNT_FILE="path/vers/credentials.json"
  export GOOGLE_SHEET_NAME="NomDuSheet"
  python -m pytest tests/test_sheets_access_live_test.py
  ```

### Script unique
Pour exécuter l'ensemble des tests (unitaires, démo et live si les variables sont présentes), utilisez le script dédié depuis la racine du projet :
```bash
./run_tests.sh
```
Vous pouvez lui passer des arguments `pytest` supplémentaires, par exemple `./run_tests.sh -k call_routing`.

## Déploiement Render

Un blueprint Render (`render.yaml`) est fourni pour déployer l'API FastAPI sur Render avec `uvicorn app.main:app`. Le guide détaillé et la préparation de la CLI (.env.render pour l'URL/token uniquement) sont décrits dans `docs/deploiement_render.md`.

