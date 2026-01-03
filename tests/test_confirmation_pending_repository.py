import unittest
from datetime import datetime
from unittest.mock import patch

from repositories.confirmation_pending_repository import ConfirmationPendingRepository


class _FakeCell:
    def __init__(self, value: str):
        self.value = value


class _FakeSheet:
    def __init__(self, headers, records, rows):
        self._headers = headers
        self._records = records
        self._rows = rows
        self.updated_cells = []

    def row_values(self, row_index: int):
        return self._rows.get(row_index, [])

    def get_all_records(self):
        return self._records

    def update_cell(self, row, col, value):
        existing_row = self._rows.setdefault(row, [""] * len(self._headers))
        if len(existing_row) < col:
            existing_row.extend([""] * (col - len(existing_row)))
        existing_row[col - 1] = value
        self.updated_cells.append((row, col, value))

    def cell(self, row, col):
        row_values = self._rows.get(row, [""] * len(self._headers))
        value = row_values[col - 1] if col - 1 < len(row_values) else ""
        return _FakeCell(value)


class ConfirmationPendingRepositoryTests(unittest.TestCase):
    def test_set_proxy_and_otp_writes_plain_client_data(self):
        headers = [
            "pending_id",
            "client_name",
            "client_mail",
            "client_real_phone",
            "proxy_number",
            "otp",
            "status",
            "created_at",
            "verified_at",
        ]
        records = [
            {
                "pending_id": "abc",
                "client_name": "",
                "client_mail": "",
                "client_real_phone": "",
                "proxy_number": "",
                "otp": "",
                "status": "",
                "created_at": "",
                "verified_at": "",
            }
        ]
        rows = {1: headers, 2: ["abc", "", "", "", "", "", "", "", ""]}
        sheet = _FakeSheet(headers, records, rows)

        with patch(
            "repositories.confirmation_pending_repository.SheetsClient.get_confirmation_pending_sheet",
            return_value=sheet,
        ):
            ConfirmationPendingRepository.set_proxy_and_otp(
                pending_id="abc",
                proxy_number="+33123456789",
                otp="123456",
                client_name="John Doe",
                client_mail="john.doe@test.fr",
                client_real_phone="+33600000000",
            )

        row_values = sheet._rows[2]
        self.assertEqual(row_values[headers.index("client_name")], "John Doe")
        self.assertEqual(row_values[headers.index("client_mail")], "john.doe@test.fr")
        self.assertEqual(row_values[headers.index("client_real_phone")], "+33600000000")
        self.assertEqual(row_values[headers.index("proxy_number")], "+33123456789")
        self.assertEqual(row_values[headers.index("otp")], "123456")
        self.assertEqual(row_values[headers.index("status")], "PENDING")

        created_at = row_values[headers.index("created_at")]
        self.assertTrue(created_at, "created_at doit être renseigné")
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))

    def test_set_proxy_and_otp_ignores_missing_optional_columns(self):
        headers = [
            "pending_id",
            "client_real_phone",
            "proxy_number",
            "otp",
            "status",
            "created_at",
        ]
        records = [
            {
                "pending_id": "abc",
                "client_real_phone": "",
                "proxy_number": "",
                "otp": "",
                "status": "",
                "created_at": "",
            }
        ]
        rows = {1: headers, 2: ["abc", "", "", "", "", ""]}
        sheet = _FakeSheet(headers, records, rows)

        with patch(
            "repositories.confirmation_pending_repository.SheetsClient.get_confirmation_pending_sheet",
            return_value=sheet,
        ):
            ConfirmationPendingRepository.set_proxy_and_otp(
                pending_id="abc",
                proxy_number="+33123456789",
                otp="654321",
                client_name="Jane Doe",
                client_mail="jane.doe@test.fr",
                client_real_phone="+33699999999",
            )

        row_values = sheet._rows[2]
        self.assertEqual(row_values[headers.index("proxy_number")], "+33123456789")
        self.assertEqual(row_values[headers.index("otp")], "654321")
        self.assertEqual(row_values[headers.index("status")], "PENDING")
        self.assertEqual(row_values[headers.index("client_real_phone")], "+33699999999")


if __name__ == "__main__":
    unittest.main()
