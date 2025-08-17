import logging
from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse

app = FastAPI()

# === Настройка логов ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("tradingview_bot")

SECRET_TOKEN = "mysecret123"

@app.get("/")
async def root():
    return {"msg": "Server alive"}

@app.get("/info")
async def info():
    return {
        "ok": True,
        "endpoints": {
            "home": "/",
            "info": "/info",
            "swagger": "/docs",
            "webhook_query": "/tv_webhook?token=<YOUR_TOKEN>",
            "webhook_path": "/webhook/<YOUR_TOKEN>"
        },
        "your_token": SECRET_TOKEN
    }

@app.post("/tv_webhook")
async def tv_webhook(request: Request, token: str = Query(...)):
    if token != SECRET_TOKEN:
        logger.warning("❌ Invalid token in request")
        return JSONResponse(status_code=403, content={"error": "Invalid token"})

    try:
        payload = await request.json()
    except Exception:
        logger.error("⚠️ Failed to parse JSON payload")
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    # === Печатаем красивый лог ===
    logger.info(f"📩 New alert received: {payload}")

    # Тут можно добавить отправку ордеров на биржу
    return {"status": "ok", "received": payload}
