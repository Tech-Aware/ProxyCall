from fastapi import APIRouter, HTTPException
from app.services.clients_service import ClientsService, ClientAlreadyExistsError

router = APIRouter()

@router.get("/{client_id}")
def get_client(client_id: str):
    client = ClientsService.get_client(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    return {
        "client_id": client.client_id,
        "client_name": client.client_name,
        "phone_real": client.phone_real,
        "phone_proxy": client.phone_proxy,
        "country_code": client.country_code,
    }


@router.post("")
def create_client(client_id: str, client_name: str, phone_real: str):
    try:
        client = ClientsService.create_client(client_id, client_name, phone_real)
    except ClientAlreadyExistsError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "client_id": client.client_id,
        "phone_proxy": client.phone_proxy,
    }
