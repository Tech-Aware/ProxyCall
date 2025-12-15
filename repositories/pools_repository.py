import logging
from datetime import datetime
from typing import List, Dict, Optional

from integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

HEADERS = [
    "country_iso",
    "phone_number",
    "status",
    "friendly_name",
    "date_achat",
    "date_attribution",
    "attribution_to_client_name",
]


class PoolsRepository:
    """Gestion du pool de numéros Twilio via Google Sheets."""

    @staticmethod
    def list_all() -> List[Dict[str, str]]:
        try:
            sheet = SheetsClient.get_pools_sheet()
            return sheet.get_all_records(numericise_ignore=["all"])
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("Impossible de lire la feuille TwilioPools", exc_info=exc)
            return []

    @staticmethod
    def list_available(country_iso: str) -> List[Dict[str, str]]:
        try:
            sheet = SheetsClient.get_pools_sheet()
            records = sheet.get_all_records()
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("Impossible de lire la feuille TwilioPools", exc_info=exc)
            return []

        available = []
        for rec in records:
            if (
                rec.get("country_iso") == country_iso
                and str(rec.get("status")).lower() == "available"
            ):
                available.append(rec)
        return available

    @staticmethod
    def save_number(
        country_iso: str,
        phone_number: str,
        status: str,
        friendly_name: Optional[str] = None,
        date_achat: Optional[str] = None,
        date_attribution: Optional[str] = None,
        attribution_to_client_name: Optional[str] = None,
    ) -> None:
        try:
            sheet = SheetsClient.get_pools_sheet()
            row = [
                country_iso,
                phone_number,
                status,
                friendly_name or "",
                date_achat or datetime.utcnow().isoformat(),
                date_attribution or "",
                attribution_to_client_name or "",
            ]
            sheet.append_row(row)
            logger.info(
                "Numéro ajouté au pool", extra={"country": country_iso, "number": phone_number}
            )
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception(
                "Impossible d'enregistrer le numéro dans TwilioPools", exc_info=exc
            )

    @staticmethod
    def mark_assigned(
        phone_number: str,
        date_attribution: Optional[str] = None,
        attribution_to_client_name: Optional[str] = None,
    ) -> None:
        try:
            sheet = SheetsClient.get_pools_sheet()
            records = sheet.get_all_records()
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("Impossible de lire la feuille TwilioPools", exc_info=exc)
            return

        target_row = None
        target_record: Dict[str, str] | None = None
        for idx, rec in enumerate(records, start=2):
            if rec.get("phone_number") == phone_number:
                target_row = idx
                target_record = rec
                break

        if not target_row or target_record is None:
            logger.warning("Numéro à attribuer introuvable dans TwilioPools", extra={"phone": phone_number})
            return

        updated_row = [
            target_record.get("country_iso"),
            target_record.get("phone_number"),
            "assigned",
            target_record.get("friendly_name", ""),
            target_record.get("date_achat", ""),
            date_attribution or datetime.utcnow().isoformat(),
            attribution_to_client_name or "",
        ]

        try:
            sheet.update(f"A{target_row}:G{target_row}", [updated_row])
            logger.info(
                "Numéro marqué comme attribué", extra={"phone": phone_number, "row": target_row}
            )
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception(
                "Impossible de mettre à jour le numéro dans TwilioPools", exc_info=exc
            )
