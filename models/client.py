from dataclasses import dataclass
from typing import Optional

@dataclass
class Client:
    client_id: str
    client_name: str
    phone_real: str
    phone_proxy: Optional[str] = None
    country_code: Optional[str] = None
