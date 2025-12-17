# api/twilio_webhook.py
import logging

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.logging_config import mask_phone
from services.call_routing_service import CallRoutingService
from services.message_routing_service import MessageRoutingService

router = APIRouter()
logger = logging.getLogger(__name__)


def _normalize_e164_like(num: str | None) -> str:
    """Nettoie le numéro : strip, retire espaces, ajoute + si besoin."""
    if not num:
        return ""
    p = str(num).strip().replace(" ", "")
    if not p.startswith("+"):
        p = "+" + p
    return p


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
    from_raw = form.get("From")
    to_raw = form.get("To")
    body = form.get("Body") or ""

    from_number = _normalize_e164_like(from_raw)
    to_number = _normalize_e164_like(to_raw)

    logger.info(
        "Webhook SMS Twilio reçu (from=%s -> to=%s)",
        mask_phone(from_number),
        mask_phone(to_number),
    )

    twiml = MessageRoutingService.handle_incoming_sms(
        proxy_number=to_number, sender_number=from_number, body=body
    )
    return Response(content=twiml, media_type="text/xml")
