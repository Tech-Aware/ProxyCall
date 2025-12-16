import re

def mask_phone(number: str | None) -> str:
    if not number:
        return ""
    # garde + puis masque tout sauf 4 derniers chiffres
    digits = re.sub(r"\D", "", str(number))
    if len(digits) <= 4:
        return f"+****{digits}"
    return f"+****{digits[-4:]}"

def mask_sid(value: str | None) -> str:
    if not value:
        return ""
    s = str(value)
    if len(s) <= 8:
        return "****"
    return f"{s[:4]}…{s[-4:]}"
