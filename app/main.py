from fastapi import FastAPI
from api import orders, twilio_webhook
from api.twilio_webhook import router as twilio_router

app = FastAPI()

app.include_router(orders.router, prefix="/orders", tags=["Orders"])
app.include_router(twilio_webhook.router, prefix="/twilio", tags=["Twilio"])
app.include_router(twilio_router)
