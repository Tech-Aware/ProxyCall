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
import tomllib
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
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - protection runtime
        stdout = (exc.stdout or "").strip()
        stderr = (exc.stderr or "").strip()
        if stdout:
            LOGGER.error("Sortie standard:\n%s", stdout)
        if stderr:
            LOGGER.error("Sortie d'erreur:\n%s", stderr)

        erreur_detaillee = "La commande '{cmd}' a échoué avec le code {code}.".format(
            cmd=" ".join(command), code=exc.returncode
        )
        retour_contenu = f" {stdout} {stderr}".lower()
        if "file already exists" in retour_contenu:
            erreur_detaillee = (
                f"{erreur_detaillee} Une archive avec cette version est déjà publiée sur l'index"
                " cible. Incrémentez la version dans pyproject.toml puis relancez la publication"
                " pour éviter le conflit."
            )

        raise RuntimeError(erreur_detaillee) from exc
    else:
        if completed.stdout:
            LOGGER.info("Sortie standard:\n%s", completed.stdout.strip())
        if completed.stderr:
            LOGGER.info("Sortie d'erreur:\n%s", completed.stderr.strip())


def _lire_version(pyproject_path: Path) -> str:
    """Lit la version courante dans pyproject.toml avec contrôle strict.

    :param pyproject_path: Chemin du fichier pyproject.toml.
    :raises RuntimeError: si la clé de version est absente.
    """

    with pyproject_path.open("rb") as fichier:
        contenu = tomllib.load(fichier)

    try:
        return str(contenu["project"]["version"])
    except KeyError as exc:  # pragma: no cover - protection runtime
        raise RuntimeError("Impossible de lire la clé [project.version] dans pyproject.toml") from exc


def _incrementer_version(pyproject_path: Path) -> str:
    """Incrémente le composant correctif (patch) de la version et met à jour pyproject.toml.

    :param pyproject_path: Chemin du fichier pyproject.toml.
    :return: Nouvelle version appliquée.
    :raises RuntimeError: si le format de version est invalide ou en cas d'échec d'écriture.
    """

    version_actuelle = _lire_version(pyproject_path)
    segments = version_actuelle.split(".")
    if len(segments) != 3 or not all(part.isdigit() for part in segments):
        raise RuntimeError(
            "Le format de version doit être semver simplifié 'X.Y.Z' (ex: 0.1.0)."
        )

    segments[-1] = str(int(segments[-1]) + 1)
    nouvelle_version = ".".join(segments)

    contenu = pyproject_path.read_text(encoding="utf-8")
    ancien_fragment = f'version = "{version_actuelle}"'
    nouveau_fragment = f'version = "{nouvelle_version}"'
    if ancien_fragment not in contenu:
        raise RuntimeError(
            "Impossible de mettre à jour pyproject.toml : motif de version introuvable."
        )

    pyproject_path.write_text(contenu.replace(ancien_fragment, nouveau_fragment, 1), encoding="utf-8")
    LOGGER.info("Version incrémentée automatiquement: %s -> %s", version_actuelle, nouvelle_version)
    return nouvelle_version


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
    pyproject_path = racine / "pyproject.toml"
    dist_dir = racine / "dist"

    try:
        _verifier_identifiants()
        _incrementer_version(pyproject_path)
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
