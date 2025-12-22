import logging
from app.logging_config import mask_phone
from typing import Optional
from models.client import Client
from integrations.sheets_client import SheetsClient


logger = logging.getLogger(__name__)


def _column_letter(index: int) -> str:
    """Convertit un index de colonne (1-indexé) en lettre Excel (A, B, ...)."""
    if index < 1:
        raise ValueError("L'index de colonne doit être supérieur ou égal à 1")

    letters = []
    while index:
        index, remainder = divmod(index - 1, 26)
        letters.append(chr(65 + remainder))
    return "".join(reversed(letters))


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
        Ajoute une nouvelle ligne en respectant l'ordre des colonnes de la feuille (headers).
        """
        sheet = SheetsClient.get_clients_sheet()
        headers = [str(h or "").strip() for h in sheet.row_values(1)]
        if not headers:
            raise RuntimeError("Feuille Clients: ligne 1 (headers) vide")

        # Ligne vide alignée sur la largeur des headers
        row = [""] * len(headers)

        def setv(col: str, val: str):
            if col not in headers:
                logger.warning("Colonne absente dans Clients, valeur ignorée", extra={"col": col})
                return
            row[headers.index(col)] = val

        setv("client_id", str(client.client_id))
        setv("client_name", str(client.client_name or ""))
        setv("client_mail", str(client.client_mail or ""))
        setv("client_real_phone", str(client.client_real_phone or ""))
        setv("client_proxy_number", str(client.client_proxy_number or ""))

        # Les autres colonnes restent vides à la création
        # client_iso_residency, client_country_code, client_last_caller

        sheet.append_row(row, value_input_option="RAW", table_range="A1")

        logger.info(
            "Client enregistré dans Sheets (aligné headers)",
            extra={
                "client_id": client.client_id,
                "proxy_number": mask_phone(str(client.client_proxy_number or "")),
            },
        )

    @staticmethod
    def update(client: Client) -> None:
        """Met à jour un client existant ou l'ajoute s'il est absent."""
        try:
            sheet = SheetsClient.get_clients_sheet()
            headers = sheet.row_values(1)
            records = sheet.get_all_records()
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception(
                "Impossible de lire la feuille Clients pour mise à jour", exc_info=exc
            )
            return

        target_row = None
        for row_idx, rec in enumerate(records, start=2):
            if str(rec.get("client_id")) == str(client.client_id):
                target_row = row_idx
                break

        if target_row is None:
            logger.warning(
                "Client introuvable pour mise à jour, ajout en fin de feuille.",
                extra={"client_id": client.client_id},
            )
            ClientsRepository.save(client)
            return

        existing_row = sheet.row_values(target_row)
        existing_map = {
            headers[i]: existing_row[i] if i < len(existing_row) else ""
            for i in range(len(headers))
        }

        updated_map = {
            "client_id": client.client_id,
            "client_name": client.client_name,
            "client_mail": client.client_mail,
            "client_real_phone": client.client_real_phone,
            "client_proxy_number": client.client_proxy_number,
            "client_last_caller": existing_map.get("client_last_caller", ""),
        }

        updates = []
        for header, value in updated_map.items():
            if header in {"client_iso_residency", "client_country_code"}:
                logger.info(
                    "Colonne protégée ignorée lors de la mise à jour", extra={"colonne": header}
                )
                continue
            try:
                col_idx = headers.index(header) + 1
            except ValueError:
                logger.warning(
                    "Colonne absente dans la feuille, mise à jour ignorée",
                    extra={"colonne": header, "client_id": client.client_id},
                )
                continue

            current_val = existing_map.get(header, "")
            if str(current_val) == str(value):
                continue

            updates.append({"range": f"{_column_letter(col_idx)}{target_row}", "values": [[value]]})

        if not updates:
            logger.info(
                "Aucune mise à jour nécessaire pour le client (colonnes protégées conservées)",
                extra={"client_id": client.client_id},
            )
            return

        try:
            sheet.batch_update(updates)
            logger.info(
                "Client mis à jour dans Sheets (colonnes F et G préservées)",
                extra={"client_id": client.client_id, "row": target_row},
            )
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception(
                "Impossible de mettre à jour le client dans Sheets", exc_info=exc
            )

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

