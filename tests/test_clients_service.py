import logging
import pytest

from models.client import Client
from services import clients_service
from services.clients_service import (
    ClientsService,
    ClientAlreadyExistsError,
    _resolve_twilio_country_code,
    _to_twilio_country_code,
    extract_country_code,
)


logging.basicConfig(level=logging.INFO)


class DummyRepo:
    def __init__(self, existing: Client | None = None):
        self.existing = existing
        self.saved: list[Client] = []

    def get_by_id(self, client_id: str):
        return self.existing

    def save(self, client: Client):
        logging.info("DummyRepo.save called", extra={"client_id": client.client_id})
        self.saved.append(client)


class DummyTwilio:
    def __init__(self, number: str = "+3399990000"):
        self.number = number
        self.calls: list[dict] = []

    def buy_number_for_client(self, **kwargs):
        self.calls.append(kwargs)
        return self.number


@pytest.mark.parametrize(
    "phone,expected",
    [
        ("+33123456789", "+33"),
        ("  442012345678", "+44"),
        ("", ""),
    ],
)
def test_extract_country_code(phone, expected):
    assert extract_country_code(phone) == expected


def test_twilio_country_code_mapping_and_validation():
    assert _to_twilio_country_code("+33") == "FR"
    assert _to_twilio_country_code("fr") == "FR"
    with pytest.raises(ValueError):
        _to_twilio_country_code("")
    with pytest.raises(ValueError):
        _to_twilio_country_code("999")


def test_resolve_twilio_country_code_prefers_iso():
    assert _resolve_twilio_country_code("de", "+33123456789") == "DE"


@pytest.mark.parametrize(
    "iso,phone,expected",
    [
        (None, "+33123456789", "FR"),
        (None, "+447000000000", "GB"),
    ],
)
def test_resolve_twilio_country_code_from_phone(iso, phone, expected):
    assert _resolve_twilio_country_code(iso, phone) == expected


def test_resolve_twilio_country_code_error_message():
    with pytest.raises(ValueError) as excinfo:
        _resolve_twilio_country_code("XXX", "abc")
    assert "Impossible de déterminer le pays Twilio" in str(excinfo.value)


def test_create_client_success(monkeypatch):
    repo = DummyRepo()
    fake_twilio = DummyTwilio("+3390001111")
    monkeypatch.setattr(clients_service, "ClientsRepository", repo)
    monkeypatch.setattr(clients_service, "TwilioClient", fake_twilio)

    client = ClientsService.create_client(
        client_id="abc",
        client_name="Test Client",
        client_mail="test@example.com",
        client_real_phone="+33123456789",
        client_iso_residency="FR",
    )

    assert client.client_proxy_number == "+3390001111"
    assert repo.saved and repo.saved[0].client_id == "abc"
    assert fake_twilio.calls[0]["friendly_name"] == "Client-abc"


def test_create_client_already_exists(monkeypatch):
    existing = Client(
        client_id="abc",
        client_name="Existing",
        client_mail="exist@example.com",
        client_real_phone="+33123456789",
        client_proxy_number="+3399990000",
    )
    repo = DummyRepo(existing)
    monkeypatch.setattr(clients_service, "ClientsRepository", repo)

    with pytest.raises(ClientAlreadyExistsError):
        ClientsService.create_client(
            client_id="abc",
            client_name="Test Client",
            client_mail="test@example.com",
            client_real_phone="+33123456789",
            client_iso_residency="FR",
        )


def test_get_or_create_returns_existing(monkeypatch):
    existing = Client(
        client_id="abc",
        client_name="Existing",
        client_mail="exist@example.com",
        client_real_phone="+33123456789",
        client_proxy_number="+3399990000",
    )
    repo = DummyRepo(existing)
    monkeypatch.setattr(clients_service, "ClientsRepository", repo)

    result = ClientsService.get_or_create_client(
        client_id="abc",
        client_name="Ignored",
        client_mail="ignored@example.com",
        client_real_phone="+33123456789",
    )

    assert result is existing
    assert not repo.saved
