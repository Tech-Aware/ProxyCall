import logging
from dataclasses import dataclass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class DemoClient:
    client_id: str
    client_name: str
    client_mail: str
    client_real_phone: str
    client_proxy_number: str
    client_iso_residency: str
    client_country_code: str


def test_create_client_and_proxy_demo():
    """Démo hors-ligne du scénario de création client et routage d'appel."""
    try:
        logger.info("Démarrage du test démo: génération de données simulées.")
        demo_client = DemoClient(
            client_id="demo-client",
            client_name="Demo User",
            client_mail="demo@example.com",
            client_real_phone="+33123456789",
            client_proxy_number="+33900000000",
            client_iso_residency="FR",
            client_country_code="33",
        )

        logger.info("Affichage des données simulées pour vérification.")
        print("=== Client simulé ===")
        print("client_id   :", demo_client.client_id)
        print("client_name :", demo_client.client_name)
        print("client_mail :", demo_client.client_mail)
        print("phone_real  :", demo_client.client_real_phone)
        print("phone_proxy :", demo_client.client_proxy_number)
        print("iso_residency:", demo_client.client_iso_residency)
        print("country_code:", demo_client.client_country_code)

        logger.info("Présentation des TwiML simulées (aucun appel réel).")
        print("\n=== TwiML avec même indicatif (démo DIAL) ===")
        print("<Response><Dial>+33123456789</Dial></Response>")

        print("\n=== TwiML avec indicatif différent (démo BLOCK) ===")
        print("<Response><Reject reason=\"rejected\" /></Response>")
    except Exception as exc:
        logger.exception("Échec du test démo client_repository: %s", exc)
        raise


if __name__ == "__main__":
    test_create_client_and_proxy_demo()
