from fastapi import APIRouter
from services.orders_service import OrdersService

router = APIRouter()

@router.post("")
def create_order(order_id: str, client_id: str, client_name: str, phone_real: str):
    return OrdersService.create_order(order_id, client_id, client_name, phone_real)
