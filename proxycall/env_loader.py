from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

logger = logging.getLogger(__name__)


def load_env_files() -> None:
    """
    Ordre de résolution (Render-first) :
    1) .env.render à côté du binaire (mode frozen PyInstaller)
    2) .env à côté du binaire
    3) find_dotenv(.env.render) depuis cwd/parents
    4) find_dotenv(.env) depuis cwd/parents
    """
    candidates: list[Path] = []

    # 1) Dossier du binaire (PyInstaller: sys.executable pointe vers le binaire)
    try:
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / ".env.render")
        candidates.append(exe_dir / ".env")
    except Exception:
        pass

    # 2) Depuis le cwd/parents
    p_render = find_dotenv(".env.render", usecwd=True)
    if p_render:
        candidates.append(Path(p_render))

    p_env = find_dotenv(".env", usecwd=True)
    if p_env:
        candidates.append(Path(p_env))

    loaded_any = False
    for p in candidates:
        try:
            if p.exists() and p.is_file():
                load_dotenv(p, override=False)
                logger.info("Fichier env chargé: %s", str(p))
                loaded_any = True
        except Exception as exc:
            logger.warning("Impossible de charger %s: %s", str(p), exc)

    if not loaded_any:
        logger.warning("Aucun fichier .env.render/.env trouvé. Utilisation des variables d'environnement uniquement.")
