import logging
from datetime import datetime, timezone

from app.logging_config import mask_phone
from models.client import Client
from repositories.clients_repository import ClientsRepository
from repositories.pools_repository import PoolsRepository

logger = logging.getLogger(__name__)


def _e164(num: str) -> str:
    s = str(num or "").strip().replace(" ", "")
    if not s.startswith("+"):
        s = "+" + s
    return s


class ConfirmationService:
    @staticmethod
    def upsert_client_and_attach_proxy(*, client_name: str, client_mail: str, client_real_phone: str,
                                       proxy_number: str) -> Client:
        """Crée ou met à jour un client (email puis téléphone) puis attache proxy."""
        email_cmp = str(client_mail or "").strip().lower()
        phone_cmp = _e164(client_real_phone).replace("+", "")
        proxy_e164 = _e164(proxy_number)

        # Recherche brute dans la feuille Clients (email ou phone)
        from integrations.sheets_client import SheetsClient
        ws = SheetsClient.get_clients_sheet()
        records = ws.get_all_records()

        found_id = None
        for rec in records:
            rec_email = str(rec.get("client_mail") or "").strip().lower()
            rec_phone = str(rec.get("client_real_phone") or "").strip().replace(" ", "")
            rec_phone_cmp = rec_phone.replace("+", "") if rec_phone.startswith("+") else rec_phone

            if rec_email and rec_email == email_cmp:
                found_id = str(rec.get("client_id"))
                break
            if rec_phone_cmp and rec_phone_cmp == phone_cmp:
                found_id = str(rec.get("client_id"))
                break

        if found_id:
            client = ClientsRepository.get_by_id(found_id)
            if not client:
                client = Client(
                    client_id=found_id,
                    client_name=client_name or "",
                    client_mail=email_cmp,
                    client_real_phone=_e164(client_real_phone),
                )

            client.client_name = client_name or client.client_name
            client.client_mail = email_cmp
            client.client_real_phone = _e164(client_real_phone)
            client.client_proxy_number = proxy_e164
            try:
                ClientsRepository.update(client)
            except Exception as exc:
                logger.error(
                    "Echec de mise à jour du client dans Sheets lors de l'attachement du proxy",
                    exc_info=exc,
                    extra={"client_id": client.client_id, "proxy": mask_phone(proxy_e164)},
                )
                raise

            logger.info(
                "Client upsert (update) + proxy attaché",
                extra={"client_id": client.client_id, "proxy": mask_phone(proxy_e164)},
            )
            return client

        new_id = ClientsRepository.get_max_client_id() + 1
        client = Client(
            client_id=str(new_id),
            client_name=client_name or "",
            client_mail=email_cmp,
            client_real_phone=_e164(client_real_phone),
            client_proxy_number=proxy_e164,
        )
        ClientsRepository.save(client)

        logger.info(
            "Client upsert (create) + proxy attaché",
            extra={"client_id": client.client_id, "proxy": mask_phone(proxy_e164)},
        )
        return client

    @staticmethod
    def finalize_pool_assignment(*, proxy_number: str, pending_id: str, client_id: str,
                                 attribution_to_client_name: str) -> None:
        """
        Finalise l'attribution dans TwilioPools :
        - status=assigned
        - date_attribution=now
        - attribution_to_client_name=...
        - reserved_by_client_id=client_id
        - reserved_token/reserved_at conservés
        """
        proxy_e164 = _e164(proxy_number)
        hit = PoolsRepository.find_row_by_phone_number(proxy_e164)
        if not hit:
            raise RuntimeError(f"TwilioPools: proxy introuvable: {proxy_e164}")

        row_index = int(hit["row_index"])
        rec = hit["record"]

        reserved_token = str(rec.get("reserved_token") or "").strip()
        reserved_at = str(rec.get("reserved_at") or "").strip()

        if reserved_token and str(reserved_token) != str(pending_id):
            raise RuntimeError(
                f"TwilioPools: reserved_token mismatch row={row_index} "
                f"(expected pending_id={pending_id}, got={reserved_token})"
            )

        if not reserved_token:
            logger.warning(
                "TwilioPools: reserved_token vide lors de la finalisation (réservation pending non implémentée ?)",
                extra={"row": row_index, "proxy": mask_phone(proxy_e164), "pending_id": pending_id},
            )
            reserved_token = str(pending_id)

        if not reserved_at:
            reserved_at = datetime.now(timezone.utc).isoformat()

        PoolsRepository.finalize_assignment_keep_friendly(
            row_index=row_index,
            reserved_token=reserved_token,
            reserved_at=reserved_at,
            reserved_by_client_id=int(client_id),
            date_attribution=datetime.now(timezone.utc).isoformat(),
            attribution_to_client_name=attribution_to_client_name or "",
        )

        logger.info(
            "TwilioPools finalisé assigned",
            extra={"row": row_index, "proxy": mask_phone(proxy_e164), "client_id": client_id},
        )
