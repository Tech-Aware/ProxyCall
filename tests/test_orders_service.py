import logging

from models.client import Client
from models.order import Order
from services import orders_service
from services.orders_service import OrdersService

logging.basicConfig(level=logging.INFO)


class DummyOrdersRepository:
    def __init__(self):
        self.saved: list[Order] = []

    def save(self, order: Order):
        logging.info("Order saved in dummy repo", extra={"order_id": order.order_id})
        self.saved.append(order)


def test_create_order_with_existing_client(monkeypatch):
    dummy_repo = DummyOrdersRepository()

    monkeypatch.setattr(orders_service, "OrdersRepository", dummy_repo)

    client = Client(
        client_id="123",
        client_name="Test",
        client_mail="test@example.com",
        client_real_phone="+33123456789",
        client_proxy_number="+3399990000",
    )

    def fake_get_or_create(*args, **kwargs):
        return client

    monkeypatch.setattr(orders_service, "ClientsService", type("CS", (), {"get_or_create_client": staticmethod(fake_get_or_create)}))

    result = OrdersService.create_order(
        order_id="order-1",
        client_id="123",
        client_name="Test",
        client_mail="test@example.com",
        client_real_phone="+33123456789",
    )

    assert result["client_proxy_number"] == "+3399990000"
    assert dummy_repo.saved and dummy_repo.saved[0].order_id == "order-1"
