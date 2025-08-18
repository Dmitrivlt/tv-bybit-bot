import os
import logging
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from pybit.unified_trading import HTTP

# === Загрузка .env ===
load_dotenv()

API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "SOLUSDT")
CATEGORY = "linear"
TIMEFRAME = os.getenv("TIMEFRAME", "5m")  # таймфрейм через .env

# === Логирование ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# === Инициализация FastAPI ===
app = FastAPI()

# === Подключение к Bybit ===
session = HTTP(
    testnet=True,
    api_key=API_KEY,
    api_secret=API_SECRET
)

# === Проверка инструмента и плеча ===
try:
    instruments = session.get_instruments_info(category=CATEGORY, symbol=SYMBOL)
    logger.info(f"ℹ️ Instruments info: {instruments}")

    leverage = 1
    response = session.set_leverage(
        category=CATEGORY,
        symbol=SYMBOL,
        buyLeverage=str(leverage),
        sellLeverage=str(leverage)
    )
    if response.get("retCode") == 0:
        logger.info(f"✅ Leverage set to {leverage}x for {SYMBOL}")
    elif response.get("retCode") == 110043:
        logger.warning(f"⚠️ Leverage already set to {leverage}x, skipping update.")
    else:
        logger.error(f"❌ Ошибка установки плеча: {response}")
except Exception as e:
    logger.error(f"❌ Не удалось подключиться к Bybit: {e}")

# === Webhook ===
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    side = data.get("side")

    if side not in ["Buy", "Sell"]:
        logger.error(f"❌ Неверный сигнал: {data}")
        return {"status": "error", "message": "Invalid signal"}

    logger.info(f"📩 Получен сигнал: {side} | Таймфрейм: {TIMEFRAME}")

    # === Логика для выставления ордера (упрощено) ===
    try:
        order = session.place_order(
            category=CATEGORY,
            symbol=SYMBOL,
            side=side,
            orderType="Market",
            qty=1,  # тестовое кол-во
            timeInForce="GTC"
        )
        logger.info(f"✅ Ордер отправлен: {order}")
        return {"status": "ok", "side": side, "timeframe": TIMEFRAME}
    except Exception as e:
        logger.error(f"❌ Ошибка отправки ордера: {e}")
        return {"status": "error", "message": str(e)}
