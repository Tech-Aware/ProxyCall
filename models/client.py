from dataclasses import dataclass
from typing import Optional


@dataclass
class Client:
    client_id: str
    client_name: str
    client_mail: str
    client_real_phone: str
    client_proxy_number: Optional[str] = None
    client_iso_residency: Optional[str] = None
    client_country_code: Optional[str] = None
    client_last_caller: Optional[str] = None

