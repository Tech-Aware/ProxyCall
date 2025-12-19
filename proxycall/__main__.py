"""Point d'entrée module pour lancer la CLI ProxyCall."""
from __future__ import annotations


from demo.cli import main
from proxycall.env_loader import load_env_files, ensure_env_render_interactive



def entrypoint() -> int:
    load_env_files()
    ensure_env_render_interactive()
    return main()


def entrypoint_live() -> int:
    load_env_files()
    ensure_env_render_interactive()
    return main(["--live"])



if __name__ == "__main__":
    raise SystemExit(entrypoint())
