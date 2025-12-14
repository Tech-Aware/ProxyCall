from services.clients_service import ClientsService
from services.call_routing_service import CallRoutingService


def test_create_client_and_proxy():
    # Mets ici TON vrai numéro en E.164 (ex: +33612345678)
    client_id = "kevin-test"
    client_name = "Kevin"
    client_mail = "kevin@example.com"
    client_real_phone = "+33783529862"  # remplace par ton numéro réel, ex: +33612345678
    client_iso_residency = "FR"

    # 1) Création / récupération client + achat numéro Twilio + enregistrement dans Sheets
    client = ClientsService.get_or_create_client(
        client_id=client_id,
        client_name=client_name,
        client_mail=client_mail,
        client_real_phone=client_real_phone,
        client_iso_residency=client_iso_residency,
    )

    print("=== Client créé ou récupéré ===")
    print("client_id   :", client.client_id)
    print("client_name :", client.client_name)
    print("client_mail :", client.client_mail)
    print("phone_real  :", client.client_real_phone)
    print("phone_proxy :", client.client_proxy_number)
    print("iso_residency:", client.client_iso_residency)
    print("country_code:", client.client_country_code)

    # 2) TwiML avec indicatif OK (doit DIAL)
    print("\n=== TwiML avec même indicatif (doit DIAL) ===")
    twiml_ok = CallRoutingService.handle_incoming_call(
        proxy_number=client.client_proxy_number,
        caller_number=client_real_phone,  # même indicatif +33
    )
    print(twiml_ok)

    # 3) TwiML avec indicatif différent (doit BLOQUER)
    print("\n=== TwiML avec indicatif différent (doit BLOQUER) ===")
    twiml_block = CallRoutingService.handle_incoming_call(
        proxy_number=client.client_proxy_number,
        caller_number="+49123456789",  # autre pays
    )
    print(twiml_block)


if __name__ == "__main__":
    test_create_client_and_proxy()
