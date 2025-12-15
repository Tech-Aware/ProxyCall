import logging
import re
from datetime import datetime

from twilio.base.exceptions import TwilioRestException
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

        effective_number_type = number_type
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
                effective_number_type = "local"
                if not available_numbers:
                    raise RuntimeError(
                        f"Aucun numéro mobile ou local disponible pour le pays {country}."
                    )
        else:
            available_numbers = twilio.available_phone_numbers(country).local.list(limit=1)
            effective_number_type = "local"
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
        if settings.TWILIO_BUNDLE_SID and effective_number_type == "local":
            create_kwargs["bundle_sid"] = settings.TWILIO_BUNDLE_SID

        try:
            incoming = twilio.incoming_phone_numbers.create(**create_kwargs)
        except TwilioRestException as exc:  # pragma: no cover - gestion détaillée testée plus bas
            if exc.code == 21649:
                raise RuntimeError(
                    "L'achat du numéro requiert un bundle et une adresse. "
                    "Renseignez TWILIO_BUNDLE_SID et TWILIO_ADDRESS_SID (adresse appartenant au bundle)."
                ) from exc
            if exc.code == 21651:
                raise RuntimeError(
                    "L'adresse fournie n'est pas rattachée au bundle Twilio. "
                    "Vérifiez que TWILIO_ADDRESS_SID correspond bien au bundle TWILIO_BUNDLE_SID."
                ) from exc
            raise

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
    def list_twilio_numbers(cls):
        """Liste tous les numéros actuellement possédés sur le compte Twilio."""

        try:
            incoming_numbers = twilio.incoming_phone_numbers.list()
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception(
                "Impossible de récupérer les numéros Twilio existants", exc_info=exc
            )
            return []

        numbers = []
        for number in incoming_numbers:
            numbers.append(
                {
                    "phone_number": getattr(number, "phone_number", ""),
                    "friendly_name": getattr(number, "friendly_name", "") or "",
                    "country_iso": getattr(number, "iso_country", "") or "",
                }
            )
        return numbers

    @staticmethod
    def _normalize_phone_number(number: str | None) -> str:
        """Normalise les numéros pour comparaison (strip, suppression des espaces)."""

        if number is None:
            return ""

        raw = str(number).strip()
        if not raw:
            return ""

        digits_only = re.sub(r"\D", "", raw)

        if not digits_only:
            return ""

        return f"+{digits_only}"

    @classmethod
    def sync_twilio_numbers_with_sheet(
        cls,
        *,
        apply: bool = True,
        twilio_numbers: list[dict[str, str]] | None = None,
    ):
        """
        Synchronise la feuille TwilioPools avec les numéros présents côté Twilio.

        :param apply: lorsqu'il est à False, la méthode ne modifie pas la feuille et
            retourne uniquement les numéros manquants.
        :param twilio_numbers: liste pré-récupérée pour éviter un double appel à
            l'API Twilio.
        :returns: dictionnaire contenant tous les numéros Twilio, les numéros
            absents de la feuille, ainsi que ceux réellement ajoutés.
        """

        numbers_from_twilio = twilio_numbers or cls.list_twilio_numbers()
        existing_records = PoolsRepository.list_all()
        existing_numbers: set[str] = set()
        for rec in existing_records:
            normalized = cls._normalize_phone_number(rec.get("phone_number"))
            if normalized:
                existing_numbers.add(normalized)

        missing_numbers: list[str] = []
        added_numbers: list[str] = []

        for number in numbers_from_twilio:
            phone_number = cls._normalize_phone_number(number.get("phone_number"))
            if not phone_number or phone_number in existing_numbers:
                continue

            missing_numbers.append(phone_number)
            if not apply:
                continue

            country_iso = (
                number.get("country_iso") or settings.TWILIO_PHONE_COUNTRY
            ).upper()
            PoolsRepository.save_number(
                country_iso=country_iso,
                phone_number=phone_number,
                status="available",
                friendly_name=number.get("friendly_name"),
                date_achat=datetime.utcnow().isoformat(),
            )
            added_numbers.append(phone_number)

        return {
            "twilio_numbers": numbers_from_twilio,
            "missing_numbers": missing_numbers,
            "added_numbers": added_numbers,
        }

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
