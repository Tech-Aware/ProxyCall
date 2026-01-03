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
        self.appended_rows = []

    def row_values(self, row_index: int):
        return self._rows.get(row_index, [])

    def get_all_records(self):
        return self._records

    def get_all_values(self):
        max_row = max(self._rows.keys(), default=0)
        return [self._rows.get(idx, []) for idx in range(1, max_row + 1)]

    def append_row(self, row, value_input_option=None, table_range=None):
        new_row_index = max(self._rows.keys(), default=0) + 1
        self._rows[new_row_index] = row
        self.appended_rows.append(
            {
                "row": row,
                "value_input_option": value_input_option,
                "table_range": table_range,
                "row_index": new_row_index,
            }
        )
        return new_row_index

    def batch_update(self, updates):
        self.updates.append(updates)

    def update(self, range_label, values):
        self.updates.append({"range": range_label, "values": values})

    def batch_clear(self, ranges):
        self.cleared.append(ranges)


class _ProtectedSheet(_FakeSheet):
    def batch_update(self, updates):
        super().batch_update(updates)
        raise Exception("Protected range")

    def update(self, range_label, values):
        super().update(range_label, values)
        raise Exception("Protected cell")


class _FailingClearSheet(_FakeSheet):
    def batch_clear(self, ranges):
        super().batch_clear(ranges)
        raise Exception("Clear failed")


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

        # Le fallback unitaire doit avoir été tenté et consigné
        self.assertTrue(sheet.updates, "Aucune tentative de mise à jour unitaire")
        flat_ranges = []
        for item in sheet.updates:
            if isinstance(item, list):
                flat_ranges.extend([elt["range"] for elt in item])
            elif isinstance(item, dict):
                flat_ranges.append(item.get("range"))

        self.assertIn("B3", flat_ranges)
        self.assertIn("C3", flat_ranges)


class ClientsRepositorySaveTests(unittest.TestCase):
    def test_save_clears_columns_f_and_g_on_creation(self):
        headers = [
            "client_id",
            "client_name",
            "client_mail",
            "client_real_phone",
            "client_proxy_number",
            "client_iso_residency",
            "client_country_code",
        ]
        records = []
        rows = {1: headers, 2: ["", "", "", "", "", "", ""]}
        sheet = _FakeSheet(headers, records, rows)

        new_client = Client(
            client_id="10",
            client_name="Test Client",
            client_mail="test@mail.test",
            client_real_phone="+33123456789",
            client_proxy_number="+33999888777",
        )

        with patch("repositories.clients_repository.SheetsClient.get_clients_sheet", return_value=sheet):
            ClientsRepository.save(new_client)

        self.assertTrue(sheet.cleared, "Les colonnes F/G doivent être vidées après la création")
        self.assertIn(["F3:G3"], sheet.cleared)

    def test_save_raises_when_clear_columns_fail(self):
        headers = [
            "client_id",
            "client_name",
            "client_mail",
            "client_real_phone",
            "client_proxy_number",
            "client_iso_residency",
            "client_country_code",
        ]
        records = []
        rows = {1: headers, 2: ["", "", "", "", "", "", ""]}
        sheet = _FailingClearSheet(headers, records, rows)

        new_client = Client(
            client_id="11",
            client_name="Client Clear Error",
            client_mail="error@mail.test",
            client_real_phone="+33999999999",
            client_proxy_number="+33888888888",
        )

        with patch("repositories.clients_repository.SheetsClient.get_clients_sheet", return_value=sheet):
            with self.assertRaises(RuntimeError):
                ClientsRepository.save(new_client)


if __name__ == "__main__":
    unittest.main()
