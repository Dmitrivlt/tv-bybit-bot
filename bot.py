# bot.py
import os
import math
import logging
from functools import lru_cache
from typing import Optional, Tuple

from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

# === Загрузка .env ===
load_dotenv()

# ---- Константы / ENV ----
WEBHOOK_TOKEN   = os.getenv("WEBHOOK_TOKEN", "mysecret123")
API_KEY         = os.getenv("BYBIT_API_KEY", "")
API_SECRET      = os.getenv("BYBIT_API_SECRET", "")
TESTNET         = os.getenv("BYBIT_TESTNET", "true").lower() == "true"
ENABLE_TRADING  = os.getenv("ENABLE_TRADING", "false").lower() == "true"

# управляемые по умолчанию параметры (могут быть переопределены алертом)
DEFAULT_LEVERAGE = int(os.getenv("DEFAULT_LEVERAGE", "1"))
DEFAULT_SL_PCT   = float(os.getenv("DEFAULT_SL_PCT", "20"))
SYMBOL           = os.getenv("SYMBOL", "CYBERUSDT")  # торгуем только им

# === Логирование ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("tradingview_bot")

# === Инициализация FastAPI ===
app = FastAPI(title="TV → Bybit bot", version="1.0.0")

# === PyBit (Bybit Unified) ===
# Устанавливается пакетом: pybit==5.8.0
try:
    from pybit.unified_trading import HTTP
except Exception as e:
    logger.error("PyBit не установлен. Добавь 'pybit==5.8.0' в requirements.txt")
    raise

session = HTTP(
    testnet=TESTNET,
    api_key=API_KEY,
    api_secret=API_SECRET,
)


# ---------- Утилиты точности/шагов ----------
def _round_to_step(x: float, step: float) -> float:
    if step <= 0:
        return x
    return math.floor(x / step) * step


def _round_qty(qty: float, lot_step: float, min_qty: float) -> float:
    q = _round_to_step(qty, lot_step)
    if q < min_qty:
        q = 0.0
    # Убираем сверхточные хвосты
    return float(f"{q:.10f}")


@lru_cache(maxsize=1)
def get_symbol_filters() -> Tuple[float, float, float]:
    """
    Возвращает (lot_step, min_qty, price_tick) для SYMBOL из Bybit Instruments Info.
    Кэшируется на процесс.
    """
    r = session.get_instruments_info(category="linear", symbol=SYMBOL)
    lst = (r or {}).get("result", {}).get("list", [])
    if not lst:
        # дефолты на случай сбоя запроса
        logger.warning("Не удалось получить filters для %s, использую дефолты", SYMBOL)
        return (0.001, 0.001, 0.0001)

    item = lst[0]
    lot_step = float(item.get("lotSizeFilter", {}).get("qtyStep", 0.001))
    min_qty  = float(item.get("lotSizeFilter", {}).get("minOrderQty", 0.001))
    price_tick = float(item.get("priceFilter", {}).get("tickSize", 0.0001))
    return (lot_step, min_qty, price_tick)


def get_last_price() -> float:
    r = session.get_tickers(category="linear", symbol=SYMBOL)
    lst = (r or {}).get("result", {}).get("list", [])
    if not lst:
        raise RuntimeError("Не удалось получить последний прайс")
    return float(lst[0]["lastPrice"])


def get_balance_usdt() -> float:
    r = session.get_wallet_balance(accountType="UNIFIED")  # Unified account
    balances = (r or {}).get("result", {}).get("list", [])
    if not balances:
        raise RuntimeError("Не удалось получить баланс кошелька")
    # ищем USDT
    for acc in balances:
        for c in acc.get("coin", []):
            if c.get("coin") == "USDT":
                # используем доступный кэш: equity или availableToWithdraw
                # для расчёта лучше брать equity (в т.ч. нереализ. PnL)
                equity = float(c.get("equity", 0.0))
                avail  = float(c.get("availableToWithdraw", 0.0))
                return equity if equity > 0 else avail
    return 0.0


def ensure_leverage(leverage: int):
    """Выставляем плечо (buy/sell одинаковое)"""
    leverage = max(1, min(int(leverage), 100))
    try:
        session.set_leverage(
            category="linear",
            symbol=SYMBOL,
            buyLeverage=str(leverage),
            sellLeverage=str(leverage),
        )
    except Exception as e:
        logger.warning(f"Не удалось установить плечо {leverage}x: {e}")


def get_open_position() -> Tuple[str, float]:
    """
    Возвращает (side, size), где side in {"Long","Short","None"}, size — контрактов.
    """
    r = session.get_positions(category="linear", symbol=SYMBOL)
    lst = (r or {}).get("result", {}).get("list", [])
    if not lst:
        return ("None", 0.0)
    p = lst[0]
    size = float(p.get("size", 0.0) or 0.0)
    side = p.get("side") or "None"
    if size <= 0.0:
        return ("None", 0.0)
    return (side, size)


def close_position_if_needed(side_to_open: str):
    """
    Если уже есть позиция и она противоположная, закрываем ее reduceOnly маркетом.
    side_to_open: "Buy" или "Sell"
    """
    cur_side, cur_size = get_open_position()
    if cur_side == "None" or cur_size <= 0:
        return

    # если открываем Buy, а сейчас Short → закрываем
    if side_to_open == "Buy" and cur_side == "Short":
        try:
            session.place_order(
                category="linear",
                symbol=SYMBOL,
                side="Buy",
                order_type="Market",
                qty=cur_size,
                reduceOnly=True
            )
            logger.info(f"↔️ Закрыл SHORT {cur_size} {SYMBOL}")
        except Exception as e:
            logger.error(f"Не смог закрыть SHORT: {e}")
            raise

    # если открываем Sell, а сейчас Long → закрываем
    if side_to_open == "Sell" and cur_side == "Long":
        try:
            session.place_order(
                category="linear",
                symbol=SYMBOL,
                side="Sell",
                order_type="Market",
                qty=cur_size,
                reduceOnly=True
            )
            logger.info(f"↔️ Закрыл LONG {cur_size} {SYMBOL}")
        except Exception as e:
            logger.error(f"Не смог закрыть LONG: {e}")
            raise


def place_stop_loss(side: str, sl_pct: float, entry_price_hint: float, price_tick: float):
    """
    Ставит стоп-лосс на открытую позицию (через set_trading_stop).
    side: "Buy"|"Sell" — сторона последнего входа (для направления SL).
    sl_pct: % от цены (10 => 10%)
    entry_price_hint: используем lastPrice как ориентир.
    """
    sl_pct = max(0.1, float(sl_pct))
    if side == "Buy":
        sl_price = entry_price_hint * (1.0 - sl_pct / 100.0)
    else:
        sl_price = entry_price_hint * (1.0 + sl_pct / 100.0)

    # округлим по шагу цены
    sl_price = math.floor(sl_price / price_tick) * price_tick
    sl_price = float(f"{sl_price:.10f}")

    try:
        session.set_trading_stop(
            category="linear",
            symbol=SYMBOL,
            stopLoss=str(sl_price)  # строкой, как требует API
        )
        logger.info(f"🛡 SL установлен @ {sl_price}")
    except Exception as e:
        logger.warning(f"Не удалось установить SL: {e}")


def compute_order_qty(percent: float, leverage: int, last_price: float,
                      lot_step: float, min_qty: float) -> float:
    """
    Расчет размера позиции:
    qty = (balance_usdt * (percent/100) * leverage) / last_price
    с учетом шага и минимума.
    """
    percent = max(1.0, min(float(percent), 100.0))
    leverage = max(1, min(int(leverage), 100))

    balance = get_balance_usdt()
    notional = balance * (percent / 100.0) * leverage
    raw_qty = notional / last_price
    qty = _round_qty(raw_qty, lot_step, min_qty)
    return qty


# ---------- FastAPI endpoints ----------
@app.get("/")
async def home():
    return {"msg": "Server alive"}

@app.get("/info")
async def info():
    lot_step, min_qty, price_tick = get_symbol_filters()
    health = {
        "ok": True,
        "symbol": SYMBOL,
        "testnet": TESTNET,
        "trading_enabled": ENABLE_TRADING,
        "default_leverage": DEFAULT_LEVERAGE,
        "default_sl_pct": DEFAULT_SL_PCT,
        "filters": {
            "lot_step": lot_step,
            "min_qty": min_qty,
            "price_tick": price_tick
        }
    }
    # баланс может падать с ошибкой если ключи не заданы
    try:
        health["balance_usdt"] = get_balance_usdt()
    except Exception as e:
        health["balance_usdt_err"] = str(e)
    return health


@app.post("/tv_webhook")
async def tv_webhook(request: Request, token: str = Query(...)):
    # 1) Проверка токена
    if token != WEBHOOK_TOKEN:
        logger.warning("❌ Invalid token in request")
        return JSONResponse(status_code=403, content={"error": "Invalid token"})

    # 2) Парсим payload
    try:
        payload = await request.json()
    except Exception:
        logger.error("⚠️ Invalid JSON")
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    logger.info(f"📩 Alert: {payload}")

    # 3) Разбираем поля
    action   = str(payload.get("action", "")).upper()   # BUY / SELL / CLOSE
    percent  = float(payload.get("percent", 50))        # % от баланса
    leverage = int(payload.get("leverage", DEFAULT_LEVERAGE))
    sl_pct   = float(payload.get("sl_pct", DEFAULT_SL_PCT))

    lot_step, min_qty, price_tick = get_symbol_filters()

    # 4) Dry-run?
    if not ENABLE_TRADING:
        # просто просчёт без реальных ордеров
        last = 0.0
        try:
            last = get_last_price()
        except Exception as e:
            logger.warning(f"Не смог получить lastPrice: {e}")
        try:
            qty_preview = compute_order_qty(percent, leverage, last or 1.0, lot_step, min_qty) if last > 0 else 0
        except Exception as e:
            qty_preview = 0
            logger.warning(f"Не смог посчитать qty: {e}")

        logger.info(f"🧪 DRY-RUN | action={action} %={percent} lev={leverage} sl%={sl_pct} "
                    f"last={last} qty≈{qty_preview}")
        return {"status": "dry-run", "received": payload, "qty_preview": qty_preview, "last": last}

    # 5) Боевая логика
    try:
        if action in ("BUY", "SELL"):
            # плечо
            ensure_leverage(leverage)

            # закрыть противоположную позицию (автореверс)
            side_to_open = "Buy" if action == "BUY" else "Sell"
            close_position_if_needed(side_to_open)

            # расчёт qty
            last = get_last_price()
            qty  = compute_order_qty(percent, leverage, last, lot_step, min_qty)
            if qty <= 0:
                raise RuntimeError("Рассчитанный qty <= 0 (возможно слишком маленький баланс/%/шаг лота)")

            # вход
            resp = session.place_order(
                category="linear",
                symbol=SYMBOL,
                side=side_to_open,
                order_type="Market",
                qty=qty
            )
            logger.info(f"✅ Открыл {action} {SYMBOL} qty={qty}")

            # стоп-лосс (только SL)
            place_stop_loss(side_to_open, sl_pct, last, price_tick)

            return {"status": "ok", "opened": {"side": side_to_open, "qty": qty}, "resp": resp}

        elif action == "CLOSE":
            # закрываем, если есть позиция
            cur_side, cur_size = get_open_position()
            if cur_side == "None" or cur_size <= 0:
                logger.info("Позиции нет — нечего закрывать")
                return {"status": "ok", "closed": "flat"}

            close_side = "Sell" if cur_side == "Long" else "Buy"
            resp = session.place_order(
                category="linear",
                symbol=SYMBOL,
                side=close_side,
                order_type="Market",
                qty=cur_size,
                reduceOnly=True
            )
            logger.info(f"🔴 Закрыл позицию: {cur_side} {cur_size}")
            return {"status": "ok", "closed": {"was": cur_side, "qty": cur_size}, "resp": resp}

        else:
            logger.warning(f"Неизвестное действие: {action}")
            return JSONResponse(status_code=400, content={"error": f"Unknown action: {action}"})

    except Exception as e:
        logger.error(f"❌ Trade error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
