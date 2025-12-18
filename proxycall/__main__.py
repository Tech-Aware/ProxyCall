"""Point d'entrée module pour lancer la CLI ProxyCall."""
from __future__ import annotations

from demo.cli import main


def entrypoint() -> int:
    """Exécute la CLI et renvoie le code de sortie approprié."""
    return main()


def entrypoint_live() -> int:
    """Exécute la CLI directement en mode Dev (live)."""
    return main(["--live"])


if __name__ == "__main__":
    raise SystemExit(entrypoint())
