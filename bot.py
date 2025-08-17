from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse
from pybit.unified_trading import HTTP
import logging

# === Настройка FastAPI ===
app = FastAPI()

# === Логи ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("tradingview_bot")

# === Секретный токен ===
SECRET_TOKEN = "mysecret123"

# === API ключи Bybit (замени на свои!) ===
API_KEY = "ТВОЙ_API_KEY"
API_SECRET = "ТВОЙ_API_SECRET"

# === Сессия Bybit ===
session = HTTP(
    testnet=True,  # ⚠️ Поставь False для реала
    api_key=API_KEY,
    api_secret=API_SECRET
)

# === Домашняя страница ===
@app.get("/")
async def root():
    return {"msg": "Server alive"}

# === Инфо (проверка что бот работает) ===
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

# === Вебхук от TradingView ===
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

    logger.info(f"📩 Alert received: {payload}")

    # === Фиксируем тикер только на CYBERUSDT ===
    symbol = "CYBERUSDT"
    action = payload.get("action")
    qty = float(payload.get("qty", 0.01))  # если не передали qty — берём 0.01

    try:
        if action == "BUY":
            session.place_order(
                category="linear",
                symbol=symbol,
                side="Buy",
                order_type="Market",
                qty=qty
            )
            logger.info("✅ Открыл LONG по CYBERUSDT")

        elif action == "SELL":
            session.place_order(
                category="linear",
                symbol=symbol,
                side="Sell",
                order_type="Market",
                qty=qty
            )
            logger.info("✅ Открыл SHORT по CYBERUSDT")

        elif action in ["CLOSE_LONG", "CLOSE_SHORT"]:
            session.cancel_all_orders(category="linear", symbol=symbol)
            logger.info("🔴 Закрыл все ордера по CYBERUSDT")

        else:
            logger.warning(f"⚠️ Неизвестное действие: {action}")
            return JSONResponse(status_code=400, content={"error": "Unknown action"})

    except Exception as e:
        logger.error(f"⚠️ Ошибка при торговле: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

    return {"status": "ok", "received": payload}
