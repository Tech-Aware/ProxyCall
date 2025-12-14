from datetime import datetime

from twilio.rest import Client as TwilioRest

from app.config import settings
from repositories.pools_repository import PoolsRepository


twilio = TwilioRest(
    settings.TWILIO_ACCOUNT_SID,
    settings.TWILIO_AUTH_TOKEN,
)


class TwilioClient:
    """Client Twilio avec gestion d'un pool de numéros par pays."""

    @staticmethod
    def _purchase_number(country: str, friendly_name: str) -> str:
        """Achète un numéro local et retourne son identifiant Twilio."""

        available_numbers = twilio.available_phone_numbers(country).local.list(limit=1)
        if not available_numbers:
            raise RuntimeError(f"Aucun numéro local disponible pour le pays {country}.")

        phone_number = available_numbers[0].phone_number
        incoming = twilio.incoming_phone_numbers.create(
            phone_number=phone_number,
            voice_url=settings.VOICE_WEBHOOK_URL,
            friendly_name=friendly_name,
        )
        return incoming.phone_number

    @classmethod
    def _fill_pool(cls, country: str, batch_size: int) -> None:
        for idx in range(batch_size):
            friendly = f"Pool-{country}-{idx + 1}"
            purchased = cls._purchase_number(country, friendly)
            PoolsRepository.save_number(
                country_iso=country,
                phone_number=purchased,
                status="available",
                friendly_name=friendly,
                date_achat=datetime.utcnow().isoformat(),
            )

    @classmethod
    def fill_pool(cls, country: str, batch_size: int) -> None:
        """Rend disponible un lot de numéros pour le pays demandé."""

        cls._fill_pool(country, batch_size)

    @classmethod
    def list_available(cls, country: str):
        """Expose les entrées disponibles du pool côté Google Sheets."""

        return PoolsRepository.list_available(country)

    @classmethod
    def buy_number_for_client(
        cls,
        friendly_name: str,
        country: str | None = None,
        attribution_to_client_name: str | None = None,
    ) -> str:
        """
        Récupère un numéro dans le pool du pays demandé, ou achète un batch
        de numéros si le pool est vide.

        :param friendly_name: label appliqué au numéro attribué au client
        :param country: code ISO pays attendu par l'API Twilio (ex: "FR", "US")
        """

        country_iso = country or settings.TWILIO_PHONE_COUNTRY
        available_records = PoolsRepository.list_available(country_iso)

        if not available_records:
            cls._fill_pool(country_iso, settings.TWILIO_POOL_SIZE)
            available_records = PoolsRepository.list_available(country_iso)

        if not available_records:
            raise RuntimeError(f"Aucun numéro disponible pour le pays {country_iso}.")

        record = available_records[0]
        number = record.get("phone_number")

        # Optionnel : on renomme le numéro pour la visibilité côté Twilio
        try:
            twilio.incoming_phone_numbers.list(phone_number=number)[0].update(
                friendly_name=friendly_name
            )
        except Exception:
            # Pas bloquant : le numéro reste fonctionnel même sans renommage
            pass

        PoolsRepository.mark_assigned(
            phone_number=number,
            date_attribution=datetime.utcnow().isoformat(),
            attribution_to_client_name=attribution_to_client_name,
        )

        return number
