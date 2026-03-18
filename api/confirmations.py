import logging
import re
from typing import Dict, Tuple
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.config import settings
from app.validator import phone_e164_strict, email_strict, name_strict, iso_country_strict, number_type_strict, ValidationIssue
from app.logging_config import mask_phone
from integrations.email_client import EmailClient
from integrations.twilio_client import TwilioClient
from repositories.clients_repository import ClientsRepository
from repositories.pools_repository import PoolsRepository
from repositories.confirmation_pending_repository import ConfirmationPendingRepository, PENDING_STATUSES
from services.confirmation_service import ConfirmationService

router = APIRouter()
logger = logging.getLogger(__name__)

VALID_CHANNELS = {"sms", "voice", "email"}


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

        proxy_number = None
        effective_type = number_type

        existing_client = ClientsRepository.find_by_email_or_phone(client_mail, client_phone)
        if existing_client and existing_client.client_proxy_number:
            proxy_number = phone_e164_strict(
                existing_client.client_proxy_number, field="client_proxy_number"
            )
            effective_type = "existant"
            logger.info(
                "Réutilisation du proxy existant pour confirmation",
                extra={
                    "pending_id": pending_id,
                    "client_id": existing_client.client_id,
                    "proxy": mask_phone(proxy_number),
                },
            )
        else:
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

            logger.info(
                "Proxy réservé depuis le pool",
                extra={
                    "pending_id": pending_id,
                    "proxy": mask_phone(proxy_number),
                    "requested_type": number_type,
                    "effective_type": effective_type,
                    "is_fallback": effective_type != number_type,
                },
            )

        # 2) Générer OTP
        otp = ConfirmationPendingRepository.generate_otp(6)

        # 3) Ecrire CONFIRMATION_PENDING (proxy + otp)
        # (la ligne pending doit exister déjà : créée par Apps Script)
        ConfirmationPendingRepository.set_proxy_and_otp(
            pending_id=pending_id,
            proxy_number=proxy_number,
            otp=otp,
            client_name=client_name,
            client_mail=client_mail,
            client_real_phone=client_phone,
        )

        # 4) S'assurer webhooks Twilio OK (utile pour la réponse SMS)
        # Note: ces fonctions retournent True si update effectué, False si déjà OK ou erreur
        # Les fonctions elles-mêmes loggent les détails (déjà OK vs erreur vs update)
        webhook_sms_updated = TwilioClient.ensure_messaging_webhook(proxy_number)
        webhook_voice_updated = TwilioClient.ensure_voice_webhook(proxy_number)
        logger.info(
            "Webhooks vérifiés",
            extra={
                "pending_id": pending_id,
                "proxy": mask_phone(proxy_number),
                "sms_webhook_updated": webhook_sms_updated,
                "voice_webhook_updated": webhook_voice_updated,
            },
        )

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
                "reuse_proxy": bool(existing_client and existing_client.client_proxy_number),
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


# ────────────────────────────────────────────────
# Statut d'une confirmation (pour Apps Script)
# ────────────────────────────────────────────────

@router.get("/status")
def get_confirmation_status(pending_id: str = ""):
    """Retourne le statut actuel d'un pending (pour polling Apps Script)."""
    pending_id = (pending_id or "").strip()
    if not pending_id:
        raise HTTPException(status_code=400, detail="pending_id requis")

    hit = ConfirmationPendingRepository.get_by_pending_id(pending_id)
    if not hit:
        raise HTTPException(status_code=404, detail="pending_id introuvable")

    status = str(hit["record"].get("status") or "").strip()
    return {"pending_id": pending_id, "status": status}


# ────────────────────────────────────────────────
# Resend OTP via un canal alternatif
# ────────────────────────────────────────────────

class ResendConfirmationPayload(BaseModel):
    pending_id: str
    channel: str  # "sms" | "voice" | "email"


@router.post("/resend")
def resend_confirmation(payload: ResendConfirmationPayload = Body(...)):
    """Renvoie l'OTP existant via SMS, appel vocal ou email."""
    try:
        pending_id = str(payload.pending_id or "").strip()
        channel = str(payload.channel or "").strip().lower()

        if not pending_id:
            raise HTTPException(status_code=400, detail="pending_id requis")
        if channel not in VALID_CHANNELS:
            raise HTTPException(status_code=400, detail=f"channel invalide (attendu: {', '.join(sorted(VALID_CHANNELS))})")

        hit = ConfirmationPendingRepository.get_by_pending_id(pending_id)
        if not hit:
            raise HTTPException(status_code=404, detail="pending_id introuvable")

        rec = hit["record"]
        status = str(rec.get("status") or "").strip().upper()
        if status not in PENDING_STATUSES:
            raise HTTPException(status_code=400, detail=f"Confirmation non en attente (status={status})")

        otp = str(rec.get("otp") or "").strip()
        proxy_number = str(rec.get("proxy_number") or "").strip()
        client_phone = str(rec.get("client_real_phone") or "").strip()
        client_name = str(rec.get("client_name") or "").strip()
        client_mail = str(rec.get("client_mail") or "").strip()

        if not otp:
            raise HTTPException(status_code=400, detail="OTP non généré pour ce pending")

        if channel == "sms":
            body = f"ProxyCall - Code de confirmation: {otp}"
            TwilioClient.send_sms(from_number=proxy_number, to_number=client_phone, body=body)

        elif channel == "voice":
            TwilioClient.make_otp_call(
                from_number=proxy_number,
                to_number=client_phone,
                pending_id=pending_id,
            )

        elif channel == "email":
            base_url = (settings.PUBLIC_BASE_URL or "").rstrip("/")
            verify_url = f"{base_url}/confirmations/verify?pending_id={pending_id}&otp={otp}"

            # 1) SMS avec lien de vérification (canal principal)
            if proxy_number and client_phone:
                sms_body = f"ProxyCall - Confirmez votre numéro ici : {verify_url}"
                TwilioClient.send_sms(from_number=proxy_number, to_number=client_phone, body=sms_body)

            # 2) Email HTML (canal secondaire, best-effort)
            if client_mail and EmailClient.is_configured():
                try:
                    EmailClient.send_otp_email(
                        to=client_mail,
                        otp=otp,
                        client_name=client_name or "Client",
                        verify_url=verify_url,
                    )
                except Exception as exc:
                    logger.warning("Echec envoi email OTP (best-effort)", exc_info=exc)

        logger.info(
            "OTP renvoyé",
            extra={"pending_id": pending_id, "channel": channel, "to": mask_phone(client_phone)},
        )
        return {"pending_id": pending_id, "channel": channel, "status": "sent"}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur resend_confirmation", exc_info=exc)
        raise HTTPException(status_code=500, detail="Erreur lors du renvoi de l'OTP") from exc


# ────────────────────────────────────────────────
# Vérification OTP par lien email (GET)
# ────────────────────────────────────────────────

def _verify_html(title: str, message: str, success: bool) -> str:
    color = "#16a34a" if success else "#dc2626"
    icon = "&#10004;" if success else "&#10008;"
    return f"""\
<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head>
<body style="font-family:sans-serif;max-width:480px;margin:40px auto;padding:20px;text-align:center;">
  <div style="font-size:48px;color:{color};">{icon}</div>
  <h1 style="color:{color};">{title}</h1>
  <p>{message}</p>
</body>
</html>"""


@router.get("/verify")
def verify_confirmation(pending_id: str = "", otp: str = ""):
    """Vérifie l'OTP via lien cliquable (email). Retourne une page HTML."""
    pending_id = (pending_id or "").strip()
    otp = (otp or "").strip()

    if not pending_id or not otp:
        return HTMLResponse(
            _verify_html("Lien invalide", "Le lien de confirmation est incomplet.", False),
            status_code=400,
        )

    hit = ConfirmationPendingRepository.get_by_pending_id(pending_id)
    if not hit:
        return HTMLResponse(
            _verify_html("Confirmation introuvable", "Cette demande de confirmation n'existe pas.", False),
            status_code=404,
        )

    rec = hit["record"]
    status = str(rec.get("status") or "").strip().upper()

    if status not in PENDING_STATUSES:
        return HTMLResponse(
            _verify_html("Déjà traité", "Cette confirmation a déjà été effectuée.", False),
            status_code=400,
        )

    expected = str(rec.get("otp") or "").strip()
    if otp != expected:
        return HTMLResponse(
            _verify_html("Code invalide", "Le code de confirmation est incorrect.", False),
            status_code=400,
        )

    # Promotion complète
    proxy_e164 = str(rec.get("proxy_number") or "").strip()
    if not proxy_e164.startswith("+"):
        proxy_e164 = "+" + re.sub(r"\D+", "", proxy_e164)
    sender_e164 = str(rec.get("client_real_phone") or "").strip()
    if not sender_e164.startswith("+"):
        sender_e164 = "+" + re.sub(r"\D+", "", sender_e164)

    try:
        ConfirmationService.promote_pending(
            pending_row=hit["row"],
            record=rec,
            proxy_e164=proxy_e164,
            sender_e164=sender_e164,
        )
    except Exception as exc:
        logger.exception("Erreur promotion via lien email", exc_info=exc)
        return HTMLResponse(
            _verify_html("Erreur", "Une erreur est survenue lors de la confirmation. Veuillez réessayer.", False),
            status_code=500,
        )

    return HTMLResponse(
        _verify_html(
            "Confirmation réussie",
            "Votre numéro ProxyCall est maintenant actif. Vous pouvez fermer cette page.",
            True,
        )
    )
