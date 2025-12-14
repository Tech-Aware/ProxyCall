from services.clients_service import ClientsService
from services.call_routing_service import CallRoutingService


def test_create_client_and_proxy():
    # Mets ici TON vrai numéro en E.164 (ex: +33612345678)
    client_id = "kevin-test"
    client_name = "Kevin"
    phone_real = "+33783529862"  # remplace par ton numéro réel, ex: +33612345678

    # 1) Création / récupération client + achat numéro Twilio + enregistrement dans Sheets
    client = ClientsService.get_or_create_client(
        client_id=client_id,
        client_name=client_name,
        phone_real=phone_real,
    )

    print("=== Client créé ou récupéré ===")
    print("client_id   :", client.client_id)
    print("client_name :", client.client_name)
    print("phone_real  :", client.phone_real)
    print("phone_proxy :", client.phone_proxy)
    print("country_code:", client.country_code)

    # 2) TwiML avec indicatif OK (doit DIAL)
    print("\n=== TwiML avec même indicatif (doit DIAL) ===")
    twiml_ok = CallRoutingService.handle_incoming_call(
        proxy_number=client.phone_proxy,
        caller_number=phone_real,  # même indicatif +33
    )
    print(twiml_ok)

    # 3) TwiML avec indicatif différent (doit BLOQUER)
    print("\n=== TwiML avec indicatif différent (doit BLOQUER) ===")
    twiml_block = CallRoutingService.handle_incoming_call(
        proxy_number=client.phone_proxy,
        caller_number="+49123456789",  # autre pays
    )
    print(twiml_block)


if __name__ == "__main__":
    test_create_client_and_proxy()
