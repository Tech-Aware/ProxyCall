import unittest
from unittest.mock import patch

from models.client import Client
from repositories.clients_repository import ClientsRepository


class _FakeSheet:
    def __init__(self, headers, records, rows):
        self._headers = headers
        self._records = records
        self._rows = rows
        self.updates = []
        self.cleared = []

    def row_values(self, row_index: int):
        return self._rows.get(row_index, [])

    def get_all_records(self):
        return self._records

    def batch_update(self, updates):
        self.updates.append(updates)

    def batch_clear(self, ranges):
        self.cleared.append(ranges)


class _ProtectedSheet(_FakeSheet):
    def batch_update(self, updates):
        super().batch_update(updates)
        raise Exception("Protected range")


class ClientsRepositoryUpdateTests(unittest.TestCase):
    def test_update_ignores_reserved_row_and_targets_data_row(self):
        headers = [
            "client_id",
            "client_name",
            "client_mail",
            "client_real_phone",
            "client_proxy_number",
            "client_iso_residency",
            "client_country_code",
            "client_last_caller",
        ]
        # Ligne 2 réservée (formules), première ligne de données = row 3
        records = [
            {"client_id": "", "client_name": "", "client_mail": ""},
            {
                "client_id": "42",
                "client_name": "Ancien Nom",
                "client_mail": "old@mail.test",
                "client_real_phone": "+33123456789",
                "client_proxy_number": "+33999888777",
                "client_last_caller": "",
            },
        ]
        rows = {
            1: headers,
            2: ["", "", "", "", "", "", "", ""],
            3: ["42", "Ancien Nom", "old@mail.test", "+33123456789", "+33999888777", "", "", ""],
        }
        sheet = _FakeSheet(headers, records, rows)

        updated_client = Client(
            client_id="42",
            client_name="Nouveau Nom",
            client_mail="new@mail.test",
            client_real_phone="+33123456789",
            client_proxy_number="+33999888777",
        )

        with patch("repositories.clients_repository.SheetsClient.get_clients_sheet", return_value=sheet):
            ClientsRepository.update(updated_client)

        self.assertTrue(sheet.updates, "Aucune mise à jour envoyée à Sheets")
        all_ranges = [item["range"] for item in sheet.updates[0]]
        self.assertIn("B3", all_ranges)  # client_name
        self.assertIn("C3", all_ranges)  # client_mail
        self.assertNotIn("B2", all_ranges, "La ligne réservée (2) ne doit pas être ciblée")

    def test_update_raises_runtime_error_on_protected_cells(self):
        headers = ["client_id", "client_name", "client_mail", "client_real_phone", "client_proxy_number"]
        records = [
            {"client_id": "", "client_name": "", "client_mail": "", "client_real_phone": "", "client_proxy_number": ""},
            {"client_id": "99", "client_name": "Nom", "client_mail": "mail@test", "client_real_phone": "+111", "client_proxy_number": "+222"},
        ]
        rows = {
            1: headers,
            2: ["", "", "", "", ""],
            3: ["99", "Nom", "mail@test", "+111", "+222"],
        }
        sheet = _ProtectedSheet(headers, records, rows)

        updated_client = Client(
            client_id="99",
            client_name="Nom modifié",
            client_mail="mail-new@test",
            client_real_phone="+111",
            client_proxy_number="+222",
        )

        with patch("repositories.clients_repository.SheetsClient.get_clients_sheet", return_value=sheet):
            with self.assertRaises(RuntimeError):
                ClientsRepository.update(updated_client)


if __name__ == "__main__":
    unittest.main()
