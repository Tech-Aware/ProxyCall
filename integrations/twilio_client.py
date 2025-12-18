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

    @staticmethod
    def send_sms(*, from_number: str, to_number: str, body: str) -> dict[str, object]:
        """Envoie un SMS via Twilio avec journalisation détaillée."""

        from_norm = TwilioClient._normalize_phone_number(from_number)
        to_norm = TwilioClient._normalize_phone_number(to_number)

        if not from_norm or not to_norm:
            raise ValueError("from_number et to_number doivent être renseignés au format E.164")

        body_safe = body or ""
        try:
            logger.info(
                "[cyan]Twilio[/cyan] send SMS",
                extra={
                    "from": mask_phone(from_norm),
                    "to": mask_phone(to_norm),
                    "length": len(body_safe),
                },
            )
            msg = twilio.messages.create(from_=from_norm, to=to_norm, body=body_safe)
            sid = getattr(msg, "sid", "")
            logger.info(
                "[green]Twilio[/green] SMS envoyé",
                extra={"sid": mask_sid(str(sid)), "to": mask_phone(to_norm)},
            )
            return {"sid": sid, "to": to_norm, "from": from_norm}
        except TwilioRestException as exc:
            logger.error(
                "[red]Twilio[/red] SMS refusé code=%s status=%s err=%s",
                getattr(exc, "code", None),
                getattr(exc, "status", None),
                str(exc),
            )
            raise
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("[red]Twilio[/red] SMS échec inattendu", exc_info=exc)
            raise

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

    @staticmethod
    def ensure_messaging_webhook(phone_number: str) -> bool:
        """S'assure que sms_url du numéro Twilio pointe vers le webhook configuré.

        Retourne True si une mise à jour a été effectuée, False sinon.
        """

        try:
            pn = TwilioClient._normalize_phone_number(phone_number)
            if not pn:
                return False

            target = (settings.MESSAGING_WEBHOOK_URL or "").strip()
            if not target:
                logger.warning(
                    "MESSAGING_WEBHOOK_URL vide: impossible de corriger %s",
                    mask_phone(pn),
                )
                return False

            incoming = twilio.incoming_phone_numbers.list(phone_number=pn, limit=1)
            if not incoming:
                return False

            current = (getattr(incoming[0], "sms_url", "") or "").strip()
            if current == target:
                return False

            incoming[0].update(sms_url=target)
            logger.info(
                "[cyan]Twilio[/cyan] sms_url updated number=%s",
                mask_phone(pn),
            )
            return True

        except Exception as exc:
            logger.warning(
                "ensure_messaging_webhook failed: phone=%s err=%s",
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
        fix_sms: bool = True,
    ) -> dict[str, object]:
        """
        Parcourt TwilioPools (Sheets) et s'assure que tous les numéros ont
        voice_url=VOICE_WEBHOOK_URL (et sms_url=MESSAGING_WEBHOOK_URL si fix_sms=True)
        côté Twilio.

        Paramètres:
          - only_status: "available" / "assigned" / ... (None => tous)
          - dry_run: True => ne fait aucun update, seulement un rapport
          - only_country: "FR" par ex (None => tous)
          - fix_sms: True => met à jour aussi sms_url

        Retour:
          rapport dict (checked, need_fix_voice, fixed_voice, need_fix_sms, fixed_sms, not_found_on_twilio, errors, ...)
        """
        target_voice_url = (settings.VOICE_WEBHOOK_URL or "").strip()
        if not target_voice_url:
            raise RuntimeError("VOICE_WEBHOOK_URL est vide: impossible de fixer les webhooks voix.")

        target_sms_url = (settings.MESSAGING_WEBHOOK_URL or "").strip() if fix_sms else ""
        if fix_sms and not target_sms_url:
            raise RuntimeError("MESSAGING_WEBHOOK_URL est vide: impossible de fixer les webhooks SMS.")

        records = PoolsRepository.list_all()

        checked = 0
        not_found: list[str] = []
        need_fix_voice: list[dict[str, str]] = []
        need_fix_sms: list[dict[str, str]] = []
        fixed_voice: list[str] = []
        fixed_sms: list[str] = []
        errors: list[dict[str, str]] = []

        status_filter = (only_status or "").strip().lower() or None
        country_filter = (only_country or "").strip().upper() or None

        logger.info(
            "[magenta]POOL[/magenta] fix_pool_voice_webhooks start dry_run=%s fix_sms=%s only_status=%s only_country=%s",
            dry_run,
            fix_sms,
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

                voice_current = (getattr(incoming[0], "voice_url", "") or "").strip()
                sms_current = (getattr(incoming[0], "sms_url", "") or "").strip()

                voice_needs_fix = voice_current != target_voice_url
                sms_needs_fix = fix_sms and target_sms_url and sms_current != target_sms_url

                if voice_needs_fix:
                    need_fix_voice.append({"phone_number": phone, "current_voice_url": voice_current})
                if sms_needs_fix:
                    need_fix_sms.append({"phone_number": phone, "current_sms_url": sms_current})

                if (voice_needs_fix or sms_needs_fix) and not dry_run:
                    payload: dict[str, str] = {}
                    if voice_needs_fix:
                        payload["voice_url"] = target_voice_url
                    if sms_needs_fix:
                        payload["sms_url"] = target_sms_url
                    incoming[0].update(**payload)
                    if voice_needs_fix:
                        fixed_voice.append(phone)
                    if sms_needs_fix:
                        fixed_sms.append(phone)

            except Exception as exc:
                errors.append({"phone_number": str(rec.get("phone_number", "")), "err": str(exc)})

        logger.info(
            "[magenta]POOL[/magenta] fix_pool_voice_webhooks done checked=%s need_fix_voice=%s fixed_voice=%s need_fix_sms=%s fixed_sms=%s not_found=%s errors=%s dry_run=%s",
            checked,
            len(need_fix_voice),
            len(fixed_voice),
            len(need_fix_sms),
            len(fixed_sms),
            len(not_found),
            len(errors),
            dry_run,
        )

        return {
            "target_voice_url": target_voice_url,
            "target_sms_url": target_sms_url,
            "checked": checked,
            "need_fix": need_fix_voice,
            "fixed": fixed_voice,
            "need_fix_voice": need_fix_voice,
            "need_fix_sms": need_fix_sms,
            "fixed_voice": fixed_voice,
            "fixed_sms": fixed_sms,
            "not_found_on_twilio": not_found,
            "errors": errors,
            "dry_run": dry_run,
            "only_status": only_status,
            "only_country": only_country,
            "ts": datetime.utcnow().isoformat(),
        }

    @classmethod
    def fix_pool_messaging_webhooks(
        cls,
        *,
        only_status: str | None = None,
        dry_run: bool = False,
        only_country: str | None = None,
    ) -> dict[str, object]:
        """Synchronise sms_url des numéros du pool avec le webhook configuré."""

        target_url = (settings.MESSAGING_WEBHOOK_URL or "").strip()
        if not target_url:
            raise RuntimeError(
                "MESSAGING_WEBHOOK_URL est vide: impossible de fixer les webhooks SMS."
            )

        records = PoolsRepository.list_all()

        checked = 0
        not_found: list[str] = []
        need_fix: list[dict[str, str]] = []
        fixed: list[str] = []
        errors: list[dict[str, str]] = []

        status_filter = (only_status or "").strip().lower() or None
        country_filter = (only_country or "").strip().upper() or None

        logger.info(
            "[magenta]POOL[/magenta] fix_pool_messaging_webhooks start dry_run=%s only_status=%s only_country=%s",
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

                current = (getattr(incoming[0], "sms_url", "") or "").strip()
                if current != target_url:
                    need_fix.append({"phone_number": phone, "current_sms_url": current})
                    if not dry_run:
                        incoming[0].update(sms_url=target_url)
                        fixed.append(phone)

            except Exception as exc:
                errors.append({"phone_number": str(rec.get("phone_number", "")), "err": str(exc)})

        logger.info(
            "[magenta]POOL[/magenta] fix_pool_messaging_webhooks done checked=%s need_fix=%s fixed=%s not_found=%s errors=%s dry_run=%s",
            checked,
            len(need_fix),
            len(fixed),
            len(not_found),
            len(errors),
            dry_run,
        )

        return {
            "target_sms_url": target_url,
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
        require_voice_capability: bool = True,
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
            list_kwargs: dict[str, object] = {
                "limit": lim,
                "page_size": min(lim, 100),
            }

            if require_sms_capability:
                list_kwargs["sms_enabled"] = True
            if require_voice_capability:
                list_kwargs["voice_enabled"] = True

            logger.info(
                "[cyan]Twilio[/cyan] search available country=%s type=%s limit=%s sms_enabled=%s voice_enabled=%s",
                country,
                kind,
                lim,
                list_kwargs.get("sms_enabled", False),
                list_kwargs.get("voice_enabled", False),
            )

            candidates = getattr(apn, kind).list(**list_kwargs)

            filtered: list[Any] = []
            for candidate in candidates:
                voice_ok, sms_ok = _has_voice_and_sms(candidate)
                if require_sms_capability and not sms_ok:
                    logger.info(
                        "[cyan]Twilio[/cyan] ignore candidat sans SMS country=%s type=%s number=%s caps=%s",
                        country,
                        kind,
                        mask_phone(getattr(candidate, "phone_number", "")),
                        getattr(candidate, "capabilities", {}),
                    )
                    continue

                if require_voice_capability and not voice_ok:
                    logger.info(
                        "[cyan]Twilio[/cyan] ignore candidat sans VOICE country=%s type=%s number=%s caps=%s",
                        country,
                        kind,
                        mask_phone(getattr(candidate, "phone_number", "")),
                        getattr(candidate, "capabilities", {}),
                    )
                    continue

                filtered.append(candidate)

            return filtered

        requested = (number_type or "mobile").strip().lower()
        effective = "local" if requested == "national" else requested
        if effective not in ("mobile", "local"):
            raise RuntimeError(
                f"Type de numéro invalide: {number_type!r} (attendu mobile/local/national)"
            )

        logger.info(
            "[cyan]Twilio[/cyan] lookup available numbers country=%s requested=%s effective=%s limit=%s require_sms=%s require_voice=%s",
            country,
            requested,
            effective,
            max(1, int(candidates_limit or 10)),
            require_sms_capability,
            require_voice_capability,
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
                "sms_url": settings.MESSAGING_WEBHOOK_URL,
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
        require_voice_capability: bool = True,
    ) -> list[str]:
        country = (country or "").upper().strip()
        requested = (number_type or "mobile").strip().lower()
        stored_type = "local" if requested == "national" else requested
        if stored_type not in ("mobile", "local"):
            stored_type = "mobile"

        qty = max(1, int(batch_size or 1))

        logger.info(
            "[magenta]POOL[/magenta] fill start country=%s requested_qty=%s requested_type=%s stored_type=%s require_sms=%s require_voice=%s",
            country,
            qty,
            requested,
            stored_type,
            require_sms_capability,
            require_voice_capability,
        )

        logger.info(
            "[magenta]POOL[/magenta] fill params candidates_limit=%s",
            candidates_limit,
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
                    require_voice_capability=require_voice_capability,
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
        require_voice_capability: bool = True,
    ) -> list[str]:
        return cls._fill_pool(
            country,
            batch_size,
            number_type=number_type,
            candidates_limit=candidates_limit,
            require_sms_capability=require_sms_capability,
            require_voice_capability=require_voice_capability,
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
    def purge_pool_without_sms_capability(cls) -> dict[str, object]:
        """Supprime du pool (et de Twilio) les numéros sans capacité SMS."""

        records = PoolsRepository.list_all()

        checked = 0
        kept: list[str] = []
        released: list[str] = []
        removed_from_pool: list[str] = []
        missing_on_twilio: list[str] = []
        errors: list[dict[str, str]] = []

        logger.info(
            "[magenta]POOL[/magenta] purge des numéros sans SMS start records=%s",
            len(records),
        )

        for rec in records:
            phone = cls._normalize_phone_number(rec.get("phone_number"))
            if not phone:
                continue

            checked += 1
            try:
                incoming = twilio.incoming_phone_numbers.list(phone_number=phone, limit=1)
                if not incoming:
                    logger.warning(
                        "[magenta]POOL[/magenta] numéro introuvable côté Twilio => ignoré",
                        extra={"phone": mask_phone(phone)},
                    )
                    missing_on_twilio.append(phone)
                    continue

                candidate = incoming[0]
                capabilities = getattr(candidate, "capabilities", {}) or {}
                has_sms = bool(capabilities.get("sms")) or bool(
                    getattr(candidate, "sms_enabled", False)
                )

                if has_sms:
                    kept.append(phone)
                    logger.info(
                        "[magenta]POOL[/magenta] conservation du numéro (SMS OK)",
                        extra={"phone": mask_phone(phone)},
                    )
                    continue

                logger.info(
                    "[magenta]POOL[/magenta] numéro sans SMS -> suppression",
                    extra={"phone": mask_phone(phone)},
                )

                removed = PoolsRepository.remove_number(phone)
                if removed:
                    removed_from_pool.append(phone)
                else:
                    errors.append({"phone_number": phone, "err": "Suppression pool échouée"})

                try:
                    candidate.delete()
                    released.append(phone)
                    logger.info(
                        "[cyan]Twilio[/cyan] numéro libéré côté Twilio",
                        extra={"phone": mask_phone(phone)},
                    )
                except Exception as exc:  # pragma: no cover - dépendances externes
                    errors.append({"phone_number": phone, "err": str(exc)})
                    logger.exception(
                        "[red]Twilio[/red] échec de libération du numéro", exc_info=exc
                    )

            except Exception as exc:  # pragma: no cover - dépendances externes
                errors.append({"phone_number": phone, "err": str(exc)})
                logger.exception(
                    "[magenta]POOL[/magenta] purge: erreur inattendue", exc_info=exc
                )

        logger.info(
            "[magenta]POOL[/magenta] purge terminée checked=%s kept=%s removed=%s released=%s missing_twilio=%s errors=%s",
            checked,
            len(kept),
            len(removed_from_pool),
            len(released),
            len(missing_on_twilio),
            len(errors),
        )

        return {
            "checked": checked,
            "kept_sms_capable": kept,
            "removed_from_pool": removed_from_pool,
            "released_on_twilio": released,
            "missing_on_twilio": missing_on_twilio,
            "errors": errors,
            "ts": datetime.utcnow().isoformat(),
        }

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
        friendly_name: str | None = None,
    ) -> str:
        country_iso = (country or "").strip().upper()
        requested_type = (number_type or settings.TWILIO_NUMBER_TYPE).strip().lower()
        friendly = friendly_name or attribution_to_client_name or f"Client-{client_id}"

        if requested_type == "national":
            requested_type = "local"

        if requested_type not in {"mobile", "local"}:
            raise ValueError(f"number_type invalide: {requested_type}")

        if not country_iso:
            raise ValueError("country_iso obligatoire pour l'attribution depuis le pool")

        logger.info(
            "[magenta]POOL[/magenta] assign start client_id=%s country=%s type=%s friendly=%s",
            client_id,
            country_iso,
            requested_type,
            friendly,
        )

        reservation = PoolsRepository.reserve_first_available(
            country_iso=country_iso,
            number_type=requested_type,
            client_id=client_id,
        )

        if not reservation:
            raise RuntimeError(f"Aucun numéro disponible pour le pays {country_iso} (type {requested_type})")

        phone = cls._normalize_phone_number(reservation.get("phone_number"))
        if not phone:
            raise RuntimeError("Numéro réservé invalide (pool)")

        row_index = int(reservation.get("row_index", 0))
        reserved_token = str(reservation.get("reserved_token", ""))
        reserved_at = str(reservation.get("reserved_at", ""))

        try:
            incoming = twilio.incoming_phone_numbers.list(phone_number=phone, limit=1)
            if incoming:
                incoming[0].update(friendly_name=friendly)
                logger.info(
                    "[cyan]Twilio[/cyan] friendly_name mis à jour pour %s",
                    mask_phone(phone),
                )
            else:
                logger.warning(
                    "[cyan]Twilio[/cyan] numéro %s introuvable pour mise à jour du friendly_name",
                    mask_phone(phone),
                )
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.warning(
                "[cyan]Twilio[/cyan] impossible de mettre à jour le friendly_name %s: %s",
                mask_phone(phone),
                exc,
            )

        finalized = PoolsRepository.finalize_assignment_keep_friendly(
            row_index=row_index,
            reserved_token=reserved_token,
            reserved_at=reserved_at,
            reserved_by_client_id=client_id,
            attribution_to_client_name=attribution_to_client_name,
        )

        if not finalized:
            raise RuntimeError("Conflit lors de la finalisation de l'attribution (pool)")

        cls.ensure_voice_webhook(phone)
        cls.ensure_messaging_webhook(phone)

        logger.info(
            "[magenta]POOL[/magenta] assign success client_id=%s country=%s type=%s phone=%s",
            client_id,
            country_iso,
            requested_type,
            mask_phone(phone),
        )

        return phone
