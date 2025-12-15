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

DEFAULT_NUMBER_TYPE = os.getenv("TWILIO_NUMBER_TYPE", "mobile").lower()

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


# =========================
# Logging (square logs)
# =========================
class _ContextFilter(logging.Filter):
    def __init__(self, ctx: dict[str, Any]):
        super().__init__()
        self.ctx = ctx

    def filter(self, record: logging.LogRecord) -> bool:
        # Provide a stable field for formatting.
        record.ctx = " ".join([f"{k}={v}" for k, v in self.ctx.items() if v is not None])
        return True


def setup_logging(level: str, *, json_logs: bool, ctx: dict[str, Any]) -> logging.Logger:
    logger = logging.getLogger("proxycall.demo")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    h = logging.StreamHandler(sys.stdout)
    h.setLevel(logger.level)

    if json_logs:
        # Minimal JSON logs without external deps
        class JsonFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                payload = {
                    "ts": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    "level": record.levelname,
                    "logger": record.name,
                    "msg": record.getMessage(),
                    "ctx": getattr(record, "ctx", ""),
                }
                if record.exc_info:
                    payload["exc"] = self.formatException(record.exc_info)
                return json.dumps(payload, ensure_ascii=False)

        h.setFormatter(JsonFormatter())
    else:
        # "Square" logs: [ts] [LEVEL] [logger] message | ctx=...
        h.setFormatter(
            logging.Formatter(
                fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s | %(ctx)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    logger.addHandler(h)
    logger.addFilter(_ContextFilter(ctx))
    return logger


# =========================
# Console helpers
# =========================
ANSI_COLORS = {
    "red": "\033[91m",
    "reset": "\033[0m",
}


def colorize(text: str, color: str) -> str:
    if not sys.stdout.isatty():  # pas de couleur en sortie non interactive
        return text
    prefix = ANSI_COLORS.get(color, "")
    suffix = ANSI_COLORS["reset"] if prefix else ""
    return f"{prefix}{text}{suffix}"


# =========================
# Domain model
# =========================
PHONE_DIGITS_RE = re.compile(r"^[0-9]{8,15}$")  # 8 à 15 chiffres, sans signe +

# Règles de validation spécifiques par indicatif : indicatif -> longueur du numéro local
# (hors indicatif). Ces règles sont appliquées uniquement si l'indicatif est reconnu.
COUNTRY_PHONE_RULES: dict[str, int] = {
    "33": 9,   # France : 9 chiffres après l'indicatif (ex: 33 601020304)
    "351": 9,  # Portugal : 9 chiffres après l'indicatif (ex: 351 609875678)
}

# Exemples de formats attendus par indicatif pour des messages d'erreur plus clairs
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
    client_real_phone: int
    client_proxy_number: Optional[int]
    client_iso_residency: str
    client_country_code: str


def _detect_country_code(phone_digits: str) -> Optional[str]:
    """Détecte l'indicatif en privilégiant le plus long préfixe connu."""

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
    raw = str(phone or "").strip().replace(" ", "")
    if not raw:
        raise ValidationError(f"{label} manquant.")

    # accepte 00xx
    if raw.startswith("00"):
        raw = "+" + raw[2:]

    # accepte 33... ou +33...
    if raw.startswith("+"):
        digits = raw[1:]
    else:
        digits = raw

    if not PHONE_DIGITS_RE.match(digits):
        raise ValidationError(f"{label} invalide (8 à 15 chiffres attendus).", details={"value": raw})

    _validate_country_specific(digits, label=label)
    return "+" + digits



def phone_digits_to_str(phone: int | str, *, label: str = "numéro") -> str:
    return str(normalize_phone_digits(phone, label=label))


def phone_digits_to_e164(phone: int | str, *, label: str = "numéro") -> str:
    digits = phone_digits_to_str(phone, label=label)
    return "+" + digits


def extract_country_code_simple(phone: int | str) -> str:
    """Renvoie l'indicatif pays basique (premiers chiffres)."""

    digits = phone_digits_to_str(phone)
    detected = _detect_country_code(digits)
    if detected:
        return detected
    return digits[:2]


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
        """Retourne le plus grand identifiant connu, ou 0 si aucun."""

        raise NotImplementedError


class PoolStore:
    def list_available(self, country_iso: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def provision(
        self, country_iso: str, batch_size: int, *, friendly_prefix: str, number_type: str = "mobile"
    ) -> list[str]:
        raise NotImplementedError

    def assign_number(
        self, country_iso: str, friendly_name: str, client_name: str, *, number_type: str = "mobile"
    ) -> str:
        raise NotImplementedError

    def sync_with_provider(
        self, *, apply: bool = True, twilio_numbers: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        raise NotImplementedError


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
                    if proxy_raw is None or not str(proxy_raw).strip():
                        proxy_number = None
                    else:
                        proxy_number = normalize_phone_digits(proxy_raw, label="client_proxy_number")
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
                if proxy_raw is None or not str(proxy_raw).strip():
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

        new_row: dict[str, Any] = {
            "client_id": client.client_id,
            "client_name": client.client_name,
            "client_mail": client.client_mail,
            "client_real_phone": client.client_real_phone,
            "client_proxy_number": client.client_proxy_number if client.client_proxy_number is not None else "",
        }

        # Ne jamais écrire/écraser les colonnes calculées ; on ne les copie que si
        # elles existaient déjà dans le JSON pour préserver l'état antérieur.
        if preserved_iso is not None:
            new_row["client_iso_residency"] = preserved_iso
        if preserved_cc is not None:
            new_row["client_country_code"] = preserved_cc

        filtered.append(new_row)
        self._dump(filtered)

    def list_all(self) -> list[DemoClient]:
        clients: list[DemoClient] = []
        for r in self._load():
            try:
                proxy_val = None
                proxy_raw = r.get("client_proxy_number", "")
                if proxy_raw is not None and str(proxy_raw).strip():
                    proxy_val = normalize_phone_digits(proxy_raw, label="client_proxy_number")
                clients.append(
                    DemoClient(
                        client_id=parse_client_id(r.get("client_id")),
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
        "mobile": {
            "FR": "+337990",
            "US": "+155520",
        },
        "local": {
            "FR": "+331900",
            "US": "+140820",
        },
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

    def list_available(self, country_iso: str) -> list[dict[str, Any]]:
        country = country_iso.upper()
        return [rec for rec in self._load() if rec.get("country_iso") == country and str(rec.get("status")).lower() == "available"]

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
        self.logger.info("Pool mock approvisionné", extra={"country": country, "count": batch_size})
        return added

    def assign_number(
        self, country_iso: str, friendly_name: str, client_name: str, *, number_type: str = "mobile"
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
                    self.logger.info(
                        "Aucun mobile mock disponible, basculement sur un numéro local.",
                        extra={"country": country},
                    )
                    break

        if available_idx is None:
            self.provision(
                country,
                self.default_batch,
                friendly_prefix=f"Pool-{country}",
                number_type=requested_type,
            )
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
                    self.logger.info(
                        "Aucun mobile mock disponible après provision, basculement sur un numéro local.",
                        extra={"country": country},
                    )
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

    def sync_with_provider(
        self, *, apply: bool = True, twilio_numbers: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        raise ValidationError("Synchronisation disponible uniquement en mode LIVE.")


class LivePoolStore(PoolStore):
    def __init__(self, logger: logging.Logger, *, default_batch: int = 2):
        self.logger = logger
        self.default_batch = default_batch

    def list_available(self, country_iso: str) -> list[dict[str, Any]]:
        from integrations.twilio_client import TwilioClient

        return TwilioClient.list_available(country_iso.upper())

    def provision(
        self, country_iso: str, batch_size: int, *, friendly_prefix: str, number_type: str = "mobile"
    ) -> list[str]:
        from integrations.twilio_client import TwilioClient

        TwilioClient.fill_pool(country_iso.upper(), batch_size, number_type=number_type)
        self.logger.info("Pool Twilio approvisionné", extra={"country": country_iso, "count": batch_size})
        refreshed = TwilioClient.list_available(country_iso.upper())
        return [r.get("phone_number", "") for r in refreshed if str(r.get("status", "")).lower() == "available"]

    def assign_number(
        self, country_iso: str, friendly_name: str, client_name: str, *, number_type: str = "mobile"
    ) -> str:
        from integrations.twilio_client import TwilioClient

        return TwilioClient.buy_number_for_client(
            friendly_name=friendly_name,
            country=country_iso.upper(),
            attribution_to_client_name=client_name,
            number_type=number_type,
        )

    def sync_with_provider(
        self,
        *,
        apply: bool = True,
        twilio_numbers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from integrations.twilio_client import TwilioClient

        sync_result = TwilioClient.sync_twilio_numbers_with_sheet(
            apply=apply, twilio_numbers=twilio_numbers
        )
        self.logger.info(
            "Synchronisation Twilio -> TwilioPools terminée",
            extra={
                "added": len(sync_result.get("added_numbers", [])),
                "missing": len(sync_result.get("missing_numbers", [])),
                "applied": apply,
            },
        )
        return sync_result

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
                self.logger.info(
                    "En-têtes Sheets conservées (ordre différent ou colonnes supplémentaires détectées)." + suffix
                )
        except Exception as e:
            raise ExternalServiceError("Erreur lecture/initialisation headers Sheets.") from e

    def _all_records(self) -> list[dict[str, Any]]:
        try:
            # gspread returns list of dicts with headers
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
                    if proxy_raw is None or not str(proxy_raw).strip():
                        proxy_number = None
                    else:
                        proxy_number = normalize_phone_digits(proxy_raw, label="client_proxy_number")
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
                if proxy_raw is None or not str(proxy_raw).strip():
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
        # Upsert sans jamais toucher aux colonnes calculées (ISO / country code).
        try:
            records = self._all_records()
            # Need the row index: get_all_records excludes header; row index starts at 2
            for i, r in enumerate(records, start=2):
                try:
                    cid = parse_client_id(r.get("client_id", ""))
                except ValidationError:
                    continue
                if cid == client.client_id:
                    # Mise à jour uniquement des colonnes A à E.
                    self.ws.update(
                        f"A{i}:E{i}",
                        [
                            [
                                client.client_id,
                                client.client_name,
                                client.client_mail,
                                client.client_real_phone,
                                client.client_proxy_number or "",
                            ]
                        ],
                    )
                    return

            # Append uniquement sur les colonnes A à E ; les colonnes calculées sont
            # laissées au Sheet (formules/auto-calculs).
            self.ws.append_row(
                [
                    client.client_id,
                    client.client_name,
                    client.client_mail,
                    client.client_real_phone,
                    client.client_proxy_number or "",
                ]
            )
        except Exception as e:
            raise ExternalServiceError("Erreur écriture Sheets (save).") from e

    def list_all(self) -> list[DemoClient]:
        clients: list[DemoClient] = []
        for r in self._all_records():
            try:
                proxy_val = None
                proxy_raw = r.get("client_proxy_number", "")
                if proxy_raw is not None and str(proxy_raw).strip():
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
# Twilio (LIVE)
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
        incoming = cli.incoming_phone_numbers.create(
            phone_number=phone_number,
            voice_url=voice_url,
            friendly_name=friendly_name,
        )
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
    """Charge les fichiers .env pour le mode LIVE (racine du repo + découverte)."""
    loaded: list[Path] = []

    repo_env = Path(__file__).resolve().parent.parent / ".env"
    if repo_env.exists():
        load_dotenv(repo_env)
        loaded.append(repo_env)

    discovered = Path(find_dotenv(usecwd=True))
    if discovered and discovered.exists() and discovered not in loaded:
        load_dotenv(discovered)
        loaded.append(discovered)

    return loaded


def make_proxy_mock(client_id: int, country_code: str) -> int:
    # proxy stable et "réaliste" (mais fake) basé sur hash(client_id)
    h = hashlib.sha256(str(client_id).encode("utf-8")).hexdigest()
    digits = "".join([c for c in h if c.isdigit()])[:9].ljust(9, "0")
    proxy_digits = f"{country_code}{digits}"
    return normalize_phone_digits(proxy_digits, label="client_proxy_number")


def parse_client_id(value: str | int) -> int:
    """Convertit un identifiant vers un entier.

    Accepte des valeurs comme "7", "#7" ou "client-7" pour conserver la
    robustesse par rapport aux données existantes, mais force un retour int.
    """

    if isinstance(value, int):
        return value

    raw = str(value or "").strip()
    if not raw:
        raise ValidationError("ID client manquant.")

    m = re.search(r"(\d+)$", raw)
    if not m:
        raise ValidationError("ID client invalide: un entier est requis en suffixe.")

    return int(m.group(1))


def compute_next_client_id(store: ClientStore) -> int:
    """Génère un nouvel ID client en incrémentant le dernier entier connu.

    On s'appuie exclusivement sur la colonne ``client_id`` pour éviter qu'une
    ligne partiellement invalide (ex: numéro mal formé) ne soit ignorée.
    """

    try:
        max_num = store.max_client_id()
    except Exception:
        max_num = 0

    return max_num + 1


def do_create_client(args: argparse.Namespace, store: ClientStore, logger: logging.Logger) -> int:
    raw_id_val = getattr(args, "client_id", None)
    raw_id = str(raw_id_val).strip() if raw_id_val is not None else ""
    if not raw_id:
        client_id = compute_next_client_id(store)
        logger.info("ID attribué automatiquement.", extra={"client_id": client_id})
    else:
        client_id = parse_client_id(raw_id_val)

    existing = store.get_by_id(client_id)

    client_name = (args.name or (existing.client_name if existing else "")).strip()
    if not client_name:
        raise ValidationError("--name requis.")
    client_mail = (args.client_mail or (existing.client_mail if existing else "")).strip()
    if not client_mail:
        raise ValidationError("--client-mail requis.")

    real_phone_input = args.client_real_phone or (existing.client_real_phone if existing else "")
    client_real_phone = normalize_phone_digits(real_phone_input, label="client_real_phone")

    cc = extract_country_code_simple(client_real_phone)

    # Les colonnes "client_iso_residency" et "client_country_code" sont désormais
    # exclusivement calculées côté Google Sheets : on ne les renseigne jamais lors
    # des créations/mises à jour, mais on préserve les valeurs déjà présentes si
    # le store les expose (ex: champs calculés existants).
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
    store.save(client)

    if existing:
        logger.info("Client mis à jour (affiché ci-dessous).")
    else:
        logger.info("Client créé.")
    print(json.dumps(dataclasses.asdict(client), indent=2, ensure_ascii=False))
    return 0


def do_lookup(args: argparse.Namespace, store: ClientStore, logger: logging.Logger) -> int:
    proxy = normalize_phone_digits(args.proxy, label="proxy")
    client = store.get_by_proxy(proxy)
    if not client:
        raise NotFoundError("Aucun client trouvé pour ce proxy.", details={"proxy": proxy})
    logger.info("Client trouvé.")
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
        logger.warning("Routage refusé (country mismatch).")
        print(twiml_block("Sorry, calls are only allowed from the same country."))
        return 0

    logger.info("Routage autorisé (Dial vers phone_real).")
    print(
        twiml_dial(
            proxy_number=phone_digits_to_e164(client.client_proxy_number, label="proxy"),
            real_number=phone_digits_to_e164(client.client_real_phone, label="client_real_phone"),
        )
    )
    return 0


def do_create_order(args: argparse.Namespace, store: ClientStore, logger: logging.Logger) -> int:
    # Démo simple: "order" => garantit client + affiche proxy à communiquer
    order_id = (args.order_id or "").strip()
    if not order_id:
        raise ValidationError("--order-id requis.")

    # On réutilise la création client (idempotente côté store).
    # Si le client n'existe pas, on le crée.
    client_id = parse_client_id(args.client_id)
    args2 = argparse.Namespace(
        client_id=client_id,
        name=args.name,
        client_mail=args.client_mail,
        client_real_phone=args.client_real_phone,
        mode=args.mode,
    )
    do_create_client(args2, store, logger)

    client = store.get_by_id(client_id)
    if not client:
        raise ExternalServiceError("Création client échouée (client introuvable après save).")

    logger.info("Commande créée (démo).")
    out = {
        "order_id": order_id,
        "client_id": client.client_id,
        "proxy_number_to_share": client.client_proxy_number,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def do_pool_list(args: argparse.Namespace, pool_store: PoolStore, logger: logging.Logger) -> int:
    country = (args.country or "FR").upper()
    rows = pool_store.list_available(country)
    logger.info("Pool interrogé", extra={"country": country, "available": len(rows)})
    if not rows:
        print(f"Aucun numéro disponible pour {country}.")
        return 0

    print(f"Numéros disponibles pour {country} ({len(rows)}) :")
    for rec in rows:
        print(f"- {rec.get('phone_number')} (friendly_name={rec.get('friendly_name', '')})")
    return 0


def do_pool_provision(args: argparse.Namespace, pool_store: PoolStore, logger: logging.Logger) -> int:
    country = (args.country or "FR").upper()
    batch_size = max(1, int(args.batch_size or 1))
    number_type = str(getattr(args, "number_type", "mobile") or "mobile").lower()
    try:
        added = pool_store.provision(
            country, batch_size, friendly_prefix=f"Pool-{country}", number_type=number_type
        )
    except RuntimeError as exc:
        raise ExternalServiceError(str(exc)) from exc
    logger.info("Approvisionnement terminé", extra={"country": country, "count": len(added)})
    print(json.dumps({"country": country, "added": added}, indent=2, ensure_ascii=False))
    return 0


def do_pool_assign(
    args: argparse.Namespace,
    store: ClientStore,
    pool_store: PoolStore,
    logger: logging.Logger,
) -> int:
    client_id = parse_client_id(args.client_id)
    client = store.get_by_id(client_id)
    if not client:
        raise NotFoundError("Client introuvable pour attribution depuis le pool.")
    if client.client_proxy_number:
        raise ValidationError("Le client possède déjà un proxy.")

    country = (client.client_iso_residency or client.client_country_code or os.getenv("TWILIO_PHONE_COUNTRY", "FR")).upper()
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
            logger.info("Attribution annulée par l'utilisateur", extra={"client_id": client_id})
            print("Attribution annulée.\n")
            return 0

    number_type = str(getattr(args, "number_type", "mobile") or "mobile").lower()
    proxy = pool_store.assign_number(
        country, friendly, client.client_name, number_type=number_type
    )

    client.client_proxy_number = normalize_phone_digits(proxy, label="proxy")
    store.save(client)
    logger.info("Proxy attribué depuis le pool", extra={"client_id": client_id, "proxy": proxy})
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
        logger.info(
            "Aucun numéro manquant côté TwilioPools",
            extra={"missing": 0},
        )
        return 0

    print(
        f"\n{len(missing_numbers)} numéro(s) Twilio absent(s) de TwilioPools :"
    )
    for num in missing_numbers:
        print(f"- {num}")

    auto_confirm = bool(getattr(args, "yes", False))
    if not auto_confirm:
        confirm = input(
            "Importer ces numéros manquants dans TwilioPools ? (o/N) : "
        ).strip().lower()
        if confirm not in {"o", "oui", "y", "yes"}:
            logger.info(
                "Import des numéros manquants annulé par l'utilisateur",
                extra={"missing": len(missing_numbers)},
            )
            print("Import annulé.\n")
            return 0

    sync_result = pool_store.sync_with_provider(
        apply=True, twilio_numbers=twilio_numbers
    )
    added_numbers = sync_result.get("added_numbers", [])

    print(
        f"\n{len(added_numbers)} numéro(s) importé(s) dans TwilioPools :"
    )
    for num in added_numbers:
        print(f"- {num}")

    logger.info(
        "Synchronisation TwilioPools réalisée",
        extra={"added": len(added_numbers)},
    )
    return 0


# =========================
# CLI wiring
# =========================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="proxycall-demo", description="ProxyCall DEMO CLI (mock/live)")
    p.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"), help="DEBUG, INFO, WARNING, ERROR")
    p.add_argument("--json-logs", action="store_true", help="Logs JSON (utile pour ingestion).")
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

    p.epilog = "Astuce : lance simplement `python cli.py` et laisse-toi guider, aucun argument n'est requis."

    sp = p.add_subparsers(dest="cmd", required=False)

    c1 = sp.add_parser("create-client", help="Crée (ou affiche si existe) un client + proxy.")
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

    c5 = sp.add_parser(
        "pool-list",
        help="Liste les numéros disponibles dans le pool (par pays).",
    )
    c5.add_argument("--country", default=os.getenv("TWILIO_PHONE_COUNTRY", "FR"))

    c6 = sp.add_parser(
        "pool-provision",
        help="Approvisionne le pool en achetant des numéros (mock ou live).",
    )
    c6.add_argument("--country", default=os.getenv("TWILIO_PHONE_COUNTRY", "FR"))
    c6.add_argument("--batch-size", type=int, default=int(os.getenv("TWILIO_POOL_SIZE", "2")))
    c6.add_argument(
        "--number-type",
        choices=["mobile", "local"],
        default=DEFAULT_NUMBER_TYPE,
        help="Type de numéro à acheter (mobile par défaut, local sinon).",
    )

    c7 = sp.add_parser(
        "pool-assign",
        help="Attribue un numéro du pool à un client existant (et met à jour sa fiche).",
    )
    c7.add_argument("--client-id", required=True)
    c7.add_argument("--yes", action="store_true", help="Ne pas demander de confirmation interactive.")
    c7.add_argument(
        "--number-type",
        choices=["mobile", "local"],
        default=DEFAULT_NUMBER_TYPE,
        help="Type de numéro à attribuer (mobile par défaut, local sinon).",
    )

    c8 = sp.add_parser(
        "pool-sync",
        help="Affiche les numéros Twilio et ajoute les manquants dans TwilioPools.",
    )
    c8.add_argument("--yes", action="store_true", help="Ne pas demander de confirmation avant import.")

    return p


def select_mode(args: argparse.Namespace) -> str:
    try:
        if args.live:
            return "live"
        if args.mock:
            return "mock"

        print("Bienvenue ! Choisis le mode de démonstration :")
        print("  1) Démo simulée (MOCK) — recommandé, aucun prérequis")
        print("  2) Démo live (LIVE) — Twilio + Google Sheets requis")

        while True:
            user_choice = input("Sélection (1 par défaut) : ").strip() or "1"
            if user_choice == "1":
                return "mock"
            if user_choice == "2":
                return "live"
            print("Merci de répondre par 1 ou 2.")
    except Exception as exc:  # pragma: no cover - interaction console
        LOGGER.exception("Échec lors du choix de mode interactif: %s", exc)
        raise


def make_store(mode: str, args: argparse.Namespace, logger: logging.Logger) -> ClientStore:
    if mode == "mock":
        return MockJsonStore(Path(args.fixtures), logger=logger)

    # LIVE: requires Sheets config
    sheet_name = ensure_env("GOOGLE_SHEET_NAME")
    sa_env = ensure_env("GOOGLE_SERVICE_ACCOUNT_FILE")
    sa_file = Path(sa_env).expanduser()

    # Si le chemin est relatif, on l'interprète depuis la racine du dépôt pour les utilisateurs
    # qui lancent le script ailleurs (ex: PyCharm). Cela évite l'erreur "Fichier introuvable".
    if not sa_file.is_absolute():
        repo_root = Path(__file__).resolve().parent.parent
        candidate = repo_root / sa_file
        if candidate.exists():
            sa_file = candidate

    sa_file = sa_file.resolve()

    worksheet = os.getenv("GOOGLE_CLIENTS_WORKSHEET", "Clients")
    return SheetsStore(
        sheet_name=sheet_name,
        service_account_file=str(sa_file),
        worksheet=worksheet,
        logger=logger,
    )


def make_pool_store(mode: str, args: argparse.Namespace, logger: logging.Logger) -> PoolStore:
    batch_size = int(os.getenv("TWILIO_POOL_SIZE", "2"))
    if mode == "mock":
        return MockPoolStore(Path(args.pools_fixtures), logger=logger, default_batch=batch_size)
    return LivePoolStore(logger=logger, default_batch=batch_size)


def interactive_menu(args: argparse.Namespace, store: ClientStore, pool_store: PoolStore, logger: logging.Logger) -> int:
    try:
        print("\n=== ProxyCall DEMO ===")
        print(
            "Répondez simplement par le numéro du menu. Tapez 0 pour quitter."
            "\nLes valeurs par défaut sont préremplies pour aller vite.\n"
        )
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
                logger.info("Fin de la démo interactive.")
                print("Au revoir !")
                return 0

            if choice == "1":
                logger.info("Menu 1: gestion client (créer/rechercher).")
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
                        client_id = compute_next_client_id(store)
                        print(f"ID attribué automatiquement : {client_id}")
                        name = input("Nom client (ex: Client Démo) : ").strip() or "Client Démo"
                        client_mail = input("Email client (ex: demo@example.com) : ").strip() or "demo@example.com"
                        client_real_phone = input("Numéro réel (ex: +33601020304) : ").strip() or "+33601020304"
                        assign_proxy_answer = (input("Attribuer un proxy maintenant ? [O/n] : ").strip().lower() or "o")
                        assign_proxy = not assign_proxy_answer.startswith("n")
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
                            msg = f"\n❌ Impossible de créer le client : {exc}\n"
                            print(colorize(msg, "red"))
                        continue

                    if sub_choice == "2":
                        print("Rechercher par :")
                        print("  1) ID client")
                        print("  2) Numéro proxy")
                        lookup_choice = input("Votre sélection (1 par défaut) : ").strip() or "1"
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
                            proxy = input("Numéro proxy (ex: 33900000000) : ").strip()
                            try:
                                proxy_norm = normalize_phone_digits(proxy, label="proxy")
                            except CLIError as exc:
                                logger.error("Proxy invalide: %s", exc)
                                continue
                            found = store.get_by_proxy(proxy_norm)
                        else:
                            print("Merci de choisir 1 ou 2.\n")
                            continue

                        if not found:
                            logger.warning("Client introuvable.")
                            print("Aucun client correspondant.")
                            continue

                        logger.info("Client trouvé (affiché ci-dessous).")
                        print(json.dumps(dataclasses.asdict(found), indent=2, ensure_ascii=False))
                        continue

                    if sub_choice == "3":
                        client_id = input("ID du client à équiper d'un proxy : ").strip()
                        if not client_id:
                            print("Merci de saisir un ID valide.\n")
                            continue
                        try:
                            client_id_val = parse_client_id(client_id)
                        except CLIError as exc:
                            logger.error("ID invalide: %s", exc)
                            continue
                        existing = store.get_by_id(client_id_val)
                        if not existing:
                            logger.warning("Client introuvable pour attribution proxy.")
                            print("Aucun client correspondant.\n")
                            continue
                        if existing.client_proxy_number:
                            logger.info("Proxy déjà attribué, rien à faire.")
                            print("Ce client possède déjà un proxy.\n")
                            continue

                        args_client = argparse.Namespace(
                            client_id=existing.client_id,
                            name=existing.client_name,
                            client_mail=existing.client_mail,
                            client_real_phone=existing.client_real_phone,
                            assign_proxy=True,
                            mode=args.mode,
                        )
                        try:
                            do_create_client(args_client, store, logger)
                        except CLIError as exc:
                            logger.error("Erreur attribution proxy: %s", exc)
                        continue

                    print("Merci de choisir 0, 1, 2 ou 3.\n")
                continue

            if choice == "2":
                logger.info("Menu 2: simulation appel autorisé.")
                from_number = input("Numéro appelant (même pays, ex: 33111111111) : ").strip() or "33111111111"
                to_number = input("Numéro proxy appelé (ex: 33900000000) : ").strip() or "33900000000"
                args_call = argparse.Namespace(from_number=from_number, to_number=to_number)
                try:
                    do_simulate_call(args_call, store, logger)
                except CLIError as exc:
                    logger.error("Erreur simulation appel autorisé: %s", exc)
                continue

            if choice == "3":
                logger.info("Menu 3: simulation appel bloqué.")
                from_number = input("Numéro appelant (autre pays, ex: 442222222222) : ").strip() or "442222222222"
                to_number = input("Numéro proxy appelé (ex: 33900000000) : ").strip() or "33900000000"
                args_call = argparse.Namespace(from_number=from_number, to_number=to_number)
                try:
                    do_simulate_call(args_call, store, logger)
                except CLIError as exc:
                    logger.error("Erreur simulation appel bloqué: %s", exc)
                continue

            if choice == "4":
                logger.info("Menu 4: gestion du pool.")
                while True:
                    print("\nPool de numéros :")
                    print("  1) Lister les numéros disponibles par pays")
                    print("  2) Approvisionner le pool")
                    print("  3) Attribuer un numéro du pool à un client")
                    print("  4) Vérifier et compléter TwilioPools avec les numéros Twilio")
                    print("  0) Retour au menu principal")
                    pool_choice = input("Votre sélection : ").strip() or "0"

                    if pool_choice == "0":
                        break

                    if pool_choice == "1":
                        country = input("Pays ISO (ex: FR) : ").strip() or os.getenv("TWILIO_PHONE_COUNTRY", "FR")
                        args_pool = argparse.Namespace(country=country)
                        try:
                            do_pool_list(args_pool, pool_store, logger)
                        except CLIError as exc:
                            logger.error("Erreur listing pool: %s", exc)
                        continue

                    if pool_choice == "2":
                        country = input("Pays ISO (ex: FR) : ").strip() or os.getenv("TWILIO_PHONE_COUNTRY", "FR")
                        batch_raw = input("Combien de numéros acheter ? (2 par défaut) : ").strip() or "2"
                        try:
                            batch_size = int(batch_raw)
                        except ValueError:
                            print("Merci d'indiquer un entier.\n")
                            continue
                        args_pool = argparse.Namespace(
                            country=country,
                            batch_size=batch_size,
                            number_type=DEFAULT_NUMBER_TYPE,
                        )
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
                        args_pool = argparse.Namespace(
                            client_id=client_raw, yes=False, number_type=DEFAULT_NUMBER_TYPE
                        )
                        try:
                            do_pool_assign(args_pool, store, pool_store, logger)
                        except CLIError as exc:
                            logger.error("Erreur attribution pool: %s", exc)
                        continue

                    if pool_choice == "4":
                        try:
                            do_pool_sync(argparse.Namespace(yes=False), pool_store, logger)
                        except CLIError as exc:
                            logger.error("Erreur synchronisation pool: %s", exc)
                        continue

                    print("Merci de choisir 0, 1, 2, 3 ou 4.\n")
                continue

            logger.warning("Choix inconnu: %s", choice)
            print("Veuillez choisir 0, 1, 2, 3 ou 4.\n")
    except Exception as exc:  # pragma: no cover - boucle interactive
        logger.exception("Erreur inattendue dans le menu interactif: %s", exc)
        return 4


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    loaded_env_files = load_env_files()

    mode = select_mode(args)
    ctx = {"mode": mode, "cmd": args.cmd or "menu"}

    logger = setup_logging(args.log_level, json_logs=args.json_logs, ctx=ctx)

    if loaded_env_files:
        logger.debug("Fichiers .env chargés: %s", ", ".join(str(p) for p in loaded_env_files))

    try:
        try:
            store = make_store(mode, args, logger)
            pool_store = make_pool_store(mode, args, logger)
        except ConfigError as exc:
            logger.error(str(exc))
            if args.cmd is None and mode == "live":
                print(
                    "\nLe mode LIVE n'est pas prêt (variables d'environnement manquantes)."
                    "\nVoulez-vous basculer en mode simulé (MOCK) ? [O/n]",
                    end=" ",
                )
                answer = (input().strip().lower() or "o") if sys.stdin.isatty() else "o"
                if answer.startswith("o"):
                    mode = "mock"
                    args.mode = mode
                    ctx = {"mode": mode, "cmd": args.cmd or "menu"}
                    logger = setup_logging(args.log_level, json_logs=args.json_logs, ctx=ctx)
                    store = make_store(mode, args, logger)
                    pool_store = make_pool_store(mode, args, logger)
                else:
                    return exc.exit_code
            else:
                return exc.exit_code

        args.mode = mode

        if args.cmd is None:
            return interactive_menu(args, store, pool_store, logger)

        if args.cmd == "create-client":
            args.assign_proxy = not args.no_proxy
            args.mode = mode
            return do_create_client(args, store, logger)
        if args.cmd == "lookup":
            return do_lookup(args, store, logger)
        if args.cmd == "simulate-call":
            return do_simulate_call(args, store, logger)
        if args.cmd == "create-order":
            args.mode = mode
            return do_create_order(args, store, logger)
        if args.cmd == "pool-list":
            return do_pool_list(args, pool_store, logger)
        if args.cmd == "pool-provision":
            return do_pool_provision(args, pool_store, logger)
        if args.cmd == "pool-assign":
            return do_pool_assign(args, store, pool_store, logger)
        if args.cmd == "pool-sync":
            return do_pool_sync(args, pool_store, logger)

        raise ValidationError("Commande inconnue.")
    except CLIError as e:
        logger.error(str(e))
        if args.verbose and e.__cause__ is not None:
            logger.exception("Détails exception:", exc_info=e.__cause__)
        if getattr(e, "details", None):
            logger.error("Details=%s", json.dumps(e.details, ensure_ascii=False))
        return e.exit_code
    except Exception as e:
        # Catch-all unexpected errors
        logger.exception("Erreur inattendue: %s", str(e))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
