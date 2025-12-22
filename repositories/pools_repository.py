# repositories/pools_repository.py
import logging
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from app.validator import phone_e164_strict, ValidationIssue
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
    def list_available(country_iso: str, number_type: Optional[str] = None) -> List[Dict[str, str]]:
        """
        Liste les numéros disponibles.
        - number_type=None / "" / "all" => tous les types (mobile + local) pour ce pays.
        - "national" => alias de "local".
        - sinon filtre strict sur le type fourni.
        """
        country = (country_iso or "").strip().upper()

        nt_raw = (number_type or "").strip().lower()
        nt = "local" if nt_raw == "national" else nt_raw
        filter_type = nt not in ("", "all", "none")

        logger.debug(
            "[magenta]POOL[/magenta] repo.list_available start country=%s number_type=%s filter_type=%s",
            country,
            nt if nt else "all",
            filter_type,
        )

        try:
            sheet = SheetsClient.get_pools_sheet()
            records = sheet.get_all_records()
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("[red]POOL[/red] repo.list_available failed to read TwilioPools", exc_info=exc)
            return []

        available: List[Dict[str, str]] = []

        for rec in records:
            rec_country = str(rec.get("country_iso", "")).strip().upper()
            rec_status = str(rec.get("status", "")).strip().lower()
            rec_type = str(rec.get("number_type", "")).strip().lower()

            if rec_country != country:
                continue
            if rec_status != "available":
                continue
            if filter_type and rec_type != nt:
                continue

            available.append(rec)

        breakdown: dict[str, int] = {}
        for rec in available:
            t = str(rec.get("number_type", "")).strip().lower() or "unknown"
            breakdown[t] = breakdown.get(t, 0) + 1

        logger.info(
            "[magenta]POOL[/magenta] repo.list_available done country=%s returned=%s type=%s breakdown=%s",
            country,
            len(available),
            nt if filter_type else "all",
            breakdown,
        )

        return available

    @staticmethod
    def reserve_first_available_pending(
            *,
            country_iso: str,
            number_type: str,
            pending_id: str,
            attribution_to_client_name: str = "",
            max_tries: int = 10,
            stale_after_minutes: int = 10,
    ) -> Optional[Dict[str, str]]:
        """
        Réserve un numéro pour une demande pending (sans client_id).
        - status -> reserved
        - reserved_token = pending_id
        - reserved_at = now
        - reserved_by_client_id = "" (vide)
        - attribution_to_client_name peut être rempli (col G)
        - date_attribution reste vide (col F)
        """
        try:
            sheet = SheetsClient.get_pools_sheet()
        except Exception as exc:  # pragma: no cover
            logger.exception("Impossible d'ouvrir la feuille TwilioPools", exc_info=exc)
            return None

        country = (country_iso or "").strip().upper()
        requested = (number_type or "mobile").strip().lower()
        if requested == "national":
            requested = "local"

        def _is_stale_reserved(status: str, reserved_at: str) -> bool:
            st = (status or "").strip().lower()
            if st != "reserved":
                return False
            if not reserved_at:
                return True
            try:
                t = datetime.fromisoformat(str(reserved_at).replace("Z", ""))
            except Exception:
                return True
            return t < (datetime.utcnow() - timedelta(minutes=stale_after_minutes))

        logger.info(
            "[magenta]POOL[/magenta] reserve_pending start country=%s type=%s pending_id=%s",
            country,
            requested,
            pending_id,
        )

        for attempt in range(max_tries):
            try:
                values = sheet.get_all_values()
            except Exception as exc:  # pragma: no cover
                logger.exception("Impossible de lire TwilioPools (get_all_values)", exc_info=exc)
                return None

            if not values or len(values) < 2:
                return None

            data_rows = values[1:]  # header
            for row_index, row in enumerate(data_rows, start=2):
                c_iso = (row[0] if len(row) > 0 else "").strip().upper()
                phone = (row[1] if len(row) > 1 else "").strip()
                status = (row[2] if len(row) > 2 else "").strip().lower()
                ntype = (row[7] if len(row) > 7 else "").strip().lower()
                reserved_at_existing = (row[9] if len(row) > 9 else "").strip()

                if c_iso != country:
                    continue
                if ntype != requested:
                    continue
                if status != "available" and not _is_stale_reserved(status, reserved_at_existing):
                    continue

                now = datetime.utcnow().isoformat()

                try:
                    # C status -> reserved
                    sheet.update(f"C{row_index}:C{row_index}", [["reserved"]])

                    # G attribution_to_client_name (optionnel)
                    if attribution_to_client_name is not None:
                        sheet.update(f"G{row_index}:G{row_index}", [[attribution_to_client_name]])

                    # I/J/K : token, reserved_at, reserved_by_client_id (vide)
                    sheet.update(f"I{row_index}:K{row_index}", [[str(pending_id), now, ""]])
                except Exception as exc:  # pragma: no cover
                    logger.exception("Impossible de réserver un numéro (update pending)", exc_info=exc)
                    continue

                # check I matches
                try:
                    check = sheet.get(f"I{row_index}")
                    current_token = (check[0][0] if check and check[0] else "")
                except Exception:
                    current_token = ""

                if str(current_token).strip() == str(pending_id).strip():
                    logger.info(
                        "[magenta]POOL[/magenta] reserve_pending ok row=%s phone=****%s",
                        row_index,
                        phone[-4:] if phone else "",
                    )
                    return {
                        "row_index": str(row_index),
                        "phone_number": phone,
                        "reserved_token": str(pending_id),
                        "reserved_at": now,
                    }

                logger.warning(
                    "[magenta]POOL[/magenta] reserve_pending conflict retry attempt=%s row=%s",
                    attempt + 1,
                    row_index,
                )
                break

        logger.warning("[magenta]POOL[/magenta] reserve_pending failed (no candidate)")
        return None

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
        Réserve un numéro (anti-conflit) :
        - trouve une ligne "available" (ou "reserved" expiré)
        - écrit C="reserved" + I/J/K (token, timestamp, client_id)
        - relit I pour confirmer que le token est bien celui qu'on a écrit
        - ne modifie PAS friendly_name

        Retour:
          {"row_index": "<int>", "phone_number": "<str>", "reserved_token": "<uuid>", "reserved_at": "<iso>"} ou None
        """
        try:
            sheet = SheetsClient.get_pools_sheet()
        except Exception as exc:  # pragma: no cover
            logger.exception("Impossible d'ouvrir la feuille TwilioPools", exc_info=exc)
            return None

        country = (country_iso or "").strip().upper()
        requested = (number_type or "mobile").strip().lower()
        if requested == "national":
            requested = "local"

        def _is_stale_reserved(status: str, reserved_at: str) -> bool:
            st = (status or "").strip().lower()
            if st != "reserved":
                return False
            if not reserved_at:
                return True
            try:
                t = datetime.fromisoformat(str(reserved_at).replace("Z", ""))
            except Exception:
                return True
            return t < (datetime.utcnow() - timedelta(minutes=stale_after_minutes))

        logger.info(
            "[magenta]POOL[/magenta] reserve start country=%s type=%s client_id=%s",
            country,
            requested,
            client_id,
        )

        for attempt in range(max_tries):
            try:
                values = sheet.get_all_values()  # inclut header
            except Exception as exc:  # pragma: no cover
                logger.exception("Impossible de lire TwilioPools (get_all_values)", exc_info=exc)
                return None

            if not values or len(values) < 2:
                return None

            data_rows = values[1:]  # header en ligne 1

            for row_index, row in enumerate(data_rows, start=2):
                c_iso = (row[0] if len(row) > 0 else "").strip().upper()
                phone = (row[1] if len(row) > 1 else "").strip()
                status = (row[2] if len(row) > 2 else "").strip().lower()
                ntype = (row[7] if len(row) > 7 else "").strip().lower()
                reserved_at_existing = (row[9] if len(row) > 9 else "").strip()

                if c_iso != country:
                    continue
                if ntype != requested:
                    continue

                if status != "available" and not _is_stale_reserved(status, reserved_at_existing):
                    continue

                token = str(uuid.uuid4())
                now = datetime.utcnow().isoformat()

                try:
                    # status -> reserved
                    sheet.update(f"C{row_index}:C{row_index}", [["reserved"]])
                    # set reservation trace
                    sheet.update(f"I{row_index}:K{row_index}", [[token, now, str(client_id)]])
                except Exception as exc:  # pragma: no cover
                    logger.exception("Impossible de réserver un numéro (update)", exc_info=exc)
                    continue

                # Vérification token (I)
                try:
                    check = sheet.get(f"I{row_index}")
                    current_token = (check[0][0] if check and check[0] else "")
                except Exception:
                    current_token = ""

                if str(current_token).strip() == token:
                    logger.info(
                        "[magenta]POOL[/magenta] reserve ok row=%s phone=****%s",
                        row_index,
                        phone[-4:] if phone else "",
                    )
                    return {
                        "row_index": str(row_index),
                        "phone_number": phone,
                        "reserved_token": token,
                        "reserved_at": now,
                    }

                logger.warning(
                    "[magenta]POOL[/magenta] reserve conflict retry attempt=%s row=%s",
                    attempt + 1,
                    row_index,
                )
                break

        logger.warning("[magenta]POOL[/magenta] reserve failed (no candidate)")
        return None

    @staticmethod
    def release_reservation_by_token(*, reserved_token: str) -> int:
        """
        Libère (retour 'available') toutes les lignes TwilioPools dont reserved_token == token
        et status == reserved.
        Retourne le nombre de lignes libérées.
        """
        token = str(reserved_token or "").strip()
        if not token:
            return 0

        try:
            sheet = SheetsClient.get_pools_sheet()
            values = sheet.get_all_values()
        except Exception as exc:  # pragma: no cover
            logger.exception("Impossible de lire TwilioPools pour release", exc_info=exc)
            return 0

        if not values or len(values) < 2:
            return 0

        count = 0
        data_rows = values[1:]
        for row_index, row in enumerate(data_rows, start=2):
            status = (row[2] if len(row) > 2 else "").strip().lower()
            tok = (row[8] if len(row) > 8 else "").strip()  # I = reserved_token
            if status == "reserved" and tok == token:
                try:
                    # status -> available
                    sheet.update(f"C{row_index}:C{row_index}", [["available"]])
                    # clear I/J/K
                    sheet.update(f"I{row_index}:K{row_index}", [["", "", ""]])
                    # optional: clear attribution_to_client_name (G) because it was only pending context
                    sheet.update(f"G{row_index}:G{row_index}", [[""]])
                    count += 1
                except Exception as exc:  # pragma: no cover
                    logger.exception("Release failed row=%s", row_index, exc_info=exc)
                    continue

        logger.info("[magenta]POOL[/magenta] release_reservation_by_token token=%s released=%s", token, count)
        return count


    @staticmethod
    def finalize_assignment_keep_friendly(
        *,
        row_index: int,
        reserved_token: str,
        reserved_at: str,
        reserved_by_client_id: int,
        date_attribution: Optional[str] = None,
        attribution_to_client_name: Optional[str] = None,
    ) -> bool:
        """
        Finalise l'attribution SANS modifier friendly_name :
        - vérifie que I{row_index} == reserved_token (anti-conflit)
        - C="assigned"
        - F/G renseignés
        - I/J/K renseignés (token/time/client_id) => trace
        """
        try:
            sheet = SheetsClient.get_pools_sheet()
        except Exception as exc:  # pragma: no cover
            logger.exception("Impossible d'ouvrir la feuille TwilioPools", exc_info=exc)
            return False

        # check token (I)
        try:
            check = sheet.get(f"I{row_index}")
            current_token = (check[0][0] if check and check[0] else "")
        except Exception:
            current_token = ""

        if str(current_token).strip() != str(reserved_token).strip():
            logger.warning("[magenta]POOL[/magenta] finalize token mismatch row=%s", row_index)
            return False

        date_attr = date_attribution or datetime.utcnow().isoformat()
        attr_name = attribution_to_client_name or ""

        try:
            # status -> assigned
            sheet.update(f"C{row_index}:C{row_index}", [["assigned"]])
            # attribution fields
            sheet.update(f"F{row_index}:G{row_index}", [[date_attr, attr_name]])
            # keep reservation trace (I/J/K)
            sheet.update(f"I{row_index}:K{row_index}", [[reserved_token, reserved_at, str(reserved_by_client_id)]])
        except Exception as exc:  # pragma: no cover
            logger.exception("Impossible de finaliser l'attribution (finalize)", exc_info=exc)
            return False

        logger.info("[magenta]POOL[/magenta] finalize ok row=%s", row_index)
        return True

    @staticmethod
    def mark_assigned_reserved(
        *,
        row_index: int,
        friendly_name: str,  # conservé pour compat signature; NON utilisé (on ne touche pas friendly_name)
        date_attribution: Optional[str] = None,
        attribution_to_client_name: Optional[str] = None,
        reserved_token: Optional[str] = None,
        reserved_at: Optional[str] = None,
        reserved_by_client_id: Optional[str] = None,
    ) -> None:
        """
        Finalise une attribution sur une ligne déjà déterminée (row_index),
        SANS modifier friendly_name.

        Met à jour :
        - C: assigned
        - F/G: date_attribution + attribution_to_client_name
        - I/J/K: reserved_token + reserved_at + reserved_by_client_id (si fournis)

        NOTE: cette méthode n'est pas "anti-conflit" par elle-même (pas de token-check).
        Pour le flux safe: utiliser reserve_first_available + finalize_assignment_keep_friendly.
        """
        try:
            sheet = SheetsClient.get_pools_sheet()
        except Exception as exc:  # pragma: no cover
            logger.exception("Impossible d'ouvrir la feuille TwilioPools", exc_info=exc)
            return

        date_attr = date_attribution or datetime.utcnow().isoformat()
        attr_name = attribution_to_client_name or ""

        try:
            sheet.update(f"C{row_index}:C{row_index}", [["assigned"]])
            sheet.update(f"F{row_index}:G{row_index}", [[date_attr, attr_name]])

            if reserved_token is not None or reserved_at is not None or reserved_by_client_id is not None:
                sheet.update(
                    f"I{row_index}:K{row_index}",
                    [[
                        reserved_token or "",
                        reserved_at or "",
                        reserved_by_client_id or "",
                    ]],
                )
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
            try:
                phone_number = phone_e164_strict(phone_number, field="phone_number")
            except ValidationIssue as exc:
                logger.error("[POOL] Refus save_number: %s", exc)
                return

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
            logger.info("Numéro ajouté au pool", extra={"country": country_iso, "number": phone_number})
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("Impossible d'enregistrer le numéro dans TwilioPools", exc_info=exc)

    @staticmethod
    def remove_number(phone_number: str) -> bool:
        """Supprime un numéro du pool TwilioPools.

        Retourne True si la ligne a été supprimée, False sinon.
        """

        try:
            normalized = phone_e164_strict(phone_number, field="phone_number")
        except ValidationIssue as exc:
            logger.error("[POOL] Suppression refusée: numéro invalide (%s)", exc)
            return False

        try:
            sheet = SheetsClient.get_pools_sheet()
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("Impossible d'ouvrir la feuille TwilioPools pour suppression", exc_info=exc)
            return False

        try:
            values = sheet.get_all_values()
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("Impossible de lire TwilioPools (suppression)", exc_info=exc)
            return False

        if not values or len(values) < 2:
            logger.warning("Aucune donnée dans TwilioPools pour supprimer %s", normalized)
            return False

        for idx, row in enumerate(values[1:], start=2):  # header sur la première ligne
            phone = (row[1] if len(row) > 1 else "").strip()
            if phone != normalized:
                continue

            try:
                sheet.delete_rows(idx)
                logger.info(
                    "Numéro supprimé de TwilioPools",
                    extra={"phone": normalized, "row_index": idx},
                )
                return True
            except Exception as exc:  # pragma: no cover - dépendances externes
                logger.exception(
                    "Impossible de supprimer la ligne %s pour le numéro %s",
                    idx,
                    normalized,
                    exc_info=exc,
                )
                return False

        logger.warning(
            "Numéro introuvable dans TwilioPools pour suppression",
            extra={"phone": normalized},
        )
        return False

    @staticmethod
    def mark_assigned(
        phone_number: str,
        date_attribution: Optional[str] = None,
        attribution_to_client_name: Optional[str] = None,
    ) -> None:
        """
        (Compat) Finalise l'attribution en retrouvant la ligne via phone_number,
        puis en appelant mark_assigned_reserved().

        IMPORTANT: ne gère pas la concurrence (pas de token-check).
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
            if str(rec.get("phone_number", "")).strip() == str(phone_number).strip():
                target_row = idx
                target_record = rec
                break

        if not target_row or target_record is None:
            logger.warning("Numéro à attribuer introuvable dans TwilioPools", extra={"phone": phone_number})
            return

        # friendly_name conservé tel quel (et on ne le réécrit pas)
        friendly = target_record.get("friendly_name", "") or ""

        PoolsRepository.mark_assigned_reserved(
            row_index=target_row,
            friendly_name=friendly,  # compat
            date_attribution=date_attribution,
            attribution_to_client_name=attribution_to_client_name,
        )

        @staticmethod
        def find_row_by_phone_number(phone_number: str) -> Optional[dict]:
            """Retourne {'row_index': int, 'record': dict} pour phone_number (col B = phone_number)."""
            try:
                sheet = SheetsClient.get_pools_sheet()
                records = sheet.get_all_records()
            except Exception as exc:  # pragma: no cover
                logger.exception("Impossible de lire TwilioPools", exc_info=exc)
                return None

            target = str(phone_number or "").strip().replace(" ", "")
            if not target.startswith("+"):
                target = "+" + target

            for row_idx, rec in enumerate(records, start=2):
                rec_phone = str(rec.get("phone_number") or "").strip().replace(" ", "")
                if rec_phone and not rec_phone.startswith("+"):
                    rec_phone = "+" + rec_phone
                if rec_phone == target:
                    return {"row_index": row_idx, "record": rec}

            return None

