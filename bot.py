import os
import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from pybit.unified_trading import HTTP

# ── Загрузка .env ──────────────────────────────────────────────────────────────
load_dotenv()

WEBHOOK_TOKEN      = os.getenv("WEBHOOK_TOKEN", "mysecret123")
BYBIT_API_KEY      = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET   = os.getenv("BYBIT_API_SECRET", "")
BYBIT_TESTNET      = os.getenv("BYBIT_TESTNET", "true").lower() in ("1", "true", "yes")
ENABLE_TRADING     = os.getenv("ENABLE_TRADING", "false").lower() in ("1", "true", "yes")
DEFAULT_LEVERAGE   = int(os.getenv("DEFAULT_LEVERAGE", "1"))
DEFAULT_SL_PCT     = float(os.getenv("DEFAULT_SL_PCT", "20"))  # 20% по умолчанию
SYMBOL             = os.getenv("SYMBOL", "CYBERUSDT")

# ── Логи ───────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tv-bybit-bot")

# ── FastAPI ────────────────────────────────────────────────────────────────────
app = FastAPI(title="TV→Bybit Bot", version="1.0")

# ── Соединение с Bybit (testnet/real) ─────────────────────────────────────────
session: Optional[HTTP] = None
if ENABLE_TRADING:
    if not BYBIT_API_KEY or not BYBIT_API_SECRET:
        logger.warning("ENABLE_TRADING=true, но API ключи пустые — торговля будет отключена.")
        ENABLE_TRADING = False
    else:
        try:
            session = HTTP(
                testnet=BYBIT_TESTNET,
                api_key=BYBIT_API_KEY,
                api_secret=BYBIT_API_SECRET,
            )
            # Выставим плечо для обеих сторон
            session.set_leverage(
                category="linear",
                symbol=SYMBOL,
                buyLeverage=DEFAULT_LEVERAGE,
                sellLeverage=DEFAULT_LEVERAGE,
            )
            logger.info(f"✅ Bybit подключен (testnet={BYBIT_TESTNET}), плечо={DEFAULT_LEVERAGE}x, символ={SYMBOL}")
        except Exception as e:
            logger.error(f"❌ Не удалось подключиться к Bybit: {e}")
            session = None
            ENABLE_TRADING = False

# ── Вспомогательные функции ───────────────────────────────────────────────────
def _get_last_price(symbol: str) -> Optional[float]:
    """Текущая цена (last/mark) для расчёта SL."""
    if not session:
        return None
    try:
        tick = session.get_tickers(category="linear", symbol=SYMBOL)
        # Ответ Bybit: {'result': {'list': [{'lastPrice': '1.2345', ...}]}, ...}
        lst = tick.get("result", {}).get("list", [])
        if not lst:
            return None
        price_str = lst[0].get("lastPrice") or lst[0].get("markPrice")
        return float(price_str) if price_str else None
    except Exception as e:
        logger.warning(f"Не удалось получить цену для {symbol}: {e}")
        return None

def _place_market_with_sl(side: str, qty: float, sl_pct: float) -> Dict[str, Any]:
    """
    Маркет-вход + стоп-лосс в процентах от текущей цены.
    side: 'Buy' | 'Sell'
    qty: количество в контрактах/коин-терминологии Bybit (для USDT-перп — в количестве монеты)
    """
    assert side in ("Buy", "Sell")

    if not session:
        return {"error": "session is None"}

    # Текущая цена для расчёта SL
    last_price = _get_last_price(SYMBOL)
    if last_price is None:
        # fallback: без SL
        logger.warning("Не удалось получить текущую цену — отправляем ордер без SL.")
        return session.place_order(
            category="linear",
            symbol=SYMBOL,
            side=side,
            order_type="Market",
            qty=qty,
        )

    if side == "Buy":
        sl_price = last_price * (1 - sl_pct / 100.0)
    else:  # Sell
        sl_price = last_price * (1 + sl_pct / 100.0)

    # В unified trading можно передать stopLoss в цене:
    # Документация Bybit: для linear поддерживается параметр stopLoss
    return session.place_order(
        category="linear",
        symbol=SYMBOL,
        side=side,
        order_type="Market",
        qty=qty,
        stopLoss=f"{sl_price:.6f}",
        # reduceOnly=False — это вход в позицию
    )

def _close_all_positions(symbol: str) -> None:
    """Закрыть все открытые ордера и попытаться закрыть позицию рыночным ордером."""
    if not session:
        return
    try:
        # Отменяем все открытые ордера
        session.cancel_all_orders(category="linear", symbol=symbol)
    except Exception as e:
        logger.warning(f"Не удалось отменить ордера: {e}")

    try:
        # Пытаемся получить позицию
        pos = session.get_positions(category="linear", symbol=symbol)
        items = pos.get("result", {}).get("list", []) if pos else []
        if not items:
            return
        size = float(items[0].get("size", 0) or 0)
        side = items[0].get("side", "").lower()  # 'Buy'/'Sell' может прийти в разных регистрах
        if size == 0:
            return

        # Закрываем позицию обратной стороной на тот же qty
        if side == "buy":
            session.place_order(
                category="linear",
                symbol=symbol,
                side="Sell",
                order_type="Market",
                qty=size,
                reduceOnly=True,
            )
        elif side == "sell":
            session.place_order(
                category="linear",
                symbol=symbol,
                side="Buy",
                order_type="Market",
                qty=size,
                reduceOnly=True,
            )
    except Exception as e:
        logger.warning(f"Не удалось закрыть позицию: {e}")

# ── Эндпоинты ─────────────────────────────────────────────────────────────────
@app.get("/")
async def home():
    return {"ok": True, "msg": "Server alive"}

@app.get("/info")
async def info():
    return {
        "ok": True,
        "env": {
            "testnet": BYBIT_TESTNET,
            "enable_trading": ENABLE_TRADING,
            "symbol": SYMBOL,
            "default_leverage": DEFAULT_LEVERAGE,
            "default_sl_pct": DEFAULT_SL_PCT,
        },
        "endpoints": {
            "home": "/",
            "info": "/info",
            "swagger": "/docs",
            "tv_webhook_query": "/tv_webhook?token=<YOUR_TOKEN>",
        },
    }

@app.post("/tv_webhook")
async def tv_webhook(request: Request, token: str = Query(...)):
    # Проверка токена
    if token != WEBHOOK_TOKEN:
        logger.warning("❌ Неверный token в запросе")
        return JSONResponse(status_code=403, content={"detail": "Invalid token"})

    # Парсинг JSON
    try:
        payload = await request.json()
    except Exception:
        logger.error("⚠️ Некорректный JSON от TradingView")
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON"})

    logger.info(f"📩 Alert payload: {payload}")

    action = str(payload.get("action", "")).upper()  # BUY / SELL / CLOSE
    # qty можно прислать из TradingView, по умолчанию 1 (для CYBERUSDT это 1 CYBER)
    qty = float(payload.get("qty", 1))

    # Если торги выключены — только логируем
    if not ENABLE_TRADING or session is None:
        logger.info(f"🟨 Торговля выключена (ENABLE_TRADING={ENABLE_TRADING}). Эмулирую действие: {action}, qty={qty}")
        return {"status": "ok", "received": payload, "trading": "disabled"}

    try:
        if action == "BUY":
            # Сначала закрываем возможный шорт
            _close_all_positions(SYMBOL)
            # Входим long + ставим SL по DEFAULT_SL_PCT
            resp = _place_market_with_sl("Buy", qty, DEFAULT_SL_PCT)
            logger.info(f"✅ BUY {SYMBOL} qty={qty} SL={DEFAULT_SL_PCT}% | resp: {resp}")

        elif action == "SELL":
            # Сначала закрываем возможный лонг
            _close_all_positions(SYMBOL)
            # Входим short + ставим SL по DEFAULT_SL_PCT
            resp = _place_market_with_sl("Sell", qty, DEFAULT_SL_PCT)
            logger.info(f"✅ SELL {SYMBOL} qty={qty} SL={DEFAULT_SL_PCT}% | resp: {resp}")

        elif action == "CLOSE":
            _close_all_positions(SYMBOL)
            logger.info(f"🔴 CLOSE {SYMBOL} — все позиции и ордера закрыты/отменены")

        else:
            logger.warning(f"⚠️ Неизвестное действие: {action}")
            return JSONResponse(status_code=400, content={"detail": f"Unknown action: {action}"})

    except Exception as e:
        logger.error(f"❌ Ошибка торговли: {e}")
        return JSONResponse(status_code=500, content={"detail": str(e)})

    return {"status": "ok", "received": payload}
