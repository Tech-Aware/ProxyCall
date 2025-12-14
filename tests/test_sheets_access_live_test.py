import logging
import os

import pytest

from integrations.sheets_client import SheetsClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


pytestmark = pytest.mark.skipif(
    os.getenv("PROXYCALL_RUN_LIVE") != "1"
    or not os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    or not os.getenv("GOOGLE_SHEET_NAME"),
    reason="Accès live au sheet désactivé sans PROXYCALL_RUN_LIVE=1 et config Google.",
)


def test_sheets_access_live():
    """Test live pour vérifier l'accès au Google Sheet réel."""
    try:
        logger.info("Récupération du sheet des clients en mode live.")
        sheet = SheetsClient.get_clients_sheet()
        records = sheet.get_all_records()
        logger.info("Lecture du sheet terminée, %d lignes trouvées.", len(records))
        print(records)
        print("OK -> Accès réussi au sheet")
    except Exception as exc:
        logger.exception("Échec du test live d'accès au sheet: %s", exc)
        raise


if __name__ == "__main__":
    test_sheets_access_live()
