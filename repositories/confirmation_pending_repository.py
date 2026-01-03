import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional
import re
OTP_RE = re.compile(r"\b(\d{4,8})\b")  # 4 à 8 chiffres

from integrations.sheets_client import SheetsClient




logger = logging.getLogger(__name__)

def _norm_cmp(num: str | None) -> str:
    s = str(num or "").strip().replace(" ", "")
    if s.startswith("+"):
        s = s[1:]
    return s



class ConfirmationPendingRepository:
    REQUIRED = {"pending_id", "client_name", "client_mail", "client_real_phone", "proxy_number", "otp", "status", "created_at", "verified_at"}

    @staticmethod
    def _col(headers: list[str], name: str) -> int:
        try:
            return headers.index(name) + 1
        except ValueError as exc:
            raise RuntimeError(f"Colonne '{name}' manquante dans CONFIRMATION_PENDING") from exc

    @staticmethod
    def _headers(sheet) -> list[str]:
        return [str(x or "").strip() for x in sheet.row_values(1)]

    @staticmethod
    def get_by_pending_id(pending_id: str) -> Optional[Dict[str, Any]]:
        pid = str(pending_id or "").strip()
        if not pid:
            return None
        sheet = SheetsClient.get_confirmation_pending_sheet()
        records = sheet.get_all_records()
        for row_idx, rec in enumerate(records, start=2):
            if str(rec.get("pending_id") or "").strip() == pid:
                return {"row": row_idx, "record": rec, "headers": ConfirmationPendingRepository._headers(sheet)}
        return None

    @staticmethod
    def set_proxy_and_otp(
        *,
        pending_id: str,
        proxy_number: str,
        otp: str,
        client_name: str | None = None,
        client_mail: str | None = None,
        client_real_phone: str | None = None,
    ) -> None:
        sheet = SheetsClient.get_confirmation_pending_sheet()
        hit = ConfirmationPendingRepository.get_by_pending_id(pending_id)
        if not hit:
            raise RuntimeError(f"pending_id introuvable dans CONFIRMATION_PENDING: {pending_id}")

        row = hit["row"]
        headers = hit["headers"]
        now = datetime.now(timezone.utc).isoformat()

        def _update_cell(column: str, value: str, *, required: bool = False) -> None:
            try:
                col_idx = ConfirmationPendingRepository._col(headers, column)
            except RuntimeError as exc:
                if required:
                    raise
                logger.warning(
                    "Colonne absente dans CONFIRMATION_PENDING, mise à jour ignorée",
                    exc_info=exc,
                    extra={"pending_id": pending_id, "column": column},
                )
                return

            sheet.update_cell(row, col_idx, value)
            logger.info(
                "CONFIRMATION_PENDING cellule mise à jour",
                extra={"pending_id": pending_id, "column": column, "row": row},
            )

        _update_cell("proxy_number", str(proxy_number), required=True)
        _update_cell("otp", str(otp), required=True)
        _update_cell("status", "PENDING", required=True)

        if client_name is not None:
            _update_cell("client_name", str(client_name))
        if client_mail is not None:
            _update_cell("client_mail", str(client_mail))
        if client_real_phone is not None:
            _update_cell("client_real_phone", str(client_real_phone))

        # created_at rempli seulement si vide
        created_col = ConfirmationPendingRepository._col(headers, "created_at")
        existing_created = sheet.get_range(row, created_col) if hasattr(sheet, "get_range") else None  # compat
        # fallback simple: on lit la cellule created_at
        try:
            existing_created = sheet.cell(row, created_col).value
        except Exception:
            existing_created = ""

        if not str(existing_created or "").strip():
            sheet.update_cell(row, created_col, now)
            logger.info(
                "CONFIRMATION_PENDING horodatage créé",
                extra={"pending_id": pending_id, "row": row, "created_at": now},
            )

        logger.info(
            "CONFIRMATION_PENDING set proxy+otp avec données client en clair",
            extra={
                "pending_id": pending_id,
                "row": row,
                "client_mail_present": client_mail is not None,
                "client_phone_present": client_real_phone is not None,
                "client_name_present": client_name is not None,
            },
        )

    @staticmethod
    def generate_otp(length: int = 6) -> str:
        """
        Génère un OTP numérique de `length` chiffres, dont le premier chiffre n'est jamais 0.
        Exemple length=6 -> 100000..999999
        """
        if length < 2:
            raise ValueError("OTP length must be >= 2")

        first = secrets.randbelow(9) + 1  # 1..9
        rest = secrets.randbelow(10 ** (length - 1))  # 0 .. 10^(n-1)-1
        return f"{first}{rest:0{length - 1}d}"

    @staticmethod
    def find_pending(proxy_number: str, client_phone: str):
        # Alias rétro-compatible vers la méthode existante
        return ConfirmationPendingRepository.find_pending_by_proxy_and_phone(
            proxy_number=proxy_number,
            client_phone=client_phone,
        )

    @staticmethod
    def find_pending_by_proxy_and_phone(*, proxy_number: str, client_phone: str) -> Optional[Dict[str, Any]]:
        """
        Retourne la ligne pending en status=PENDING qui match:
        - proxy_number (To) == proxy_number stocké
        - client_real_phone (From) == client_phone stocké
        """
        sheet = SheetsClient.get_confirmation_pending_sheet()
        headers = ConfirmationPendingRepository._headers(sheet)
        records = sheet.get_all_records()

        proxy_cmp = _norm_cmp(proxy_number)
        phone_cmp = _norm_cmp(client_phone)

        for row_idx, rec in enumerate(records, start=2):
            status = str(rec.get("status") or "").strip().upper()
            if status != "PENDING":
                continue

            rec_proxy = _norm_cmp(rec.get("proxy_number"))
            rec_phone = _norm_cmp(rec.get("client_real_phone"))

            if rec_proxy == proxy_cmp and rec_phone == phone_cmp:
                return {"row": row_idx, "record": rec, "headers": headers}

        return None

    @staticmethod
    def mark_verified(row: int) -> None:
        sheet = SheetsClient.get_confirmation_pending_sheet()
        headers = ConfirmationPendingRepository._headers(sheet)
        now = datetime.now(timezone.utc).isoformat()

        sheet.update_cell(row, ConfirmationPendingRepository._col(headers, "status"), "VERIFIED")
        if "verified_at" in headers:
            sheet.update_cell(row, ConfirmationPendingRepository._col(headers, "verified_at"), now)

        logger.info("CONFIRMATION_PENDING marqué VERIFIED", extra={"row": row})

    @staticmethod
    def mark_promoted(row: int) -> None:
        sheet = SheetsClient.get_confirmation_pending_sheet()
        headers = ConfirmationPendingRepository._headers(sheet)

        sheet.update_cell(row, ConfirmationPendingRepository._col(headers, "status"), "PROMOTED")
        logger.info("CONFIRMATION_PENDING marqué PROMOTED", extra={"row": row})

    @staticmethod
    def mark_updated(row: int, details: str) -> None:
        """
        Met à jour le statut en UPDATED avec un détail explicite sur les champs modifiés.
        Example: UPDATED (mail), UPDATED (telephone), UPDATED (mail + telephone)
        """
        sheet = SheetsClient.get_confirmation_pending_sheet()
        headers = ConfirmationPendingRepository._headers(sheet)

        status_value = f"UPDATED ({details})" if details else "UPDATED"
        sheet.update_cell(row, ConfirmationPendingRepository._col(headers, "status"), status_value)
        logger.info(
            "CONFIRMATION_PENDING marqué UPDATED",
            extra={"row": row, "details": details or "none"},
        )


    @staticmethod
    def expire_older_than(hours: int = 48) -> list[dict[str, str]]:
        """
        Passe en EXPIRED tous les PENDING vieux de >hours.
        Retourne la liste des pending expirés: [{pending_id, proxy_number}]
        """
        sheet = SheetsClient.get_confirmation_pending_sheet()
        headers = ConfirmationPendingRepository._headers(sheet)
        records = sheet.get_all_records()

        expired: list[dict[str, str]] = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        for row_idx, rec in enumerate(records, start=2):
            status = str(rec.get("status") or "").strip().upper()
            if status != "PENDING":
                continue

            created_raw = str(rec.get("created_at") or "").strip()
            if not created_raw:
                continue

            try:
                created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue

            if created_dt > cutoff:
                continue

            pid = str(rec.get("pending_id") or "").strip()
            proxy = str(rec.get("proxy_number") or "").strip()

            try:
                sheet.update_cell(row_idx, ConfirmationPendingRepository._col(headers, "status"), "EXPIRED")
            except Exception:
                continue

            expired.append({"pending_id": pid, "proxy_number": proxy})

        logger.info("CONFIRMATION_PENDING expired", extra={"count": len(expired)})
        return expired

    @staticmethod
    def extract_otp(body: str) -> str:
        body_clean = (body or "").strip()
        m = OTP_RE.search(body_clean)
        if m:
            return m.group(1)
        return re.sub(r"\D+", "", body_clean)
