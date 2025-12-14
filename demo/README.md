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
