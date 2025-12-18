import json
import logging
from pathlib import Path

from demo.scenarios import cli_command_examples, run_mock_client_journey

logging.basicConfig(level=logging.INFO)


def test_run_mock_client_journey(tmp_path):
    fixtures = tmp_path / "clients.json"
    fixtures.write_text(
        json.dumps(
            [
                {
                    "client_id": 1,
                    "client_name": "Client Démo",
                    "client_mail": "demo@example.com",
                    "client_real_phone": "+33123456789",
                    "client_proxy_number": "+33900000000",
                    "client_iso_residency": "FR",
                    "client_country_code": "33",
                }
            ]
        ),
        encoding="utf-8",
    )

    outputs = run_mock_client_journey(fixtures_path=fixtures)

    assert set(outputs.keys()) == {
        "create_client",
        "lookup",
        "simulate_call_same_country",
        "simulate_call_other_country",
    }
    assert "Client Démo" in outputs["create_client"]
    assert "Twilio" not in outputs["simulate_call_same_country"]
    assert "Sorry" in outputs["simulate_call_other_country"]


def test_cli_command_examples(tmp_path):
    fixtures = tmp_path / "clients.json"
    fixtures.write_text("[]", encoding="utf-8")

    commands = cli_command_examples(fixtures_path=fixtures)

    assert commands and "python -m demo.cli" in commands[0]
