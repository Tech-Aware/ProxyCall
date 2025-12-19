"""Point d'entrée module pour lancer la CLI ProxyCall."""
from __future__ import annotations
from proxycall.env_loader import load_env_files


from demo.cli import main


def entrypoint() -> int:
    load_env_files()
    return main()


def entrypoint_live() -> int:
    """Exécute la CLI directement en mode Dev (live)."""
    return main(["--live"])



if __name__ == "__main__":
    raise SystemExit(entrypoint())
