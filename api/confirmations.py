import logging
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.validator import phone_e164_strict, email_strict, name_strict, iso_country_strict, number_type_strict, ValidationIssue
from app.logging_config import mask_phone
from integrations.twilio_client import TwilioClient
from repositories.pools_repository import PoolsRepository
from repositories.confirmation_pending_repository import ConfirmationPendingRepository

router = APIRouter()
logger = logging.getLogger(__name__)


class CreateConfirmationPayload(BaseModel):
    pending_id: str
    client_name: str
    client_mail: str
    client_real_phone: str
    country_iso: str | None = None
    number_type: str | None = None


@router.post("/create")
def create_confirmation(payload: CreateConfirmationPayload = Body(...)):
    """
    Étape 1 du workflow:
    - réserve un proxy dans TwilioPools (reserved_token=pending_id)
    - écrit proxy_number + otp dans CONFIRMATION_PENDING
    - envoie SMS OTP via Twilio depuis le proxy vers le client
    """
    try:
        pending_id = str(payload.pending_id or "").strip()
        if not pending_id:
            raise ValidationIssue("valeur manquante", field="pending_id")

        client_name = name_strict(payload.client_name, field="client_name")
        client_mail = email_strict(payload.client_mail, field="client_mail").lower()
        client_phone = phone_e164_strict(payload.client_real_phone, field="client_real_phone")

        country_iso = iso_country_strict(payload.country_iso or settings.TWILIO_PHONE_COUNTRY, field="country_iso")
        number_type = number_type_strict(payload.number_type or settings.TWILIO_NUMBER_TYPE, field="number_type")

        # 1) Réservation pool (pending)
        reservation = PoolsRepository.reserve_first_available_pending(
            country_iso=country_iso,
            number_type=number_type,
            pending_id=pending_id,
            attribution_to_client_name=client_name,  # tu as choisi de le conserver
        )
        if not reservation:
            raise RuntimeError(f"Aucun numéro disponible pour {country_iso} ({number_type})")

        proxy_number = str(reservation.get("phone_number") or "").strip()
        if not proxy_number:
            raise RuntimeError("Proxy réservé invalide")

        # 2) Générer OTP
        otp = ConfirmationPendingRepository.generate_otp(6)

        # 3) Ecrire CONFIRMATION_PENDING (proxy + otp)
        # (la ligne pending doit exister déjà : créée par Apps Script)
        ConfirmationPendingRepository.set_proxy_and_otp(
            pending_id=pending_id,
            proxy_number=proxy_number,
            otp=otp,
        )

        # 4) S'assurer webhooks Twilio OK (utile pour la réponse SMS)
        TwilioClient.ensure_messaging_webhook(proxy_number)
        TwilioClient.ensure_voice_webhook(proxy_number)

        # 5) Envoyer SMS OTP
        body = f"ProxyCall - Code de confirmation: {otp}"
        TwilioClient.send_sms(from_number=proxy_number, to_number=client_phone, body=body)

        logger.info(
            "Confirmation créée (pending)",
            extra={"pending_id": pending_id, "proxy": mask_phone(proxy_number), "to": mask_phone(client_phone)},
        )
        return {"pending_id": pending_id, "proxy_number": proxy_number, "status": "PENDING"}

    except ValidationIssue as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        logger.error("create_confirmation refusé: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("Erreur create_confirmation", exc_info=exc)
        raise HTTPException(status_code=500, detail="Erreur interne confirmation") from exc


@router.post("/expire")
def expire_pending(hours: int = Body(48)):
    """
    Expire les pending >hours et libère les proxys réservés.
    """
    try:
        expired = ConfirmationPendingRepository.expire_older_than(hours=int(hours))
        released_total = 0

        for item in expired:
            pid = item.get("pending_id", "")
            if pid:
                released_total += PoolsRepository.release_reservation_by_token(reserved_token=pid)

        return {"expired": expired, "released_total": released_total}

    except Exception as exc:  # pragma: no cover
        logger.exception("Erreur expire_pending", exc_info=exc)
        raise HTTPException(status_code=500, detail="Erreur expiration confirmations") from exc
