import logging
from app.config import settings
from models.client import Client
from repositories.clients_repository import ClientsRepository
from integrations.twilio_client import TwilioClient


logger = logging.getLogger(__name__)


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


def _to_twilio_country_code(client_country_code: str) -> str:
    """Convertit un indicatif client en code pays ISO compatible Twilio.

    - Si un code ISO (2 lettres) est fourni, on le renvoie en majuscules.
    - Si un indicatif numérique est fourni (ex: +33, 33), on mappe vers l'ISO.
    - Si l'indicatif est inconnu, on lève une ValueError pour éviter un mismatch.
    """

    if not client_country_code:
        raise ValueError("Aucun indicatif pays client fourni.")

    cc = client_country_code.strip()
    if len(cc) == 2 and cc.isalpha():
        return cc.upper()

    dial_code = cc.lstrip("+")
    mapping = {
        "1": "US",  # USA/Canada - on privilégie US par défaut
        "33": "FR",
        "34": "ES",
        "39": "IT",
        "44": "GB",
        "49": "DE",
    }

    if dial_code in mapping:
        return mapping[dial_code]

    raise ValueError(f"Indicatif pays client inconnu ou non supporté: {client_country_code}")


def _resolve_twilio_country_code(
    client_iso_residency: str | None, client_real_phone: str
) -> str:
    """Détermine le code pays ISO-2 pour Twilio.

    Priorité : client_iso_residency (si valide sur 2 lettres), puis mapping de
    l'indicatif téléphonique. Lève une ValueError avec un message explicite si
    aucune source n'est exploitable.
    """

    iso_errors: list[str] = []
    if client_iso_residency:
        iso_code = client_iso_residency.strip()
        if len(iso_code) == 2 and iso_code.isalpha():
            return iso_code.upper()
        iso_errors.append(
            f"client_iso_residency invalide (attendu code ISO-2): {client_iso_residency}"
        )

    cc = extract_country_code(client_real_phone)
    try:
        return _to_twilio_country_code(cc)
    except Exception as exc:
        phone_error = str(exc)

    error_parts = iso_errors + [phone_error]
    raise ValueError(
        "Impossible de déterminer le pays Twilio : " + "; ".join(error_parts)
    )



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
        try:
            return ClientsRepository.get_by_id(client_id)
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("Erreur lors de la récupération du client", exc_info=exc)
            return None

    @staticmethod
    def get_client_by_proxy(proxy: str) -> Client | None:
        """Recherche un client par numéro proxy (Sheets)."""
        try:
            return ClientsRepository.get_by_proxy_number(proxy)
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("Erreur lors de la recherche client par proxy", exc_info=exc)
            return None

    @staticmethod
    def create_client(
        client_id: str,
        client_name: str,
        client_mail: str,
        client_real_phone: str,
        client_iso_residency: str | None = None,
    ) -> Client:
        """
        Crée un nouveau client de manière stricte.
        - Si le client existe déjà -> lève ClientAlreadyExistsError.
        - Sinon -> génère un proxy Twilio et sauvegarde le client.
        Utilisé par l'API POST /clients.
        """
        existing = ClientsRepository.get_by_id(client_id)
        if existing:
            raise ClientAlreadyExistsError(f"Client {client_id} existe déjà.")

        cc = extract_country_code(client_real_phone)
        proxy_country = client_iso_residency or settings.TWILIO_PHONE_COUNTRY

        try:
            twilio_country = _resolve_twilio_country_code(
                client_iso_residency, client_real_phone
            )
            cc = extract_country_code(client_real_phone)
            proxy = TwilioClient.buy_number_for_client(
                friendly_name=f"Client-{client_id}",
                country=twilio_country,
                attribution_to_client_name=client_name,
            )
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("Erreur lors de l'achat du numéro proxy", exc_info=exc)
            raise

        client = Client(
            client_id=client_id,
            client_name=client_name,
            client_mail=client_mail,
            client_real_phone=client_real_phone,
            client_proxy_number=proxy,
            client_iso_residency=client_iso_residency,
            client_country_code=cc,
        )

        ClientsRepository.save(client)
        return client

    # ==============
    # Méthode utilisée par OrdersService
    # ==============

    @staticmethod
    def get_or_create_client(
        client_id: str,
        client_name: str,
        client_mail: str,
        client_real_phone: str,
        client_iso_residency: str | None = None,
    ) -> Client:
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
            client_mail=client_mail,
            client_real_phone=client_real_phone,
            client_iso_residency=client_iso_residency,
        )
