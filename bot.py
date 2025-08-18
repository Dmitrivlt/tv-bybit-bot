import os
import logging
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv
from pybit.unified_trading import HTTP

# =========================
# Настройка логов
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# =========================
# Загружаем .env
# =========================
load_dotenv()

WEBHOOK_TOKEN   = os.getenv("WEBHOOK_TOKEN")
BYBIT_API_KEY   = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET= os.getenv("BYBIT_API_SECRET")
BYBIT_TESTNET   = os.getenv("BYBIT_TESTNET", "true").lower() == "true"
ENABLE_TRADING  = os.getenv("ENABLE_TRADING", "true").lower() == "true"

DEFAULT_LEVERAGE= int(os.getenv("DEFAULT_LEVERAGE", 1))
DEFAULT_SL_PCT  = float(os.getenv("DEFAULT_SL_PCT", 20))
SYMBOL          = os.getenv("SYMBOL", "SOLUSDT")

# =========================
# Bybit client
# =========================
base_url = "https://api-testnet.bybit.com" if BYBIT_TESTNET else "https://api.bybit.com"

session = HTTP(
    testnet=BYBIT_TESTNET,
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET
)

# =========================
# FastAPI
# =========================
app = FastAPI()

# =========================
# Проверка и установка плеча
# =========================
def ensure_leverage(symbol: str, leverage: int):
    try:
        # Получаем текущие данные инструмента
        info = session.get_instruments_info(category="linear", symbol=symbol)
        logging.info(f"ℹ️ Instruments info: {info}")

        # Получаем текущие позиции, чтобы узнать плечо
        pos = session.get_positions(category="linear", symbol=symbol)
        if pos["retCode"] == 0 and pos["result"]["list"]:
            current_lev = int(float(pos["result"]["list"][0]["leverage"]))
            if current_lev == leverage:
                logging.warning(f"⚠️ Leverage already set to {leverage}x, skipping update.")
                return
        # Если другое — меняем
        res = session.set_leverage(
            category="linear",
            symbol=symbol,
            buyLeverage=str(leverage),
            sellLeverage=str(leverage)
        )
        if res["retCode"] == 0:
            logging.info(f"✅ Leverage set to {leverage}x for {symbol}")
        else:
            logging.error(f"❌ Ошибка при установке плеча: {res}")
    except Exception as e:
        logging.error(f"❌ Не удалось проверить/установить плечо: {e}")

# =========================
# Маркет-ордер
# =========================
def place_market_order(symbol: str, side: str, sl_pct: float):
    try:
        balance = session.get_wallet_balance(accountType="UNIFIED")
        usdt_balance = float(balance["result"]["list"][0]["coin"][0]["walletBalance"])
        logging.info(f"💰 Баланс USDT: {usdt_balance}")

        # Цена для расчета количества
        ticker = session.get_ticker(category="linear", symbol=symbol)
        last_price = float(ticker["result"]["list"][0]["lastPrice"])

        qty = round(usdt_balance / last_price, 2)
        logging.info(f"📊 Рассчитанное количество {qty} {symbol.split('USDT')[0]}")

        # Ордера
        order = session.place_order(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=str(qty),
            timeInForce="GoodTillCancel",
            reduceOnly=False,
            closeOnTrigger=False
        )
        logging.info(f"✅ Market {side} ордер отправлен: {order}")

        # Добавляем стоп-лосс
        if sl_pct > 0:
            sl_price = last_price * (1 - sl_pct / 100) if side == "Buy" else last_price * (1 + sl_pct / 100)
            sl_order = session.set_trading_stop(
                category="linear",
                symbol=symbol,
                stopLoss=str(round(sl_price, 2))
            )
            logging.info(f"🛑 Stop Loss установлен: {sl_order}")

    except Exception as e:
        logging.error(f"❌ Ошибка при выставлении ордера: {e}")

# =========================
# Webhook
# =========================
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    token = request.headers.get("Authorization")

    if token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

    logging.info(f"📩 Получен сигнал: {data}")

    if ENABLE_TRADING:
        side = data.get("side")  # "Buy" или "Sell"
        if side in ["Buy", "Sell"]:
            place_market_order(SYMBOL, side, DEFAULT_SL_PCT)
        else:
            logging.warning("⚠️ Неверный сигнал (нет side).")
    else:
        logging.info("🚫 Торговля выключена (ENABLE_TRADING=false).")

    return {"status": "ok"}

# =========================
# При старте — проверка плеча
# =========================
@app.on_event("startup")
async def startup_event():
    ensure_leverage(SYMBOL, DEFAULT_LEVERAGE)
