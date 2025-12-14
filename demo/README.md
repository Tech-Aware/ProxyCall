# ProxyCall — DEMO

Ce dossier **DEMO** sert à présenter, de façon simple et reproductible, le concept de **numéro proxy** pour protéger le numéro réel d’un client et permettre le **routage d’appels** via Twilio.

## À quoi sert la démo ?

- Montrer la valeur produit en quelques minutes, sans UI.
- Illustrer le parcours “proxy → routage → client”, avec un résultat concret (TwiML / décision de routage).
- Permettre une démonstration **stable** (mode MOCK) ou **réelle** (mode LIVE).

## Fonctionnalités démontrées

- Attribution d’un **numéro proxy** unique à un client
- Recherche d’un client à partir du **numéro proxy**
- Routage d’un appel entrant vers le **numéro réel** du client
- Règle simple de contrôle (ex. cohérence d’indicatif pays) pour illustrer la logique de filtrage

## Modes de démonstration

### Mode MOCK
- Objectif : démo **sans dépendances externes**
- Pas d’achat de numéro Twilio
- Pas d’accès Google Sheets
- Données manipulées localement (fixtures / stockage local)

✅ Idéal pour une démo “sans surprise” (offline, rapide, reproductible)

### Mode LIVE (Twilio + Google Sheets)
- Objectif : démo **réelle** de bout en bout
- Achat/configuration d’un numéro proxy via Twilio
- Webhook voix actif (Twilio → app)
- Stockage/lecture des clients dans Google Sheets (onglet `Clients`)

✅ Idéal pour un effet “wow” (vrai numéro proxy, vrai routage)

## Prérequis (selon le mode)

- Python (environnement projet)
- Mode LIVE uniquement :
  - Compte Twilio + crédits
  - Accès à un Google Sheet (avec l’onglet `Clients`)
- Une URL publique HTTPS pour recevoir les webhooks (ex : ngrok)

## Notes

- Le contenu de ce dossier est orienté **présentation** : il vise à montrer le fonctionnement et la valeur, pas à détailler toute l’implémentation.
- Les secrets (Twilio, Google) doivent rester hors du dépôt (fichier `.env`, clé de service account, etc.).
- Le CLI charge automatiquement un fichier `.env` à la racine du repo (et tout `.env` détecté via `find_dotenv`), ce qui permet de tester le mode LIVE sans exporter manuellement les variables.
- Le chemin vers le fichier **service account** peut être donné relativement à la racine du dépôt (pratique depuis PyCharm ou un autre répertoire).
- L’onglet `Clients` du Google Sheet doit contenir au moins les colonnes suivantes (ordre attendu) : `client_id`, `client_name`, `client_mail`, `client_real_phone`, `client_proxy_number`, `client_iso_residency`, `client_country_code`. Des colonnes supplémentaires peuvent exister, le CLI ne réécrit pas les en-têtes.

## Lancer la démo pour un utilisateur non averti

Lancez simplement la commande ci-dessous **sans aucun argument** :

```bash
python cli.py
```

Le CLI va :

1. Vous demander le **mode** :
   - `1` pour la démo **simulée (mock)** — recommandé, aucun prérequis
   - `2` pour la démo **live** — nécessite Twilio + Google Sheets
2. Afficher un menu simple `1, 2, 3` pour :
   - `1` Gérer un client : choix entre **créer** (saisie guidée champ par champ) ou **rechercher/afficher** un client existant
   - `2` Simuler un appel autorisé (même indicatif pays)
   - `3` Simuler un appel bloqué (indicatif différent)

Les entrées par défaut sont préremplies (ex : `demo-client`, `+33900000000`) afin de pouvoir enchaîner très vite lors d’une démo live ou simulée.

ℹ️ Si vous choisissez le mode **LIVE** sans avoir renseigné les variables d’environnement requises, le CLI vous proposera automatiquement de basculer en mode **MOCK** afin de continuer la démonstration sans erreur.

Si vous préférez déclencher ces étapes depuis Python (pour afficher le rendu dans un notebook ou un script interne), utilisez le helper `run_mock_client_journey` de `demo/scenarios.py` :

```python
from demo.scenarios import run_mock_client_journey

outputs = run_mock_client_journey()
for step, stdout in outputs.items():
    print(f"\n=== {step} ===")
    print(stdout)
```
