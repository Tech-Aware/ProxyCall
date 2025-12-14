import logging

import pytest
from twilio.twiml.voice_response import VoiceResponse

from models.client import Client
from services import call_routing_service
from services.call_routing_service import CallRoutingService

logging.basicConfig(level=logging.INFO)


class DummyRepo:
    def __init__(self, client: Client | None = None, should_raise: Exception | None = None):
        self.client = client
        self.should_raise = should_raise
        self.calls = 0

    def get_by_proxy_number(self, proxy_number: str):
        self.calls += 1
        if self.should_raise:
            raise self.should_raise
        return self.client


def _as_twiml(xml: str) -> VoiceResponse:
    resp = VoiceResponse()
    resp.append(xml)
    return resp


def test_handle_incoming_call_unknown_proxy(monkeypatch):
    dummy_repo = DummyRepo()
    monkeypatch.setattr(call_routing_service, "ClientsRepository", dummy_repo)

    twiml = CallRoutingService.handle_incoming_call(proxy_number="+33900000000", caller_number="+33700000000")

    assert "n'est pas reconnu" in twiml
    assert dummy_repo.calls == 1


def test_handle_incoming_call_country_mismatch(monkeypatch):
    client = Client(
        client_id="c1",
        client_name="Test",
        client_mail="test@example.com",
        client_real_phone="+442012345678",
        client_proxy_number="+33900000000",
        client_country_code="44",
    )
    dummy_repo = DummyRepo(client)
    monkeypatch.setattr(call_routing_service, "ClientsRepository", dummy_repo)

    twiml = CallRoutingService.handle_incoming_call(proxy_number="+33900000000", caller_number="+33101010101")

    assert "n'est pas accessible" in twiml


def test_handle_incoming_call_success(monkeypatch):
    client = Client(
        client_id="c1",
        client_name="Test",
        client_mail="test@example.com",
        client_real_phone="+33123456789",
        client_proxy_number="+33900000000",
        client_country_code="33",
    )
    dummy_repo = DummyRepo(client)
    monkeypatch.setattr(call_routing_service, "ClientsRepository", dummy_repo)

    twiml = CallRoutingService.handle_incoming_call(proxy_number="+33900000000", caller_number="+33101010101")

    assert "Dial" in twiml
    assert "+33900000000" in twiml
    assert "+33123456789" in twiml


def test_handle_incoming_call_repository_failure(monkeypatch):
    dummy_repo = DummyRepo(should_raise=RuntimeError("boom"))
    monkeypatch.setattr(call_routing_service, "ClientsRepository", dummy_repo)

    twiml = CallRoutingService.handle_incoming_call(proxy_number="+33900000000", caller_number="+33101010101")

    assert "temporairement indisponible" in twiml
