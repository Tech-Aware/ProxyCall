from models.order import Order
from repositories.orders_repository import OrdersRepository
from services.clients_service import ClientsService

class OrdersService:

    @staticmethod
    def create_order(order_id: str, client_id: str, client_name: str, phone_real: str):
        # On récupère OU on crée le client
        client = ClientsService.get_or_create_client(
            client_id=client_id,
            client_name=client_name,
            phone_real=phone_real
        )

        order = Order(order_id=order_id, client_id=client_id)
        OrdersRepository.save(order)

        # On renvoie le proxy pour transmission au fournisseur
        return {
            "order_id": order_id,
            "client_id": client_id,
            "phone_proxy": client.phone_proxy
        }
