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
# Domain model
# =========================
E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")  # pragmatic E.164 check


@dataclasses.dataclass
class DemoClient:
    client_id: str
    client_name: str
    client_mail: str
    client_real_phone: str
    client_proxy_number: str
    client_iso_residency: str
    client_country_code: str


def normalize_e164_like(phone: str) -> str:
    p = str(phone or "").strip().replace(" ", "")
    if not p:
        raise ValidationError("Numéro vide.")
    if not p.startswith("+"):
        p = "+" + p
    return p


def validate_e164(phone: str, label: str) -> str:
    p = normalize_e164_like(phone)
    if not E164_RE.match(p):
        raise ValidationError(f"{label} invalide (attendu format E.164, ex: +33601020304).", details={"value": p})
    return p


def extract_country_code_simple(phone_e164: str) -> str:
    # Simple (comme ton service actuel): +33, +49, +1 ...
    p = normalize_e164_like(phone_e164)
    return p[:3]


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
    def get_by_id(self, client_id: str) -> Optional[DemoClient]:
        raise NotImplementedError

    def get_by_proxy(self, proxy_number: str) -> Optional[DemoClient]:
        raise NotImplementedError

    def save(self, client: DemoClient) -> None:
        raise NotImplementedError

    def list_all(self) -> list[DemoClient]:
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

    def get_by_id(self, client_id: str) -> Optional[DemoClient]:
        for r in self._load():
            if r.get("client_id") == client_id:
                return DemoClient(**r)
        return None

    def get_by_proxy(self, proxy_number: str) -> Optional[DemoClient]:
        p = normalize_e164_like(proxy_number)
        for r in self._load():
            if normalize_e164_like(r.get("client_proxy_number", "")) == p:
                return DemoClient(**r)
        return None

    def save(self, client: DemoClient) -> None:
        rows = self._load()
        rows = [r for r in rows if r.get("client_id") != client.client_id]
        rows.append(dataclasses.asdict(client))
        self._dump(rows)

    def list_all(self) -> list[DemoClient]:
        return [DemoClient(**r) for r in self._load()]


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

    def get_by_id(self, client_id: str) -> Optional[DemoClient]:
        for r in self._all_records():
            if str(r.get("client_id", "")).strip() == client_id:
                return DemoClient(
                    client_id=str(r.get("client_id", "")),
                    client_name=str(r.get("client_name", "")),
                    client_mail=str(r.get("client_mail", "")),
                    client_real_phone=str(r.get("client_real_phone", "")),
                    client_proxy_number=str(r.get("client_proxy_number", "")),
                    client_iso_residency=str(r.get("client_iso_residency", "")),
                    client_country_code=str(r.get("client_country_code", "")),
                )
        return None

    def get_by_proxy(self, proxy_number: str) -> Optional[DemoClient]:
        p = normalize_e164_like(proxy_number)
        for r in self._all_records():
            if normalize_e164_like(str(r.get("client_proxy_number", ""))) == p:
                return DemoClient(
                    client_id=str(r.get("client_id", "")),
                    client_name=str(r.get("client_name", "")),
                    client_mail=str(r.get("client_mail", "")),
                    client_real_phone=str(r.get("client_real_phone", "")),
                    client_proxy_number=str(r.get("client_proxy_number", "")),
                    client_iso_residency=str(r.get("client_iso_residency", "")),
                    client_country_code=str(r.get("client_country_code", "")),
                )
        return None

    def save(self, client: DemoClient) -> None:
        # Upsert naïf : si trouvé -> update la ligne; sinon -> append.
        try:
            records = self._all_records()
            # Need the row index: get_all_records excludes header; row index starts at 2
            for i, r in enumerate(records, start=2):
                if str(r.get("client_id", "")).strip() == client.client_id:
                    self.ws.update(
                        f"A{i}:G{i}",
                        [
                            [
                                client.client_id,
                                client.client_name,
                                client.client_mail,
                                client.client_real_phone,
                                client.client_proxy_number,
                                client.client_iso_residency,
                                client.client_country_code,
                            ]
                        ],
                    )
                    return

            self.ws.append_row(
                [
                    client.client_id,
                    client.client_name,
                    client.client_mail,
                    client.client_real_phone,
                    client.client_proxy_number,
                    client.client_iso_residency,
                    client.client_country_code,
                ]
            )
        except Exception as e:
            raise ExternalServiceError("Erreur écriture Sheets (save).") from e

    def list_all(self) -> list[DemoClient]:
        return [
            DemoClient(
                client_id=str(r.get("client_id", "")),
                client_name=str(r.get("client_name", "")),
                client_mail=str(r.get("client_mail", "")),
                client_real_phone=str(r.get("client_real_phone", "")),
                client_proxy_number=str(r.get("client_proxy_number", "")),
                client_iso_residency=str(r.get("client_iso_residency", "")),
                client_country_code=str(r.get("client_country_code", "")),
            )
            for r in self._all_records()
        ]


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


def make_proxy_mock(client_id: str, country_code: str) -> str:
    # proxy stable et "réaliste" (mais fake) basé sur hash(client_id)
    h = hashlib.sha256(client_id.encode("utf-8")).hexdigest()
    digits = "".join([c for c in h if c.isdigit()])[:9].ljust(9, "0")
    # country_code is like "+33" => keep it and pad digits
    return f"{country_code}{digits}"


def compute_next_client_id(store: ClientStore) -> str:
    """Génère un nouvel ID client en incrémentant le dernier entier connu.

    Si aucun ID numérique n'est trouvé, on commence à 1. Le préfixe éventuel
    (ex: "#" ou "client-") du dernier ID trouvé est conservé.
    """

    max_num = 0
    prefix_for_max = ""

    for client in store.list_all():
        cid = (client.client_id or "").strip()
        m = re.search(r"^(.*?)(\d+)$", cid)
        if not m:
            continue
        num = int(m.group(2))
        if num > max_num:
            max_num = num
            prefix_for_max = m.group(1)

    return f"{prefix_for_max}{max_num + 1}" if prefix_for_max else str(max_num + 1)


def do_create_client(args: argparse.Namespace, store: ClientStore, logger: logging.Logger) -> int:
    client_id = (getattr(args, "client_id", "") or "").strip()
    if not client_id:
        client_id = compute_next_client_id(store)
        logger.info("ID attribué automatiquement.", extra={"client_id": client_id})

    existing = store.get_by_id(client_id)

    client_name = (args.name or (existing.client_name if existing else "")).strip()
    if not client_name:
        raise ValidationError("--name requis.")
    client_mail = (args.client_mail or (existing.client_mail if existing else "")).strip()
    if not client_mail:
        raise ValidationError("--client-mail requis.")

    real_phone_input = args.client_real_phone or (existing.client_real_phone if existing else "")
    client_real_phone = validate_e164(real_phone_input, "client_real_phone")

    cc = extract_country_code_simple(client_real_phone)
    iso_residency = (args.client_iso_residency or (existing.client_iso_residency if existing else "")).strip() or cc.replace(
        "+", ""
    )
    iso_residency = iso_residency.upper()
    country_code = (args.client_country_code or (existing.client_country_code if existing else "")).strip() or cc
    if country_code and not country_code.startswith("+"):
        country_code = "+" + country_code

    assign_proxy = getattr(args, "assign_proxy", True)

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
        proxy = ""

    client = DemoClient(
        client_id=client_id,
        client_name=client_name,
        client_mail=client_mail,
        client_real_phone=client_real_phone,
        client_proxy_number=normalize_e164_like(proxy) if proxy else "",
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
    proxy = validate_e164(args.proxy, "proxy")
    client = store.get_by_proxy(proxy)
    if not client:
        raise NotFoundError("Aucun client trouvé pour ce proxy.", details={"proxy": proxy})
    logger.info("Client trouvé.")
    print(json.dumps(dataclasses.asdict(client), indent=2, ensure_ascii=False))
    return 0


def do_simulate_call(args: argparse.Namespace, store: ClientStore, logger: logging.Logger) -> int:
    caller = validate_e164(args.from_number, "from")
    proxy = validate_e164(args.to_number, "to (proxy)")

    client = store.get_by_proxy(proxy)
    if not client:
        raise NotFoundError("Proxy inconnu (aucun client associé).", details={"proxy": proxy})

    caller_cc = extract_country_code_simple(caller)
    if client.client_country_code and caller_cc != client.client_country_code:
        logger.warning("Routage refusé (country mismatch).")
        print(twiml_block("Sorry, calls are only allowed from the same country."))
        return 0

    logger.info("Routage autorisé (Dial vers phone_real).")
    print(
        twiml_dial(
            proxy_number=normalize_e164_like(client.client_proxy_number),
            real_number=normalize_e164_like(client.client_real_phone),
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
    args2 = argparse.Namespace(
        client_id=args.client_id,
        name=args.name,
        client_mail=args.client_mail,
        client_real_phone=args.client_real_phone,
        client_iso_residency=args.client_iso_residency,
        client_country_code=args.client_country_code,
        mode=args.mode,
    )
    do_create_client(args2, store, logger)

    client = store.get_by_id(args.client_id)
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
    c1.add_argument("--client-iso-residency", default="")
    c1.add_argument("--client-country-code", default="")
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
    c4.add_argument("--client-iso-residency", default="")
    c4.add_argument("--client-country-code", default="")

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


def interactive_menu(args: argparse.Namespace, store: ClientStore, logger: logging.Logger) -> int:
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
                        client_real_phone = input("Numéro réel (ex: +33123456789) : ").strip() or "+33123456789"
                        assign_proxy_answer = (input("Attribuer un proxy maintenant ? [O/n] : ").strip().lower() or "o")
                        assign_proxy = not assign_proxy_answer.startswith("n")
                        args_client = argparse.Namespace(
                            client_id=client_id,
                            name=name,
                            client_mail=client_mail,
                            client_real_phone=client_real_phone,
                            client_iso_residency="",
                            client_country_code="",
                            assign_proxy=assign_proxy,
                            mode=args.mode,
                        )
                        try:
                            do_create_client(args_client, store, logger)
                        except CLIError as exc:
                            logger.error("Erreur création client: %s", exc)
                        continue

                    if sub_choice == "2":
                        print("Rechercher par :")
                        print("  1) ID client")
                        print("  2) Numéro proxy")
                        lookup_choice = input("Votre sélection (1 par défaut) : ").strip() or "1"
                        if lookup_choice == "1":
                            client_id = input("ID client (ex: demo-client) : ").strip() or "demo-client"
                            found = store.get_by_id(client_id)
                        elif lookup_choice == "2":
                            proxy = input("Numéro proxy (ex: +33900000000) : ").strip()
                            try:
                                proxy_norm = validate_e164(proxy, "proxy")
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
                        existing = store.get_by_id(client_id)
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
                            client_iso_residency=existing.client_iso_residency,
                            client_country_code=existing.client_country_code,
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
                from_number = input("Numéro appelant (même pays, ex: +33111111111) : ").strip() or "+33111111111"
                to_number = input("Numéro proxy appelé (ex: +33900000000) : ").strip() or "+33900000000"
                args_call = argparse.Namespace(from_number=from_number, to_number=to_number)
                try:
                    do_simulate_call(args_call, store, logger)
                except CLIError as exc:
                    logger.error("Erreur simulation appel autorisé: %s", exc)
                continue

            if choice == "3":
                logger.info("Menu 3: simulation appel bloqué.")
                from_number = input("Numéro appelant (autre pays, ex: +442222222222) : ").strip() or "+442222222222"
                to_number = input("Numéro proxy appelé (ex: +33900000000) : ").strip() or "+33900000000"
                args_call = argparse.Namespace(from_number=from_number, to_number=to_number)
                try:
                    do_simulate_call(args_call, store, logger)
                except CLIError as exc:
                    logger.error("Erreur simulation appel bloqué: %s", exc)
                continue

            logger.warning("Choix inconnu: %s", choice)
            print("Veuillez choisir 0, 1, 2 ou 3.\n")
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
                else:
                    return exc.exit_code
            else:
                return exc.exit_code

        args.mode = mode

        if args.cmd is None:
            return interactive_menu(args, store, logger)

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
