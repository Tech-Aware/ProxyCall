import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_sheets_access_demo():
    """Test démo hors-ligne qui illustre le format des données du sheet."""
    try:
        logger.info("Chargement de données simulées pour le sheet clients.")
        fake_records = [
            {
                "client_id": "demo-1",
                "client_name": "Alice Demo",
                "client_mail": "alice.demo@example.com",
                "client_real_phone": "+33123456789",
                "client_proxy_number": "+33900000000",
                "client_iso_residency": "FR",
                "client_country_code": "33",
            },
            {
                "client_id": "demo-2",
                "client_name": "Bob Demo",
                "client_mail": "bob.demo@example.com",
                "client_real_phone": "+442012345678",
                "client_proxy_number": "+442000000000",
                "client_iso_residency": "GB",
                "client_country_code": "44",
            },
        ]

        logger.info("Affichage des données simulées pour validation.")
        print(fake_records)
        print("OK -> Accès démo au sheet simulé")
    except Exception as exc:
        logger.exception("Échec du test démo d'accès au sheet: %s", exc)
        raise


if __name__ == "__main__":
    test_sheets_access_demo()
