from twilio.rest import Client as TwilioRest
from app.config import settings


twilio = TwilioRest(
    settings.TWILIO_ACCOUNT_SID,
    settings.TWILIO_AUTH_TOKEN,
)


class TwilioClient:
    @staticmethod
    def buy_number_for_client(*, friendly_name: str, country: str) -> str:
        """
        1. Cherche un numéro local disponible dans le pays demandé.
        2. Achète ce numéro.
        3. Configure le webhook voice.
        4. Retourne le numéro (phone_proxy).
        """

        # 1) Récupérer un numéro disponible
        available_numbers = twilio.available_phone_numbers(country).local.list(limit=1)
        if not available_numbers:
            raise RuntimeError(f"Aucun numéro local disponible pour le pays {country}.")

        phone_number = available_numbers[0].phone_number

        # 2) Acheter ce numéro + configurer le webhook
        incoming = twilio.incoming_phone_numbers.create(
            phone_number=phone_number,
            voice_url=f"{settings.PUBLIC_BASE_URL}/twilio/voice",
            friendly_name=friendly_name,
        )

        return incoming.phone_number
