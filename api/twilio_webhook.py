# api/twilio_webhook.py

from fastapi import APIRouter, Request
from fastapi.responses import Response

from services.call_routing_service import CallRoutingService

router = APIRouter()


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
    print("=== WEBHOOK EXECUTED ===")

    form = await request.form()
    from_raw = form.get("From")
    to_raw = form.get("To")

    from_number = _normalize_e164_like(from_raw)
    to_number = _normalize_e164_like(to_raw)

    print(f"[DEBUG] twilio_voice_webhook: From='{from_number}', To='{to_number}'")

    twiml = CallRoutingService.handle_incoming_call(
        proxy_number=to_number,
        caller_number=from_number,
    )

    return Response(content=twiml, media_type="text/xml")
