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

## Démarrer une démo mock rapidement

Les commandes suivantes permettent de présenter le parcours complet sans dépendances externes en s’appuyant sur les fixtures du dossier `demo/fixtures` :

```bash
# 1) Création (idempotente) d’un client démo + proxy
python -m demo.cli --mock --fixtures demo/fixtures/clients.json \
  create-client --client-id demo-client --name "Client Démo" --phone-real +33123456789

# 2) Lookup du client depuis le proxy
python -m demo.cli --mock --fixtures demo/fixtures/clients.json \
  lookup --proxy +33900000000

# 3) Simulation d’appel autorisé (même indicatif pays)
python -m demo.cli --mock --fixtures demo/fixtures/clients.json \
  simulate-call --from +33111111111 --to +33900000000

# 4) Simulation d’appel refusé (indicatif différent)
python -m demo.cli --mock --fixtures demo/fixtures/clients.json \
  simulate-call --from +442222222222 --to +33900000000
```

Si vous préférez déclencher ces étapes depuis Python (pour afficher le rendu dans un notebook ou un script interne), utilisez les helpers de `demo/scenarios.py` :

```python
from demo.scenarios import run_mock_client_journey, cli_command_examples

outputs = run_mock_client_journey()
for step, stdout in outputs.items():
    print(f"\n=== {step} ===")
    print(stdout)

print("\nCommandes prêtes à l’emploi :")
for cmd in cli_command_examples():
    print(cmd)
```
