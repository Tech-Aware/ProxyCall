import logging
from models.order import Order
from repositories.orders_repository import OrdersRepository
from services.clients_service import ClientsService


logger = logging.getLogger(__name__)

class OrdersService:

    @staticmethod
    def create_order(
        order_id: str,
        client_id: str,
        client_name: str,
        client_mail: str,
        client_real_phone: str,
        client_iso_residency: str | None = None,
    ):
        # On récupère OU on crée le client
        client = ClientsService.get_or_create_client(
            client_id=client_id,
            client_name=client_name,
            client_mail=client_mail,
            client_real_phone=client_real_phone,
            client_iso_residency=client_iso_residency,
        )

        order = Order(order_id=order_id, client_id=client_id)
        OrdersRepository.save(order)

        logger.info(
            "Commande créée et client synchronisé",
            extra={"order_id": order_id, "client_id": client_id},
        )

        # On renvoie le proxy pour transmission au fournisseur
        return {
            "order_id": order_id,
            "client_id": client_id,
            "client_proxy_number": client.client_proxy_number,
        }
