import logging
import os

from fastapi import FastAPI
from fastapi import Header, HTTPException, status, Depends


from api import orders, twilio_webhook, clients, pool
from api.twilio_webhook import router as twilio_router
from api import orders, twilio_webhook, clients, pool, confirmations
from app.config import settings


def _configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)


_configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI()

def verify_api_token(authorization: str | None = Header(default=None)):
    expected_token = os.getenv("PROXYCALL_API_TOKEN")
    if not expected_token:
        # Pas de token configuré côté serveur : pas de vérification (API ouverte)
        return
    if authorization is None:
        raise HTTPException(status_code=403, detail="Accès non autorisé : token manquant")
    # On s’attend à un header du type "Authorization: Bearer <token>"
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="Accès non autorisé : schéma d'authentification invalide")
    token_value = authorization.removeprefix("Bearer ").strip()
    if token_value != expected_token:
        raise HTTPException(status_code=403, detail="Accès non autorisé : token invalide")
    # Si le token est correct, la fonction ne lève pas d'erreur et la requête est autorisée.


@app.on_event("startup")
async def on_startup() -> None:
    base_url = settings.PUBLIC_BASE_URL or "(non défini)"
    logger.info(
        "API ProxyCall démarrée - base publique=%s, pool=%s, type=%s",
        base_url,
        settings.TWILIO_POOL_SIZE,
        settings.TWILIO_NUMBER_TYPE,
    )


app.include_router(orders.router, prefix="/orders", tags=["Orders"], dependencies=[Depends(verify_api_token)])
app.include_router(clients.router, prefix="/clients", tags=["Clients"], dependencies=[Depends(verify_api_token)])
app.include_router(pool.router, prefix="/pool", tags=["Pool"], dependencies=[Depends(verify_api_token)])
app.include_router(twilio_webhook.router, prefix="/twilio", tags=["Twilio"])
app.include_router(confirmations.router, prefix="/confirmations", tags=["Confirmations"])
app.include_router(twilio_router)
