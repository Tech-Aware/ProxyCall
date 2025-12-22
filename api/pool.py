import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel

from integrations.twilio_client import TwilioClient

router = APIRouter()
logger = logging.getLogger(__name__)


class SyncPoolPayload(BaseModel):
    apply: bool = True


@router.get("/available")
def list_available(country_iso: str, number_type: str | None = None):
    try:
        rows = TwilioClient.list_available(country_iso, number_type=number_type)
        return {"country_iso": country_iso.upper(), "number_type": number_type or "all", "available": rows}
    except Exception as exc:  # pragma: no cover - dépendances externes
        logger.exception("Erreur lors de la lecture du pool", exc_info=exc)
        raise HTTPException(status_code=500, detail="Erreur interne pool") from exc


@router.post("/provision")
def provision(
    country_iso: str = Body(...),
    batch_size: int = Body(1),
    number_type: str = Body("mobile"),
    require_sms_capability: bool = Body(False),
    require_voice_capability: bool = Body(False),
    candidates_limit: int = Body(100),
):
    try:
        purchased = TwilioClient.fill_pool(
            country_iso,
            batch_size,
            number_type=number_type,
            require_sms_capability=require_sms_capability,
            require_voice_capability=require_voice_capability,
            candidates_limit=candidates_limit,
        )
        return {
            "country_iso": country_iso.upper(),
            "purchased_now": purchased,
            "number_type": number_type,
            "require_sms_capability": require_sms_capability,
            "require_voice_capability": require_voice_capability,
            "candidates_limit": candidates_limit,
        }
    except Exception as exc:  # pragma: no cover - dépendances externes
        logger.exception("Erreur lors de l'approvisionnement du pool", exc_info=exc)
        raise HTTPException(status_code=500, detail="Erreur approvisionnement pool") from exc


@router.post("/assign")
def assign(
    client_id: int = Body(...),
    country_iso: str = Body(...),
    client_name: str = Body(...),
    number_type: str = Body("mobile"),
    friendly_name: str | None = Body(None),
):
    try:
        proxy = TwilioClient.assign_number_from_pool(
            client_id=client_id,
            country=country_iso,
            attribution_to_client_name=client_name,
            number_type=number_type,
            friendly_name=friendly_name,
        )
        return {"client_id": client_id, "proxy": proxy}
    except (ValueError, RuntimeError) as exc:
        logger.error(
            "Attribution du pool refusée (%s)",
            exc,
            extra={
                "client_id": client_id,
                "country_iso": country_iso,
                "number_type": number_type,
            },
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - dépendances externes
        logger.exception("Erreur lors de l'attribution d'un numéro", exc_info=exc)
        raise HTTPException(status_code=500, detail="Erreur attribution pool") from exc


@router.post("/sync")
def sync_pool(payload: SyncPoolPayload | bool | dict[str, Any] = Body(True)):
    apply_bool = True

    try:
        if isinstance(payload, SyncPoolPayload):
            apply_bool = bool(payload.apply)
        elif isinstance(payload, dict):
            apply_bool = bool(payload.get("apply", True))
        else:
            apply_bool = bool(payload)

        logger.info("Synchronisation du pool Twilio (apply=%s)", apply_bool)
        result = TwilioClient.sync_twilio_numbers_with_sheet(apply=apply_bool)
        return result
    except Exception as exc:  # pragma: no cover - dépendances externes
        logger.exception(
            "Erreur de synchronisation Twilio/Sheets (apply=%s)", apply_bool, exc_info=exc
        )
        raise HTTPException(status_code=500, detail="Erreur sync pool") from exc


@router.post("/purge-sans-sms")
def purge_sans_sms():
    try:
        result = TwilioClient.purge_pool_without_sms_capability()
        return result
    except Exception as exc:  # pragma: no cover - dépendances externes
        logger.exception(
            "Erreur lors de la purge des numéros sans SMS", exc_info=exc
        )
        raise HTTPException(status_code=500, detail="Erreur purge pool") from exc


@router.post("/fix-webhooks")
def fix_webhooks(
    dry_run: bool = Body(True),
    only_country: str | None = Body(None),
    only_status: str | None = Body(None),
    fix_sms: bool = Body(True),
):
    try:
        result = TwilioClient.fix_pool_voice_webhooks(
            dry_run=dry_run,
            only_country=only_country,
            only_status=only_status,
            fix_sms=fix_sms,
        )
        return result
    except Exception as exc:  # pragma: no cover - dépendances externes
        logger.exception("Erreur lors de la correction des webhooks Twilio", exc_info=exc)
        raise HTTPException(status_code=500, detail="Erreur correction webhooks") from exc
