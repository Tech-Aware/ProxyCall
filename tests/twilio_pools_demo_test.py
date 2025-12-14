import logging
from unittest.mock import patch

from app.config import settings
from integrations.twilio_client import TwilioClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FakeNumber:
    def __init__(self, phone_number: str):
        self.phone_number = phone_number


class FakeAvailableLocal:
    def __init__(self, numbers):
        self.numbers = numbers

    def list(self, limit=1):
        if not self.numbers:
            return []
        number = self.numbers.pop(0)
        return [FakeNumber(number)]


class FakeAvailableNumbers:
    def __init__(self, numbers):
        self._numbers = numbers
        self.local = FakeAvailableLocal(self._numbers)


class FakeIncomingNumber:
    def __init__(self, phone_number: str):
        self.phone_number = phone_number

    def update(self, friendly_name: str):  # pragma: no cover - trivial
        return None


class FakeIncomingManager:
    def __init__(self):
        self.created = []

    def create(self, phone_number: str, voice_url: str, friendly_name: str):
        self.created.append(phone_number)
        return FakeIncomingNumber(phone_number)

    def list(self, phone_number: str):
        return [FakeIncomingNumber(phone_number)]


class FakeTwilio:
    def __init__(self, numbers):
        self._numbers = numbers
        self.incoming_phone_numbers = FakeIncomingManager()

    def available_phone_numbers(self, country):
        return FakeAvailableNumbers(self._numbers)


class FakeSheet:
    def __init__(self, records=None):
        self.records = records or []

    def get_all_records(self):
        return [rec.copy() for rec in self.records]

    def append_row(self, row):
        keys = [
            "country_iso",
            "phone_number",
            "status",
            "friendly_name",
            "date_achat",
            "date_attribution",
            "attribution_to_client_name",
        ]
        self.records.append(dict(zip(keys, row)))

    def update(self, range_str, values):
        row_index = int(range_str.split("A")[1].split(":")[0]) - 2
        keys = [
            "country_iso",
            "phone_number",
            "status",
            "friendly_name",
            "date_achat",
            "date_attribution",
            "attribution_to_client_name",
        ]
        self.records[row_index] = dict(zip(keys, values[0]))



def test_pool_purchase_and_assignment_demo():
    fake_sheet = FakeSheet()
    fake_twilio = FakeTwilio(numbers=["+33000000001", "+33000000002"])

    with patch(
        "repositories.pools_repository.SheetsClient.get_pools_sheet",
        return_value=fake_sheet,
    ), patch("integrations.twilio_client.twilio", fake_twilio):
        previous_pool_size = settings.TWILIO_POOL_SIZE
        settings.TWILIO_POOL_SIZE = 2
        try:
            number = TwilioClient.buy_number_for_client(
                friendly_name="Client-demo",
                country="FR",
                attribution_to_client_name="Demo User",
            )
        finally:
            settings.TWILIO_POOL_SIZE = previous_pool_size

    assert number == "+33000000001"
    assert len(fake_sheet.records) == 2
    assert fake_sheet.records[0]["status"] == "assigned"
    assert fake_sheet.records[0]["attribution_to_client_name"] == "Demo User"
    assert fake_sheet.records[1]["status"] == "available"



def test_pool_reuse_available_number_without_purchase_demo():
    fake_sheet = FakeSheet(
        records=[
            {
                "country_iso": "US",
                "phone_number": "+15550000001",
                "status": "available",
                "friendly_name": "Pool-US-1",
                "date_achat": "2024-01-01T00:00:00",
                "date_attribution": "",
                "attribution_to_client_name": "",
            }
        ]
    )

    with patch(
        "repositories.pools_repository.SheetsClient.get_pools_sheet",
        return_value=fake_sheet,
    ), patch("integrations.twilio_client.twilio") as twilio_mock:
        twilio_mock.incoming_phone_numbers.list.return_value = [FakeIncomingNumber("+15550000001")]
        number = TwilioClient.buy_number_for_client(
            friendly_name="Client-reuse",
            country="US",
            attribution_to_client_name="Reuse User",
        )

    assert number == "+15550000001"
    assert fake_sheet.records[0]["status"] == "assigned"
    assert fake_sheet.records[0]["attribution_to_client_name"] == "Reuse User"
