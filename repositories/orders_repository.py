from models.order import Order

class OrdersRepository:

    @staticmethod
    def save(order: Order) -> None:
        raise NotImplementedError
