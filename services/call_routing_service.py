# services/call_routing_service.py

import logging
from twilio.twiml.voice_response import VoiceResponse, Dial
from repositories.clients_repository import ClientsRepository
from services.clients_service import extract_country_code


logger = logging.getLogger(__name__)


class CallRoutingService:
    @staticmethod
    def handle_incoming_call(proxy_number: str, caller_number: str) -> str:
        """
        proxy_number : numéro appelé (proxy / Twilio)
        caller_number : numéro appelant (livreur)
        Retourne le TwiML à envoyer à Twilio.
        """
        logger.info(
            "Réception d'un appel sur le proxy",
            extra={"proxy_number": proxy_number, "caller_number": caller_number},
        )

        resp = VoiceResponse()

        try:
            client = ClientsRepository.get_by_proxy_number(proxy_number)
        except Exception as exc:  # pragma: no cover - dépendances externes
            logger.exception("Erreur lors de la récupération du client par proxy", exc_info=exc)
            resp.say("Service temporairement indisponible.", language="fr-FR")
            return str(resp)

        if not client:
            resp.say("Ce numéro n'est pas reconnu.", language="fr-FR")
            return str(resp)

        client_cc = str(client.client_country_code or "")
        if client_cc and not client_cc.startswith("+"):
            client_cc = "+" + client_cc

        caller_cc = extract_country_code(caller_number)
        logger.info(
            "Comparaison des indicatifs pays",
            extra={"client_country_code": client_cc, "caller_country_code": caller_cc},
        )

        if client_cc and client_cc != caller_cc:
            logger.warning(
                "Appel bloqué : indicatif différent",
                extra={"client_country_code": client_cc, "caller_country_code": caller_cc},
            )
            resp.say("Ce numéro n'est pas accessible depuis votre pays.", language="fr-FR")
            return str(resp)

        proxy_e164 = proxy_number if proxy_number.startswith("+") else f"+{proxy_number}"
        real_e164 = (
            client.client_real_phone
            if str(client.client_real_phone).startswith("+")
            else f"+{client.client_real_phone}"
        )

        # Si le client rappelle son proxy => on appelle le dernier livreur
        if caller_number == real_e164:
            last = str(getattr(client, "client_last_caller", "") or "").strip().replace(" ", "")
            if not last:
                resp.say("Aucun appelant récent.", language="fr-FR")
                return str(resp)

            if not last.startswith("+"):
                last = "+" + last

            dial = Dial(callerId=proxy_e164)
            dial.number(last)
            resp.append(dial)
            return str(resp)

        try:
            ClientsRepository.update_last_caller_by_proxy(proxy_e164, caller_number)
        except Exception as exc:
            logger.warning("Impossible de mettre à jour client_last_caller", exc_info=exc)

        dial = Dial(callerId=proxy_e164)
        dial.number(real_e164)
        resp.append(dial)

        logger.info(
            "Routage de l'appel vers le numéro réel",
            extra={"proxy": proxy_e164, "destination": real_e164},
        )
        return str(resp)
