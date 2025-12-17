import logging
from datetime import datetime
from typing import Any
import re

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client as TwilioRest

from app.config import settings
from app.logging_config import mask_phone, mask_sid

from repositories.pools_repository import PoolsRepository
from integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

twilio = TwilioRest(
    settings.TWILIO_ACCOUNT_SID,
    settings.TWILIO_AUTH_TOKEN,
)


class TwilioClient:
    """Client Twilio avec gestion d'un pool de numéros par pays."""

    # -----------------------
    # Utils
    # -----------------------
    @staticmethod
    def _normalize_phone_number(number: str | None) -> str:
        """
        Normalise un numéro pour les appels Twilio :
        - retire tout sauf digits
        - garantit un format +XXXXXXXX
        """
        if number is None:
            return ""
        raw = str(number).strip()
        if not raw:
            return ""
        digits_only = re.sub(r"\D", "", raw)
        if not digits_only:
            return ""
        # accepte 00xx -> +xx
        if digits_only.startswith("00"):
            digits_only = digits_only[2:]
        return f"+{digits_only}"

    @staticmethod
    def auth_check() -> bool:
        """
        Vérifie que les identifiants Twilio sont OK.
        Retourne True si OK, False sinon (et log).
        """
        try:
            sid = (settings.TWILIO_ACCOUNT_SID or "").strip()
            if not sid:
                logger.error("[red]Twilio[/red] auth_check: TWILIO_ACCOUNT_SID vide")
                return False
            twilio.api.accounts(sid).fetch()
            return True
        except Exception as exc:
            logger.error("[red]Twilio[/red] auth_check FAILED: %s", exc)
            return False

    # -----------------------
    # Webhook helpers
    # -----------------------
    @staticmethod
    def ensure_voice_webhook(phone_number: str) -> bool:
        """
        S'assure que voice_url du numéro Twilio == settings.VOICE_WEBHOOK_URL.

        Retourne:
          - True si update effectué
          - False si déjà OK / introuvable / erreur (non bloquant)
        """
        try:
            pn = TwilioClient._normalize_phone_number(phone_number)
            if not pn:
                return False

            target = (settings.VOICE_WEBHOOK_URL or "").strip()
            if not target:
                logger.warning("VOICE_WEBHOOK_URL vide: impossible de corriger %s", mask_phone(pn))
                return False

            incoming = twilio.incoming_phone_numbers.list(phone_number=pn, limit=1)
            if not incoming:
                return False

            current = (getattr(incoming[0], "voice_url", "") or "").strip()
            if current == target:
                return False

            incoming[0].update(voice_url=target)
            logger.info(
                "[cyan]Twilio[/cyan] voice_url updated number=%s",
                mask_phone(pn),
            )
            return True

        except Exception as exc:
            logger.warning(
                "ensure_voice_webhook failed: phone=%s err=%s",
                mask_phone(str(phone_number)),
                exc,
            )
            return False

    @classmethod
    def fix_pool_voice_webhooks(
        cls,
        *,
        only_status: str | None = None,
        dry_run: bool = False,
        only_country: str | None = None,
    ) -> dict[str, object]:
        """
        Parcourt TwilioPools (Sheets) et s'assure que tous les numéros ont voice_url=VOICE_WEBHOOK_URL côté Twilio.

        Paramètres:
          - only_status: "available" / "assigned" / ... (None => tous)
          - dry_run: True => ne fait aucun update, seulement un rapport
          - only_country: "FR" par ex (None => tous)

        Retour:
          rapport dict (checked, need_fix, fixed, not_found_on_twilio, errors, ...)
        """
        target_url = (settings.VOICE_WEBHOOK_URL or "").strip()
        if not target_url:
            raise RuntimeError("VOICE_WEBHOOK_URL est vide: impossible de fixer les webhooks.")

        records = PoolsRepository.list_all()

        checked = 0
        not_found: list[str] = []
        need_fix: list[dict[str, str]] = []
        fixed: list[str] = []
        errors: list[dict[str, str]] = []

        status_filter = (only_status or "").strip().lower() or None
        country_filter = (only_country or "").strip().upper() or None

        logger.info(
            "[magenta]POOL[/magenta] fix_pool_voice_webhooks start dry_run=%s only_status=%s only_country=%s",
            dry_run,
            status_filter or "all",
            country_filter or "all",
        )

        for rec in records:
            try:
                if status_filter:
                    st = str(rec.get("status", "")).strip().lower()
                    if st != status_filter:
                        continue

                if country_filter:
                    c = str(rec.get("country_iso", "")).strip().upper()
                    if c != country_filter:
                        continue

                phone = cls._normalize_phone_number(rec.get("phone_number"))
                if not phone:
                    continue

                checked += 1

                incoming = twilio.incoming_phone_numbers.list(phone_number=phone, limit=1)
                if not incoming:
                    not_found.append(phone)
                    continue

                current = (getattr(incoming[0], "voice_url", "") or "").strip()
                if current != target_url:
                    need_fix.append({"phone_number": phone, "current_voice_url": current})
                    if not dry_run:
                        incoming[0].update(voice_url=target_url)
                        fixed.append(phone)

            except Exception as exc:
                errors.append({"phone_number": str(rec.get("phone_number", "")), "err": str(exc)})

        logger.info(
            "[magenta]POOL[/magenta] fix_pool_voice_webhooks done checked=%s need_fix=%s fixed=%s not_found=%s errors=%s dry_run=%s",
            checked,
            len(need_fix),
            len(fixed),
            len(not_found),
            len(errors),
            dry_run,
        )

        return {
            "target_voice_url": target_url,
            "checked": checked,
            "need_fix": need_fix,
            "fixed": fixed,
            "not_found_on_twilio": not_found,
            "errors": errors,
            "dry_run": dry_run,
            "only_status": only_status,
            "only_country": only_country,
            "ts": datetime.utcnow().isoformat(),
        }

    # -----------------------
    # Ton code existant (inchangé)
    # -----------------------
    @staticmethod
    def _purchase_number(
        country: str,
        friendly_name: str,
        number_type: str = settings.TWILIO_NUMBER_TYPE,
        candidates_limit: int = 10,
        require_sms_capability: bool = True,
    ) -> str:
        """
        Achète un numéro Twilio et retourne son phone_number.

        Principes:
        - 'national' est un alias interne -> 'local' (Twilio FR: endpoint National absent)
        - On récupère plusieurs candidats et on essaie d'acheter jusqu'à réussite
        - On envoie toujours address_sid + bundle_sid si présents
        """

        def _has_voice_and_sms(candidate: Any) -> tuple[bool, bool]:
            capabilities = getattr(candidate, "capabilities", {}) or {}
            voice_ok = bool(capabilities.get("voice") or capabilities.get("VOICE"))
            sms_ok = bool(capabilities.get("sms") or capabilities.get("SMS"))

            # Compat attributs Twilio
            voice_ok = voice_ok or bool(getattr(candidate, "voice_enabled", False))
            sms_ok = sms_ok or bool(getattr(candidate, "sms_enabled", False))
            return voice_ok, sms_ok

        def _list_available(kind: str):
            apn = twilio.available_phone_numbers(country)
            if not hasattr(apn, kind):
                return []
            lim = max(1, int(candidates_limit or 10))
            candidates = getattr(apn, kind).list(limit=lim)

            if not require_sms_capability:
                return candidates

            filtered: list[Any] = []
            for candidate in candidates:
                voice_ok, sms_ok = _has_voice_and_sms(candidate)
                if voice_ok and sms_ok:
                    filtered.append(candidate)
                    continue

                logger.info(
                    "[cyan]Twilio[/cyan] ignore candidat sans voice+sms country=%s type=%s number=%s caps=%s",
                    country,
                    kind,
                    mask_phone(getattr(candidate, "phone_number", "")),
                    getattr(candidate, "capabilities", {}),
                )

            return filtered

        requested = (number_type or "mobile").strip().lower()
        effective = "local" if requested == "national" else requested
        if effective not in ("mobile", "local"):
            raise RuntimeError(
                f"Type de numéro invalide: {number_type!r} (attendu mobile/local/national)"
            )

        logger.info(
            "[cyan]Twilio[/cyan] lookup available numbers country=%s requested=%s effective=%s limit=%s",
            country,
            requested,
            effective,
            max(1, int(candidates_limit or 10)),
        )

        available_numbers = []
        if effective == "mobile":
            try:
                available_numbers = _list_available("mobile")
            except Exception as exc:
                logger.debug(
                    "[cyan]Twilio[/cyan] lookup mobile failed country=%s err=%s",
                    country,
                    exc,
                    exc_info=True,
                )
                available_numbers = []

            if not available_numbers:
                logger.info(
                    "[cyan]Twilio[/cyan] no mobile available country=%s -> fallback local",
                    country,
                )
                try:
                    available_numbers = _list_available("local")
                except Exception as exc:
                    logger.debug(
                        "[cyan]Twilio[/cyan] lookup local failed country=%s err=%s",
                        country,
                        exc,
                        exc_info=True,
                    )
                    available_numbers = []
                effective = "local"
        else:
            try:
                available_numbers = _list_available("local")
            except Exception as exc:
                logger.debug(
                    "[cyan]Twilio[/cyan] lookup local failed country=%s err=%s",
                    country,
                    exc,
                    exc_info=True,
                )
                available_numbers = []

        if not available_numbers:
            sms_clause = " avec capacité SMS" if require_sms_capability else ""
            raise RuntimeError(
                f"Aucun numéro disponible pour {country} (type={effective}{sms_clause})."
            )

        logger.info(
            "[cyan]Twilio[/cyan] candidates found country=%s effective=%s count=%s",
            country,
            effective,
            len(available_numbers),
        )

        last_exc: Exception | None = None

        for idx, cand in enumerate(available_numbers, start=1):
            phone_number = getattr(cand, "phone_number", None)
            if not phone_number:
                logger.debug(
                    "[cyan]Twilio[/cyan] candidate %s/%s has no phone_number attribute -> skip",
                    idx,
                    len(available_numbers),
                )
                continue

            create_kwargs: dict[str, object] = {
                "phone_number": phone_number,
                "voice_url": settings.VOICE_WEBHOOK_URL,
                "friendly_name": friendly_name,
            }

            has_address = bool(settings.TWILIO_ADDRESS_SID)
            has_bundle = bool(settings.TWILIO_BUNDLE_SID)

            if has_address:
                create_kwargs["address_sid"] = settings.TWILIO_ADDRESS_SID
            if has_bundle:
                create_kwargs["bundle_sid"] = settings.TWILIO_BUNDLE_SID

            logger.info(
                "[cyan]Twilio[/cyan] purchase attempt %s/%s country=%s requested=%s effective=%s candidate=%s send_address=%s send_bundle=%s bundle=%s",
                idx,
                len(available_numbers),
                country,
                requested,
                effective,
                mask_phone(str(phone_number)),
                has_address,
                has_bundle,
                mask_sid(str(settings.TWILIO_BUNDLE_SID or "")),
            )

            try:
                incoming = twilio.incoming_phone_numbers.create(**create_kwargs)
                purchased = getattr(incoming, "phone_number", "")
                logger.info(
                    "[green]Twilio[/green] purchase success country=%s effective=%s number=%s",
                    country,
                    effective,
                    mask_phone(str(purchased)),
                )
                return purchased

            except TwilioRestException as exc:
                last_exc = exc
                code = getattr(exc, "code", None)
                status = getattr(exc, "status", None)

                logger.warning(
                    "[yellow]Twilio[/yellow] purchase refused country=%s effective=%s candidate=%s code=%s status=%s",
                    country,
                    effective,
                    mask_phone(str(phone_number)),
                    code,
                    status,
                )
                logger.debug(
                    "[yellow]Twilio[/yellow] exception detail code=%s status=%s err=%s",
                    code,
                    status,
                    str(exc),
                    exc_info=True,
                )

                if code == 21649:
                    continue

                if code == 21651:
                    raise RuntimeError(
                        "L'adresse fournie n'est pas rattachée au bundle Twilio. "
                        "Vérifiez que TWILIO_ADDRESS_SID correspond bien au bundle TWILIO_BUNDLE_SID."
                    ) from exc

                raise

            except Exception as exc:
                last_exc = exc
                logger.error(
                    "[red]Twilio[/red] unexpected error during purchase country=%s effective=%s candidate=%s err=%s",
                    country,
                    effective,
                    mask_phone(str(phone_number)),
                    exc,
                    exc_info=True,
                )
                raise

        if isinstance(last_exc, TwilioRestException) and getattr(last_exc, "code", None) == 21649:
            raise RuntimeError(
                "Aucun des numéros proposés n'est provisionnable avec le bundle actuel "
                f"(TWILIO_BUNDLE_SID={mask_sid(str(settings.TWILIO_BUNDLE_SID or ''))}). "
                "Twilio indique: le bundle n'a pas le bon 'regulation type' pour ces numéros."
            ) from last_exc

        logger.error(
            "[red]Twilio[/red] purchase failed country=%s requested=%s effective=%s attempts=%s last_code=%s",
            country,
            requested,
            effective,
            len(available_numbers),
            getattr(last_exc, "code", None) if isinstance(last_exc, TwilioRestException) else None,
        )
        raise RuntimeError("Achat Twilio impossible après essais de plusieurs candidats.") from last_exc

    @classmethod
    def _fill_pool(
        cls,
        country: str,
        batch_size: int,
        number_type: str = settings.TWILIO_NUMBER_TYPE,
        candidates_limit: int = 10,
        require_sms_capability: bool = True,
    ) -> list[str]:
        country = (country or "").upper().strip()
        requested = (number_type or "mobile").strip().lower()
        stored_type = "local" if requested == "national" else requested
        if stored_type not in ("mobile", "local"):
            stored_type = "mobile"

        qty = max(1, int(batch_size or 1))

        logger.info(
            "[magenta]POOL[/magenta] fill start country=%s requested_qty=%s requested_type=%s stored_type=%s require_sms=%s",
            country,
            qty,
            requested,
            stored_type,
            require_sms_capability,
        )

        added: list[str] = []
        for idx in range(qty):
            friendly = f"Pool-{country}-{idx + 1}"
            logger.info(
                "[magenta]POOL[/magenta] buy %s/%s country=%s type=%s",
                idx + 1,
                qty,
                country,
                requested,
            )

            try:
                purchased = cls._purchase_number(
                    country,
                    friendly,
                    number_type=number_type,
                    candidates_limit=candidates_limit,
                    require_sms_capability=require_sms_capability,
                )
            except RuntimeError as exc:
                logger.warning(
                    "[yellow]POOL[/yellow] buy failed %s/%s country=%s type=%s err=%s",
                    idx + 1,
                    qty,
                    country,
                    requested,
                    exc,
                )
                continue

            PoolsRepository.save_number(
                country_iso=country,
                phone_number=purchased,
                status="available",
                friendly_name=friendly,
                date_achat=datetime.utcnow().isoformat(),
                number_type=stored_type,
                reserved_token="",
                reserved_at="",
                reserved_by_client_id="",
            )
            added.append(purchased)

            logger.info(
                "[green]POOL[/green] saved %s/%s country=%s number=%s",
                idx + 1,
                qty,
                country,
                mask_phone(str(purchased)),
            )

        logger.info(
            "[magenta]POOL[/magenta] fill end country=%s requested_qty=%s purchased=%s type=%s",
            country,
            qty,
            len(added),
            stored_type,
        )
        return added

    @classmethod
    def fill_pool(
        cls,
        country: str,
        batch_size: int,
        number_type: str = settings.TWILIO_NUMBER_TYPE,
        candidates_limit: int = 10,
        require_sms_capability: bool = True,
    ) -> list[str]:
        return cls._fill_pool(
            country,
            batch_size,
            number_type=number_type,
            candidates_limit=candidates_limit,
            require_sms_capability=require_sms_capability,
        )

    @classmethod
    def list_available(cls, country: str, number_type: str | None = None):
        c = (country or "").strip().upper()
        nt_raw = (number_type or "").strip().lower()
        nt = "local" if nt_raw == "national" else nt_raw

        logger.debug(
            "[magenta]POOL[/magenta] TwilioClient.list_available country=%s number_type=%s",
            c,
            nt if nt else "all",
        )
        rows = PoolsRepository.list_available(c, number_type=number_type)

        logger.info(
            "[magenta]POOL[/magenta] TwilioClient.list_available done country=%s returned=%s type=%s",
            c,
            len(rows),
            nt if nt else "all",
        )
        return rows

    @classmethod
    def list_twilio_numbers(cls):
        logger.info("[cyan]Twilio[/cyan] listing incoming_phone_numbers")
        try:
            incoming_numbers = twilio.incoming_phone_numbers.list()
        except Exception as exc:
            logger.exception("[red]Twilio[/red] impossible de récupérer les numéros Twilio existants", exc_info=exc)
            return []

        numbers = []
        for number in incoming_numbers:
            pn = getattr(number, "phone_number", "") or ""
            numbers.append(
                {
                    "phone_number": pn,
                    "friendly_name": getattr(number, "friendly_name", "") or "",
                    "country_iso": getattr(number, "iso_country", "") or "",
                }
            )

        logger.info("[cyan]Twilio[/cyan] list complete count=%s", len(numbers))
        return numbers

    @classmethod
    def sync_twilio_numbers_with_sheet(
        cls,
        *,
        apply: bool = True,
        twilio_numbers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        # ... inchangé chez toi ...
        # (garde ton implémentation actuelle)
        return super().sync_twilio_numbers_with_sheet(apply=apply, twilio_numbers=twilio_numbers)  # type: ignore

    @classmethod
    def assign_number_from_pool(
        cls,
        *,
        client_id: int,
        country: str,
        attribution_to_client_name: str,
        number_type: str = settings.TWILIO_NUMBER_TYPE,
    ) -> str:
        # ... inchangé chez toi ...
        return super().assign_number_from_pool(  # type: ignore
            client_id=client_id,
            country=country,
            attribution_to_client_name=attribution_to_client_name,
            number_type=number_type,
        )
