from models.client import Client
from repositories.clients_repository import ClientsRepository
from integrations.twilio_client import TwilioClient


class ClientAlreadyExistsError(Exception):
    """Levée quand on essaie de créer un client qui existe déjà."""
    pass


def extract_country_code(phone: str) -> str:
    """Retourne un indicatif pays normalisé type +33 à partir d'un numéro."""
    if not phone:
        return ""

    p = str(phone).strip().replace(" ", "")
    if not p.startswith("+"):
        p = "+" + p

    # Simpliste mais suffisant pour ton cas : +33, +49, etc.
    return p[:3]



class ClientsService:

    # ==============
    # Méthodes de base
    # ==============

    @staticmethod
    def get_client(client_id: str) -> Client | None:
        """
        Récupère un client par son ID.
        Utilisé par l'API GET /clients/{client_id}.
        """
        return ClientsRepository.get_by_id(client_id)

    @staticmethod
    def create_client(client_id: str, client_name: str, phone_real: str) -> Client:
        """
        Crée un nouveau client de manière stricte.
        - Si le client existe déjà -> lève ClientAlreadyExistsError.
        - Sinon -> génère un proxy Twilio et sauvegarde le client.
        Utilisé par l'API POST /clients.
        """
        existing = ClientsRepository.get_by_id(client_id)
        if existing:
            raise ClientAlreadyExistsError(f"Client {client_id} existe déjà.")

        cc = extract_country_code(phone_real)
        proxy = TwilioClient.buy_number_for_client(
            friendly_name=f"Client-{client_id}"
        )

        client = Client(
            client_id=client_id,
            client_name=client_name,
            phone_real=phone_real,
            phone_proxy=proxy,
            country_code=cc,
        )

        ClientsRepository.save(client)
        return client

    # ==============
    # Méthode utilisée par OrdersService
    # ==============

    @staticmethod
    def get_or_create_client(client_id: str, client_name: str, phone_real: str) -> Client:
        """
        Utilisée côté OrdersService :
        - Si le client existe -> le renvoie tel quel (on ne recrée pas de proxy).
        - Sinon -> crée le client + proxy via create_client().
        """
        client = ClientsRepository.get_by_id(client_id)
        if client:
            return client

        # On réutilise la logique de création stricte
        return ClientsService.create_client(
            client_id=client_id,
            client_name=client_name,
            phone_real=phone_real,
        )
