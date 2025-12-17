import logging

from models.client import Client
from services import message_routing_service
from services.message_routing_service import MessageRoutingService


logging.basicConfig(level=logging.INFO)


class DummyRepo:
    def __init__(self, client: Client | None = None):
        self.client = client
        self.calls = 0
        self.last_update: tuple[str, str] | None = None

    def get_by_proxy_number(self, proxy_number: str):
        self.calls += 1
        return self.client

    def update_last_caller_by_proxy(self, proxy_number: str, caller_number: str) -> None:
        self.last_update = (proxy_number, caller_number)


class DummyTwilio:
    def __init__(self):
        self.sent: list[dict[str, str]] = []

    def send_sms(self, *, from_number: str, to_number: str, body: str):
        self.sent.append({"from": from_number, "to": to_number, "body": body})
        return {"sid": "SM123", "to": to_number}


def test_sms_unknown_proxy(monkeypatch):
    dummy_repo = DummyRepo()
    monkeypatch.setattr(message_routing_service, "ClientsRepository", dummy_repo)
    monkeypatch.setattr(message_routing_service, "TwilioClient", DummyTwilio())

    twiml = MessageRoutingService.handle_incoming_sms(
        proxy_number="+33900000000", sender_number="+33700000000", body="test"
    )

    assert "n'est pas reconnu" in twiml
    assert dummy_repo.calls == 1


def test_sms_country_mismatch(monkeypatch):
    client = Client(
        client_id="c1",
        client_name="Test",
        client_mail="test@example.com",
        client_real_phone="+442012345678",
        client_proxy_number="+33900000000",
        client_country_code="44",
    )
    dummy_repo = DummyRepo(client)
    monkeypatch.setattr(message_routing_service, "ClientsRepository", dummy_repo)
    monkeypatch.setattr(message_routing_service, "TwilioClient", DummyTwilio())

    twiml = MessageRoutingService.handle_incoming_sms(
        proxy_number="+33900000000", sender_number="+33101010101", body="test"
    )

    assert "n'est pas accessible" in twiml


def test_sms_relay_to_client(monkeypatch):
    client = Client(
        client_id="c1",
        client_name="Test",
        client_mail="test@example.com",
        client_real_phone="+33123456789",
        client_proxy_number="+33900000000",
        client_country_code="33",
    )
    dummy_repo = DummyRepo(client)
    dummy_twilio = DummyTwilio()
    monkeypatch.setattr(message_routing_service, "ClientsRepository", dummy_repo)
    monkeypatch.setattr(message_routing_service, "TwilioClient", dummy_twilio)

    twiml = MessageRoutingService.handle_incoming_sms(
        proxy_number="+33900000000", sender_number="+33101010101", body="Bonjour"
    )

    assert "Response" in twiml
    assert dummy_repo.last_update == ("+33900000000", "+33101010101")
    assert dummy_twilio.sent[0]["to"] == "+33123456789"
    assert dummy_twilio.sent[0]["body"] == "Bonjour"


def test_sms_from_client_without_history(monkeypatch):
    client = Client(
        client_id="c1",
        client_name="Test",
        client_mail="test@example.com",
        client_real_phone="+33123456789",
        client_proxy_number="+33900000000",
        client_country_code="33",
        client_last_caller=None,
    )
    dummy_repo = DummyRepo(client)
    dummy_twilio = DummyTwilio()
    monkeypatch.setattr(message_routing_service, "ClientsRepository", dummy_repo)
    monkeypatch.setattr(message_routing_service, "TwilioClient", dummy_twilio)

    twiml = MessageRoutingService.handle_incoming_sms(
        proxy_number="+33900000000", sender_number="+33123456789", body="Bonjour"
    )

    assert "Aucun correspondant récent" in twiml
    assert not dummy_twilio.sent


def test_sms_from_client_to_last_caller(monkeypatch):
    client = Client(
        client_id="c1",
        client_name="Test",
        client_mail="test@example.com",
        client_real_phone="+33123456789",
        client_proxy_number="+33900000000",
        client_country_code="33",
        client_last_caller="+33987654321",
    )
    dummy_repo = DummyRepo(client)
    dummy_twilio = DummyTwilio()
    monkeypatch.setattr(message_routing_service, "ClientsRepository", dummy_repo)
    monkeypatch.setattr(message_routing_service, "TwilioClient", dummy_twilio)

    twiml = MessageRoutingService.handle_incoming_sms(
        proxy_number="+33900000000", sender_number="+33123456789", body="Rebonjour"
    )

    assert "Response" in twiml
    assert dummy_twilio.sent[0]["to"] == "+33987654321"
    assert dummy_twilio.sent[0]["body"] == "Rebonjour"
