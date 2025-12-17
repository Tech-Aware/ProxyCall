"""Point d'entrée pour lancer l'API ProxyCall avec gestion robuste du port Render."""
import logging
import os
import re
import sys

import uvicorn

from app.main import app


LOGGER = logging.getLogger(__name__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")



def _configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)
    LOGGER.debug("Journalisation configurée (niveau=%s)", level_name)


def _extraire_port() -> int:
    port_brut = os.getenv("PORT", "8000")
    port_nettoye = port_brut.strip()
    LOGGER.debug("Valeur brute de PORT reçue: %r", port_brut)

    if port_nettoye.isdigit():
        port_str = port_nettoye
    else:
        correspondances = re.findall(r"\d+", port_nettoye)
        if len(correspondances) == 1:
            port_str = correspondances[0]
            LOGGER.warning(
                "Valeur PORT normalisée de %r vers %s pour rester compatible avec uvicorn.",
                port_brut,
                port_str,
            )
        elif correspondances:
            LOGGER.error(
                "Valeur PORT ambiguë (%r) : plusieurs groupes de chiffres détectés (%s).",
                port_brut,
                ", ".join(correspondances),
            )
            raise ValueError(f"Port ambigu: {port_brut}")
        else:
            LOGGER.error(
                "Impossible d'extraire un port numérique depuis PORT=%r.", port_brut
            )
            raise ValueError(f"Port invalide: {port_brut}")

    try:
        port = int(port_str)
    except (TypeError, ValueError) as exc:  # pragma: no cover - log avant sortie
        LOGGER.error(
            "Port fourni invalide: %r. Veuillez définir une variable PORT numérique.",
            port_brut,
            exc_info=exc,
        )
        raise

    if not 0 < port < 65536:
        LOGGER.error("Port hors plage autorisée: %s", port)
        raise ValueError(f"Port hors plage autorisée: {port}")

    return port


def lancer_serveur() -> None:
    _configure_logging()
    try:
        port = _extraire_port()
    except Exception:
        LOGGER.critical("Arrêt du serveur: impossible de déterminer un port valide.")
        sys.exit(1)

    LOGGER.info("Lancement d'uvicorn sur 0.0.0.0:%s", port)
    print("DÉMARRAGE - Valeur PORT =", port)
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    lancer_serveur()
