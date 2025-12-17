import logging
import os

from fastapi import FastAPI

from api import orders, twilio_webhook, clients, pool
from api.twilio_webhook import router as twilio_router
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


@app.on_event("startup")
async def on_startup() -> None:
    base_url = settings.PUBLIC_BASE_URL or "(non défini)"
    logger.info(
        "API ProxyCall démarrée - base publique=%s, pool=%s, type=%s",
        base_url,
        settings.TWILIO_POOL_SIZE,
        settings.TWILIO_NUMBER_TYPE,
    )


app.include_router(orders.router, prefix="/orders", tags=["Orders"])
app.include_router(clients.router, prefix="/clients", tags=["Clients"])
app.include_router(pool.router, prefix="/pool", tags=["Pool"])
app.include_router(twilio_webhook.router, prefix="/twilio", tags=["Twilio"])
app.include_router(twilio_router)
