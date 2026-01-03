import unittest

from app.validator import ValidationIssue, phone_e164_strict


class PhoneE164StrictTests(unittest.TestCase):
    def test_accepts_valid_e164(self):
        expected = "+33601020304"
        self.assertEqual(phone_e164_strict(expected, field="client_proxy_number"), expected)

    def test_auto_prefix_on_digits_only(self):
        normalized = phone_e164_strict("33601020304", field="client_proxy_number")
        self.assertEqual(normalized, "+33601020304")

    def test_rejects_separators(self):
        with self.assertRaises(ValidationIssue):
            phone_e164_strict("+33 60 10 20 304", field="client_proxy_number")

    def test_normalizes_double_zero_prefix(self):
        normalized = phone_e164_strict("0033601020304", field="client_proxy_number")
        self.assertEqual(normalized, "+33601020304")


if __name__ == "__main__":
    unittest.main()
