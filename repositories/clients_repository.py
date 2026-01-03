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
    def find_by_email_or_phone(client_mail: str | None, client_real_phone: str | None) -> Optional[Client]:
        """
        Recherche un client par email (case-insensitive) ou numéro de téléphone (normalisé sans '+').
        Retourne le premier match trouvé ou None si rien ne correspond.
        """
        try:
            sheet = SheetsClient.get_clients_sheet()
            records = sheet.get_all_records()
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("Impossible de lire la feuille Clients", exc_info=exc)
            return None

        email_cmp = str(client_mail or "").strip().lower()
        phone_raw = str(client_real_phone or "").strip().replace(" ", "")
        phone_cmp = phone_raw[1:] if phone_raw.startswith("+") else phone_raw

        logger.info(
            "Recherche client par email ou téléphone",
            extra={"email": email_cmp or None, "phone": mask_phone(phone_raw) if phone_raw else None},
        )

        for rec in records:
            rec_email = str(rec.get("client_mail") or "").strip().lower()
            rec_phone = str(rec.get("client_real_phone") or "").strip().replace(" ", "")
            rec_phone_cmp = rec_phone[1:] if rec_phone.startswith("+") else rec_phone

            if email_cmp and rec_email and rec_email == email_cmp:
                logger.info("Client trouvé par email", extra={"client_id": rec.get("client_id")})
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

            if phone_cmp and rec_phone_cmp and rec_phone_cmp == phone_cmp:
                logger.info("Client trouvé par téléphone", extra={"client_id": rec.get("client_id")})
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

        logger.info("Aucun client correspondant à l'email ou au téléphone fourni", extra={"email": email_cmp})
        return None

    @staticmethod
    def save(client: Client) -> None:
        """
        Ajoute une nouvelle ligne en respectant l'ordre des colonnes de la feuille (headers),
        SANS écrire dans F/G (sinon ça casse les ARRAYFORMULA).
        """
        sheet = SheetsClient.get_clients_sheet()
        headers = [str(h or "").strip() for h in sheet.row_values(1)]
        if not headers:
            raise RuntimeError("Feuille Clients: ligne 1 (headers) vide")

        # On écrit uniquement jusqu'à client_proxy_number (col E normalement)
        try:
            last_write_col = headers.index("client_proxy_number") + 1  # 1-indexé
        except ValueError:
            raise RuntimeError("Colonne 'client_proxy_number' introuvable dans la feuille Clients.")

        # Ligne alignée mais tronquée (A..E) -> F/G/H restent VRAIMENT vides
        row = [""] * last_write_col

        def setv(col: str, val: str):
            if col not in headers:
                logger.warning("Colonne absente dans Clients, valeur ignorée", extra={"col": col})
                return
            idx = headers.index(col)
            if idx >= last_write_col:
                # sécurité : on n'écrit pas au-delà de last_write_col
                return
            row[idx] = val

        setv("client_id", str(client.client_id))
        setv("client_name", str(client.client_name or ""))
        setv("client_mail", str(client.client_mail or ""))
        setv("client_real_phone", str(client.client_real_phone or ""))
        setv("client_proxy_number", str(client.client_proxy_number or ""))

        # Append à partir de A3, sans toucher ligne 2
        sheet.append_row(row, value_input_option="RAW", table_range="A3")

        # Récupère la ligne réellement ajoutée (après append)
        # get_all_values inclut la ligne 1 (headers) + ligne 2 (array formulas)
        new_row_index = len(sheet.get_all_values())

        # Filet de sécurité : on clear F/G sur la nouvelle ligne
        # (clear => cellule vraiment vide, arrayformula peut s'y déverser)
        try:
            sheet.batch_clear([f"F{new_row_index}:G{new_row_index}"])
        except Exception as exc:  # pragma: no cover
            logger.warning("Impossible de clear F/G après append", exc_info=exc)

        logger.info(
            "Client enregistré dans Sheets (aligné headers, F/G laissées vides)",
            extra={
                "client_id": client.client_id,
                "proxy_number": mask_phone(str(client.client_proxy_number or "")),
                "row": new_row_index,
            },
        )

    @staticmethod
    def update(client: Client) -> None:
        """Met à jour un client existant ou l'ajoute s'il est absent.
        Important: on ne doit JAMAIS écrire dans F/G, et on clear F/G après update.
        """
        DATA_START_ROW = 3  # Ligne 2 réservée aux formules (ARRAYFORMULA)

        try:
            sheet = SheetsClient.get_clients_sheet()
            headers = [str(h or "").strip() for h in sheet.row_values(1)]
            records = sheet.get_all_records()
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception(
                "Impossible de lire la feuille Clients pour mise à jour", exc_info=exc
            )
            return

        try:
            client_id_col_idx = headers.index("client_id") + 1
        except ValueError:
            logger.error(
                "Colonne 'client_id' introuvable dans la feuille Clients : mise à jour impossible",
                extra={"client_id": client.client_id},
            )
            return

        # get_all_records() peut ignorer les lignes vides/protégées (ex: ligne 2),
        # on scanne donc la colonne ID directement pour récupérer l'index de ligne réel.
        target_row = None
        try:
            client_id_column_values = sheet.col_values(client_id_col_idx)
        except AttributeError:
            client_id_column_values = None
            logger.warning(
                "Méthode col_values indisponible sur la feuille, bascule sur get_all_records",
                extra={"client_id": client.client_id},
            )
        except Exception as exc:  # pragma: no cover
            logger.exception(
                "Impossible de lire la colonne client_id dans Sheets", exc_info=exc
            )
            return

        if client_id_column_values is not None:
            for row_idx, value in enumerate(client_id_column_values, start=1):
                if row_idx < DATA_START_ROW:
                    continue
                if str(value).strip() == str(client.client_id):
                    target_row = row_idx
                    break
        else:
            for row_idx, rec in enumerate(records, start=2):
                if row_idx < DATA_START_ROW:
                    continue
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

        # À partir de la première colonne protégée (iso/country), on n'écrit plus rien
        protected_headers = [h for h in ("client_iso_residency", "client_country_code") if h in headers]
        first_protected_col = min(
            [headers.index(h) + 1 for h in protected_headers],
            default=len(headers) + 1,
        )

        updates = []
        for header, value in updated_map.items():
            try:
                col_idx = headers.index(header) + 1
            except ValueError:
                logger.warning(
                    "Colonne absente dans la feuille, mise à jour ignorée",
                    extra={"colonne": header, "client_id": client.client_id},
                )
                continue

            if col_idx >= first_protected_col:
                logger.info(
                    "Mise à jour ignorée car colonne protégée",
                    extra={"colonne": header, "client_id": client.client_id, "col_idx": col_idx},
                )
                continue

            current_val = existing_map.get(header, "")
            if str(current_val) == str(value):
                continue

            updates.append({"range": f"{_column_letter(col_idx)}{target_row}", "values": [[value]]})

        if updates:
            try:
                sheet.batch_update(updates)
                logger.info(
                    "Client mis à jour dans Sheets (F/G non modifiées)",
                    extra={"client_id": client.client_id, "row": target_row},
                )
            except Exception as exc:  # pragma: no cover
                protected = "protect" in str(exc).lower()
                logger.exception(
                    "Impossible de mettre à jour le client dans Sheets",
                    exc_info=exc,
                    extra={"client_id": client.client_id, "row": target_row, "updates": updates},
                )
                message = (
                    "Mise à jour du client refusée : cellules protégées dans la feuille Clients."
                    if protected
                    else "Mise à jour du client refusée : erreur lors de l'écriture dans la feuille Clients."
                )
                raise RuntimeError(message) from exc

        # Filet de sécurité: clear F/G sur la ligne (au cas où elles auraient été "occupées" par un vieux run)
        try:
            sheet.batch_clear([f"F{target_row}:G{target_row}"])
        except Exception as exc:  # pragma: no cover
            logger.warning("Impossible de clear F/G après update", exc_info=exc)

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
            if row_idx < 3:
                continue
            rec_proxy_norm = str(rec.get("client_proxy_number") or "").strip().replace(" ", "").replace("+", "")
            if rec_proxy_norm and rec_proxy_norm == target_norm:
                sheet.update_cell(row_idx, last_caller_col, str(caller_number))
                logger.info(
                    "client_last_caller mis à jour",
                    extra={"proxy": proxy_number, "last_caller": caller_number, "row": row_idx},
                )
                return
