import logging
from fastapi import APIRouter
from services.orders_service import OrdersService

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("")
def create_order(
    order_id: str,
    client_id: str,
    client_name: str,
    client_mail: str,
    client_real_phone: str,
    client_iso_residency: str | None = None,
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
    except Exception as exc:
        logger.exception("Erreur lors de la création de la commande", exc_info=exc)
        return {"detail": "Erreur interne"}
