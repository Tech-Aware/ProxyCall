import unittest
from unittest.mock import patch

from integrations.twilio_client import TwilioClient


class _FakeIncomingNumber:
    def __init__(self, *, sms_url="", sms_method="", voice_url="", voice_method=""):
        self.sms_url = sms_url
        self.sms_method = sms_method
        self.voice_url = voice_url
        self.voice_method = voice_method
        self.update_calls = []

    def update(self, **kwargs):
        self.update_calls.append(kwargs)
        if "sms_url" in kwargs:
            self.sms_url = kwargs["sms_url"]
        if "sms_method" in kwargs:
            self.sms_method = kwargs["sms_method"]
        if "voice_url" in kwargs:
            self.voice_url = kwargs["voice_url"]
        if "voice_method" in kwargs:
            self.voice_method = kwargs["voice_method"]


class _FakeIncomingPhoneNumbersApi:
    def __init__(self, number):
        self.number = number

    def list(self, **kwargs):
        return [self.number]


class TwilioWebhookEnsureTests(unittest.TestCase):
    @patch("integrations.twilio_client.settings")
    @patch("integrations.twilio_client.twilio")
    def test_ensure_messaging_webhook_updates_when_method_not_post(self, twilio_mock, settings_mock):
        settings_mock.MESSAGING_WEBHOOK_URL = "https://proxycall.onrender.com/twilio/sms"
        number = _FakeIncomingNumber(
            sms_url="https://proxycall.onrender.com/twilio/sms",
            sms_method="GET",
        )
        twilio_mock.incoming_phone_numbers = _FakeIncomingPhoneNumbersApi(number)

        updated = TwilioClient.ensure_messaging_webhook("+33939242476")

        self.assertTrue(updated)
        self.assertEqual(len(number.update_calls), 1)
        self.assertEqual(
            number.update_calls[0],
            {"sms_url": "https://proxycall.onrender.com/twilio/sms", "sms_method": "POST"},
        )

    @patch("integrations.twilio_client.settings")
    @patch("integrations.twilio_client.twilio")
    def test_ensure_messaging_webhook_no_update_when_url_and_method_already_ok(self, twilio_mock, settings_mock):
        settings_mock.MESSAGING_WEBHOOK_URL = "https://proxycall.onrender.com/twilio/sms"
        number = _FakeIncomingNumber(
            sms_url="https://proxycall.onrender.com/twilio/sms",
            sms_method="POST",
        )
        twilio_mock.incoming_phone_numbers = _FakeIncomingPhoneNumbersApi(number)

        updated = TwilioClient.ensure_messaging_webhook("+33939242476")

        self.assertFalse(updated)
        self.assertEqual(number.update_calls, [])

    @patch("integrations.twilio_client.settings")
    @patch("integrations.twilio_client.twilio")
    def test_ensure_voice_webhook_updates_when_method_not_post(self, twilio_mock, settings_mock):
        settings_mock.VOICE_WEBHOOK_URL = "https://proxycall.onrender.com/twilio/voice"
        number = _FakeIncomingNumber(
            voice_url="https://proxycall.onrender.com/twilio/voice",
            voice_method="GET",
        )
        twilio_mock.incoming_phone_numbers = _FakeIncomingPhoneNumbersApi(number)

        updated = TwilioClient.ensure_voice_webhook("+33939242476")

        self.assertTrue(updated)
        self.assertEqual(len(number.update_calls), 1)
        self.assertEqual(
            number.update_calls[0],
            {"voice_url": "https://proxycall.onrender.com/twilio/voice", "voice_method": "POST"},
        )


if __name__ == "__main__":
    unittest.main()
