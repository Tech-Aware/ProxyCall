import logging
from fastapi import APIRouter, HTTPException, Body
from services.clients_service import ClientsService, ClientAlreadyExistsError

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/next-id")
def get_next_client_id():
    try:
        next_id = ClientsService.get_next_client_id()
    except Exception as exc:
        logger.exception(
            "Erreur lors du calcul du prochain client_id", exc_info=exc
        )
        raise HTTPException(status_code=500, detail="Erreur calcul client_id")

    return {"next_client_id": next_id}

@router.get("/{client_id}")
def get_client(client_id: int):
    client = ClientsService.get_client(str(client_id))
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


@router.get("/by-proxy/{proxy}")
def get_client_by_proxy(proxy: str):
    client = ClientsService.get_client_by_proxy(proxy)
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
    client_id: str = Body(...),
    client_name: str = Body(...),
    client_mail: str = Body(...),
    client_real_phone: str = Body(...),
    client_iso_residency: str | None = Body(None),
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


@router.put("/{client_id}")
def update_client(
    client_id: int,
    client_name: str | None = Body(None),
    client_mail: str | None = Body(None),
    client_real_phone: str | None = Body(None),
    client_proxy_number: str | None = Body(None),
    client_iso_residency: str | None = Body(None),
    client_country_code: str | None = Body(None),
):
    try:
        client = ClientsService.update_client(
            str(client_id),
            client_name=client_name,
            client_mail=client_mail,
            client_real_phone=client_real_phone,
            client_proxy_number=client_proxy_number,
            client_iso_residency=client_iso_residency,
            client_country_code=client_country_code,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # pragma: no cover - dépendances externes
        logger.exception("Erreur lors de la mise à jour du client", exc_info=exc)
        raise HTTPException(status_code=500, detail="Erreur interne")

    return {
        "client_id": client.client_id,
        "client_proxy_number": client.client_proxy_number,
        "client_name": client.client_name,
        "client_mail": client.client_mail,
        "client_real_phone": client.client_real_phone,
        "client_iso_residency": client.client_iso_residency,
        "client_country_code": client.client_country_code,
        "client_last_caller": client.client_last_caller,
    }
