import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN")

    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL")  # ex: https://xxxx.ngrok.io
    VOICE_WEBHOOK_URL: str = f"{PUBLIC_BASE_URL}/twilio/voice"

    GOOGLE_SHEET_NAME: str = os.getenv("GOOGLE_SHEET_NAME")
    GOOGLE_SERVICE_ACCOUNT_FILE: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")

    # Nouveau : pays dans lequel on va chercher les numéros Twilio
    TWILIO_PHONE_COUNTRY: str = os.getenv("TWILIO_PHONE_COUNTRY", "US")

settings = Settings()
