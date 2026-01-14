import logging
import re
from typing import Callable

from twilio.twiml.messaging_response import MessagingResponse

from app.logging_config import mask_phone
from integrations.twilio_client import TwilioClient
from repositories.clients_repository import ClientsRepository
from repositories.confirmation_pending_repository import ConfirmationPendingRepository
from services.confirmation_service import ConfirmationService
from services.clients_service import extract_country_code

logger = logging.getLogger(__name__)

# Indicatifs pays de l'Union Européenne + EEE + Suisse
EU_COUNTRY_CODES = {
    "+30",   # Grèce
    "+31",   # Pays-Bas
    "+32",   # Belgique
    "+33",   # France
    "+34",   # Espagne
    "+351",  # Portugal
    "+352",  # Luxembourg
    "+353",  # Irlande
    "+354",  # Islande (EEE)
    "+356",  # Malte
    "+357",  # Chypre
    "+358",  # Finlande
    "+359",  # Bulgarie
    "+36",   # Hongrie
    "+370",  # Lituanie
    "+371",  # Lettonie
    "+372",  # Estonie
    "+385",  # Croatie
    "+386",  # Slovénie
    "+39",   # Italie
    "+40",   # Roumanie
    "+41",   # Suisse
    "+420",  # République tchèque
    "+421",  # Slovaquie
    "+43",   # Autriche
    "+45",   # Danemark
    "+46",   # Suède
    "+47",   # Norvège (EEE)
    "+48",   # Pologne
    "+49",   # Allemagne
}

OTP_RE = re.compile(r"\b(\d{4,8})\b")  # 4 à 8 chiffres


class MessageRoutingService:
    @staticmethod
    def _build_response(message: str | None = None) -> str:
        resp = MessagingResponse()
        if message:
            resp.message(message)
        return str(resp)

    @staticmethod
    def _with_error_handling(func: Callable[[], str]) -> str:
        try:
            return func()
        except Exception as exc:  # pragma: no cover
            logger.exception("Erreur lors du routage SMS", exc_info=exc)
            return MessageRoutingService._build_response("Service SMS temporairement indisponible.")

    @staticmethod
    def handle_incoming_sms(*, proxy_number: str, sender_number: str, body: str | None) -> str:
        return MessageRoutingService._with_error_handling(
            lambda: MessageRoutingService._route_sms(
                proxy_number=proxy_number, sender_number=sender_number, body=body or ""
            )
        )

    @staticmethod
    def _extract_otp(body: str) -> str:
        body_clean = (body or "").strip()
        m = OTP_RE.search(body_clean)
        if m:
            return m.group(1)
        return re.sub(r"\D+", "", body_clean)

    @staticmethod
    def _route_sms(*, proxy_number: str, sender_number: str, body: str) -> str:
        logger.info("MESSAGE_ROUTING_VERSION=v2-otpfix")

        proxy_e164 = proxy_number if proxy_number.startswith("+") else f"+{proxy_number}"
        sender_e164 = sender_number if sender_number.startswith("+") else f"+{sender_number}"

        logger.info(
            "SMS entrant reçu sur le proxy",
            extra={
                "proxy_number": mask_phone(proxy_e164),
                "sender_number": mask_phone(sender_e164),
                "body_preview": (body[:80] + "..." if len(body) > 80 else body),
            },
        )

        # 1) Branche CONFIRMATION_PENDING
        pending = ConfirmationPendingRepository.find_pending(proxy_e164, sender_e164)
        if pending:
            rec = pending["record"]
            headers = pending["headers"]
            pending_row = pending["row"]

            expected = str(rec.get("otp", "")).strip()
            provided = MessageRoutingService._extract_otp(body)

            logger.info(
                "SMS de confirmation reçu (pending)",
                extra={
                    "proxy_number": mask_phone(proxy_e164),
                    "sender_number": mask_phone(sender_e164),
                    "pending_id": rec.get("pending_id"),
                },
            )

            if not expected:
                return MessageRoutingService._build_response("Erreur: code introuvable. Contactez le support.")

            if provided != expected:
                return MessageRoutingService._build_response("Code invalide. Réessayez.")

            # VERIFIED
            ConfirmationPendingRepository.mark_verified(pending_row)

            # Promotion Clients + attachement proxy
            upsert_result = ConfirmationService.upsert_client_and_attach_proxy(
                client_name=str(rec.get("client_name") or "").strip(),
                client_mail=str(rec.get("client_mail") or "").strip(),
                client_real_phone=str(rec.get("client_real_phone") or "").strip(),
                proxy_number=str(rec.get("proxy_number") or "").strip(),
                pending_id=str(rec.get("pending_id") or "").strip(),
            )
            client = upsert_result.client

            # Finalisation pool
            ConfirmationService.finalize_pool_assignment(
                proxy_number=str(rec.get("proxy_number") or "").strip(),
                pending_id=str(rec.get("pending_id") or "").strip(),
                client_id=str(client.client_id),
                attribution_to_client_name=str(rec.get("client_name") or "").strip(),
            )

            # PROMOTED ou UPDATED selon le scénario
            if upsert_result.created:
                ConfirmationPendingRepository.mark_promoted(pending_row)
            else:
                if not upsert_result.updated_fields:
                    details = "aucune modification de contact"
                elif upsert_result.updated_fields == {"mail", "telephone"}:
                    details = "mail + telephone"
                else:
                    details = " et ".join(sorted(upsert_result.updated_fields))
                ConfirmationPendingRepository.mark_updated(pending_row, details)

            # 5) Notifier explicitement le client (ne pas dépendre du TwiML reply)
            try:
                TwilioClient.send_sms(
                    from_number=proxy_e164,
                    to_number=sender_e164,
                    body="Confirmation OK. Merci ! Enregistre ce numéro pour tes prochaines livraison !",
                )
            except Exception as exc:
                logger.warning(
                    "Impossible d'envoyer le SMS de confirmation au client",
                    exc_info=exc,
                    extra={"proxy_number": mask_phone(proxy_e164), "sender_number": mask_phone(sender_e164)},
                )

            # TwiML vide (on a déjà envoyé un SMS sortant)
            return MessageRoutingService._build_response()

        # 2) Routage “normal” via Clients
        client = ClientsRepository.get_by_proxy_number(proxy_e164)
        if not client:
            logger.warning("SMS rejeté : proxy inconnu", extra={"proxy_number": mask_phone(proxy_e164)})
            return MessageRoutingService._build_response("Ce numéro proxy n'est pas reconnu.")

        client_cc = str(getattr(client, "client_country_code", "") or "")
        if client_cc and not client_cc.startswith("+"):
            client_cc = "+" + client_cc

        sender_cc = extract_country_code(sender_e164)

        logger.info(
            "Comparaison des indicatifs pays (SMS)",
            extra={"client_country_code": client_cc, "sender_country_code": sender_cc},
        )

        # Vérifier si l'expéditeur est dans la zone EU/EEE/Suisse
        sender_in_eu = any(sender_cc.startswith(code) for code in EU_COUNTRY_CODES) if sender_cc else False

        if not sender_in_eu:
            logger.warning(
                f"SMS bloqué : hors zone EU (client={client_cc}, expéditeur={sender_cc})",
                extra={"client_country_code": client_cc, "sender_country_code": sender_cc},
            )
            return MessageRoutingService._build_response("Ce numéro n'est pas accessible depuis votre pays.")

        # Log informatif si indicatif différent mais EU autorisé
        if client_cc and client_cc != sender_cc:
            logger.info(
                f"SMS EU autorisé malgré indicatif différent (client={client_cc}, expéditeur={sender_cc})",
                extra={"client_country_code": client_cc, "sender_country_code": sender_cc},
            )

        real_e164 = (
            client.client_real_phone
            if str(client.client_real_phone).startswith("+")
            else f"+{client.client_real_phone}"
        )

        # Client -> dernier correspondant
        if sender_e164 == real_e164:
            last_caller = str(getattr(client, "client_last_caller", "") or "").strip().replace(" ", "")
            if not last_caller:
                logger.info("SMS client sans dernier correspondant connu", extra={"client_id": client.client_id})
                return MessageRoutingService._build_response("Aucun correspondant récent pour ce proxy.")

            if not last_caller.startswith("+"):
                last_caller = "+" + last_caller

            MessageRoutingService._relay_sms(from_number=proxy_e164, to_number=last_caller, body=body)
            logger.info(
                "SMS client relayé vers le dernier correspondant",
                extra={"client_id": client.client_id, "destination": mask_phone(last_caller)},
            )
            return MessageRoutingService._build_response()

        # Tiers -> client
        ClientsRepository.update_last_caller_by_proxy(proxy_e164, sender_e164)
        MessageRoutingService._relay_sms(from_number=proxy_e164, to_number=real_e164, body=body)
        logger.info(
            "SMS relayé vers le client",
            extra={"client_id": client.client_id, "destination": mask_phone(real_e164)},
        )
        return MessageRoutingService._build_response()

    @staticmethod
    def _relay_sms(*, from_number: str, to_number: str, body: str) -> None:
        try:
            TwilioClient.send_sms(from_number=from_number, to_number=to_number, body=body)
        except Exception as exc:
            logger.error(
                "Echec de l'envoi du SMS relayé",
                exc_info=exc,
                extra={"from": mask_phone(from_number), "to": mask_phone(to_number)},
            )
            raise
