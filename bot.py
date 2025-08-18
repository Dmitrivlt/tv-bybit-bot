import os
import logging
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from pybit.unified_trading import HTTP

# =========================
# ЛОГИ
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("tv_bybit_bot")

# =========================
# .ENV
# =========================
load_dotenv()

WEBHOOK_TOKEN     = os.getenv("WEBHOOK_TOKEN")
BYBIT_API_KEY     = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET  = os.getenv("BYBIT_API_SECRET")
BYBIT_TESTNET     = os.getenv("BYBIT_TESTNET", "true").lower() == "true"
ENABLE_TRADING    = os.getenv("ENABLE_TRADING", "true").lower() == "true"

DEFAULT_LEVERAGE  = int(os.getenv("DEFAULT_LEVERAGE", 50))
DEFAULT_SL_PCT    = float(os.getenv("DEFAULT_SL_PCT", 20))
SYMBOL            = os.getenv("SYMBOL", "USDCUSDT")

# =========================
# Bybit client (Unified Trading)
# =========================
session = HTTP(
    testnet=BYBIT_TESTNET,
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET
)

# =========================
# FastAPI
# =========================
app = FastAPI(
    title="TV→Bybit Bot",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None
)

# =========================
# Вспомогательные
# =========================
def ensure_leverage_once(symbol: str, leverage: int):
    """
    Проверяем текущее плечо по позиции.
    Если уже такое же — ничего не делаем.
    Если другое — один раз пробуем установить.
    """
    try:
        info = session.get_instruments_info(category="linear", symbol=symbol)
        log.info(f"ℹ️ Instruments info for {symbol}: {info}")

        pos = session.get_positions(category="linear", symbol=symbol)
        if pos.get("retCode") == 0 and pos["result"]["list"]:
            current = pos["result"]["list"][0].get("leverage")
            try:
                current_lev = int(float(current))
            except Exception:
                current_lev = None

            if current_lev == leverage:
                log.warning(f"⚠️ Leverage already {leverage}x, skip set.")
                return

        res = session.set_leverage(
            category="linear",
            symbol=symbol,
            buyLeverage=str(leverage),
            sellLeverage=str(leverage)
        )
        if res.get("retCode") == 0:
            log.info(f"✅ Leverage set to {leverage}x for {symbol}")
        else:
            log.error(f"❌ Set leverage error: {res}")
    except Exception as e:
        log.error(f"❌ ensure_leverage_once failed: {e}")

def place_market_order(symbol: str, side: str, sl_pct: float):
    """
    РЫНОЧНЫЙ вход со 100% доступного USDT (уточни логику при необходимости),
    плюс установка стоп-лосса в процентах.
    side: 'Buy' или 'Sell'
    """
    try:
        # Баланс (Unified)
        bal = session.get_wallet_balance(accountType="UNIFIED")
        usdt = 0.0
        if bal.get("retCode") == 0:
            coins = bal["result"]["list"][0].get("coin", [])
            for c in coins:
                if c.get("coin") == "USDT":
                    usdt = float(c.get("walletBalance", 0))
                    break
        log.info(f"💰 USDT balance: {usdt}")

        # Тикер
        tk = session.get_ticker(category="linear", symbol=symbol)
        last_price = float(tk["result"]["list"][0]["lastPrice"])
        # Количество базового актива на весь баланс (упрощённо)
        qty = round(usdt / last_price, 2)
        if qty <= 0:
            raise RuntimeError("Insufficient balance to place market order")

        log.info(f"📊 qty={qty} {symbol.split('USDT')[0]} @ ~{last_price}")

        # Рыночный ордер
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
        log.info(f"✅ Market {side} sent: {order}")

        # Стоп-лосс (по цене входа на момент отправки; биржа привяжет к позиции)
        if sl_pct > 0:
            sl_price = last_price * (1 - sl_pct / 100) if side == "Buy" else last_price * (1 + sl_pct / 100)
            sl_order = session.set_trading_stop(
                category="linear",
                symbol=symbol,
                stopLoss=str(round(sl_price, 4))
            )
            log.info(f"🛑 Stop Loss set: {sl_order}")

    except Exception as e:
        log.error(f"❌ place_market_order error: {e}")
        raise

# =========================
# Роуты
# =========================
@app.get("/")
def home():
    return JSONResponse(
        {"ok": True, "msg": "TV→Bybit bot is running. See /info and /docs"}
    )

@app.get("/info")
def info():
    return {
        "ok": True,
        "symbol":       SYMBOL,
        "testnet":      BYBIT_TESTNET,
        "enableTrade":  ENABLE_TRADING,
        "defaultLev":   DEFAULT_LEVERAGE,
        "defaultSLpct": DEFAULT_SL_PCT,
        "endpoints": {
            "health": "/",
            "info":   "/info",
            "docs":   "/docs",
            "tv_webhook": "/tv_webhook?token=<WEBHOOK_TOKEN>",
            "webhook":    "/webhook (Authorization header)"
        }
    }

# Вебхук c токеном в заголовке Authorization (вариант для curl/Postman)
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    token = request.headers.get("Authorization")
    if token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized (header)")

    log.info(f"📩 /webhook payload: {data}")

    if not ENABLE_TRADING:
        log.info("🚫 Trading disabled (ENABLE_TRADING=false)")
        return {"status": "ok", "trading": "disabled"}

    side = data.get("side")
    if side not in ["Buy", "Sell"]:
        raise HTTPException(status_code=400, detail="Bad payload: missing side Buy/Sell")

    place_market_order(SYMBOL, side, DEFAULT_SL_PCT)
    return {"status": "ok", "symbol": SYMBOL, "side": side}

# Вебхук c токеном в query (?token=...) — удобен для TradingView
@app.post("/tv_webhook")
async def tv_webhook(request: Request, token: str = Query(None)):
    if token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized (token)")

    data = await request.json()
    log.info(f"📩 /tv_webhook payload: {data}")

    if not ENABLE_TRADING:
        log.info("🚫 Trading disabled (ENABLE_TRADING=false)")
        return {"status": "ok", "trading": "disabled"}

    side = data.get("side")
    if side not in ["Buy", "Sell"]:
        raise HTTPException(status_code=400, detail="Bad payload: missing side Buy/Sell")

    place_market_order(SYMBOL, side, DEFAULT_SL_PCT)
    return {"status": "ok", "symbol": SYMBOL, "side": side, "source": "tv"}

# =========================
# Стартовые действия
# =========================
@app.on_event("startup")
async def on_startup():
    # Один раз пытаемся установить плечо (если уже 50 — просто пропустим)
    ensure_leverage_once(SYMBOL, DEFAULT_LEVERAGE)
    log.info("🚀 Startup complete")
