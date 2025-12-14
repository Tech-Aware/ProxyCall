import types

import pytest

from integrations import sheets_client
from integrations.sheets_client import SheetsClient


@pytest.fixture(autouse=True)
def reset_gc():
    # Réinitialise le singleton pour éviter les fuites entre tests
    sheets_client.gc = None
    yield
    sheets_client.gc = None


def test_get_clients_sheet_with_lazy_init(monkeypatch):
    calls = {}

    def fake_from_service_account_file(path, scopes):
        calls["creds"] = {"path": path, "scopes": scopes}
        return "fake-creds"

    fake_gc = types.SimpleNamespace(open=lambda name: types.SimpleNamespace(worksheet=lambda w: f"sheet:{w}"))

    monkeypatch.setattr(sheets_client, "Credentials", types.SimpleNamespace(from_service_account_file=fake_from_service_account_file))
    monkeypatch.setattr(sheets_client, "gspread", types.SimpleNamespace(authorize=lambda creds: fake_gc))
    monkeypatch.setattr(sheets_client.settings, "GOOGLE_SERVICE_ACCOUNT_FILE", "/tmp/key.json")
    monkeypatch.setattr(sheets_client.settings, "GOOGLE_SHEET_NAME", "Demo")

    sheet = SheetsClient.get_clients_sheet()

    assert sheet == "sheet:Clients"
    assert calls["creds"]["path"] == "/tmp/key.json"


def test_get_gc_missing_service_account(monkeypatch):
    monkeypatch.setattr(sheets_client.settings, "GOOGLE_SERVICE_ACCOUNT_FILE", "")

    with pytest.raises(FileNotFoundError):
        SheetsClient.get_clients_sheet()

