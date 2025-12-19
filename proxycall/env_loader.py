from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

logger = logging.getLogger(__name__)

APP_DIR = Path.home() / ".proxycall"
USER_ENV_RENDER = APP_DIR / ".env.render"


def _load_if_exists(path: Path) -> bool:
    try:
        if path.exists() and path.is_file():
            load_dotenv(path, override=False)
            logger.info("Fichier env chargé: %s", str(path))
            return True
    except Exception as exc:
        logger.warning("Impossible de charger %s: %s", str(path), exc)
    return False


def ensure_env_render_interactive() -> None:
    """
    S'assure que PUBLIC_BASE_URL est défini.
    Si absent, tente de créer une config user (~/.proxycall/.env.render) via prompts.
    """
    if os.getenv("PUBLIC_BASE_URL"):
        return

    # Si pas de TTY (ex: exécution non interactive), on ne prompt pas
    if not sys.stdin.isatty():
        logger.error("PUBLIC_BASE_URL manquant et terminal non-interactif: impossible de demander les valeurs.")
        return

    print("Configuration ProxyCall (mode Render) :")
    base_url = input("PUBLIC_BASE_URL (ex: https://proxycall.onrender.com) : ").strip()
    token = input("PROXYCALL_API_TOKEN (laisser vide si non utilisé) : ").strip()

    if not base_url:
        logger.error("PUBLIC_BASE_URL est obligatoire. Annulation de la configuration.")
        return

    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        content = f"PUBLIC_BASE_URL={base_url}\n"
        if token:
            content += f"PROXYCALL_API_TOKEN={token}\n"
        USER_ENV_RENDER.write_text(content, encoding="utf-8")
        logger.info("Configuration enregistrée dans %s", str(USER_ENV_RENDER))
        load_dotenv(USER_ENV_RENDER, override=False)
    except Exception as exc:
        logger.exception("Impossible d'écrire la config utilisateur %s", str(USER_ENV_RENDER), exc_info=exc)


def load_env_files() -> None:
    """
    Ordre de recherche :
      1) À côté du binaire (PyInstaller: sys.executable)
      2) find_dotenv depuis cwd/parents (.env.render puis .env)
      3) ~/.proxycall/.env.render
    """
    loaded_any = False

    # 1) Dossier du binaire (important pour PyInstaller + double-clic)
    try:
        exe_dir = Path(sys.executable).resolve().parent
        loaded_any |= _load_if_exists(exe_dir / ".env.render")
        loaded_any |= _load_if_exists(exe_dir / ".env")
    except Exception:
        pass

    # 2) Depuis cwd/parents
    p = find_dotenv(".env.render", usecwd=True)
    if p:
        loaded_any |= _load_if_exists(Path(p))

    p = find_dotenv(".env", usecwd=True)
    if p:
        loaded_any |= _load_if_exists(Path(p))

    # 3) Config utilisateur
    loaded_any |= _load_if_exists(USER_ENV_RENDER)

    if not loaded_any:
        logger.warning("Aucun fichier .env/.env.render trouvé : utilisation exclusive des variables d'environnement")
