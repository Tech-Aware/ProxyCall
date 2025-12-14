from integrations.sheets_client import SheetsClient

sheet = SheetsClient.get_clients_sheet()
print(sheet.get_all_records())
print("OK -> Accès réussi au sheet")
