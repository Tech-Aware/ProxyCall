"""Outil de publication PyPI pour proxycall-cli.

Ce script automatise l'installation des dépendances de build, la génération des artefacts
et l'upload vers PyPI (ou un index compatible). Il suppose que les variables d'environnement
TWINE_USERNAME et TWINE_PASSWORD sont définies (token API). Utilisez l'option --dry-run pour
vérifier les étapes sans déclencher l'upload.
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable


LOG_FORMAT = "[%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
LOGGER = logging.getLogger("publier_sur_pypi")


def _run_command(description: str, command: Iterable[str]) -> None:
    """Exécute une commande système en journalisant chaque étape.

    :param description: Phrase courte décrivant la commande exécutée.
    :param command: Liste/itérable des segments de commande à passer à subprocess.
    :raises RuntimeError: si la commande retourne un code de sortie non nul.
    """
    LOGGER.info("%s", description)
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:  # pragma: no cover - protection runtime
        LOGGER.error("Échec de la commande (%s): code=%s", " ".join(command), exc.returncode)
        raise RuntimeError(f"La commande '{' '.join(command)}' a échoué") from exc


def _verifier_identifiants() -> None:
    """Vérifie la présence des identifiants Twine dans l'environnement."""
    username = os.getenv("TWINE_USERNAME")
    password = os.getenv("TWINE_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "TWINE_USERNAME et TWINE_PASSWORD doivent être définis (token API PyPI/TestPyPI)."
        )

    if username != "__token__":
        raise RuntimeError(
            "TWINE_USERNAME doit être '__token__' pour utiliser un jeton API PyPI/TestPyPI."
        )

    if not password.startswith(("pypi-", "testpypi-")):
        raise RuntimeError(
            "TWINE_PASSWORD doit être un jeton API valide (préfixe pypi-/testpypi-)."
        )

    LOGGER.info("Identifiants Twine détectés (utilisateur=%s, jeton détecté).", username)


def _nettoyer_dist(dist_dir: Path) -> None:
    """Supprime les anciens artefacts pour éviter les uploads obsolètes."""
    if dist_dir.exists():
        for element in dist_dir.iterdir():
            LOGGER.info("Suppression de l'artefact précédent: %s", element.name)
            element.unlink()
    else:
        dist_dir.mkdir(parents=True, exist_ok=True)


def _installer_outils_build() -> None:
    """Installe ou met à jour les dépendances nécessaires (build, twine)."""
    _run_command(
        "Installation/maj de build et twine",
        [sys.executable, "-m", "pip", "install", "--upgrade", "build", "twine"],
    )


def _build_distribution(racine: Path) -> None:
    """Construit la distribution via python -m build depuis la racine du projet."""
    _run_command("Construction des artefacts (wheel + sdist)", [sys.executable, "-m", "build"],)
    dist_dir = racine / "dist"
    archives = list(dist_dir.glob("*"))
    if not archives:
        raise RuntimeError("Aucun artefact généré dans dist/ après la construction.")
    for archive in archives:
        LOGGER.info("Artefact généré: %s", archive.name)


def _uploader(dist_dir: Path, repository_url: str, dry_run: bool) -> None:
    """Upload des artefacts via twine."""
    archives = sorted(dist_dir.glob("*"))
    if not archives:
        raise RuntimeError("dist/ est vide, impossible de lancer l'upload.")

    commande = [
        sys.executable,
        "-m",
        "twine",
        "upload",
        "--repository-url",
        repository_url,
        *(str(archive) for archive in archives),
    ]

    if dry_run:
        LOGGER.info("Mode --dry-run actif : upload simulé, aucune requête envoyée.")
        LOGGER.info("Commande préparée: %s", " ".join(commande))
        return

    _run_command("Upload des artefacts vers l'index cible", commande)


def main() -> int:
    parser = argparse.ArgumentParser(description="Publication automatisée sur PyPI")
    parser.add_argument(
        "--repository-url",
        default="https://upload.pypi.org/legacy/",
        help="URL de l'index compatible PyPI (défaut: PyPI production)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Construit les artefacts sans déclencher l'upload",
    )
    args = parser.parse_args()

    racine = Path(__file__).resolve().parent.parent
    dist_dir = racine / "dist"

    try:
        _verifier_identifiants()
        _installer_outils_build()
        _nettoyer_dist(dist_dir)
        _build_distribution(racine)
        _uploader(dist_dir, args.repository_url, args.dry_run)
    except Exception as exc:  # pragma: no cover - point central de gestion des erreurs runtime
        LOGGER.error("Publication interrompue: %s", exc)
        return 1

    LOGGER.info("Publication terminée avec succès.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
