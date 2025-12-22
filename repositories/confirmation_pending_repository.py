import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)


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
    def set_proxy_and_otp(*, pending_id: str, proxy_number: str, otp: str) -> None:
        sheet = SheetsClient.get_confirmation_pending_sheet()
        hit = ConfirmationPendingRepository.get_by_pending_id(pending_id)
        if not hit:
            raise RuntimeError(f"pending_id introuvable dans CONFIRMATION_PENDING: {pending_id}")

        row = hit["row"]
        headers = hit["headers"]
        now = datetime.now(timezone.utc).isoformat()

        sheet.update_cell(row, ConfirmationPendingRepository._col(headers, "proxy_number"), str(proxy_number))
        sheet.update_cell(row, ConfirmationPendingRepository._col(headers, "otp"), str(otp))
        sheet.update_cell(row, ConfirmationPendingRepository._col(headers, "status"), "PENDING")

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

        logger.info("CONFIRMATION_PENDING set proxy+otp", extra={"pending_id": pending_id})

    @staticmethod
    def generate_otp(length: int = 6) -> str:
        # 6 digits cryptographically strong
        n = secrets.randbelow(10**length)
        return str(n).zfill(length)

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
