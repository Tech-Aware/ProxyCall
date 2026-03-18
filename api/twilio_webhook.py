# api/twilio_webhook.py
import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.logging_config import mask_phone
from repositories.confirmation_pending_repository import ConfirmationPendingRepository, PENDING_STATUSES
from services.call_routing_service import CallRoutingService
from services.confirmation_service import ConfirmationService
from services.message_routing_service import MessageRoutingService

router = APIRouter()
logger = logging.getLogger(__name__)


def _normalize_e164_like(num: str | None) -> str:
    """Nettoie le numéro Twilio (E.164 ou préfixé whatsapp:) en format +digits."""
    if not num:
        return ""
    digits = re.sub(r"\D+", "", str(num))
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    return "+" + digits


@router.post("/twilio/voice")
async def twilio_voice_webhook(request: Request):
    form = await request.form()
    from_raw = form.get("From")
    to_raw = form.get("To")

    from_number = _normalize_e164_like(from_raw)
    to_number = _normalize_e164_like(to_raw)

    logger.info(
        "Webhook Twilio reçu (from=%s -> to=%s)",
        mask_phone(from_number),
        mask_phone(to_number),
    )

    try:
        twiml = CallRoutingService.handle_incoming_call(
            proxy_number=to_number,
            caller_number=from_number,
        )
        return Response(content=twiml, media_type="text/xml")
    except Exception as exc:  # pragma: no cover
        logger.exception(
            "Erreur lors du traitement du webhook Twilio", exc_info=exc
        )
        fallback = "<Response><Say>Service temporairement indisponible.</Say></Response>"
        return Response(content=fallback, media_type="text/xml", status_code=500)


@router.post("/twilio/sms")
async def twilio_sms_webhook(request: Request):
    form = await request.form()

    # ── Filtre 1 : status callbacks (accusés de réception Twilio) ──
    message_status = form.get("MessageStatus")
    if message_status:
        logger.info(
            "Twilio status callback ignoré (status=%s, sid=%s)",
            message_status,
            form.get("MessageSid", "?"),
        )
        return Response(content="<Response></Response>", media_type="text/xml")

    from_raw = form.get("From")
    to_raw = form.get("To")
    body = form.get("Body") or ""

    from_number = _normalize_e164_like(from_raw)
    to_number = _normalize_e164_like(to_raw)

    # ── Filtre 2 : From ou To manquant ──
    if not from_number or not to_number:
        logger.warning(
            "Webhook SMS ignoré : From ou To manquant (from=%r, to=%r, keys=%s)",
            from_raw,
            to_raw,
            sorted(form.keys()),
        )
        return Response(content="<Response></Response>", media_type="text/xml")

    logger.info(
        "Webhook SMS Twilio reçu (from=%s -> to=%s)",
        mask_phone(from_number),
        mask_phone(to_number),
    )

    try:
        twiml = MessageRoutingService.handle_incoming_sms(
            proxy_number=to_number, sender_number=from_number, body=body
        )
        return Response(content=twiml, media_type="text/xml")
    except Exception as exc:  # pragma: no cover - dépendances externes
        logger.exception(
            "Erreur lors du traitement du webhook SMS Twilio",
            exc_info=exc,
            extra={
                "from": mask_phone(from_number),
                "to": mask_phone(to_number),
                "body_preview": (body[:80] + "..." if len(body) > 80 else body),
            },
        )
        fallback = "<Response><Message>Service SMS temporairement indisponible.</Message></Response>"
        return Response(content=fallback, media_type="text/xml", status_code=500)


MAX_VOICE_OTP_ATTEMPTS = 3


@router.post("/twilio/voice/otp")
async def twilio_voice_otp(request: Request):
    """TwiML pour l'appel OTP sortant : lit le code et demande saisie DTMF."""
    form = await request.form()
    pending_id = str(form.get("pending_id") or request.query_params.get("pending_id") or "").strip()

    if not pending_id:
        resp = VoiceResponse()
        resp.say("Erreur technique. Au revoir.", language="fr-FR")
        return Response(content=str(resp), media_type="text/xml")

    hit = ConfirmationPendingRepository.get_by_pending_id(pending_id)
    if not hit or str(hit["record"].get("status") or "").strip().upper() not in PENDING_STATUSES:
        resp = VoiceResponse()
        resp.say("Cette demande de confirmation n'est plus valide. Au revoir.", language="fr-FR")
        return Response(content=str(resp), media_type="text/xml")

    otp = str(hit["record"].get("otp") or "").strip()
    digits_spaced = ". ".join(otp)

    resp = VoiceResponse()
    resp.say("Bonjour, voici votre code de confirmation ProxyCall.", language="fr-FR")
    resp.pause(length=1)
    resp.say(digits_spaced, language="fr-FR")
    resp.pause(length=1)
    resp.say("Je répète.", language="fr-FR")
    resp.pause(length=1)
    resp.say(digits_spaced, language="fr-FR")
    resp.pause(length=1)

    gather = Gather(
        num_digits=len(otp),
        action=f"/twilio/voice/otp/gather?pending_id={pending_id}&attempt=1",
        method="POST",
        timeout=15,
    )
    gather.say("Veuillez saisir votre code sur votre clavier téléphonique.", language="fr-FR")
    resp.append(gather)

    resp.say("Nous n'avons pas reçu de réponse. Au revoir.", language="fr-FR")
    return Response(content=str(resp), media_type="text/xml")


@router.post("/twilio/voice/otp/gather")
async def twilio_voice_otp_gather(request: Request):
    """Callback DTMF : valide les chiffres saisis contre l'OTP stocké."""
    form = await request.form()
    pending_id = str(form.get("pending_id") or request.query_params.get("pending_id") or "").strip()
    digits = str(form.get("Digits") or "").strip()
    attempt = int(request.query_params.get("attempt") or "1")

    if not pending_id:
        resp = VoiceResponse()
        resp.say("Erreur technique. Au revoir.", language="fr-FR")
        return Response(content=str(resp), media_type="text/xml")

    hit = ConfirmationPendingRepository.get_by_pending_id(pending_id)
    if not hit or str(hit["record"].get("status") or "").strip().upper() not in PENDING_STATUSES:
        resp = VoiceResponse()
        resp.say("Cette demande n'est plus valide. Au revoir.", language="fr-FR")
        return Response(content=str(resp), media_type="text/xml")

    rec = hit["record"]
    expected = str(rec.get("otp") or "").strip()
    proxy_e164 = str(rec.get("proxy_number") or "").strip()
    if not proxy_e164.startswith("+"):
        proxy_e164 = "+" + re.sub(r"\D+", "", proxy_e164)
    sender_e164 = str(rec.get("client_real_phone") or "").strip()
    if not sender_e164.startswith("+"):
        sender_e164 = "+" + re.sub(r"\D+", "", sender_e164)

    if digits == expected:
        logger.info("OTP vocal validé", extra={"pending_id": pending_id})
        try:
            ConfirmationService.promote_pending(
                pending_row=hit["row"],
                record=rec,
                proxy_e164=proxy_e164,
                sender_e164=sender_e164,
            )
        except Exception as exc:
            logger.exception("Erreur promotion après OTP vocal", exc_info=exc)
            resp = VoiceResponse()
            resp.say("Erreur technique lors de la confirmation. Veuillez réessayer plus tard. Au revoir.", language="fr-FR")
            return Response(content=str(resp), media_type="text/xml")

        resp = VoiceResponse()
        resp.say("Code confirmé. Merci et au revoir.", language="fr-FR")
        return Response(content=str(resp), media_type="text/xml")

    # Mismatch
    logger.warning("OTP vocal mismatch", extra={"pending_id": pending_id, "attempt": attempt})

    resp = VoiceResponse()
    if attempt >= MAX_VOICE_OTP_ATTEMPTS:
        resp.say("Trop de tentatives incorrectes. Au revoir.", language="fr-FR")
        return Response(content=str(resp), media_type="text/xml")

    resp.say("Code incorrect.", language="fr-FR")
    gather = Gather(
        num_digits=len(expected),
        action=f"/twilio/voice/otp/gather?pending_id={pending_id}&attempt={attempt + 1}",
        method="POST",
        timeout=15,
    )
    gather.say("Veuillez saisir à nouveau votre code.", language="fr-FR")
    resp.append(gather)
    resp.say("Au revoir.", language="fr-FR")
    return Response(content=str(resp), media_type="text/xml")
