import logging

from fastapi import APIRouter, HTTPException, Body

from services.orders_service import OrdersService

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("")
def create_order(
    order_id: str = Body(...),
    client_id: str = Body(...),
    client_name: str = Body(...),
    client_mail: str = Body(...),
    client_real_phone: str = Body(...),
    client_iso_residency: str | None = Body(None),
):
    try:
        return OrdersService.create_order(
            order_id,
            client_id,
            client_name,
            client_mail,
            client_real_phone,
            client_iso_residency,
        )
    except Exception as exc:  # pragma: no cover - log + réponse HTTP claire
        logger.exception(
            "Erreur lors de la création de la commande (%s / %s)",
            order_id,
            client_id,
            exc_info=exc,
        )
        raise HTTPException(
            status_code=500, detail="Erreur interne lors de la création de la commande"
        ) from exc
