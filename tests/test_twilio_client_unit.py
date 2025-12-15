import types

import pytest

from integrations import twilio_client
from integrations.twilio_client import TwilioClient


class DummyPoolsRepo:
    def __init__(self, available=None):
        self.available = list(available or [])
        self.mark_assigned_calls = []
        self.saved_numbers = []

    def list_available(self, country_iso):
        return list(self.available)

    def mark_assigned(self, **kwargs):
        self.mark_assigned_calls.append(kwargs)

    def save_number(self, **kwargs):
        self.saved_numbers.append(kwargs)
        # Simule l'ajout d'un numéro disponible dans le pool
        self.available.append({"phone_number": kwargs.get("phone_number")})


class DummyNumber:
    def __init__(self, phone_number="+33123456789"):
        self.phone_number = phone_number

    def update(self, **kwargs):
        return kwargs


class DummyTwilio:
    def __init__(self, numbers=None):
        self._numbers = [DummyNumber()] if numbers is None else list(numbers)
        self.purchase_calls = []
        self.update_calls = []

        self._available_local = types.SimpleNamespace(list=lambda limit=1: list(self._numbers))
        self._available_mobile = types.SimpleNamespace(list=lambda limit=1: list(self._numbers))

        self.incoming_phone_numbers = types.SimpleNamespace(create=self._create, list=self._list)

    def available_phone_numbers(self, country):
        return types.SimpleNamespace(local=self._available_local, mobile=self._available_mobile)

    def _create(self, phone_number, voice_url, friendly_name, **kwargs):
        payload = {
            "phone_number": phone_number,
            "voice_url": voice_url,
            "friendly_name": friendly_name,
        }
        payload.update(kwargs)
        self.purchase_calls.append(payload)
        return DummyNumber(phone_number)

    def _list(self, phone_number):
        return [DummyNumber(phone_number)]


@pytest.fixture(autouse=True)
def reset_twilio_global():
    # Nettoyage du singleton Twilio pour chaque test
    original_twilio = twilio_client.twilio
    yield
    twilio_client.twilio = original_twilio


def test_buy_number_uses_available_pool(monkeypatch):
    dummy_repo = DummyPoolsRepo(available=[{"phone_number": "+3399990000"}])
    monkeypatch.setattr(twilio_client, "PoolsRepository", dummy_repo)
    dummy_twilio = DummyTwilio()
    monkeypatch.setattr(twilio_client, "twilio", dummy_twilio)

    number = TwilioClient.buy_number_for_client(friendly_name="Client-1", country="FR", attribution_to_client_name="Client 1")

    assert number == "+3399990000"
    assert dummy_repo.mark_assigned_calls[0]["phone_number"] == "+3399990000"


def test_buy_number_fills_pool_when_empty(monkeypatch):
    dummy_repo = DummyPoolsRepo(available=[])
    # On épingle les appels sur l'instance, pas sur la classe
    monkeypatch.setattr(twilio_client, "PoolsRepository", dummy_repo)

    purchased_number = DummyNumber("+44700000000")
    dummy_twilio = DummyTwilio(numbers=[purchased_number])
    monkeypatch.setattr(twilio_client, "twilio", dummy_twilio)

    number = TwilioClient.buy_number_for_client(friendly_name="Client-2", country="GB", attribution_to_client_name="Client 2")

    assert number == "+44700000000"
    assert dummy_repo.saved_numbers, "Le remplissage du pool doit persister les numéros achetés"
    assert dummy_repo.mark_assigned_calls, "Le numéro doit être marqué comme attribué"


def test_purchase_number_without_availability(monkeypatch):
    dummy_twilio = DummyTwilio(numbers=[])
    monkeypatch.setattr(twilio_client, "twilio", dummy_twilio)

    with pytest.raises(RuntimeError):
        TwilioClient._purchase_number(country="FR", friendly_name="Test")


def test_purchase_number_fr_local_requires_bundle(monkeypatch):
    dummy_twilio = DummyTwilio(numbers=[DummyNumber("+33123456789")])
    monkeypatch.setattr(twilio_client, "twilio", dummy_twilio)
    monkeypatch.setattr(twilio_client.settings, "TWILIO_BUNDLE_SID", None)

    with pytest.raises(RuntimeError):
        TwilioClient._purchase_number(country="FR", friendly_name="Test", number_type="local")


def test_purchase_number_local_uses_bundle_when_provided(monkeypatch):
    dummy_twilio = DummyTwilio()
    monkeypatch.setattr(twilio_client, "twilio", dummy_twilio)
    monkeypatch.setattr(twilio_client.settings, "TWILIO_BUNDLE_SID", "BU123")
    monkeypatch.setattr(twilio_client.settings, "TWILIO_ADDRESS_SID", None)

    TwilioClient._purchase_number(country="FR", friendly_name="Test", number_type="local")

    assert dummy_twilio.purchase_calls[0]["bundle_sid"] == "BU123"

