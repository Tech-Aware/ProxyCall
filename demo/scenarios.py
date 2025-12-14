"""Scénarios de démonstration pour le client et le CLI.

Chaque scénario est pensé pour être rejouable rapidement en mode MOCK
sans dépendances externes. Les fonctions retournent les sorties capturées
pour pouvoir montrer le rendu du CLI dans une démo live ou un notebook.
"""
from __future__ import annotations

import argparse
import io
import logging
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List

from demo.cli import (
    CLIError,
    MockJsonStore,
    do_create_client,
    do_lookup,
    do_simulate_call,
    setup_logging,
)


LOGGER = logging.getLogger("proxycall.demo.scenarios")
LOGGER.setLevel(logging.INFO)
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)


@contextmanager
def _capture_stdout() -> io.StringIO:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        yield buffer


def _build_logger(ctx: Dict[str, Any]) -> logging.Logger:
    try:
        return setup_logging("INFO", json_logs=False, ctx=ctx)
    except Exception as exc:  # pragma: no cover - défensif
        LOGGER.exception("Échec configuration des logs de scénario: %s", exc)
        raise


def run_mock_client_journey(fixtures_path: Path | str = Path("demo/fixtures/clients.json")) -> Dict[str, str]:
    """Joue un parcours complet (création, lookup, routage) en mode mock.

    Retourne un dictionnaire avec les sorties standard du CLI afin de pouvoir
    les afficher facilement pendant une démo.
    """
    outputs: Dict[str, str] = {}
    logger = _build_logger({"scenario": "mock-client-journey"})
    store = MockJsonStore(Path(fixtures_path), logger=logger)

    try:
        LOGGER.info("Démarrage du scénario mock (fixtures=%s).", fixtures_path)

        args_create = argparse.Namespace(
            client_id=1,
            name="Client Démo",
            client_mail="demo@example.com",
            client_real_phone="+33123456789",
            mode="mock",
        )
        with _capture_stdout() as buf_create:
            do_create_client(args_create, store, logger)
        outputs["create_client"] = buf_create.getvalue().strip()

        args_lookup = argparse.Namespace(proxy="+33900000000")
        with _capture_stdout() as buf_lookup:
            do_lookup(args_lookup, store, logger)
        outputs["lookup"] = buf_lookup.getvalue().strip()

        args_call_ok = argparse.Namespace(from_number="+33111111111", to_number="+33900000000")
        with _capture_stdout() as buf_call_ok:
            do_simulate_call(args_call_ok, store, logger)
        outputs["simulate_call_same_country"] = buf_call_ok.getvalue().strip()

        args_call_block = argparse.Namespace(from_number="+442222222222", to_number="+33900000000")
        with _capture_stdout() as buf_call_block:
            do_simulate_call(args_call_block, store, logger)
        outputs["simulate_call_other_country"] = buf_call_block.getvalue().strip()

        LOGGER.info("Scénario mock terminé avec succès.")
        return outputs
    except CLIError as exc:
        logger.exception("Erreur fonctionnelle pendant le scénario mock: %s", exc)
        raise
    except Exception as exc:  # pragma: no cover - catch-all démo
        logger.exception("Erreur inattendue pendant le scénario mock: %s", exc)
        raise


def cli_command_examples(fixtures_path: Path | str = Path("demo/fixtures/clients.json")) -> List[str]:
    """Fournit la commande unique pour lancer le menu interactif."""
    try:
        path_str = str(fixtures_path)
        LOGGER.info("Commande de lancement CLI (fixtures=%s).", path_str)
        base = f"python -m demo.cli --mock --fixtures {path_str}"
        return [f"{base}  # puis répondre 1/2/3/4 dans le menu interactif"]
    except Exception as exc:
        LOGGER.exception("Impossible de préparer la commande de démo: %s", exc)
        raise


__all__ = [
    "run_mock_client_journey",
    "cli_command_examples",
]
