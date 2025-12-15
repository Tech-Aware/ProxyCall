import logging
from datetime import datetime
from typing import List, Dict, Optional
import uuid
from datetime import timedelta


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
    "number_type",
    "reserved_token",
    "reserved_at",
    "reserved_by_client_id",
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
    def list_available(country_iso: str, number_type: str = "national") -> List[Dict[str, str]]:
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
                    and str(rec.get("number_type", "")).lower() == str(number_type or "national").lower()
            ):
                available.append(rec)

        return available

    @staticmethod
    def reserve_first_available(
        *,
        country_iso: str,
        number_type: str,
        client_id: int,
        max_tries: int = 10,
        stale_after_minutes: int = 10,
    ) -> Optional[Dict[str, str]]:
        """
        Réserve un numéro disponible en passant status=reserved + token, puis relit pour vérifier.
        Retourne {"row_index": int, "phone_number": str, "reserved_token": str} ou None.
        """
        try:
            sheet = SheetsClient.get_pools_sheet()
        except Exception as exc:  # pragma: no cover
            logger.exception("Impossible d'ouvrir la feuille TwilioPools", exc_info=exc)
            return None

        def _is_stale_reserved(status: str, reserved_at: str) -> bool:
            if status != "reserved":
                return False
            if not reserved_at:
                return True
            try:
                t = datetime.fromisoformat(reserved_at.replace("Z", ""))
            except Exception:
                return True
            return t < (datetime.utcnow() - timedelta(minutes=stale_after_minutes))

        # IMPORTANT : on lit en "values" pour garder l'index de ligne (row_index)
        for _ in range(max_tries):
            try:
                values = sheet.get_all_values()  # inclut l'en-tête
            except Exception as exc:  # pragma: no cover
                logger.exception("Impossible de lire TwilioPools (get_all_values)", exc_info=exc)
                return None

            if not values or len(values) < 2:
                return None

            # Ligne 1 = en-têtes, données à partir de la ligne 2
            data_rows = values[1:]

            for row_index, row in enumerate(data_rows, start=2):
                # Colonnes: A..K => index 0..10
                c_iso = (row[0] if len(row) > 0 else "").upper()
                phone = (row[1] if len(row) > 1 else "")
                status = (row[2] if len(row) > 2 else "").lower()
                ntype = (row[7] if len(row) > 7 else "").lower()
                reserved_at = (row[9] if len(row) > 9 else "")

                if c_iso != country_iso.upper():
                    continue
                if ntype != str(number_type or "mobile").lower():
                    continue

                # On accepte available, ou reserved expiré (stale)
                if status != "available" and not _is_stale_reserved(status, reserved_at):
                    continue

                token = str(uuid.uuid4())
                now = datetime.utcnow().isoformat()

                # On réserve en écrivant C=status, I=token, J=reserved_at, K=client_id
                # Range: C{row}:K{row} (C..K = 9 colonnes)
                # On doit inclure D..H aussi dans le range => on les relit de "row"
                d_friendly = row[3] if len(row) > 3 else ""
                e_date_achat = row[4] if len(row) > 4 else ""
                f_date_attr = row[5] if len(row) > 5 else ""
                g_attr_name = row[6] if len(row) > 6 else ""
                h_number_type = row[7] if len(row) > 7 else str(number_type or "mobile")

                try:
                    sheet.update(
                        f"C{row_index}:K{row_index}",
                        [[
                            "reserved",      # C
                            d_friendly,      # D
                            e_date_achat,    # E
                            f_date_attr,     # F
                            g_attr_name,     # G
                            h_number_type,   # H
                            token,           # I
                            now,             # J
                            str(client_id),  # K
                        ]],
                    )
                except Exception as exc:  # pragma: no cover
                    logger.exception("Impossible de réserver un numéro (update)", exc_info=exc)
                    continue

                # Vérif: relire la cellule I{row_index}
                try:
                    check = sheet.get(f"I{row_index}")
                    current_token = (check[0][0] if check and check[0] else "")
                except Exception:
                    current_token = ""

                if current_token == token:
                    logger.info("Numéro réservé", extra={"phone": phone, "row": row_index})
                    return {"row_index": str(row_index), "phone_number": phone, "reserved_token": token}

                # Sinon conflit -> on retente
                logger.warning("Conflit de réservation, retry", extra={"row": row_index, "phone": phone})
                break

        return None

    @staticmethod
    def mark_assigned_reserved(
        *,
        row_index: int,
        friendly_name: str,
        date_attribution: Optional[str] = None,
        attribution_to_client_name: Optional[str] = None,
    ) -> None:
        try:
            sheet = SheetsClient.get_pools_sheet()
        except Exception as exc:  # pragma: no cover
            logger.exception("Impossible d'ouvrir la feuille TwilioPools", exc_info=exc)
            return

        date_attr = date_attribution or datetime.utcnow().isoformat()
        attr_name = attribution_to_client_name or ""

        # Mises à jour ciblées (évite d'écraser date_achat)
        try:
            sheet.update(f"C{row_index}:C{row_index}", [["assigned"]])
            sheet.update(f"F{row_index}:G{row_index}", [[date_attr, attr_name]])
        except Exception as exc:  # pragma: no cover
            logger.exception("Impossible de finaliser l'attribution (reserved->assigned)", exc_info=exc)
            return

        logger.info("Numéro finalisé en assigned", extra={"row": row_index})



    @staticmethod
    def save_number(
            country_iso: str,
            phone_number: str,
            status: str,
            friendly_name: Optional[str] = None,
            date_achat: Optional[str] = None,
            date_attribution: Optional[str] = None,
            attribution_to_client_name: Optional[str] = None,
            number_type: str = "mobile",
            reserved_token: str = "",
            reserved_at: str = "",
            reserved_by_client_id: str = "",
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
                (number_type or "mobile"),
                reserved_token or "",
                reserved_at or "",
                reserved_by_client_id or "",
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
        """
        (Compat) Finalise l'attribution en retrouvant la ligne via phone_number,
        puis en appelant mark_assigned_reserved().
        """
        try:
            sheet = SheetsClient.get_pools_sheet()
            records = sheet.get_all_records()
        except Exception as exc:  # pragma: no cover
            logger.exception("Impossible de lire la feuille TwilioPools", exc_info=exc)
            return

        target_row = None
        target_record: Dict[str, str] | None = None
        for idx, rec in enumerate(records, start=2):  # start=2 car header en ligne 1
            if rec.get("phone_number") == phone_number:
                target_row = idx
                target_record = rec
                break

        if not target_row or target_record is None:
            logger.warning(
                "Numéro à attribuer introuvable dans TwilioPools",
                extra={"phone": phone_number},
            )
            return

        # On conserve le friendly_name existant si présent (sinon vide)
        friendly = target_record.get("friendly_name", "") or ""

        # Finalisation via la méthode standard
        PoolsRepository.mark_assigned_reserved(
            row_index=target_row,
            friendly_name=friendly,
            date_attribution=date_attribution,
            attribution_to_client_name=attribution_to_client_name,
        )

