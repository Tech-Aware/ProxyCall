import logging
from datetime import datetime

from twilio.rest import Client as TwilioRest

from app.config import settings
from repositories.pools_repository import PoolsRepository


logger = logging.getLogger(__name__)

twilio = TwilioRest(
    settings.TWILIO_ACCOUNT_SID,
    settings.TWILIO_AUTH_TOKEN,
)


class TwilioClient:
    """Client Twilio avec gestion d'un pool de numéros par pays."""

    @staticmethod
    def _purchase_number(
        country: str,
        friendly_name: str,
        number_type: str = settings.TWILIO_NUMBER_TYPE,
    ) -> str:
        """
        Achète un numéro (mobile par défaut) et retourne son identifiant Twilio.

        Si aucun numéro mobile n'est disponible, la méthode bascule explicitement sur
        l'achat d'un numéro local en émettant un message, et lève une erreur si aucun
        numéro local n'est disponible non plus.
        """

        if number_type == "mobile":
            try:
                available_numbers = (
                    twilio.available_phone_numbers(country).mobile.list(limit=1)
                )
            except Exception:
                logger.info(
                    "Les numéros mobiles ne sont pas disponibles pour %s, tentative avec des numéros locaux.",
                    country,
                )
                available_numbers = []

            if not available_numbers:
                logger.info(
                    "Aucun numéro mobile disponible pour le pays %s, basculement vers les numéros locaux.",
                    country,
                )
                available_numbers = (
                    twilio.available_phone_numbers(country).local.list(limit=1)
                )
                if not available_numbers:
                    raise RuntimeError(
                        f"Aucun numéro mobile ou local disponible pour le pays {country}."
                    )
        else:
            available_numbers = twilio.available_phone_numbers(country).local.list(limit=1)
            if not available_numbers:
                raise RuntimeError(
                    f"Aucun numéro local disponible pour le pays {country}."
                )

        phone_number = available_numbers[0].phone_number
        create_kwargs = {
            "phone_number": phone_number,
            "voice_url": settings.VOICE_WEBHOOK_URL,
            "friendly_name": friendly_name,
        }
        if settings.TWILIO_ADDRESS_SID:
            create_kwargs["address_sid"] = settings.TWILIO_ADDRESS_SID

        incoming = twilio.incoming_phone_numbers.create(**create_kwargs)
        return incoming.phone_number

    @classmethod
    def _fill_pool(
        cls,
        country: str,
        batch_size: int,
        number_type: str = settings.TWILIO_NUMBER_TYPE,
    ) -> None:
        for idx in range(batch_size):
            friendly = f"Pool-{country}-{idx + 1}"
            purchased = cls._purchase_number(country, friendly, number_type=number_type)
            PoolsRepository.save_number(
                country_iso=country,
                phone_number=purchased,
                status="available",
                friendly_name=friendly,
                date_achat=datetime.utcnow().isoformat(),
            )

    @classmethod
    def fill_pool(
        cls,
        country: str,
        batch_size: int,
        number_type: str = settings.TWILIO_NUMBER_TYPE,
    ) -> None:
        """
        Rend disponible un lot de numéros pour le pays demandé.

        :param number_type: type de numéro à acheter ("mobile" par défaut, "local" sinon)
        """

        cls._fill_pool(country, batch_size, number_type=number_type)

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
        number_type: str = settings.TWILIO_NUMBER_TYPE,
    ) -> str:
        """
        Récupère un numéro dans le pool du pays demandé, ou achète un batch
        de numéros si le pool est vide.

        :param friendly_name: label appliqué au numéro attribué au client
        :param country: code ISO pays attendu par l'API Twilio (ex: "FR", "US")
        :param number_type: type de numéro à acheter si nécessaire ("mobile" par défaut)
        """

        country_iso = country or settings.TWILIO_PHONE_COUNTRY
        available_records = PoolsRepository.list_available(country_iso)

        if not available_records:
            cls._fill_pool(
                country_iso,
                settings.TWILIO_POOL_SIZE,
                number_type=number_type,
            )
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
