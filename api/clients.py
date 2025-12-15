import logging
from fastapi import APIRouter, HTTPException
from services.clients_service import ClientsService, ClientAlreadyExistsError

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/{client_id}")
def get_client(client_id: str):
    client = ClientsService.get_client(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    return {
        "client_id": client.client_id,
        "client_name": client.client_name,
        "client_mail": client.client_mail,
        "client_real_phone": client.client_real_phone,
        "client_proxy_number": client.client_proxy_number,
        "client_iso_residency": client.client_iso_residency,
        "client_country_code": client.client_country_code,
        "client_last_caller": client.client_last_caller,
    }


@router.post("")
def create_client(
    client_id: str,
    client_name: str,
    client_mail: str,
    client_real_phone: str,
    client_iso_residency: str | None = None,
):
    try:
        client = ClientsService.create_client(
            client_id,
            client_name,
            client_mail,
            client_real_phone,
            client_iso_residency,
        )
    except ClientAlreadyExistsError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as exc:
        logger.exception("Erreur lors de la création du client", exc_info=exc)
        raise HTTPException(status_code=500, detail="Erreur interne")

    return {
        "client_id": client.client_id,
        "client_proxy_number": client.client_proxy_number,
    }
