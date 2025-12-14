import gspread
from google.oauth2.service_account import Credentials
from app.config import settings  # adapte si ton module config est ailleurs

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_file(
    settings.GOOGLE_SERVICE_ACCOUNT_FILE,
    scopes=SCOPES
)
gc = gspread.authorize(creds)


class SheetsClient:
    @staticmethod
    def get_clients_sheet():
        """
        Retourne la feuille 'Clients' du Google Sheet défini dans .env
        (GOOGLE_SHEET_NAME).
        """
        sh = gc.open(settings.GOOGLE_SHEET_NAME)
        return sh.worksheet("Clients")

    @staticmethod
    def get_pools_sheet():
        """Retourne la feuille 'TwilioPools' pour le pool de numéros."""
        sh = gc.open(settings.GOOGLE_SHEET_NAME)
        return sh.worksheet("TwilioPools")
