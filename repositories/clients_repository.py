from typing import Optional
from models.client import Client
from integrations.sheets_client import SheetsClient


class ClientsRepository:
    """
    Implémentation Google Sheets.
    Feuille 'Clients' avec colonnes :
    client_id | client_name | phone_real | phone_proxy | country_code
    """

    @staticmethod
    def get_by_id(client_id: str) -> Optional[Client]:
        sheet = SheetsClient.get_clients_sheet()
        records = sheet.get_all_records()  # liste de dicts (ignore la 1re ligne)

        for rec in records:
            if str(rec.get("client_id")) == str(client_id):
                return Client(
                    client_id=rec.get("client_id"),
                    client_name=rec.get("client_name"),
                    phone_real=rec.get("phone_real"),
                    phone_proxy=rec.get("phone_proxy"),
                    country_code=rec.get("country_code"),
                )
        return None

    @staticmethod
    @staticmethod
    def get_by_proxy_number(proxy_number: str) -> Optional[Client]:
        sheet = SheetsClient.get_clients_sheet()
        records = sheet.get_all_records()

        target_raw = str(proxy_number or "")
        target_norm = target_raw.strip().replace(" ", "").replace("+", "")

        print(f"[DEBUG] lookup proxy: raw='{target_raw}', norm='{target_norm}'")

        for rec in records:
            rec_proxy_raw = rec.get("phone_proxy")
            rec_proxy_norm = str(rec_proxy_raw or "").strip().replace(" ", "").replace("+", "")

            print(f"[DEBUG] row proxy: raw='{rec_proxy_raw}', norm='{rec_proxy_norm}'")

            if rec_proxy_norm and rec_proxy_norm == target_norm:
                print(f"[DEBUG] MATCH with row: {rec}")
                return Client(
                    client_id=rec.get("client_id"),
                    client_name=rec.get("client_name"),
                    phone_real=rec.get("phone_real"),
                    phone_proxy=rec.get("phone_proxy"),
                    country_code=rec.get("country_code"),
                )

        print("[DEBUG] no proxy match found")
        return None

    @staticmethod
    def save(client: Client) -> None:
        """
        Version simple : on ajoute une nouvelle ligne.
        Hypothèse pour l’instant : on n’appelle pas save plusieurs fois
        pour le même client_id (sinon tu auras des doublons).
        """
        sheet = SheetsClient.get_clients_sheet()
        row = [
            client.client_id,
            client.client_name,
            client.phone_real,
            client.phone_proxy,
            client.country_code,
        ]
        sheet.append_row(row)
