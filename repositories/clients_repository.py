import logging
from typing import Optional
from models.client import Client
from integrations.sheets_client import SheetsClient


logger = logging.getLogger(__name__)


class ClientsRepository:
    """
    Implémentation Google Sheets.
    Feuille 'Clients' avec colonnes :
    client_id | client_name | client_mail | client_real_phone | client_proxy_number | client_iso_residency | client_country_code
    """

    @staticmethod
    def get_by_id(client_id: str) -> Optional[Client]:
        try:
            sheet = SheetsClient.get_clients_sheet()
            records = sheet.get_all_records()  # liste de dicts (ignore la 1re ligne)
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("Impossible de lire la feuille Clients", exc_info=exc)
            return None

        for rec in records:
            if str(rec.get("client_id")) == str(client_id):
                logger.info("Client trouvé dans Sheets", extra={"client_id": client_id})
                return Client(
                    client_id=rec.get("client_id"),
                    client_name=rec.get("client_name"),
                    client_mail=rec.get("client_mail"),
                    client_real_phone=rec.get("client_real_phone"),
                    client_proxy_number=rec.get("client_proxy_number"),
                    client_iso_residency=rec.get("client_iso_residency"),
                    client_country_code=rec.get("client_country_code"),
                    client_last_caller=rec.get("client_last_caller"),
                )
        logger.info("Client introuvable dans Sheets", extra={"client_id": client_id})
        return None

    @staticmethod
    def get_by_proxy_number(proxy_number: str) -> Optional[Client]:
        try:
            sheet = SheetsClient.get_clients_sheet()
            records = sheet.get_all_records()
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("Impossible de lire la feuille Clients", exc_info=exc)
            return None

        target_raw = str(proxy_number or "")
        target_norm = target_raw.strip().replace(" ", "").replace("+", "")
        logger.info("Recherche du client par proxy", extra={"proxy": target_norm})

        for rec in records:
            rec_proxy_raw = rec.get("client_proxy_number")
            rec_proxy_norm = str(rec_proxy_raw or "").strip().replace(" ", "").replace("+", "")

            if rec_proxy_norm and rec_proxy_norm == target_norm:
                logger.info("Client associé au proxy trouvé", extra={"proxy": target_norm})
                return Client(
                    client_id=rec.get("client_id"),
                    client_name=rec.get("client_name"),
                    client_mail=rec.get("client_mail"),
                    client_real_phone=rec.get("client_real_phone"),
                    client_proxy_number=rec.get("client_proxy_number"),
                    client_iso_residency=rec.get("client_iso_residency"),
                    client_country_code=rec.get("client_country_code"),
                    client_last_caller=rec.get("client_last_caller"),
                )

        logger.info("Aucun client trouvé pour ce proxy", extra={"proxy": target_norm})
        return None

    @staticmethod
    def save(client: Client) -> None:
        """
        Version simple : on ajoute une nouvelle ligne.
        Hypothèse pour l’instant : on n’appelle pas save plusieurs fois
        pour le même client_id (sinon tu auras des doublons).
        """
        try:
            sheet = SheetsClient.get_clients_sheet()
            row = [
                client.client_id,
                client.client_name,
                client.client_mail,
                client.client_real_phone,
                client.client_proxy_number,
                client.client_iso_residency,
                client.client_country_code,
            ]
            sheet.append_row(row)
            logger.info(
                "Client enregistré dans Sheets",
                extra={"client_id": client.client_id, "proxy": client.client_proxy_number},
            )
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("Impossible d'enregistrer le client dans Sheets", exc_info=exc)

    @staticmethod
    def get_max_client_id() -> int:
        """Retourne le plus grand client_id présent dans la feuille Clients."""
        try:
            sheet = SheetsClient.get_clients_sheet()
            records = sheet.get_all_records()
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("Impossible de lire la feuille Clients", exc_info=exc)
            raise

        max_id = 0
        for rec in records:
            try:
                cid = int(str(rec.get("client_id", 0)).strip())
            except Exception:
                continue
            max_id = max(max_id, cid)

        return max_id

    @staticmethod
    def update_last_caller_by_proxy(proxy_number: str, caller_number: str) -> None:
        sheet = SheetsClient.get_clients_sheet()

        headers = sheet.row_values(1)
        try:
            last_caller_col = headers.index("client_last_caller") + 1
        except ValueError:
            raise RuntimeError("Colonne 'client_last_caller' introuvable dans la feuille Clients.")

        target_norm = str(proxy_number or "").strip().replace(" ", "").replace("+", "")
        records = sheet.get_all_records()

        for row_idx, rec in enumerate(records, start=2):  # ligne 1 = header
            rec_proxy_norm = str(rec.get("client_proxy_number") or "").strip().replace(" ", "").replace("+", "")
            if rec_proxy_norm and rec_proxy_norm == target_norm:
                sheet.update_cell(row_idx, last_caller_col, str(caller_number))
                logger.info(
                    "client_last_caller mis à jour",
                    extra={"proxy": proxy_number, "last_caller": caller_number, "row": row_idx},
                )
                return

