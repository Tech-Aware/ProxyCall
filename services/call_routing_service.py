# services/call_routing_service.py

from twilio.twiml.voice_response import VoiceResponse, Dial
from repositories.clients_repository import ClientsRepository
from services.clients_service import extract_country_code


class CallRoutingService:
    @staticmethod
    def handle_incoming_call(proxy_number: str, caller_number: str) -> str:
        """
        proxy_number : numéro appelé (proxy / Twilio)
        caller_number : numéro appelant (livreur)
        Retourne le TwiML à envoyer à Twilio.
        """
        print(
            f"[DEBUG] CallRoutingService.handle_incoming_call("
            f"proxy_number='{proxy_number}', caller_number='{caller_number}')"
        )

        resp = VoiceResponse()

        # 1) Récupérer le client via le proxy
        client = ClientsRepository.get_by_proxy_number(proxy_number)
        print(f"[DEBUG] client from proxy: {client}")

        if not client:
            resp.say("Ce numéro n'est pas reconnu.", language="fr-FR")
            return str(resp)

        # 2) Filtre indicatif pays
        client_cc = str(client.country_code)
        if not client_cc.startswith("+"):
            client_cc = "+" + client_cc

        caller_cc = extract_country_code(caller_number)
        print(f"[DEBUG] country_codes: client_cc='{client_cc}', caller_cc='{caller_cc}'")

        if client_cc != caller_cc:
            resp.say("Ce numéro n'est pas accessible depuis votre pays.", language="fr-FR")
            return str(resp)

        # 3) Routage vers le vrai numéro
        proxy_e164 = proxy_number
        if not proxy_e164.startswith("+"):
            proxy_e164 = "+" + proxy_e164

        real_e164 = str(client.phone_real)
        if not real_e164.startswith("+"):
            real_e164 = "+" + real_e164

        dial = Dial(callerId=proxy_e164)
        dial.number(real_e164)
        resp.append(dial)

        return str(resp)
