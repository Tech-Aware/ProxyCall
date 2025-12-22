import logging
import re
import secrets
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

OTP_RE = re.compile(r"\b(\d{4,8})\b")  # 4 à 8 chiffres


def _norm_cmp(num: str | None) -> str:
    s = str(num or "").strip().replace(" ", "")
    if s.startswith("+"):
        s = s[1:]
    return s


class ConfirmationPendingRepository:
    REQUIRED = {
        "pending_id",
        "client_name",
        "client_mail",
        "client_real_phone",
        "proxy_number",
        "otp",
        "status",
        "created_at",
        "verified_at",
    }

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
        try:
            existing_created = sheet.cell(row, created_col).value
        except Exception:
            existing_created = ""

        if not str(existing_created or "").strip():
            sheet.update_cell(row, created_col, now)

        logger.info("CONFIRMATION_PENDING set proxy+otp", extra={"pending_id": pending_id})

    @staticmethod
    def generate_otp(length: int = 6) -> str:
        n = secrets.randbelow(10**length)
        return str(n).zfill(length)

    @staticmethod
    def extract_otp(body: str) -> str:
        body_clean = (body or "").strip()
        m = OTP_RE.search(body_clean)
        if m:
            return m.group(1)
        # fallback: garde uniquement les chiffres
        return re.sub(r"\D+", "", body_clean)

    @staticmethod
    def find_pending(proxy_number: str, client_phone: str) -> Optional[Dict[str, Any]]:
        sheet = SheetsClient.get_confirmation_pending_sheet()
        headers = ConfirmationPendingRepository._headers(sheet)

        missing = ConfirmationPendingRepository.REQUIRED - set(headers)
        if missing:
            raise RuntimeError(f"CONFIRMATION_PENDING: colonnes manquantes: {sorted(missing)}")

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
    def mark_verified(row: int, headers: list[str]) -> None:
        sheet = SheetsClient.get_confirmation_pending_sheet()
        now = datetime.now(timezone.utc).isoformat()

        sheet.update_cell(row, ConfirmationPendingRepository._col(headers, "status"), "VERIFIED")
        if "verified_at" in headers:
            sheet.update_cell(row, ConfirmationPendingRepository._col(headers, "verified_at"), now)

        logger.info("CONFIRMATION_PENDING marqué VERIFIED", extra={"row": row})

    @staticmethod
    def mark_promoted(row: int, headers: list[str]) -> None:
        sheet = SheetsClient.get_confirmation_pending_sheet()
        sheet.update_cell(row, ConfirmationPendingRepository._col(headers, "status"), "PROMOTED")
        logger.info("CONFIRMATION_PENDING marqué PROMOTED", extra={"row": row})

    @staticmethod
    def expire_older_than(hours: int = 48) -> list[dict[str, str]]:
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
