import os
import logging
from decimal import Decimal, ROUND_DOWN

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

# Binance USDⓈ-M Futures SDK
from binance.um_futures import UMFutures
from binance.error import ClientError

# =========================
# ЛОГИ
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("tv_binance_futures_bot")

# =========================
# .ENV
# =========================
load_dotenv()

WEBHOOK_TOKEN     = os.getenv("WEBHOOK_TOKEN")

BINANCE_API_KEY   = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET= os.getenv("BINANCE_API_SECRET")
BINANCE_TESTNET   = os.getenv("BINANCE_TESTNET", "false").lower() == "true"

ENABLE_TRADING    = os.getenv("ENABLE_TRADING", "true").lower() == "true"
DEFAULT_LEVERAGE  = int(os.getenv("DEFAULT_LEVERAGE", 10))
DEFAULT_SL_PCT    = float(os.getenv("DEFAULT_SL_PCT", 20))
SYMBOL            = os.getenv("SYMBOL", "USDCUSDT")

BASE_URL = "https://testnet.binancefuture.com" if BINANCE_TESTNET else "https://fapi.binance.com"

# =========================
# Binance USDⓈ-M Futures client
# =========================
client = UMFutures(
    key=BINANCE_API_KEY,
    secret=BINANCE_API_SECRET,
    base_url=BASE_URL,
)

# =========================
# FastAPI
# =========================
app = FastAPI(
    title="TV→Binance Futures Bot",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None
)

# =========================
# Вспомогательные
# =========================
def _get_symbol_filters(symbol: str):
    """Берём шаг количества (stepSize) и minQty для округления."""
    info = client.exchange_info()
    syms = {s["symbol"]: s for s in info["symbols"]}
    if symbol not in syms:
        raise RuntimeError(f"Symbol {symbol} not found on Binance Futures")
    s = syms[symbol]
    step_size = None
    min_qty = None
    for f in s["filters"]:
        if f["filterType"] in ("LOT_SIZE", "MARKET_LOT_SIZE"):
            step_size = f.get("stepSize")
            min_qty = f.get("minQty")
            break
    if step_size is None:
        # Fallback: иногда нужный фильтр называется LOT_SIZE
        for f in s["filters"]:
            if f["filterType"] == "LOT_SIZE":
                step_size = f.get("stepSize")
                min_qty = f.get("minQty")
                break
    if step_size is None:
        raise RuntimeError(f"No LOT_SIZE filter for {symbol}")
    return Decimal(step_size), Decimal(min_qty)

def _floor_to_step(qty: Decimal, step: Decimal) -> Decimal:
    """Округляем количество вниз до шага биржи."""
    if step <= 0:
        return qty
    # quantize к количеству знаков step
    step_digits = abs(step.as_tuple().exponent)
    return (qty.quantize(step, rounding=ROUND_DOWN)
                if step_digits > 0
                else (qty // step) * step)

def ensure_leverage_once(symbol: str, leverage: int):
    """Ставим плечо один раз; если уже такое — пропускаем."""
    try:
        # Узнаём текущее плечо через позицию (если открыта),
        # либо просто пытаемся установить — Binance вернёт ту же настройку.
        res = client.change_leverage(symbol=symbol, leverage=leverage)
        # Если всё ок, вернётся {'leverage': 10, 'maxNotionalValue': 'XXXXX'}
        if "leverage" in res:
            log.info(f"✅ Leverage set to {res['leverage']}x for {symbol}")
        else:
            log.info(f"ℹ️ Leverage change response: {res}")
    except ClientError as e:
        # Часто это «leverage not modified», это не критично
        log.warning(f"⚠️ change_leverage warn: {e.error_message}")
    except Exception as e:
        log.error(f"❌ ensure_leverage_once failed: {e}")

def _get_free_usdt() -> Decimal:
    """Доступный USDT на USDⓈ-M Futures кошельке."""
    acc = client.balance()  # список словарей
    for a in acc:
        if a.get("asset") == "USDT":
            # availableBalance — лучше чем просто balance
            return Decimal(a.get("availableBalance", "0"))
    return Decimal("0")

def _get_last_price(symbol: str) -> Decimal:
    t = client.ticker_price(symbol=symbol)
    return Decimal(t["price"])

def _round_qty_for_symbol(symbol: str, qty: Decimal) -> Decimal:
    step, min_qty = _get_symbol_filters(symbol)
    q = _floor_to_step(qty, step)
    if q < min_qty:
        return Decimal("0")
    return q

def place_market_order(symbol: str, side: str, sl_pct: float):
    """
    Рыночный вход на 100% доступного USDT (упрощенно),
    затем создаём STOP_MARKET с closePosition=true (SL %).
    side: 'Buy' или 'Sell'
    """
    try:
        free_usdt = _get_free_usdt()
        log.info(f"💰 USDT available: {free_usdt}")

        price = _get_last_price(symbol)
        if price <= 0:
            raise RuntimeError("Bad price from ticker")

        raw_qty = (free_usdt / price)
        qty = _round_qty_for_symbol(symbol, raw_qty)
        if qty <= 0:
            raise RuntimeError("Insufficient balance after step rounding")

        log.info(f"📊 qty={qty} @ ~{price} (raw={raw_qty})")

        # Рыночный ордер
        order = client.new_order(
            symbol=symbol,
            side=side.upper(),             # BUY / SELL
            type="MARKET",
            quantity=str(qty),
            newOrderRespType="RESULT"
        )
        log.info(f"✅ Market {side} sent: {order}")

        # Стоп-лосс: STOP_MARKET closePosition=true
        if sl_pct > 0:
            if side.upper() == "BUY":
                stop_price = price * (Decimal("1") - Decimal(sl_pct) / Decimal("100"))
                sl_side = "SELL"
            else:
                stop_price = price * (Decimal("1") + Decimal(sl_pct) / Decimal("100"))
                sl_side = "BUY"

            # Binance требует целочисленную точность цены по тик-шагу.
            # Для STOP_MARKET достаточно округлить в разумных пределах.
            stop_price = stop_price.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)

            sl = client.new_order(
                symbol=symbol,
                side=sl_side,
                type="STOP_MARKET",
                closePosition="true",
                stopPrice=str(stop_price),
                workingType="CONTRACT_PRICE",   # можно "MARK_PRICE"
                newOrderRespType="RESULT"
            )
            log.info(f"🛑 SL set {sl_side} STOP_MARKET @ {stop_price}: {sl}")

    except ClientError as e:
        log.error(f"❌ Binance ClientError: {e.error_message}")
        raise
    except Exception as e:
        log.error(f"❌ place_market_order error: {e}")
        raise

# =========================
# Роуты
# =========================
@app.get("/")
def home():
    return JSONResponse(
        {"ok": True, "msg": "TV→Binance Futures bot is running. See /info and /docs"}
    )

@app.get("/info")
def info():
    return {
        "ok": True,
        "symbol":       SYMBOL,
        "testnet":      BINANCE_TESTNET,
        "enableTrade":  ENABLE_TRADING,
        "defaultLev":   DEFAULT_LEVERAGE,
        "defaultSLpct": DEFAULT_SL_PCT,
        "baseUrl":      BASE_URL,
        "endpoints": {
            "health": "/",
            "info":   "/info",
            "docs":   "/docs",
            "tv_webhook": "/tv_webhook?token=<WEBHOOK_TOKEN>",
            "webhook":    "/webhook (Authorization header)"
        }
    }

# Вебхук с токеном в заголовке Authorization (для curl/Postman)
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
    if side not in ["Buy", "Sell", "BUY", "SELL"]:
        raise HTTPException(status_code=400, detail="Bad payload: missing side Buy/Sell")

    place_market_order(SYMBOL, side, DEFAULT_SL_PCT)
    return {"status": "ok", "symbol": SYMBOL, "side": side}

# Вебхук с токеном в query (?token=...) — удобно для TradingView
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
    if side not in ["Buy", "Sell", "BUY", "SELL"]:
        raise HTTPException(status_code=400, detail="Bad payload: missing side Buy/Sell")

    place_market_order(SYMBOL, side, DEFAULT_SL_PCT)
    return {"status": "ok", "symbol": SYMBOL, "side": side, "source": "tv"}

# =========================
# Стартовые действия
# =========================
@app.on_event("startup")
async def on_startup():
    ensure_leverage_once(SYMBOL, DEFAULT_LEVERAGE)
    log.info("🚀 Startup complete")
