import logging
import os

import pytest

from services.call_routing_service import CallRoutingService
from services.clients_service import ClientsService


pytestmark = pytest.mark.skipif(
    os.getenv("PROXYCALL_RUN_LIVE") != "1"
    or not os.getenv("TWILIO_ACCOUNT_SID")
    or not os.getenv("TWILIO_AUTH_TOKEN")
    or not os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE"),
    reason="Tests live désactivés sans PROXYCALL_RUN_LIVE=1 et credentials complets.",
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_create_client_and_proxy_live():
    """Test d'intégration en conditions réelles avec Twilio et Google Sheets."""
    try:
        logger.info("Démarrage du test live: création ou récupération du client et numéro proxy.")
        client_id = "kevin-test"
        client_name = "Kevin"
        client_mail = "kevin@example.com"
        client_real_phone = "+33783529862"  # remplace par ton numéro réel, ex: +33612345678
        client_iso_residency = "FR"

        client = ClientsService.get_or_create_client(
            client_id=client_id,
            client_name=client_name,
            client_mail=client_mail,
            client_real_phone=client_real_phone,
            client_iso_residency=client_iso_residency,
        )

        logger.info("Client créé ou récupéré avec succès.")
        print("=== Client créé ou récupéré ===")
        print("client_id   :", client.client_id)
        print("client_name :", client.client_name)
        print("client_mail :", client.client_mail)
        print("phone_real  :", client.client_real_phone)
        print("phone_proxy :", client.client_proxy_number)
        print("iso_residency:", client.client_iso_residency)
        print("country_code:", client.client_country_code)

        logger.info("Vérification de la TwiML avec indicatif identique (doit DIAL).")
        print("\n=== TwiML avec même indicatif (doit DIAL) ===")
        twiml_ok = CallRoutingService.handle_incoming_call(
            proxy_number=client.client_proxy_number,
            caller_number=client_real_phone,
        )
        print(twiml_ok)

        logger.info("Vérification de la TwiML avec indicatif différent (doit BLOQUER).")
        print("\n=== TwiML avec indicatif différent (doit BLOQUER) ===")
        twiml_block = CallRoutingService.handle_incoming_call(
            proxy_number=client.client_proxy_number,
            caller_number="+49123456789",
        )
        print(twiml_block)
    except Exception as exc:
        logger.exception("Échec du test live client_repository: %s", exc)
        raise


if __name__ == "__main__":
    test_create_client_and_proxy_live()
