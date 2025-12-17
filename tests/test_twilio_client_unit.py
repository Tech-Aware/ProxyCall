import types

import pytest
from twilio.base.exceptions import TwilioRestException

from integrations import twilio_client
from integrations.twilio_client import TwilioClient


class DummyPoolsRepo:
    def __init__(self, available=None, records=None):
        self.available = list(available or [])
        self.records = list(records) if records is not None else list(self.available)
        self.mark_assigned_calls = []
        self.saved_numbers = []

    def list_available(self, country_iso):
        return list(self.available)

    def list_all(self):
        return list(self.records)

    def mark_assigned(self, **kwargs):
        self.mark_assigned_calls.append(kwargs)

    def save_number(self, **kwargs):
        self.saved_numbers.append(kwargs)
        # Simule l'ajout d'un numéro disponible dans le pool
        self.available.append({"phone_number": kwargs.get("phone_number")})
        self.records.append(
            {
                "phone_number": kwargs.get("phone_number"),
                "country_iso": kwargs.get("country_iso"),
                "status": kwargs.get("status", "available"),
            }
        )


class DummyNumber:
    def __init__(
        self,
        phone_number="+33123456789",
        friendly_name="",
        iso_country="FR",
        capabilities=None,
    ):
        self.phone_number = phone_number
        self.friendly_name = friendly_name
        self.iso_country = iso_country
        self.capabilities = capabilities or {}
        self.sms_enabled = bool(self.capabilities.get("sms"))
        self.voice_enabled = bool(self.capabilities.get("voice"))

    def update(self, **kwargs):
        return kwargs


class DummyTwilio:
    def __init__(self, numbers=None, create_exception=None):
        self._numbers = [DummyNumber()] if numbers is None else list(numbers)
        self.purchase_calls = []
        self.update_calls = []
        self.create_exception = create_exception

        self.last_list_calls = []

        self._available_local = types.SimpleNamespace(list=self._list_available)
        self._available_mobile = types.SimpleNamespace(list=self._list_available)

        self.incoming_phone_numbers = types.SimpleNamespace(create=self._create, list=self._list)

    def available_phone_numbers(self, country):
        return types.SimpleNamespace(local=self._available_local, mobile=self._available_mobile)

    def _list_available(self, limit=1, **kwargs):
        params = {"limit": limit}
        params.update(kwargs)
        self.last_list_calls.append(params)
        return list(self._numbers)

    def _create(self, phone_number, voice_url, friendly_name, **kwargs):
        if self.create_exception:
            raise self.create_exception
        payload = {
            "phone_number": phone_number,
            "voice_url": voice_url,
            "friendly_name": friendly_name,
        }
        payload.update(kwargs)
        self.purchase_calls.append(payload)
        return DummyNumber(phone_number)

    def _list(self, phone_number=None):
        if phone_number:
            return [DummyNumber(phone_number)]
        return list(self._numbers)


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


def test_purchase_number_local_uses_bundle_when_provided(monkeypatch):
    dummy_twilio = DummyTwilio()
    monkeypatch.setattr(twilio_client, "twilio", dummy_twilio)
    monkeypatch.setattr(twilio_client.settings, "TWILIO_BUNDLE_SID", "BU123")
    monkeypatch.setattr(twilio_client.settings, "TWILIO_ADDRESS_SID", None)

    TwilioClient._purchase_number(country="FR", friendly_name="Test", number_type="local")

    assert dummy_twilio.purchase_calls[0]["bundle_sid"] == "BU123"


def test_purchase_number_local_bundle_address_mismatch(monkeypatch):
    exc = TwilioRestException(status=400, uri="", msg="", code=21651)
    dummy_twilio = DummyTwilio(create_exception=exc)
    monkeypatch.setattr(twilio_client, "twilio", dummy_twilio)
    monkeypatch.setattr(twilio_client.settings, "TWILIO_BUNDLE_SID", "BU123")
    monkeypatch.setattr(twilio_client.settings, "TWILIO_ADDRESS_SID", "AD123")

    with pytest.raises(RuntimeError) as err:
        TwilioClient._purchase_number(country="FR", friendly_name="Test", number_type="local")

    assert "rattachée au bundle" in str(err.value)


def test_purchase_number_local_bundle_required(monkeypatch):
    exc = TwilioRestException(status=400, uri="", msg="", code=21649)
    dummy_twilio = DummyTwilio(create_exception=exc)
    monkeypatch.setattr(twilio_client, "twilio", dummy_twilio)
    monkeypatch.setattr(twilio_client.settings, "TWILIO_BUNDLE_SID", None)
    monkeypatch.setattr(twilio_client.settings, "TWILIO_ADDRESS_SID", None)

    with pytest.raises(RuntimeError) as err:
        TwilioClient._purchase_number(country="FR", friendly_name="Test", number_type="local")

    assert "bundle" in str(err.value)


def test_purchase_number_uses_sms_and_voice_filters(monkeypatch):
    dummy_twilio = DummyTwilio()
    monkeypatch.setattr(twilio_client, "twilio", dummy_twilio)

    TwilioClient._purchase_number(
        country="FR",
        friendly_name="Test",
        number_type="local",
        candidates_limit=5,
    )

    assert dummy_twilio.last_list_calls, "La recherche Twilio doit être appelée"
    params = dummy_twilio.last_list_calls[-1]
    assert params["sms_enabled"] is True
    assert params["voice_enabled"] is True
    assert params["limit"] == 5


def test_purchase_number_accepts_sms_only_when_voice_not_required(monkeypatch):
    sms_only = DummyNumber("+33900000000", capabilities={"sms": True, "voice": False})
    dummy_twilio = DummyTwilio(numbers=[sms_only])
    monkeypatch.setattr(twilio_client, "twilio", dummy_twilio)

    purchased = TwilioClient._purchase_number(
        country="FR",
        friendly_name="Test",
        number_type="local",
        require_voice_capability=False,
    )

    assert purchased == "+33900000000"
    params = dummy_twilio.last_list_calls[-1]
    assert params.get("sms_enabled") is True
    assert params.get("voice_enabled") in (None, False)


def test_sync_twilio_numbers_adds_missing(monkeypatch):
    existing_records = [
        {"phone_number": "+33123456789", "country_iso": "FR", "status": "available"}
    ]
    dummy_repo = DummyPoolsRepo(records=existing_records)
    monkeypatch.setattr(twilio_client, "PoolsRepository", dummy_repo)

    dummy_twilio = DummyTwilio(
        numbers=[
            DummyNumber("+33123456789", friendly_name="Existant", iso_country="FR"),
            DummyNumber("+44777000000", friendly_name="Nouveau", iso_country="GB"),
        ]
    )
    monkeypatch.setattr(twilio_client, "twilio", dummy_twilio)

    result = TwilioClient.sync_twilio_numbers_with_sheet()

    assert "+44777000000" in result["added_numbers"]
    assert "+44777000000" in result["missing_numbers"]
    assert dummy_repo.saved_numbers[0]["country_iso"] == "GB"


def test_sync_twilio_numbers_preview_only(monkeypatch):
    existing_records = [
        {"phone_number": "+33123456789", "country_iso": "FR", "status": "available"}
    ]
    dummy_repo = DummyPoolsRepo(records=existing_records)
    monkeypatch.setattr(twilio_client, "PoolsRepository", dummy_repo)

    dummy_twilio = DummyTwilio(
        numbers=[
            DummyNumber("+33123456789", friendly_name="Existant", iso_country="FR"),
            DummyNumber("+44777000000", friendly_name="Nouveau", iso_country="GB"),
        ]
    )
    monkeypatch.setattr(twilio_client, "twilio", dummy_twilio)

    result = TwilioClient.sync_twilio_numbers_with_sheet(apply=False)

    assert result["missing_numbers"] == ["+44777000000"]
    assert not result["added_numbers"]
    assert not dummy_repo.saved_numbers


def test_sync_twilio_numbers_respects_sheet_formatting(monkeypatch):
    existing_records = [
        {"phone_number": " +33 1 23 45 67 89 ", "country_iso": "FR", "status": "available"},
        {"phone_number": 44777000000, "country_iso": "GB", "status": "available"},
    ]
    dummy_repo = DummyPoolsRepo(records=existing_records)
    monkeypatch.setattr(twilio_client, "PoolsRepository", dummy_repo)

    dummy_twilio = DummyTwilio(
        numbers=[
            DummyNumber("+33123456789", friendly_name="Existant", iso_country="FR"),
            DummyNumber("+44777000000", friendly_name="Existant2", iso_country="GB"),
        ]
    )
    monkeypatch.setattr(twilio_client, "twilio", dummy_twilio)

    result = TwilioClient.sync_twilio_numbers_with_sheet()

    assert result["missing_numbers"] == []
    assert result["added_numbers"] == []
    assert not dummy_repo.saved_numbers

