# demo/cli.py
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from dotenv import find_dotenv, load_dotenv
import httpx

# ✅ New: Rich + redaction logging for CLI
from app.cli_logging import configure_cli_logging
from app.validator import (
    ValidationIssue,
    int_strict,
    phone_e164_strict,
    email_strict,
    name_strict,
    iso_country_strict,
    number_type_strict,
)

DEFAULT_NUMBER_TYPE = os.getenv("TWILIO_NUMBER_TYPE", "national").lower()

# --- Optional deps (LIVE + TwiML) ---
try:
    from twilio.rest import Client as TwilioRestClient
    from twilio.base.exceptions import TwilioRestException
    from twilio.twiml.voice_response import VoiceResponse, Dial
except Exception:  # pragma: no cover
    TwilioRestClient = None  # type: ignore
    TwilioRestException = Exception  # type: ignore
    VoiceResponse = None  # type: ignore
    Dial = None  # type: ignore

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:  # pragma: no cover
    gspread = None  # type: ignore
    Credentials = None  # type: ignore


# =========================
# Errors (fine-grained)
# =========================
class CLIError(Exception):
    exit_code = 4

    def __init__(self, message: str, *, exit_code: Optional[int] = None, details: Optional[dict] = None):
        super().__init__(message)
        if exit_code is not None:
            self.exit_code = exit_code
        self.details = details or {}


class ValidationError(CLIError):
    exit_code = 2


class NotFoundError(CLIError):
    exit_code = 2


class ExternalServiceError(CLIError):
    exit_code = 3


class ConfigError(CLIError):
    exit_code = 2


def v_or_raise(fn, *args, **kwargs):
    """Wrap app.validator -> CLI ValidationError"""
    try:
        return fn(*args, **kwargs)
    except ValidationIssue as e:
        raise ValidationError(str(e), details={"field": e.field, "value": e.value}) from e


# =========================
# Console helpers
# =========================
ANSI_COLORS = {"red": "\033[91m", "reset": "\033[0m"}


def colorize(text: str, color: str) -> str:
    if not sys.stdout.isatty():
        return text
    prefix = ANSI_COLORS.get(color, "")
    suffix = ANSI_COLORS["reset"] if prefix else ""
    return f"{prefix}{text}{suffix}"


# =========================
# Domain model
# =========================
PHONE_DIGITS_RE = re.compile(r"^[0-9]{8,15}$")  # 8 à 15 chiffres, sans signe +

COUNTRY_PHONE_RULES: dict[str, int] = {
    "33": 9,   # France
    "351": 9,  # Portugal
}

COUNTRY_PHONE_EXAMPLES: dict[str, str] = {
    "33": "33601020304",
    "351": "351609875678",
}

POOL_FIXTURES_DEFAULT = Path(__file__).parent / "fixtures" / "pools.json"


@dataclasses.dataclass
class DemoClient:
    client_id: int
    client_name: str
    client_mail: str
    client_real_phone: str
    client_proxy_number: Optional[str]
    client_iso_residency: str
    client_country_code: str


def _detect_country_code(phone_digits: str) -> Optional[str]:
    matches = [cc for cc in COUNTRY_PHONE_RULES if phone_digits.startswith(cc)]
    if not matches:
        return None
    return max(matches, key=len)


def _validate_country_specific(phone_digits: str, *, label: str) -> None:
    country_code = _detect_country_code(phone_digits)
    if not country_code:
        return

    subscriber_length = len(phone_digits) - len(country_code)
    expected_subscriber_length = COUNTRY_PHONE_RULES[country_code]
    if subscriber_length != expected_subscriber_length:
        example = COUNTRY_PHONE_EXAMPLES.get(country_code)
        example_hint = f" (ex : {example})" if example else ""
        raise ValidationError(
            f"{label} invalide pour l'indicatif {country_code} : indiquez {expected_subscriber_length} chiffres après l'indicatif{example_hint}.",
            details={
                "value": phone_digits,
                "country_code": country_code,
                "expected_subscriber_length": expected_subscriber_length,
            },
        )


def normalize_phone_digits(phone: str | int, *, label: str = "numéro") -> str:
    # Strict E.164 only
    return v_or_raise(phone_e164_strict, phone, field=label)


def phone_digits_to_str(phone: int | str, *, label: str = "numéro") -> str:
    return normalize_phone_digits(phone, label=label)


def phone_digits_to_e164(phone: int | str, *, label: str = "numéro") -> str:
    # déjà E.164
    return normalize_phone_digits(phone, label=label)


def extract_country_code_simple(phone: int | str) -> str:
    digits = phone_digits_to_str(phone)
    detected = _detect_country_code(digits.lstrip("+"))
    if detected:
        return detected
    return digits.lstrip("+")[:2]


# =========================
# TwiML helpers
# =========================
def twiml_dial(*, proxy_number: str, real_number: str) -> str:
    if VoiceResponse is None or Dial is None:
        raise ExternalServiceError("Dépendance Twilio TwiML manquante. Installe 'twilio'.")
    resp = VoiceResponse()
    dial = Dial(callerId=proxy_number)
    dial.number(real_number)
    resp.append(dial)
    return str(resp)


def twiml_block(message: str) -> str:
    if VoiceResponse is None:
        raise ExternalServiceError("Dépendance Twilio TwiML manquante. Installe 'twilio'.")
    resp = VoiceResponse()
    resp.say(message)
    return str(resp)


# =========================
# Storage interfaces
# =========================
class ClientStore:
    def get_by_id(self, client_id: str | int) -> Optional[DemoClient]:
        raise NotImplementedError

    def get_by_proxy(self, proxy_number: str | int) -> Optional[DemoClient]:
        raise NotImplementedError

    def save(self, client: DemoClient) -> None:
        raise NotImplementedError

    def list_all(self) -> list[DemoClient]:
        raise NotImplementedError

    def max_client_id(self) -> int:
        raise NotImplementedError


class PoolStore:
    def list_available(self, country_iso: str, number_type: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    def provision(
        self, country_iso: str, batch_size: int, *, friendly_prefix: str, number_type: str = "mobile"
    ) -> list[str]:
        raise NotImplementedError

    def assign_number(
        self, country_iso: str, friendly_name: str, client_name: str, client_id: int, *, number_type: str = "mobile"
    ) -> str:
        raise NotImplementedError

    def sync_with_provider(
        self, *, apply: bool = True, twilio_numbers: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        raise NotImplementedError

    # ✅ NEW: webhook fix on pool numbers
    def fix_voice_webhooks(
        self,
        *,
        dry_run: bool = True,
        only_country: str | None = None,
        only_status: str | None = None,
        fix_sms: bool = True,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def purge_without_sms_capability(self, *, auto_confirm: bool = False) -> dict[str, Any]:
        raise NotImplementedError


class RenderAPIClient:
    def __init__(self, base_url: str, token: str | None, logger: logging.Logger):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.logger = logger

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            resp = httpx.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=self._headers(),
                timeout=20,
            )
        except Exception as exc:  # pragma: no cover - réseau
            self.logger.error("[red]RENDER[/red] appel %s %s échoué: %s", method, url, exc)
            raise ExternalServiceError("Appel Render impossible (réseau)", details={"url": url}) from exc

        if resp.status_code >= 400:
            detail = resp.text
            try:
                detail_json = resp.json()
                detail = detail_json.get("detail", detail)
            except Exception:
                detail_json = None
            self.logger.error(
                "[red]RENDER[/red] %s %s -> %s : %s", method, url, resp.status_code, detail
            )
            raise ExternalServiceError(
                f"API Render renvoie {resp.status_code}",
                details={"url": url, "status": resp.status_code, "detail": detail_json or detail},
            )

        try:
            return resp.json()
        except Exception as exc:  # pragma: no cover
            raise ExternalServiceError("Réponse Render invalide (JSON)", details={"url": url}) from exc

    # --- Clients ---
    def create_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/clients", json_body=payload)

    def update_client(self, client_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", f"/clients/{client_id}", json_body=payload)

    def get_client(self, client_id: str) -> dict[str, Any]:
        return self._request("GET", f"/clients/{client_id}")

    def get_client_by_proxy(self, proxy: str) -> dict[str, Any]:
        return self._request("GET", f"/clients/by-proxy/{proxy}")

    def get_next_client_id(self) -> dict[str, Any]:
        return self._request("GET", "/clients/next-id")

    # --- Orders ---
    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/orders", json_body=payload)

    # --- Pool ---
    def pool_available(self, country_iso: str, number_type: str | None) -> dict[str, Any]:
        return self._request(
            "GET",
            "/pool/available",
            params={"country_iso": country_iso, "number_type": number_type},
        )

    def pool_provision(self, country_iso: str, batch_size: int, number_type: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/pool/provision",
            json_body={"country_iso": country_iso, "batch_size": batch_size, "number_type": number_type},
        )

    def pool_assign(
        self, client_id: int, country_iso: str, client_name: str, number_type: str, friendly_name: str | None
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/pool/assign",
            json_body={
                "client_id": client_id,
                "country_iso": country_iso,
                "client_name": client_name,
                "number_type": number_type,
                "friendly_name": friendly_name,
            },
        )

    def pool_sync(self, apply: bool = True) -> dict[str, Any]:
        return self._request("POST", "/pool/sync", json_body={"apply": bool(apply)})

    def pool_fix_webhooks(
        self,
        *,
        dry_run: bool = True,
        only_country: str | None = None,
        only_status: str | None = None,
        fix_sms: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/pool/fix-webhooks",
            json_body={
                "dry_run": dry_run,
                "only_country": only_country,
                "only_status": only_status,
                "fix_sms": fix_sms,
            },
        )

    def pool_purge_without_sms(self) -> dict[str, Any]:
        return self._request("POST", "/pool/purge-sans-sms")


class MockJsonStore(ClientStore):
    def __init__(self, path: Path, logger: logging.Logger):
        self.path = path
        self.logger = logger
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def _load(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ExternalServiceError("Fixtures JSON corrompues.", details={"path": str(self.path)}) from e

    def _dump(self, rows: list[dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_by_id(self, client_id: str | int) -> Optional[DemoClient]:
        target = parse_client_id(client_id)
        for r in self._load():
            try:
                if parse_client_id(r.get("client_id")) == target:
                    real_phone = normalize_phone_digits(r.get("client_real_phone", ""), label="client_real_phone")
                    proxy_raw = r.get("client_proxy_number", "")
                    proxy_number = normalize_phone_digits(proxy_raw, label="client_proxy_number") if str(proxy_raw).strip() else None
                    return DemoClient(
                        client_id=target,
                        client_name=str(r.get("client_name", "")),
                        client_mail=str(r.get("client_mail", "")),
                        client_real_phone=real_phone,
                        client_proxy_number=proxy_number,
                        client_iso_residency=str(r.get("client_iso_residency", "")),
                        client_country_code=str(r.get("client_country_code", "")),
                    )
            except ValidationError:
                continue
        return None

    def get_by_proxy(self, proxy_number: str) -> Optional[DemoClient]:
        try:
            p = normalize_phone_digits(proxy_number, label="proxy")
        except ValidationError:
            return None
        for r in self._load():
            try:
                proxy_raw = r.get("client_proxy_number", "")
                if not str(proxy_raw).strip():
                    continue
                proxy_val = normalize_phone_digits(proxy_raw, label="client_proxy_number")
            except ValidationError:
                continue
            if proxy_val == p:
                try:
                    cid = parse_client_id(r.get("client_id"))
                    real_phone = normalize_phone_digits(r.get("client_real_phone", ""), label="client_real_phone")
                except ValidationError:
                    continue
                return DemoClient(
                    client_id=cid,
                    client_name=str(r.get("client_name", "")),
                    client_mail=str(r.get("client_mail", "")),
                    client_real_phone=real_phone,
                    client_proxy_number=proxy_val,
                    client_iso_residency=str(r.get("client_iso_residency", "")),
                    client_country_code=str(r.get("client_country_code", "")),
                )
        return None

    def save(self, client: DemoClient) -> None:
        rows = self._load()
        preserved_iso = None
        preserved_cc = None

        filtered: list[dict[str, Any]] = []
        for r in rows:
            try:
                if parse_client_id(r.get("client_id", 0)) == client.client_id:
                    preserved_iso = r.get("client_iso_residency")
                    preserved_cc = r.get("client_country_code")
                    continue
            except ValidationError:
                continue
            filtered.append(r)

        try:
            real_phone = normalize_phone_digits(client.client_real_phone, label="client_real_phone")
        except ValidationError:
            raw_real = str(client.client_real_phone).lstrip("+")
            real_phone = normalize_phone_digits(f"+{raw_real}", label="client_real_phone")
            self.logger.warning(
                "[cyan]MOCK[/cyan] client_real_phone sans préfixe '+', normalisé en %s", real_phone
            )

        proxy_phone = ""
        if client.client_proxy_number is not None and str(client.client_proxy_number).strip():
            try:
                proxy_phone = normalize_phone_digits(client.client_proxy_number, label="client_proxy_number")
            except ValidationError:
                raw_proxy = str(client.client_proxy_number).lstrip("+")
                proxy_phone = normalize_phone_digits(f"+{raw_proxy}", label="client_proxy_number")
                self.logger.warning(
                    "[cyan]MOCK[/cyan] client_proxy_number sans préfixe '+', normalisé en %s", proxy_phone
                )

        new_row: dict[str, Any] = {
            "client_id": client.client_id,
            "client_name": client.client_name,
            "client_mail": client.client_mail,
            "client_real_phone": real_phone,
            "client_proxy_number": proxy_phone,
            "client_iso_residency": client.client_iso_residency or preserved_iso or "",
            "client_country_code": client.client_country_code or preserved_cc or "",
        }

        filtered.append(new_row)
        self._dump(filtered)

    def list_all(self) -> list[DemoClient]:
        clients: list[DemoClient] = []
        for r in self._load():
            try:
                proxy_val = None
                proxy_raw = r.get("client_proxy_number", "")
                if str(proxy_raw).strip():
                    proxy_val = normalize_phone_digits(proxy_raw, label="client_proxy_number")
                clients.append(
                    DemoClient(
                        client_id=parse_client_id(r.get("client_id", 0)),
                        client_name=str(r.get("client_name", "")),
                        client_mail=str(r.get("client_mail", "")),
                        client_real_phone=normalize_phone_digits(r.get("client_real_phone", ""), label="client_real_phone"),
                        client_proxy_number=proxy_val,
                        client_iso_residency=str(r.get("client_iso_residency", "")),
                        client_country_code=str(r.get("client_country_code", "")),
                    )
                )
            except ValidationError:
                continue
        return clients

    def max_client_id(self) -> int:
        max_id = 0
        for r in self._load():
            try:
                cid = parse_client_id(r.get("client_id"))
            except ValidationError:
                continue
            max_id = max(max_id, cid)
        return max_id


class MockPoolStore(PoolStore):
    PREFIXES = {
        "mobile": {"FR": "+337990", "US": "+155520"},
        "local": {"FR": "+331900", "US": "+140820"},
    }

    def __init__(self, path: Path, logger: logging.Logger, *, default_batch: int = 2):
        self.path = path
        self.logger = logger
        self.default_batch = default_batch
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            seed = [
                {
                    "country_iso": "FR",
                    "phone_number": "+33799000001",
                    "status": "available",
                    "friendly_name": "Pool-FR-1",
                    "date_achat": dt.datetime.utcnow().isoformat(),
                    "date_attribution": "",
                    "attribution_to_client_name": "",
                    "number_type": "mobile",
                },
                {
                    "country_iso": "US",
                    "phone_number": "+15552000001",
                    "status": "available",
                    "friendly_name": "Pool-US-1",
                    "date_achat": dt.datetime.utcnow().isoformat(),
                    "date_attribution": "",
                    "attribution_to_client_name": "",
                    "number_type": "mobile",
                },
            ]
            self.path.write_text(json.dumps(seed, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ExternalServiceError("Fixtures pool corrompues.", details={"path": str(self.path)}) from exc

    def _dump(self, rows: list[dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    def _make_number(self, country_iso: str, index: int, number_type: str) -> str:
        prefixes_for_type = self.PREFIXES.get(number_type, {})
        prefix = prefixes_for_type.get(country_iso.upper(), f"+999{number_type.upper()}{country_iso.upper()}")
        return f"{prefix}{index:05d}"

    def list_available(self, country_iso: str, number_type: str | None = None) -> list[dict[str, Any]]:
        country = country_iso.upper()
        return [
            rec for rec in self._load()
            if rec.get("country_iso") == country and str(rec.get("status")).lower() == "available"
        ]

    def provision(
        self,
        country_iso: str,
        batch_size: int,
        *,
        friendly_prefix: str,
        number_type: str = "mobile",
    ) -> list[str]:
        country = country_iso.upper()
        rows = self._load()
        existing = [r for r in rows if r.get("country_iso") == country]
        start_idx = len(existing) + 1
        added: list[str] = []

        for offset in range(batch_size):
            num = self._make_number(country, start_idx + offset, number_type)
            rows.append(
                {
                    "country_iso": country,
                    "phone_number": num,
                    "status": "available",
                    "friendly_name": f"{friendly_prefix}-{offset + 1}",
                    "date_achat": dt.datetime.utcnow().isoformat(),
                    "date_attribution": "",
                    "attribution_to_client_name": "",
                    "number_type": number_type,
                }
            )
            added.append(num)

        self._dump(rows)
        self.logger.info("[magenta]POOL[/magenta] mock provisioned country=%s added=%s", country, len(added))
        return added

    def assign_number(
        self, country_iso: str, friendly_name: str, client_name: str, client_id: int, *, number_type: str = "mobile"
    ) -> str:
        country = country_iso.upper()
        rows = self._load()
        available_idx = None
        requested_type = (number_type or "mobile").lower()

        for idx, rec in enumerate(rows):
            if (
                rec.get("country_iso") == country
                and str(rec.get("status")).lower() == "available"
                and str(rec.get("number_type", "mobile")).lower() == requested_type
            ):
                available_idx = idx
                break

        if available_idx is None and requested_type == "mobile":
            for idx, rec in enumerate(rows):
                if (
                    rec.get("country_iso") == country
                    and str(rec.get("status")).lower() == "available"
                    and str(rec.get("number_type", "")).lower() == "local"
                ):
                    available_idx = idx
                    self.logger.info("[magenta]POOL[/magenta] mock fallback local country=%s", country)
                    break

        if available_idx is None:
            self.provision(country, self.default_batch, friendly_prefix=f"Pool-{country}", number_type=requested_type)
            rows = self._load()
            for idx, rec in enumerate(rows):
                if (
                    rec.get("country_iso") == country
                    and str(rec.get("status")).lower() == "available"
                    and str(rec.get("number_type", "mobile")).lower() == requested_type
                ):
                    available_idx = idx
                    break

        if available_idx is None and requested_type == "mobile":
            for idx, rec in enumerate(rows):
                if (
                    rec.get("country_iso") == country
                    and str(rec.get("status")).lower() == "available"
                    and str(rec.get("number_type", "")).lower() == "local"
                ):
                    available_idx = idx
                    self.logger.info("[magenta]POOL[/magenta] mock fallback local after provision country=%s", country)
                    break

        if available_idx is None:
            raise ExternalServiceError(f"Aucun numéro disponible pour le pays {country} (mock)")

        target = rows[available_idx]
        target.update(
            {
                "status": "assigned",
                "friendly_name": friendly_name,
                "date_attribution": dt.datetime.utcnow().isoformat(),
                "attribution_to_client_name": client_name,
            }
        )
        rows[available_idx] = target
        self._dump(rows)
        return str(target.get("phone_number"))

    def sync_with_provider(self, *, apply: bool = True, twilio_numbers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        raise ValidationError("Synchronisation disponible uniquement en mode LIVE.")

    def fix_voice_webhooks(
        self,
        *,
        dry_run: bool = True,
        only_country: str | None = None,
        only_status: str | None = None,
    ) -> dict[str, Any]:
        raise ValidationError("Correction des webhooks disponible uniquement en mode LIVE.")

    def purge_without_sms_capability(self, *, auto_confirm: bool = False) -> dict[str, Any]:
        raise ValidationError("Purge SMS disponible uniquement en mode LIVE.")


class LivePoolStore(PoolStore):
    def __init__(self, logger: logging.Logger, *, default_batch: int = 2):
        self.logger = logger
        self.default_batch = default_batch

    def list_available(self, country_iso: str, number_type: str | None = None) -> list[dict[str, Any]]:
        from integrations.twilio_client import TwilioClient
        return TwilioClient.list_available(country_iso.upper(), number_type=number_type)

    def provision(self, country_iso: str, batch_size: int, *, friendly_prefix: str, number_type: str = "mobile") -> list[str]:
        """
        ✅ IMPORTANT:
        Retourne UNIQUEMENT les numéros achetés pendant CETTE exécution,
        pas tout le pool disponible.
        """
        from integrations.twilio_client import TwilioClient

        purchased_now = TwilioClient.fill_pool(country_iso.upper(), batch_size, number_type=number_type)
        self.logger.info("[magenta]POOL[/magenta] live provisioned country=%s purchased_now=%s", country_iso.upper(), len(purchased_now))
        return purchased_now

    def assign_number(self, country_iso: str, friendly_name: str, client_name: str, client_id: int, *, number_type: str = "mobile") -> str:
        from integrations.twilio_client import TwilioClient
        return TwilioClient.assign_number_from_pool(
            client_id=client_id,
            country=country_iso.upper(),
            attribution_to_client_name=client_name,
            number_type=number_type,
        )

    def sync_with_provider(self, *, apply: bool = True, twilio_numbers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        from integrations.twilio_client import TwilioClient
        sync_result = TwilioClient.sync_twilio_numbers_with_sheet(apply=apply, twilio_numbers=twilio_numbers)
        self.logger.info(
            "[magenta]POOL[/magenta] sync done added=%s missing=%s applied=%s",
            len(sync_result.get("added_numbers", [])),
            len(sync_result.get("missing_numbers", [])),
            apply,
        )
        return sync_result

    def fix_voice_webhooks(
        self,
        *,
        dry_run: bool = True,
        only_country: str | None = None,
        only_status: str | None = None,
        fix_sms: bool = True,
    ) -> dict[str, Any]:
        from integrations.twilio_client import TwilioClient
        result = TwilioClient.fix_pool_voice_webhooks(
            dry_run=dry_run,
            only_country=only_country,
            only_status=only_status,
            fix_sms=fix_sms,
        )
        self.logger.info(
            "[magenta]POOL[/magenta] fix_webhooks done checked=%s need_fix_voice=%s fixed_voice=%s need_fix_sms=%s fixed_sms=%s dry_run=%s",
            result.get("checked", 0),
            len(result.get("need_fix_voice", []) or []),
            len(result.get("fixed_voice", []) or []),
            len(result.get("need_fix_sms", []) or []),
            len(result.get("fixed_sms", []) or []),
            bool(result.get("dry_run", False)),
        )
        return result

    def purge_without_sms_capability(self, *, auto_confirm: bool = False) -> dict[str, Any]:
        from integrations.twilio_client import TwilioClient

        if not auto_confirm:
            raise ValidationError("Confirmation requise pour purger les numéros sans SMS (auto_confirm=False).")

        result = TwilioClient.purge_pool_without_sms_capability()
        self.logger.info(
            "[magenta]POOL[/magenta] purge sans SMS réalisée checked=%s kept=%s removed=%s released=%s missing=%s errors=%s",
            result.get("checked", 0),
            len(result.get("kept_sms_capable", []) or []),
            len(result.get("removed_from_pool", []) or []),
            len(result.get("released_on_twilio", []) or []),
            len(result.get("missing_on_twilio", []) or []),
            len(result.get("errors", []) or []),
        )
        return result


class RenderClientStore(ClientStore):
    def __init__(self, api: RenderAPIClient, logger: logging.Logger):
        self.api = api
        self.logger = logger
        self._cache: dict[str, DemoClient] = {}

    def _normalize_from_render(self, value: str | int, *, label: str) -> str:
        try:
            return normalize_phone_digits(value, label=label)
        except ValidationError:
            compact = re.sub(r"[^0-9+]", "", str(value or "").strip())
            if compact and not compact.startswith("+"):
                candidate = f"+{compact}"
                try:
                    normalized = normalize_phone_digits(candidate, label=label)
                    self.logger.warning(
                        "[yellow]RENDER[/yellow] %s sans préfixe '+', normalisé en %s",
                        label,
                        normalized,
                    )
                    return normalized
                except ValidationError:
                    pass
            raise

    def _to_demo_client(self, payload: dict[str, Any]) -> DemoClient:
        return DemoClient(
            client_id=parse_client_id(payload.get("client_id")),
            client_name=str(payload.get("client_name", "")),
            client_mail=str(payload.get("client_mail", "")),
            client_real_phone=self._normalize_from_render(payload.get("client_real_phone", ""), label="client_real_phone"),
            client_proxy_number=self._normalize_from_render(payload.get("client_proxy_number", ""), label="client_proxy_number") if payload.get("client_proxy_number") else None,
            client_iso_residency=str(payload.get("client_iso_residency", "")),
            client_country_code=str(payload.get("client_country_code", "")),
        )

    def get_by_id(self, client_id: str | int) -> Optional[DemoClient]:
        cid = str(parse_client_id(client_id))
        if cid in self._cache:
            return self._cache[cid]
        try:
            data = self.api.get_client(cid)
        except ExternalServiceError as exc:
            status = exc.details.get("status") if isinstance(exc, ExternalServiceError) else None
            if status == 404:
                self.logger.warning(
                    "[red]RENDER[/red] client %s introuvable (404).", cid
                )
                return None
            raise
        client = self._to_demo_client(data)
        self._cache[cid] = client
        return client

    def get_by_proxy(self, proxy_number: str | int) -> Optional[DemoClient]:
        proxy = normalize_phone_digits(proxy_number, label="proxy")
        try:
            data = self.api.get_client_by_proxy(proxy)
        except ExternalServiceError as exc:
            status = exc.details.get("status") if isinstance(exc, ExternalServiceError) else None
            if status == 404:
                self.logger.warning(
                    "[red]RENDER[/red] client introuvable pour le proxy %s (404).",
                    proxy,
                )
                return None
            raise
        client = self._to_demo_client(data)
        self._cache[str(client.client_id)] = client
        return client

    def save(self, client: DemoClient) -> None:
        payload = {
            "client_id": str(client.client_id),
            "client_name": client.client_name,
            "client_mail": client.client_mail,
            "client_real_phone": client.client_real_phone,
            "client_iso_residency": client.client_iso_residency,
            "client_proxy_number": client.client_proxy_number,
            "client_country_code": client.client_country_code,
        }
        cid = str(client.client_id)
        try:
            if cid in self._cache:
                data = self.api.update_client(cid, payload)
            else:
                data = self.api.create_client(payload)
        except ExternalServiceError as exc:
            status = exc.details.get("status") if isinstance(exc, ExternalServiceError) else None
            if status == 400:
                self.logger.info(
                    "[yellow]RENDER[/yellow] client %s existant, bascule en mise à jour.",
                    cid,
                )
                data = self.api.update_client(cid, payload)
            else:
                raise

        new_client = self._to_demo_client({**payload, **data})
        self._cache[str(new_client.client_id)] = new_client

    def list_all(self) -> list[DemoClient]:
        raise ExternalServiceError("Listing complet non exposé par l'API Render")

    def max_client_id(self) -> int:
        data = self.api.get_next_client_id()
        next_id = data.get("next_client_id") if isinstance(data, dict) else None
        if next_id is None:
            raise ExternalServiceError("Réponse Render invalide: next_client_id manquant")

        try:
            next_id_int = int(next_id)
        except (TypeError, ValueError) as exc:
            raise ExternalServiceError("next_client_id non numérique dans la réponse Render") from exc

        return max(0, next_id_int - 1)


class RenderPoolStore(PoolStore):
    def __init__(self, api: RenderAPIClient, logger: logging.Logger):
        self.api = api
        self.logger = logger

    def list_available(self, country_iso: str, number_type: str | None = None) -> list[dict[str, Any]]:
        data = self.api.pool_available(country_iso, number_type)
        available = data.get("available", []) if isinstance(data, dict) else []
        self.logger.info(
            "[magenta]POOL[/magenta] render available country=%s type=%s count=%s",
            country_iso,
            number_type or "all",
            len(available),
        )
        return available

    def provision(self, country_iso: str, batch_size: int, *, friendly_prefix: str, number_type: str = "mobile") -> list[str]:
        data = self.api.pool_provision(country_iso, batch_size, number_type)
        purchased = data.get("purchased_now", []) if isinstance(data, dict) else []
        self.logger.info(
            "[magenta]POOL[/magenta] render provision country=%s type=%s purchased=%s",
            country_iso,
            number_type,
            len(purchased),
        )
        return [str(p) for p in purchased]

    def assign_number(self, country_iso: str, friendly_name: str, client_name: str, client_id: int, *, number_type: str = "mobile") -> str:
        data = self.api.pool_assign(client_id, country_iso, client_name, number_type, friendly_name)
        proxy = data.get("proxy") if isinstance(data, dict) else None
        if not proxy:
            raise ExternalServiceError("Réponse Render invalide: proxy manquant")
        return str(proxy)

    def sync_with_provider(self, *, apply: bool = True, twilio_numbers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return self.api.pool_sync(apply=apply)

    def fix_voice_webhooks(
        self,
        *,
        dry_run: bool = True,
        only_country: str | None = None,
        only_status: str | None = None,
        fix_sms: bool = True,
    ) -> dict[str, Any]:
        return self.api.pool_fix_webhooks(
            dry_run=dry_run,
            only_country=only_country,
            only_status=only_status,
            fix_sms=fix_sms,
        )

    def purge_without_sms_capability(self, *, auto_confirm: bool = False) -> dict[str, Any]:
        if not auto_confirm:
            raise ValidationError("Confirmation requise pour purger les numéros sans SMS (auto_confirm=False).")
        data = self.api.pool_purge_without_sms()
        self.logger.info(
            "[magenta]POOL[/magenta] render purge sans SMS checked=%s kept=%s removed=%s released=%s",
            data.get("checked", 0),
            len(data.get("kept_sms_capable", []) or []),
            len(data.get("removed_from_pool", []) or []),
            len(data.get("released_on_twilio", []) or []),
        )
        return data


class SheetsStore(ClientStore):
    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    HEADERS = [
        "client_id",
        "client_name",
        "client_mail",
        "client_real_phone",
        "client_proxy_number",
        "client_iso_residency",
        "client_country_code",
    ]

    def __init__(self, *, sheet_name: str, service_account_file: str, worksheet: str, logger: logging.Logger):
        if gspread is None or Credentials is None:
            raise ExternalServiceError("Dépendance Google Sheets manquante. Installe 'gspread' et 'google-auth'.")
        self.logger = logger
        try:
            creds = Credentials.from_service_account_file(service_account_file, scopes=self.SCOPES)
            gc = gspread.authorize(creds)
            sh = gc.open(sheet_name)
            self.ws = sh.worksheet(worksheet)
        except FileNotFoundError as e:
            raise ConfigError("Fichier service account introuvable.", details={"path": service_account_file}) from e
        except Exception as e:
            raise ExternalServiceError("Impossible d’ouvrir Google Sheet / worksheet.", details={"sheet": sheet_name, "worksheet": worksheet}) from e

        self._ensure_headers()

    def _ensure_headers(self) -> None:
        try:
            first_row = [str(h).strip() for h in self.ws.row_values(1)]
            non_empty = [h for h in first_row if h]

            if len(non_empty) == 0:
                self.ws.insert_row(self.HEADERS, 1)
                return

            missing = [h for h in self.HEADERS if h not in non_empty]
            if missing:
                raise ConfigError(
                    "En-têtes Google Sheets incomplètes pour la démo LIVE.",
                    details={"missing": missing, "found": non_empty},
                )

            extra = [h for h in non_empty if h not in self.HEADERS]
            if extra or non_empty != self.HEADERS:
                suffix = f" Colonnes supplémentaires détectées: {', '.join(extra)}." if extra else ""
                self.logger.info("En-têtes Sheets conservées (ordre différent ou colonnes supplémentaires détectées)." + suffix)
        except Exception as e:
            raise ExternalServiceError("Erreur lecture/initialisation headers Sheets.") from e

    def _all_records(self) -> list[dict[str, Any]]:
        try:
            return self.ws.get_all_records()
        except Exception as e:
            raise ExternalServiceError("Erreur lecture Sheets (get_all_records).") from e

    def get_by_id(self, client_id: str | int) -> Optional[DemoClient]:
        target = parse_client_id(client_id)
        for r in self._all_records():
            try:
                if parse_client_id(r.get("client_id", "")) == target:
                    real_phone = normalize_phone_digits(r.get("client_real_phone", ""), label="client_real_phone")
                    proxy_raw = r.get("client_proxy_number", "")
                    proxy_number = normalize_phone_digits(proxy_raw, label="client_proxy_number") if str(proxy_raw).strip() else None
                    return DemoClient(
                        client_id=target,
                        client_name=str(r.get("client_name", "")),
                        client_mail=str(r.get("client_mail", "")),
                        client_real_phone=real_phone,
                        client_proxy_number=proxy_number,
                        client_iso_residency=str(r.get("client_iso_residency", "")),
                        client_country_code=str(r.get("client_country_code", "")),
                    )
            except ValidationError:
                continue
        return None

    def get_by_proxy(self, proxy_number: str) -> Optional[DemoClient]:
        try:
            p = normalize_phone_digits(proxy_number, label="proxy")
        except ValidationError:
            return None
        for r in self._all_records():
            try:
                proxy_raw = r.get("client_proxy_number", "")
                if not str(proxy_raw).strip():
                    continue
                proxy_val = normalize_phone_digits(proxy_raw, label="client_proxy_number")
            except ValidationError:
                continue
            if proxy_val == p:
                try:
                    cid = parse_client_id(r.get("client_id", ""))
                    real_phone = normalize_phone_digits(r.get("client_real_phone", ""), label="client_real_phone")
                except ValidationError:
                    continue
                return DemoClient(
                    client_id=cid,
                    client_name=str(r.get("client_name", "")),
                    client_mail=str(r.get("client_mail", "")),
                    client_real_phone=real_phone,
                    client_proxy_number=proxy_val,
                    client_iso_residency=str(r.get("client_iso_residency", "")),
                    client_country_code=str(r.get("client_country_code", "")),
                )
        return None

    def save(self, client: DemoClient) -> None:
        try:
            records = self._all_records()
            for i, r in enumerate(records, start=2):
                try:
                    cid = parse_client_id(r.get("client_id", ""))
                except ValidationError:
                    continue
                if cid == client.client_id:
                    self.ws.update(
                        f"A{i}:E{i}",
                        [[client.client_id, client.client_name, client.client_mail, client.client_real_phone, client.client_proxy_number or ""]],
                    )
                    return

            self.ws.append_row([client.client_id, client.client_name, client.client_mail, client.client_real_phone, client.client_proxy_number or ""])
        except Exception as e:
            raise ExternalServiceError("Erreur écriture Sheets (save).") from e

    def list_all(self) -> list[DemoClient]:
        clients: list[DemoClient] = []
        for r in self._all_records():
            try:
                proxy_val = None
                proxy_raw = r.get("client_proxy_number", "")
                if str(proxy_raw).strip():
                    proxy_val = normalize_phone_digits(proxy_raw, label="client_proxy_number")
                clients.append(
                    DemoClient(
                        client_id=parse_client_id(r.get("client_id", 0)),
                        client_name=str(r.get("client_name", "")),
                        client_mail=str(r.get("client_mail", "")),
                        client_real_phone=normalize_phone_digits(r.get("client_real_phone", ""), label="client_real_phone"),
                        client_proxy_number=proxy_val,
                        client_iso_residency=str(r.get("client_iso_residency", "")),
                        client_country_code=str(r.get("client_country_code", "")),
                    )
                )
            except ValidationError:
                continue
        return clients

    def max_client_id(self) -> int:
        max_id = 0
        for r in self._all_records():
            try:
                cid = parse_client_id(r.get("client_id", ""))
            except ValidationError:
                continue
            max_id = max(max_id, cid)
        return max_id


# =========================
# Twilio (LIVE) - direct buy (legacy demo path)
# =========================
def twilio_buy_number(*, account_sid: str, auth_token: str, country: str, voice_url: str, friendly_name: str) -> str:
    if TwilioRestClient is None:
        raise ExternalServiceError("Dépendance Twilio manquante. Installe 'twilio'.")
    try:
        cli = TwilioRestClient(account_sid, auth_token)
        avail = cli.available_phone_numbers(country).local.list(limit=1)
        if not avail:
            raise ExternalServiceError(f"Aucun numéro local disponible pour le pays {country}.")
        phone_number = avail[0].phone_number
        incoming = cli.incoming_phone_numbers.create(phone_number=phone_number, voice_url=voice_url, friendly_name=friendly_name)
        return incoming.phone_number
    except TwilioRestException as e:
        raise ExternalServiceError("Erreur Twilio (achat/config numéro).", details={"status": getattr(e, "status", None), "msg": str(e)}) from e


# =========================
# CLI actions
# =========================
def ensure_env(var: str) -> str:
    v = os.getenv(var)
    if not v:
        raise ConfigError(f"Variable d’environnement manquante: {var}")
    return v


def load_env_files() -> list[Path]:
    """Charge .env puis .env.render afin de préparer la CLI pour Render."""

    loaded: list[Path] = []
    repo_root = Path(__file__).resolve().parent.parent

    # 📌 Ordre d'écrasement :
    #  - on charge d'abord .env (dev local)
    #  - puis .env.render qui doit prévaloir quand on cible Render
    # Cet ordre évite qu'une ancienne config locale (ex: URL ngrok) n'écrase
    # l'URL publique renseignée dans .env.render.
    for filename in (".env", ".env.render"):
        repo_env = repo_root / filename
        if repo_env.exists():
            load_dotenv(repo_env, override=True)
            loaded.append(repo_env)

        discovered = Path(find_dotenv(filename=filename, usecwd=True))
        if discovered and discovered.exists() and discovered not in loaded:
            load_dotenv(discovered, override=True)
            loaded.append(discovered)

    return loaded


def make_render_api_client(logger: logging.Logger) -> RenderAPIClient:
    base_url = ensure_env("PUBLIC_BASE_URL")
    token = os.getenv("PROXYCALL_API_TOKEN")
    logger.info("[blue]CLI[/blue] mode Render base_url=%s token=%s", base_url, "present" if token else "absent")
    return RenderAPIClient(base_url, token, logger)


def make_proxy_mock(client_id: int, country_code: str) -> str:
    h = hashlib.sha256(str(client_id).encode("utf-8")).hexdigest()
    digits = "".join([c for c in h if c.isdigit()])[:9].ljust(9, "0")
    proxy_digits = f"{country_code}{digits}"
    return normalize_phone_digits(proxy_digits, label="client_proxy_number")


def parse_client_id(value: str | int) -> int:
    return v_or_raise(int_strict, value, field="client_id", min_value=1)


def compute_next_client_id(store: ClientStore, logger: logging.Logger) -> int:
    try:
        max_num = store.max_client_id()
    except Exception as exc:
        logger.error(
            "[red]CLIENT[/red] impossible de récupérer le max client_id, fallback 0",
            extra={"error": str(exc)},
        )
        max_num = 0
    return max_num + 1


def do_create_client(args: argparse.Namespace, store: ClientStore, logger: logging.Logger) -> int:
    raw_id_val = getattr(args, "client_id", None)
    raw_id = str(raw_id_val).strip() if raw_id_val is not None else ""
    if not raw_id:
        client_id = compute_next_client_id(store, logger)
        logger.info("[blue]CLI[/blue] auto client_id=%s", client_id)
    else:
        client_id = parse_client_id(raw_id_val)

    existing = store.get_by_id(client_id)

    client_name = v_or_raise(name_strict, (args.name or ""), field="client_name")
    if not client_name:
        raise ValidationError("--name requis.")
    client_mail = v_or_raise(email_strict, (args.client_mail or ""), field="client_mail")
    if not client_mail:
        raise ValidationError("--client-mail requis.")

    real_phone_input = args.client_real_phone or (existing.client_real_phone if existing else "")
    client_real_phone = normalize_phone_digits(real_phone_input, label="client_real_phone")

    cc = extract_country_code_simple(client_real_phone)

    iso_residency = existing.client_iso_residency if existing else ""
    country_code = existing.client_country_code if existing else ""

    assign_proxy = getattr(args, "assign_proxy", True)
    if getattr(args, "no_proxy", False):
        assign_proxy = False

    if existing and existing.client_proxy_number:
        proxy = existing.client_proxy_number
    elif assign_proxy:
        if args.mode == "mock":
            proxy = make_proxy_mock(client_id, cc)
        elif args.mode == "render":
            proxy = None  # achat géré côté backend Render
        else:
            account_sid = ensure_env("TWILIO_ACCOUNT_SID")
            auth_token = ensure_env("TWILIO_AUTH_TOKEN")
            country = os.getenv("TWILIO_PHONE_COUNTRY", "US")
            public_base_url = ensure_env("PUBLIC_BASE_URL")
            voice_url = public_base_url.rstrip("/") + "/twilio/voice"
            proxy = twilio_buy_number(
                account_sid=account_sid,
                auth_token=auth_token,
                country=country,
                voice_url=voice_url,
                friendly_name=f"Client-{client_id}",
            )
    else:
        proxy = None

    client = DemoClient(
        client_id=client_id,
        client_name=client_name,
        client_mail=client_mail,
        client_real_phone=client_real_phone,
        client_proxy_number=normalize_phone_digits(proxy, label="client_proxy_number") if proxy else None,
        client_iso_residency=iso_residency,
        client_country_code=country_code,
    )

    _ = parse_client_id(client.client_id)
    _ = v_or_raise(name_strict, client.client_name, field="client_name")
    _ = v_or_raise(email_strict, client.client_mail, field="client_mail")
    _ = normalize_phone_digits(client.client_real_phone, label="client_real_phone")
    if client.client_proxy_number:
        _ = normalize_phone_digits(client.client_proxy_number, label="client_proxy_number")

    store.save(client)

    logger.info("[green]CLI[/green] client %s", "updated" if existing else "created")
    print(json.dumps(dataclasses.asdict(client), indent=2, ensure_ascii=False))
    return 0


def do_lookup(args: argparse.Namespace, store: ClientStore, logger: logging.Logger) -> int:
    proxy = normalize_phone_digits(args.proxy, label="proxy")
    client = store.get_by_proxy(proxy)
    if not client:
        raise NotFoundError("Aucun client trouvé pour ce proxy.", details={"proxy": proxy})
    logger.info("[green]CLI[/green] client found")
    print(json.dumps(dataclasses.asdict(client), indent=2, ensure_ascii=False))
    return 0


def do_simulate_call(args: argparse.Namespace, store: ClientStore, logger: logging.Logger) -> int:
    caller = normalize_phone_digits(args.from_number, label="from")
    proxy = normalize_phone_digits(args.to_number, label="to (proxy)")

    client = store.get_by_proxy(proxy)
    if not client:
        raise NotFoundError("Proxy inconnu (aucun client associé).", details={"proxy": proxy})

    caller_cc = extract_country_code_simple(caller)
    expected_cc = client.client_country_code or extract_country_code_simple(client.client_real_phone)

    if expected_cc and caller_cc != expected_cc:
        logger.warning("[yellow]CLI[/yellow] routing refused (country mismatch)")
        print(twiml_block("Sorry, calls are only allowed from the same country."))
        return 0

    logger.info("[green]CLI[/green] routing allowed (Dial)")
    print(
        twiml_dial(
            proxy_number=phone_digits_to_e164(client.client_proxy_number or "", label="proxy"),
            real_number=phone_digits_to_e164(client.client_real_phone, label="client_real_phone"),
        )
    )
    return 0


def do_create_order(args: argparse.Namespace, store: ClientStore, logger: logging.Logger) -> int:
    order_id = (args.order_id or "").strip()
    if not order_id:
        raise ValidationError("--order-id requis.")

    client_id = parse_client_id(args.client_id)
    args2 = argparse.Namespace(
        client_id=client_id,
        name=args.name,
        client_mail=args.client_mail,
        client_real_phone=args.client_real_phone,
        mode=args.mode,
        assign_proxy=True,
    )
    do_create_client(args2, store, logger)

    client = store.get_by_id(client_id)
    if not client:
        raise ExternalServiceError("Création client échouée (client introuvable après save).")

    logger.info("[green]CLI[/green] order created")
    out = {"order_id": order_id, "client_id": client.client_id, "proxy_number_to_share": client.client_proxy_number}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def do_pool_list(args: argparse.Namespace, pool_store: PoolStore, logger: logging.Logger) -> int:
    country = v_or_raise(iso_country_strict, (args.country or "FR"), field="country_iso").upper()
    number_type = str(getattr(args, "number_type", "all") or "all").lower()
    if number_type != "all":
        number_type = v_or_raise(number_type_strict, number_type, field="number_type")

    rows = pool_store.list_available(country, number_type=None if number_type == "all" else number_type)
    logger.info("[magenta]POOL[/magenta] list country=%s type=%s available=%s", country, number_type, len(rows))
    if not rows:
        print(f"Aucun numéro disponible pour {country}.")
        return 0

    print(f"Numéros disponibles pour {country} ({len(rows)}) :")
    for rec in rows:
        print(f"- {rec.get('phone_number')} (friendly_name={rec.get('friendly_name', '')})")
    return 0


def do_pool_provision(args: argparse.Namespace, pool_store: PoolStore, logger: logging.Logger) -> int:
    country = v_or_raise(iso_country_strict, (args.country or "FR"), field="country_iso").upper()
    batch_size = v_or_raise(int_strict, (args.batch_size or 1), field="batch_size", min_value=1)
    number_type = v_or_raise(number_type_strict, (getattr(args, "number_type", DEFAULT_NUMBER_TYPE) or DEFAULT_NUMBER_TYPE), field="number_type")

    try:
        added = pool_store.provision(country, batch_size, friendly_prefix=f"Pool-{country}", number_type=number_type)
    except RuntimeError as exc:
        raise ExternalServiceError(str(exc)) from exc

    logger.info("[magenta]POOL[/magenta] provision done country=%s purchased_now=%s", country, len(added))
    print(json.dumps({"country": country, "purchased_now": added}, indent=2, ensure_ascii=False))
    return 0


def do_pool_assign(args: argparse.Namespace, store: ClientStore, pool_store: PoolStore, logger: logging.Logger) -> int:
    client_id = parse_client_id(args.client_id)
    client = store.get_by_id(client_id)
    if not client:
        raise NotFoundError("Client introuvable pour attribution depuis le pool.")
    if client.client_proxy_number:
        raise ValidationError("Le client possède déjà un proxy.")

    country_guess = client.client_iso_residency or client.client_country_code or os.getenv("TWILIO_PHONE_COUNTRY", "FR")
    country = v_or_raise(iso_country_strict, country_guess, field="country_iso").upper()
    friendly = f"Client-{client_id}" if not client.client_name else client.client_name

    auto_confirm = bool(getattr(args, "yes", False))
    if not auto_confirm:
        print(
            "\nAttribution d'un proxy au client suivant :",
            (
                "\n  ID : {id}"
                "\n  Nom : {name}"
                "\n  Email : {mail}"
                "\n  Téléphone réel : {phone}"
                "\n  ISO résidence : {iso}"
                "\n  Pays cible : {country}\n"
            ).format(
                id=client.client_id,
                name=client.client_name or "N/A",
                mail=client.client_mail,
                phone=phone_digits_to_e164(client.client_real_phone, label="client_real_phone"),
                iso=client.client_iso_residency or "N/A",
                country=country,
            ),
        )
        confirm = input("Confirmer l'attribution ? (o/N) : ").strip().lower()
        if confirm not in {"o", "oui", "y", "yes"}:
            logger.info("[yellow]POOL[/yellow] assign cancelled client_id=%s", client_id)
            print("Attribution annulée.\n")
            return 0

    number_type = v_or_raise(number_type_strict, (getattr(args, "number_type", DEFAULT_NUMBER_TYPE) or DEFAULT_NUMBER_TYPE), field="number_type")
    proxy = pool_store.assign_number(country, friendly, client.client_name, client_id, number_type=number_type)

    client.client_proxy_number = normalize_phone_digits(proxy, label="client_proxy_number")

    _ = parse_client_id(client.client_id)
    _ = v_or_raise(name_strict, client.client_name, field="client_name")
    _ = v_or_raise(email_strict, client.client_mail, field="client_mail")
    _ = normalize_phone_digits(client.client_real_phone, label="client_real_phone")
    _ = normalize_phone_digits(client.client_proxy_number, label="client_proxy_number")

    store.save(client)

    logger.info("[green]POOL[/green] assigned client_id=%s", client_id)
    print(json.dumps(dataclasses.asdict(client), indent=2, ensure_ascii=False))
    return 0


def do_pool_sync(args: argparse.Namespace, pool_store: PoolStore, logger: logging.Logger) -> int:
    preview_result = pool_store.sync_with_provider(apply=False)

    twilio_numbers = preview_result.get("twilio_numbers", [])
    missing_numbers = preview_result.get("missing_numbers", [])

    print(f"Numéros trouvés côté Twilio ({len(twilio_numbers)}) :")
    for num in twilio_numbers:
        print(
            "- {number} (friendly_name={friendly}, pays={country})".format(
                number=num.get("phone_number", ""),
                friendly=num.get("friendly_name", ""),
                country=num.get("country_iso", ""),
            )
        )

    if not missing_numbers:
        print("\nTous les numéros Twilio sont déjà présents dans TwilioPools.")
        logger.info("[magenta]POOL[/magenta] sync preview missing=0")
        return 0

    print(f"\n{len(missing_numbers)} numéro(s) Twilio absent(s) de TwilioPools :")
    for num in missing_numbers:
        print(f"- {num}")

    auto_confirm = bool(getattr(args, "yes", False))
    if not auto_confirm:
        confirm = input("Importer ces numéros manquants dans TwilioPools ? (o/N) : ").strip().lower()
        if confirm not in {"o", "oui", "y", "yes"}:
            logger.info("[yellow]POOL[/yellow] sync cancelled missing=%s", len(missing_numbers))
            print("Import annulé.\n")
            return 0

    sync_result = pool_store.sync_with_provider(apply=True, twilio_numbers=twilio_numbers)
    added_numbers = sync_result.get("added_numbers", [])

    print(f"\n{len(added_numbers)} numéro(s) importé(s) dans TwilioPools :")
    for num in added_numbers:
        print(f"- {num}")

    logger.info("[green]POOL[/green] sync applied added=%s", len(added_numbers))
    return 0


def do_pool_purge_without_sms(args: argparse.Namespace, pool_store: PoolStore, logger: logging.Logger) -> int:
    auto_confirm = bool(getattr(args, "yes", False))

    if not auto_confirm:
        print(
            "Cette action va supprimer du pool (et libérer chez Twilio) tous les numéros sans capacité SMS."
            "\nOpération irréversible et potentiellement facturante côté Twilio."
        )
        confirm = input("Confirmer la purge ? (o/N) : ").strip().lower()
        if confirm not in {"o", "oui", "y", "yes"}:
            logger.info("[yellow]POOL[/yellow] purge sans SMS annulée par l'utilisateur")
            print("Purge annulée.\n")
            return 0
        auto_confirm = True

    result = pool_store.purge_without_sms_capability(auto_confirm=auto_confirm)

    print("\n--- Rapport purge numéros sans SMS ---")
    print(f"vérifiés             : {result.get('checked', 0)}")
    print(f"conservés (SMS OK)   : {len(result.get('kept_sms_capable', []) or [])}")
    print(f"supprimés du pool    : {len(result.get('removed_from_pool', []) or [])}")
    print(f"libérés chez Twilio  : {len(result.get('released_on_twilio', []) or [])}")
    print(f"introuvables Twilio  : {len(result.get('missing_on_twilio', []) or [])}")
    print(f"erreurs              : {len(result.get('errors', []) or [])}\n")

    logger.info(
        "[green]POOL[/green] purge sans SMS terminée checked=%s kept=%s removed=%s released=%s missing=%s errors=%s",
        result.get("checked", 0),
        len(result.get("kept_sms_capable", []) or []),
        len(result.get("removed_from_pool", []) or []),
        len(result.get("released_on_twilio", []) or []),
        len(result.get("missing_on_twilio", []) or []),
        len(result.get("errors", []) or []),
    )
    return 0


# ✅ NEW: fix webhooks action
def do_pool_fix_webhooks(args: argparse.Namespace, pool_store: PoolStore, logger: logging.Logger) -> int:
    dry_run = bool(getattr(args, "dry_run", True))
    only_country = getattr(args, "country", None)
    only_status = getattr(args, "status", None)

    if only_country:
        only_country = v_or_raise(iso_country_strict, only_country, field="country_iso").upper()

    if only_status:
        only_status = str(only_status).strip().lower()

    result = pool_store.fix_voice_webhooks(
        dry_run=dry_run,
        only_country=only_country,
        only_status=only_status,
        fix_sms=True,
    )

    checked = int(result.get("checked", 0) or 0)
    need_fix_voice = result.get("need_fix_voice", []) or []
    need_fix_sms = result.get("need_fix_sms", []) or []
    fixed_voice = result.get("fixed_voice", []) or []
    fixed_sms = result.get("fixed_sms", []) or []
    not_found = result.get("not_found_on_twilio", []) or []
    errors = result.get("errors", []) or []

    print("\n--- Pool webhook fix report ---")
    print(f"dry_run              : {bool(result.get('dry_run', False))}")
    print(f"target_voice_url     : {result.get('target_voice_url', '')}")
    print(f"target_sms_url       : {result.get('target_sms_url', '')}")
    print(f"checked              : {checked}")
    print(f"need_fix_voice       : {len(need_fix_voice)}")
    print(f"fixed_voice          : {len(fixed_voice)}")
    print(f"need_fix_sms         : {len(need_fix_sms)}")
    print(f"fixed_sms            : {len(fixed_sms)}")
    print(f"not_found_on_twilio  : {len(not_found)}")
    print(f"errors               : {len(errors)}")

    # Affichage détails (léger)
    if need_fix_voice:
        print("\nNuméros à corriger (voice_url, aperçu):")
        for x in need_fix_voice[:20]:
            print(f"- {x.get('phone_number')} (current_voice={x.get('current_voice_url','')})")
        if len(need_fix_voice) > 20:
            print(f"... +{len(need_fix_voice) - 20} autres")

    if need_fix_sms:
        print("\nNuméros à corriger (sms_url, aperçu):")
        for x in need_fix_sms[:20]:
            print(f"- {x.get('phone_number')} (current_sms={x.get('current_sms_url','')})")
        if len(need_fix_sms) > 20:
            print(f"... +{len(need_fix_sms) - 20} autres")

    if not_found:
        print("\nNuméros introuvables côté Twilio (aperçu):")
        for n in not_found[:20]:
            print(f"- {n}")
        if len(not_found) > 20:
            print(f"... +{len(not_found) - 20} autres")

    if errors:
        print("\nErreurs (aperçu):")
        for e in errors[:20]:
            print(f"- {e.get('phone_number','')} err={e.get('err','')}")
        if len(errors) > 20:
            print(f"... +{len(errors) - 20} autres")

    logger.info(
        (
            "[magenta]POOL[/magenta] fix_webhooks report checked=%s "
            "need_fix_voice=%s need_fix_sms=%s fixed_voice=%s fixed_sms=%s dry_run=%s"
        ),
        checked,
        len(need_fix_voice),
        len(need_fix_sms),
        len(fixed_voice),
        len(fixed_sms),
        dry_run,
    )
    return 0


# =========================
# CLI wiring
# =========================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="proxycall-demo", description="ProxyCall DEMO CLI (mock/live)")
    p.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"), help="DEBUG, INFO, WARNING, ERROR")
    p.add_argument("--verbose", action="store_true", help="Affiche les stack traces en cas d’erreur.")
    p.add_argument(
        "--fixtures",
        default=str((Path(__file__).parent / "fixtures" / "clients.json").resolve()),
        help="Chemin fixtures JSON (mode mock).",
    )
    p.add_argument(
        "--pools-fixtures",
        default=str(POOL_FIXTURES_DEFAULT.resolve()),
        help="Chemin fixtures pool (mode mock).",
    )

    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--mock", action="store_true", help="Mode MOCK (offline).")
    mode.add_argument("--live", action="store_true", help="Mode LIVE (Twilio + Sheets).")
    mode.add_argument("--render", action="store_true", help="Mode RENDER (appels HTTP vers l'API Render).")

    p.epilog = "Astuce : lance simplement `python cli.py` et laisse-toi guider, aucun argument n'est requis."

    sp = p.add_subparsers(dest="cmd", required=False)

    c1 = sp.add_parser("create-client", help="Crée (ou met à jour) un client + proxy.")
    c1.add_argument("--client-id", required=False, help="Laisser vide pour auto-incrémenter.")
    c1.add_argument("--name", required=True)
    c1.add_argument("--client-mail", required=True)
    c1.add_argument("--client-real-phone", required=True)
    c1.add_argument("--no-proxy", action="store_true", help="Créer sans attribuer de proxy.")

    c2 = sp.add_parser("lookup", help="Retrouve un client à partir du proxy.")
    c2.add_argument("--proxy", required=True)

    c3 = sp.add_parser("simulate-call", help="Simule un appel entrant et imprime le TwiML.")
    c3.add_argument("--from", dest="from_number", required=True)
    c3.add_argument("--to", dest="to_number", required=True)

    c4 = sp.add_parser("create-order", help="Démo: crée une 'commande' et affiche le proxy à communiquer.")
    c4.add_argument("--order-id", required=True)
    c4.add_argument("--client-id", required=True)
    c4.add_argument("--name", required=True)
    c4.add_argument("--client-mail", required=True)
    c4.add_argument("--client-real-phone", required=True)

    c5 = sp.add_parser("pool-list", help="Liste les numéros disponibles dans le pool (par pays).")
    c5.add_argument("--country", default=os.getenv("TWILIO_PHONE_COUNTRY", "FR"))
    c5.add_argument("--number-type", default="all", help="all | mobile | local | national")

    c6 = sp.add_parser("pool-provision", help="Approvisionne le pool (mock ou live).")
    c6.add_argument("--country", default=os.getenv("TWILIO_PHONE_COUNTRY", "FR"))
    c6.add_argument("--batch-size", type=int, default=int(os.getenv("TWILIO_POOL_SIZE", "2")))
    c6.add_argument(
        "--number-type",
        choices=["national", "local", "mobile"],
        default=DEFAULT_NUMBER_TYPE,
        help="Type de numéro à acheter.",
    )

    c7 = sp.add_parser("pool-assign", help="Attribue un numéro du pool à un client existant.")
    c7.add_argument("--client-id", required=True)
    c7.add_argument("--yes", action="store_true", help="Ne pas demander de confirmation interactive.")
    c7.add_argument(
        "--number-type",
        choices=["national", "local", "mobile"],
        default=DEFAULT_NUMBER_TYPE,
        help="Type de numéro à attribuer.",
    )

    c8 = sp.add_parser("pool-sync", help="Ajoute les numéros Twilio manquants dans TwilioPools.")
    c8.add_argument("--yes", action="store_true", help="Ne pas demander de confirmation avant import.")

    # ✅ Commande: correction voice + SMS webhooks
    c9 = sp.add_parser(
        "pool-fix-webhooks", help="Corrige voice_url et sms_url sur les numéros Twilio listés dans TwilioPools."
    )
    c9.add_argument("--country", required=False, help="Filtrer par pays ISO (ex: FR).")
    c9.add_argument("--status", required=False, help="Filtrer par status (ex: available/assigned).")
    c9.add_argument("--apply", action="store_true", help="Appliquer les updates (sinon dry-run).")

    c10 = sp.add_parser(
        "pool-purge-sans-sms",
        help="Purge le pool (et Twilio) de tous les numéros sans capacité SMS. Disponible en LIVE/RENDER.",
    )
    c10.add_argument("--yes", action="store_true", help="Ne pas demander de confirmation avant suppression.")

    return p


def select_mode(args: argparse.Namespace) -> str:
    if args.live:
        return "live"
    if args.mock:
        return "mock"
    if getattr(args, "render", False):
        return "render"

    print("Bienvenue ! Choisis le mode de démonstration :")
    print("  1) Démo simulée (MOCK) — recommandé, aucun prérequis")
    print("  2) Démo live (LIVE) — Twilio + Google Sheets requis")
    print("  3) Mode Render distant (appels HTTP vers l'API hébergée)")

    while True:
        user_choice = input("Sélection (1 par défaut) : ").strip() or "1"
        if user_choice == "1":
            return "mock"
        if user_choice == "2":
            return "live"
        if user_choice == "3":
            return "render"
        print("Merci de répondre par 1, 2 ou 3.")


def make_store(mode: str, args: argparse.Namespace, logger: logging.Logger) -> ClientStore:
    if mode == "mock":
        return MockJsonStore(Path(args.fixtures), logger=logger)
    if mode == "render":
        api = make_render_api_client(logger)
        return RenderClientStore(api, logger)

    sheet_name = ensure_env("GOOGLE_SHEET_NAME")
    sa_env = ensure_env("GOOGLE_SERVICE_ACCOUNT_FILE")
    sa_file = Path(sa_env).expanduser()

    if not sa_file.is_absolute():
        repo_root = Path(__file__).resolve().parent.parent
        candidate = repo_root / sa_file
        if candidate.exists():
            sa_file = candidate

    sa_file = sa_file.resolve()
    worksheet = os.getenv("GOOGLE_CLIENTS_WORKSHEET", "Clients")

    return SheetsStore(sheet_name=sheet_name, service_account_file=str(sa_file), worksheet=worksheet, logger=logger)


def make_pool_store(mode: str, args: argparse.Namespace, logger: logging.Logger) -> PoolStore:
    batch_size = int(os.getenv("TWILIO_POOL_SIZE", "2"))
    if mode == "mock":
        return MockPoolStore(Path(args.pools_fixtures), logger=logger, default_batch=batch_size)
    if mode == "render":
        api = make_render_api_client(logger)
        return RenderPoolStore(api, logger)
    return LivePoolStore(logger=logger, default_batch=batch_size)


def interactive_menu(args: argparse.Namespace, store: ClientStore, pool_store: PoolStore, logger: logging.Logger) -> int:
    try:
        print("\n=== ProxyCall DEMO ===")
        print("Répondez par le numéro du menu. Tapez 0 pour quitter.\n")
        print(f"Mode sélectionné : {args.mode.upper()}\n")

        while True:
            print("Menu principal :")
            print("  1) Gérer un client (créer / rechercher / attribuer un proxy)")
            print("  2) Simuler un appel autorisé (même indicatif pays)")
            print("  3) Simuler un appel bloqué (indicatif différent)")
            print("  4) Gérer le pool de numéros (approvisionnement / attribution)")
            print("  0) Quitter")

            choice = input("Votre sélection : ").strip() or "0"

            if choice == "0":
                logger.info("[blue]CLI[/blue] end interactive")
                print("Au revoir !")
                return 0

            if choice == "1":
                while True:
                    print("\nGestion client :")
                    print("  1) Créer un client (saisie guidée)")
                    print("  2) Rechercher/afficher un client existant")
                    print("  3) Attribuer un proxy à un client existant")
                    print("  0) Retour au menu principal")
                    sub_choice = input("Votre sélection : ").strip() or "0"

                    if sub_choice == "0":
                        break

                    if sub_choice == "1":
                        client_id = compute_next_client_id(store, logger)
                        print(f"ID attribué automatiquement : {client_id}")
                        name = input("Nom client (ex: Client Démo) : ").strip() or "Client Démo"
                        client_mail = input("Email client (ex: demo@example.com) : ").strip() or "demo@example.com"
                        client_real_phone = input("Numéro réel (ex: +33601020304) : ").strip() or "+33601020304"
                        assign_proxy_answer = (input("Attribuer un proxy maintenant ? [O/n] : ").strip().lower() or "o")
                        assign_proxy = not assign_proxy_answer.startswith("n")

                        try:
                            name = v_or_raise(name_strict, name, field="client_name")
                            client_mail = v_or_raise(email_strict, client_mail, field="client_mail")
                            client_real_phone = normalize_phone_digits(client_real_phone, label="client_real_phone")
                        except ValidationError as exc:
                            logger.error("Validation input (create client menu): %s", exc)
                            print(colorize(f"\n❌ {exc}\n", "red"))
                            continue

                        args_client = argparse.Namespace(
                            client_id=client_id,
                            name=name,
                            client_mail=client_mail,
                            client_real_phone=client_real_phone,
                            assign_proxy=assign_proxy,
                            mode=args.mode,
                        )
                        try:
                            do_create_client(args_client, store, logger)
                        except CLIError as exc:
                            logger.error("Erreur création client: %s", exc)
                            print(colorize(f"\n❌ Impossible de créer le client : {exc}\n", "red"))
                        continue

                    if sub_choice == "2":
                        print("Rechercher par :")
                        print("  1) ID client")
                        print("  2) Numéro proxy")
                        lookup_choice = input("Votre sélection (1 par défaut) : ").strip() or "1"

                        found = None
                        try:
                            if lookup_choice == "1":
                                client_id_raw = input("ID client (ex: 1) : ").strip()
                                if not client_id_raw:
                                    print("Merci de saisir un ID numérique.\n")
                                    continue
                                try:
                                    client_id_val = parse_client_id(client_id_raw)
                                except CLIError as exc:
                                    logger.error("ID invalide: %s", exc)
                                    continue
                                found = store.get_by_id(client_id_val)

                            elif lookup_choice == "2":
                                proxy = input("Numéro proxy (ex: +33900000000) : ").strip()
                                try:
                                    proxy_norm = normalize_phone_digits(proxy, label="proxy")
                                except CLIError as exc:
                                    logger.error("Proxy invalide: %s", exc)
                                    continue
                                found = store.get_by_proxy(proxy_norm)
                            else:
                                print("Merci de choisir 1 ou 2.\n")
                                continue
                        except CLIError as exc:
                            logger.error("Recherche client impossible: %s", exc)
                            print(colorize(f"\n❌ {exc}\n", "red"))
                            continue

                        if not found:
                            logger.warning("Client introuvable.")
                            print("Aucun client correspondant.")
                            continue

                        print(json.dumps(dataclasses.asdict(found), indent=2, ensure_ascii=False))
                        continue

                    if sub_choice == "3":
                        client_id = input("ID du client à équiper d'un proxy : ").strip()
                        if not client_id:
                            print("Merci de saisir un ID valide.\n")
                            continue
                        try:
                            client_id_val = parse_client_id(client_id)
                        except ValidationError as exc:
                            logger.error("ID invalide: %s", exc)
                            print(colorize(f"\n❌ {exc}\n", "red"))
                            continue

                        try:
                            existing = store.get_by_id(client_id_val)
                        except CLIError as exc:
                            logger.error("Recherche client pour attribution impossible: %s", exc)
                            print(colorize(f"\n❌ {exc}\n", "red"))
                            continue
                        if not existing:
                            print("Aucun client correspondant.\n")
                            continue
                        if existing.client_proxy_number:
                            print("Ce client possède déjà un proxy.\n")
                            continue

                        args_pool = argparse.Namespace(client_id=str(existing.client_id), yes=False, number_type=DEFAULT_NUMBER_TYPE)
                        try:
                            do_pool_assign(args_pool, store, pool_store, logger)
                        except CLIError as exc:
                            logger.error("Erreur attribution proxy: %s", exc)
                        continue

                    print("Merci de choisir 0, 1, 2 ou 3.\n")
                continue

            if choice == "2":
                from_number = input("Numéro appelant (même pays, ex: +33111111111) : ").strip() or "+33111111111"
                to_number = input("Numéro proxy appelé (ex: +33900000000) : ").strip() or "+33900000000"
                try:
                    do_simulate_call(argparse.Namespace(from_number=from_number, to_number=to_number), store, logger)
                except CLIError as exc:
                    logger.error("Erreur simulation appel autorisé: %s", exc)
                continue

            if choice == "3":
                from_number = input("Numéro appelant (autre pays, ex: +442222222222) : ").strip() or "+442222222222"
                to_number = input("Numéro proxy appelé (ex: +33900000000) : ").strip() or "+33900000000"
                try:
                    do_simulate_call(argparse.Namespace(from_number=from_number, to_number=to_number), store, logger)
                except CLIError as exc:
                    logger.error("Erreur simulation appel bloqué: %s", exc)
                continue

            if choice == "4":
                while True:
                    print("\nPool de numéros :")
                    print("  1) Lister les numéros disponibles par pays")
                    print("  2) Approvisionner le pool")
                    print("  3) Attribuer un numéro du pool à un client")
                    print("  4) Vérifier et compléter TwilioPools avec les numéros Twilio")
                    print("  5) Fixer voice_url et sms_url sur les numéros du pool (LIVE/RENDER)")
                    print("  6) Purger les numéros sans capacité SMS (LIVE/RENDER)")
                    print("  0) Retour au menu principal")
                    pool_choice = input("Votre sélection : ").strip() or "0"

                    if pool_choice == "0":
                        break

                    if pool_choice == "1":
                        country = input("Pays ISO (ex: FR) : ").strip() or os.getenv("TWILIO_PHONE_COUNTRY", "FR")
                        nt = input("Type (all/mobile/local/national) [all] : ").strip().lower() or "all"
                        try:
                            do_pool_list(argparse.Namespace(country=country, number_type=nt), pool_store, logger)
                        except CLIError as exc:
                            logger.error("Erreur listing pool: %s", exc)
                        continue

                    if pool_choice == "2":
                        country = input("Pays ISO (ex: FR) : ").strip() or os.getenv("TWILIO_PHONE_COUNTRY", "FR")
                        batch_raw = input("Combien de numéros acheter ? (2 par défaut) : ").strip() or "2"
                        number_type = input("Type (mobile/local/national) [national] : ").strip().lower() or DEFAULT_NUMBER_TYPE
                        try:
                            batch_size = int(batch_raw)
                            number_type = v_or_raise(number_type_strict, number_type, field="number_type")
                        except (ValueError, ValidationError) as exc:
                            print(colorize(f"\n❌ {exc}\n", "red"))
                            continue

                        args_pool = argparse.Namespace(country=country, batch_size=batch_size, number_type=number_type)
                        try:
                            do_pool_provision(args_pool, pool_store, logger)
                        except CLIError as exc:
                            print(f"Erreur: {exc}\n")
                            logger.error("Erreur approvisionnement pool: %s", exc)
                        continue

                    if pool_choice == "3":
                        client_raw = input("ID client pour attribution : ").strip()
                        if not client_raw:
                            print("Merci de renseigner un ID client.\n")
                            continue
                        number_type = input("Type (mobile/local/national) [national] : ").strip().lower() or DEFAULT_NUMBER_TYPE
                        try:
                            number_type = v_or_raise(number_type_strict, number_type, field="number_type")
                        except ValidationError as exc:
                            print(colorize(f"\n❌ {exc}\n", "red"))
                            continue

                        try:
                            do_pool_assign(argparse.Namespace(client_id=client_raw, yes=False, number_type=number_type), store, pool_store, logger)
                        except CLIError as exc:
                            logger.error("Erreur attribution pool: %s", exc)
                        continue

                    if pool_choice == "4":
                        try:
                            do_pool_sync(argparse.Namespace(yes=False), pool_store, logger)
                        except CLIError as exc:
                            logger.error("Erreur synchronisation pool: %s", exc)
                        continue

                    if pool_choice == "5":
                        country = input("Filtrer par pays ISO (ex: FR) [vide=all] : ").strip().upper() or ""
                        status = input("Filtrer par status (available/assigned) [vide=all] : ").strip().lower() or ""
                        apply = (input("Appliquer les updates ? (o/N) : ").strip().lower() or "n") in {"o", "oui", "y", "yes"}

                        try:
                            do_pool_fix_webhooks(
                                argparse.Namespace(
                                    country=country or None,
                                    status=status or None,
                                    dry_run=(not apply),
                                ),
                                pool_store,
                                logger,
                            )
                        except CLIError as exc:
                            logger.error("Erreur fix webhooks: %s", exc)
                            print(colorize(f"\n❌ {exc}\n", "red"))
                        continue

                    if pool_choice == "6":
                        try:
                            do_pool_purge_without_sms(argparse.Namespace(yes=False), pool_store, logger)
                        except CLIError as exc:
                            logger.error("Erreur purge sans SMS: %s", exc)
                            print(colorize(f"\n❌ {exc}\n", "red"))
                        continue

                    print("Merci de choisir 0, 1, 2, 3, 4, 5 ou 6.\n")
                continue

            logger.warning("Choix inconnu: %s", choice)
            print("Veuillez choisir 0, 1, 2, 3 ou 4.\n")

    except Exception as exc:  # pragma: no cover
        logger.exception("Erreur inattendue dans le menu interactif: %s", exc)
        return 4


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # ✅ New: Rich logging config (color + redaction). Verbose => DEBUG.
    configure_cli_logging(verbose=bool(args.verbose or str(args.log_level).upper() == "DEBUG"))
    logger = logging.getLogger(__name__)

    loaded_envs = load_env_files()
    if loaded_envs:
        logger.debug(
            "Fichiers d'environnement chargés: %s",
            ", ".join(str(p) for p in loaded_envs),
        )
    else:
        logger.warning(
            "Aucun fichier .env/.env.render trouvé : utilisation exclusive des variables d'environnement"
        )

    mode = select_mode(args)
    args.mode = mode

    try:
        store = make_store(mode, args, logger)
        pool_store = make_pool_store(mode, args, logger)

        if args.cmd is None:
            return interactive_menu(args, store, pool_store, logger)

        if args.cmd == "create-client":
            args.assign_proxy = not args.no_proxy
            return do_create_client(args, store, logger)
        if args.cmd == "lookup":
            return do_lookup(args, store, logger)
        if args.cmd == "simulate-call":
            return do_simulate_call(args, store, logger)
        if args.cmd == "create-order":
            return do_create_order(args, store, logger)
        if args.cmd == "pool-list":
            return do_pool_list(args, pool_store, logger)
        if args.cmd == "pool-provision":
            return do_pool_provision(args, pool_store, logger)
        if args.cmd == "pool-assign":
            return do_pool_assign(args, store, pool_store, logger)
        if args.cmd == "pool-sync":
            return do_pool_sync(args, pool_store, logger)
        if args.cmd == "pool-fix-webhooks":
            args.dry_run = not bool(getattr(args, "apply", False))
            return do_pool_fix_webhooks(args, pool_store, logger)
        if args.cmd == "pool-purge-sans-sms":
            return do_pool_purge_without_sms(args, pool_store, logger)

        raise ValidationError("Commande inconnue.")

    except CLIError as e:
        logger.error("%s", str(e))
        if args.verbose and e.__cause__ is not None:
            logger.exception("Détails exception:", exc_info=e.__cause__)
        if getattr(e, "details", None):
            logger.error("Details=%s", json.dumps(e.details, ensure_ascii=False))
        return e.exit_code
    except Exception as e:
        logger.exception("Erreur inattendue: %s", str(e))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
