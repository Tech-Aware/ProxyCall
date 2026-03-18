import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger(__name__)


class EmailClient:
    @staticmethod
    def is_configured() -> bool:
        return bool(
            settings.SMTP_HOST
            and settings.SMTP_USER
            and settings.SMTP_PASSWORD
            and settings.SMTP_FROM
        )

    @staticmethod
    def send_otp_email(*, to: str, otp: str, client_name: str, verify_url: str) -> None:
        if not EmailClient.is_configured():
            raise RuntimeError("SMTP non configuré (SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM requis)")

        subject = f"ProxyCall - Code de confirmation: {otp}"

        html = f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:20px;">
  <h2 style="color:#333;">ProxyCall</h2>
  <p>Bonjour {client_name},</p>
  <p>Votre code de confirmation est :</p>
  <p style="font-size:32px;font-weight:bold;letter-spacing:8px;text-align:center;
            background:#f5f5f5;padding:16px;border-radius:8px;">{otp}</p>
  <p>Cliquez sur le bouton ci-dessous pour confirmer directement :</p>
  <p style="text-align:center;">
    <a href="{verify_url}"
       style="display:inline-block;background:#2563eb;color:#fff;padding:12px 32px;
              border-radius:6px;text-decoration:none;font-weight:bold;">
      Confirmer mon numéro
    </a>
  </p>
  <p style="color:#888;font-size:12px;margin-top:32px;">
    Si vous n'avez pas demandé ce code, ignorez cet email.
  </p>
</body>
</html>"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to
        msg.attach(MIMEText(f"Votre code ProxyCall : {otp}\nConfirmez ici : {verify_url}", "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        try:
            if settings.SMTP_PORT == 465:
                server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
            else:
                server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
                server.starttls()

            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM, to, msg.as_string())
            server.quit()

            logger.info("Email OTP envoyé", extra={"to": to, "subject": subject})
        except Exception as exc:
            logger.error("Echec envoi email OTP", exc_info=exc, extra={"to": to})
            raise
