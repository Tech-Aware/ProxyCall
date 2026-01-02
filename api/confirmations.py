import logging
from typing import Dict, Tuple
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

        # 1) Réservation pool (pending) avec fallback automatique si le type demandé est saturé
        reservation, effective_type = _reserve_pending_with_fallback(
            country_iso=country_iso,
            requested_type=number_type,
            pending_id=pending_id,
            attribution_to_client_name=client_name,  # tu as choisi de le conserver
        )

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
            extra={
                "pending_id": pending_id,
                "proxy": mask_phone(proxy_number),
                "to": mask_phone(client_phone),
                "number_type": effective_type,
            },
        )
        return {
            "pending_id": pending_id,
            "proxy_number": proxy_number,
            "status": "PENDING",
            "number_type": effective_type,
        }

    except ValidationIssue as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        logger.error("create_confirmation refusé: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("Erreur create_confirmation", exc_info=exc)
        raise HTTPException(status_code=500, detail="Erreur interne confirmation") from exc


def _reserve_pending_with_fallback(
    *,
    country_iso: str,
    requested_type: str,
    pending_id: str,
    attribution_to_client_name: str,
) -> Tuple[Dict[str, str], str]:
    """
    Tente une réservation pour le type demandé.
    Si aucun numéro n'est disponible, tente automatiquement l'autre type (mobile/local) avant d'échouer.
    Retourne (reservation, effective_type).
    """

    def _attempt(nt: str) -> Dict[str, str] | None:
        return PoolsRepository.reserve_first_available_pending(
            country_iso=country_iso,
            number_type=nt,
            pending_id=pending_id,
            attribution_to_client_name=attribution_to_client_name,
        )

    # 1) essai avec le type demandé
    reservation = _attempt(requested_type)
    if reservation:
        return reservation, requested_type

    # 2) fallback sur l'autre type si dispo
    fallback_type = "local" if requested_type == "mobile" else "mobile"
    available_fallback = PoolsRepository.list_available(country_iso, number_type=fallback_type)
    available_requested = PoolsRepository.list_available(country_iso, number_type=requested_type)

    if available_fallback:
        logger.warning(
            "[magenta]POOL[/magenta] fallback number_type pending_id=%s requested=%s fallback=%s available_fallback=%s",
            pending_id,
            requested_type,
            fallback_type,
            len(available_fallback),
        )
        reservation = _attempt(fallback_type)
        if reservation:
            return reservation, fallback_type

    # 3) aucune option disponible -> log contexte détaillé
    breakdown = _available_breakdown(country_iso)
    logger.error(
        "[magenta]POOL[/magenta] aucun numéro disponible pending_id=%s country=%s requested=%s fallback=%s "
        "available_requested=%s available_fallback=%s breakdown=%s",
        pending_id,
        country_iso,
        requested_type,
        fallback_type,
        len(available_requested),
        len(available_fallback),
        breakdown,
    )
    raise RuntimeError(
        f"Aucun numéro disponible pour {country_iso} ({requested_type}) | pool={breakdown}"
    )


def _available_breakdown(country_iso: str) -> dict[str, int]:
    """Retourne un breakdown par type pour aider au diagnostic."""
    breakdown: dict[str, int] = {}
    available = PoolsRepository.list_available(country_iso, number_type=None)
    for rec in available:
        nt = str(rec.get("number_type", "")).strip().lower() or "inconnu"
        breakdown[nt] = breakdown.get(nt, 0) + 1
    breakdown["total"] = len(available)
    return breakdown


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
