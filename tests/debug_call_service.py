from services.call_routing_service import CallRoutingService

if __name__ == "__main__":
    twiml = CallRoutingService.handle_incoming_call(
        proxy_number="+33123456789",      # le proxy de ton sheet
        caller_number="+33700000000",     # livreur FR
    )
    print(twiml)
