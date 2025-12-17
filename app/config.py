import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_ADDRESS_SID: str | None = os.getenv("TWILIO_ADDRESS_SID")
    TWILIO_BUNDLE_SID: str | None = os.getenv("TWILIO_BUNDLE_SID")

    # URL publique exposée (Render fournit RENDER_EXTERNAL_URL par défaut)
    PUBLIC_BASE_URL: str | None = (
        os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL")
    )
    if PUBLIC_BASE_URL:
        PUBLIC_BASE_URL = PUBLIC_BASE_URL.rstrip("/")
    VOICE_WEBHOOK_URL: str | None = (
        f"{PUBLIC_BASE_URL}/twilio/voice" if PUBLIC_BASE_URL else None
    )
    MESSAGING_WEBHOOK_URL: str | None = (
        f"{PUBLIC_BASE_URL}/twilio/sms" if PUBLIC_BASE_URL else None
    )

    GOOGLE_SHEET_NAME: str = os.getenv("GOOGLE_SHEET_NAME")
    GOOGLE_SERVICE_ACCOUNT_FILE: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")

    # Nouveau : pays dans lequel on va chercher les numéros Twilio
    TWILIO_PHONE_COUNTRY: str = os.getenv("TWILIO_PHONE_COUNTRY", "US")

    # Type de numéro Twilio par défaut (mobile ou local)
    TWILIO_NUMBER_TYPE: str = os.getenv("TWILIO_NUMBER_TYPE", "mobile").lower()

    # Pool par pays : nombre de numéros achetés d'un coup lorsque le pool est vide
    TWILIO_POOL_SIZE: int = int(os.getenv("TWILIO_POOL_SIZE", "3"))


settings = Settings()
