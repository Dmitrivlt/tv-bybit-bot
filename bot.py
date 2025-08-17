import logging
from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse
from pybit.unified_trading import HTTP

# === Настройка FastAPI ===
app = FastAPI()

# === Настройка логов ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("tradingview_bot")

# === Секрет для TradingView ===
SECRET_TOKEN = "mysecret123"

# === API ключи Bybit (⚠️ ТУТ ВПИШИ СВОИ) ===
API_KEY = "CN4jydkkSArRVvzgTD"
API_SECRET = "JdXh8mh0cBq68ZvvjjEwCTgVBhY7EPbX2kTu"

# === Подключение к Bybit ===
session = HTTP(
    testnet=True,   # ⚠️ True = тестовая среда, False = реальная торговля
    api_key=API_KEY,
    api_secret=API_SECRET
)

# === Константа: мы торгуем только CYBERUSDT ===
SYMBOL = "CYBERUSDT"


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
            "webhook": "/tv_webhook?token=<YOUR_TOKEN>"
        },
        "your_token": SECRET_TOKEN,
        "trading_symbol": SYMBOL
    }


@app.post("/tv_webhook")
async def tv_webhook(request: Request, token: str = Query(...)):
    # === Проверка токена ===
    if token != SECRET_TOKEN:
        logger.warning("❌ Invalid token in request")
        return JSONResponse(status_code=403, content={"error": "Invalid token"})

    # === Парсим payload ===
    try:
        payload = await request.json()
        logger.info(f"📩 New alert received: {payload}")
    except Exception:
        logger.error("⚠️ Failed to parse JSON payload")
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    # === Извлекаем действие и параметры ===
    action = str(payload.get("action", "")).upper()   # BUY / SELL / CLOSE
    qty = float(payload.get("qty", 1))               # размер позиции

    try:
        if action == "BUY":
            session.place_order(
                category="linear",
                symbol=SYMBOL,
                side="Buy",
                order_type="Market",
                qty=qty
            )
            logger.info(f"✅ Открыл LONG {SYMBOL} qty={qty}")

        elif action == "SELL":
            session.place_order(
                category="linear",
                symbol=SYMBOL,
                side="Sell",
                order_type="Market",
                qty=qty
            )
            logger.info(f"✅ Открыл SHORT {SYMBOL} qty={qty}")

        elif action == "CLOSE":
            session.cancel_all_orders(category="linear", symbol=SYMBOL)
            logger.info(f"🔴 Закрыл все ордера {SYMBOL}")

        else:
            logger.warning(f"⚠️ Неизвестное действие: {action}")

    except Exception as e:
        logger.error(f"❌ Ошибка при торговле: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

    return {"status": "ok", "received": payload}
