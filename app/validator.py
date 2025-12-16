# app/validator.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional


# =========================
# Errors
# =========================
@dataclass
class ValidationIssue(Exception):
    message: str
    field: str
    value: Any = None

    def __str__(self) -> str:
        if self.value is None:
            return f"{self.field}: {self.message}"
        return f"{self.field}: {self.message} (value={self.value!r})"


# =========================
# Regex (strict)
# =========================
_RE_INT_STRICT = re.compile(r"^[0-9]+$")
_RE_E164_STRICT = re.compile(r"^\+[1-9]\d{7,14}$")  # + then 8..15 digits total, no leading 0 in country code
_RE_EMAIL_STRICT = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")  # simple + strict (no spaces)


# =========================
# Helpers
# =========================
def _s(v: Any) -> str:
    return str(v if v is not None else "").strip()


def _reject_phone_separators(raw: str, *, field: str) -> None:
    # Explicitly reject common separators to enforce "hyper strict" E.164
    forbidden = (" ", "-", "(", ")", ".", "/", "\\")
    if any(ch in raw for ch in forbidden):
        raise ValidationIssue(
            "format E.164 strict requis (ex: +33601020304) — sans espaces/tirets/parenthèses",
            field=field,
            value=raw,
        )


# =========================
# Validators
# =========================
def int_strict(value: Any, *, field: str, min_value: int = 1, max_value: Optional[int] = None) -> int:
    """
    Strict int:
    - accepts int or digit-only string
    - rejects floats ("5.0"), scientific notation, hex, etc.
    """
    if isinstance(value, bool):
        raise ValidationIssue("entier strict requis (bool interdit)", field=field, value=value)

    if isinstance(value, int):
        n = value
    else:
        raw = _s(value)
        if not raw:
            raise ValidationIssue("valeur manquante", field=field)
        if not _RE_INT_STRICT.match(raw):
            raise ValidationIssue("entier strict requis (pas de float/texte)", field=field, value=raw)
        n = int(raw)

    if n < min_value:
        raise ValidationIssue(f"doit être >= {min_value}", field=field, value=n)
    if max_value is not None and n > max_value:
        raise ValidationIssue(f"doit être <= {max_value}", field=field, value=n)
    return n


def email_strict(value: Any, *, field: str = "email") -> str:
    raw = _s(value)
    if not raw:
        raise ValidationIssue("valeur manquante", field=field)
    if not _RE_EMAIL_STRICT.match(raw):
        raise ValidationIssue("email invalide", field=field, value=raw)
    if len(raw) > 254:
        raise ValidationIssue("email trop long", field=field, value=raw)
    return raw


def name_strict(value: Any, *, field: str = "name", max_len: int = 120) -> str:
    raw = _s(value)
    if not raw:
        raise ValidationIssue("valeur manquante", field=field)
    if len(raw) > max_len:
        raise ValidationIssue(f"trop long (max {max_len})", field=field, value=raw)
    # reject control chars
    if any(ord(c) < 32 for c in raw):
        raise ValidationIssue("contient des caractères de contrôle", field=field, value=raw)
    return raw


def phone_e164_strict(value: Any, *, field: str = "phone") -> str:
    """
    Hyper strict:
    - must be +[digits] only
    - rejects "00.." and local formats, and rejects separators/spaces.
    """
    raw = _s(value)
    if not raw:
        raise ValidationIssue("valeur manquante", field=field)

    # Explicitly reject common variants
    if raw.startswith("00"):
        raise ValidationIssue("doit commencer par '+' (E.164 strict), pas '00...'", field=field, value=raw)

    _reject_phone_separators(raw, field=field)

    if not _RE_E164_STRICT.match(raw):
        raise ValidationIssue("format E.164 strict requis (ex: +33601020304)", field=field, value=raw)
    return raw


def iso_country_strict(value: Any, *, field: str = "country_iso") -> str:
    raw = _s(value).upper()
    if not raw:
        raise ValidationIssue("valeur manquante", field=field)
    if len(raw) != 2 or not raw.isalpha():
        raise ValidationIssue("ISO pays invalide (ex: FR, US)", field=field, value=raw)
    return raw


def number_type_strict(value: Any, *, field: str = "number_type") -> str:
    """
    Normalise et valide:
    - "national" => "local"
    - autorisés: "mobile", "local"
    """
    raw = _s(value).lower()
    if not raw:
        raise ValidationIssue("valeur manquante", field=field)

    if raw == "national":
        raw = "local"

    if raw not in ("mobile", "local"):
        raise ValidationIssue("type invalide (attendu: mobile/local/national)", field=field, value=raw)
    return raw
