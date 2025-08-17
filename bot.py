# bot.py
import os
import json
from typing import Optional

from fastapi import FastAPI, Request, Header, Query, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

# =========================
#   ENV / CONFIG
# =========================
load_dotenv()

WEBHOOK_TOKEN   = os.getenv("WEBHOOK_TOKEN", "mysecret123")

# Торговлю можно включить ТОЛЬКО через env:
# ENABLE_TRADING=true
ENABLE_TRADING  = os.getenv("ENABLE_TRADING", "false").lower() == "true"

BYBIT_API_KEY   = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET= os.getenv("BYBIT_API_SECRET", "")
BYBIT_TESTNET   = os.getenv("BYBIT_TESTNET", "true").lower() == "true"  # testnet по умолчанию

exchange = None
if ENABLE_TRADING:
    # Инициализируем ccxt только если торги включены
    try:
        import ccxt  # импортим здесь, чтобы без ccxt тоже можно было запускать сервер
        exchange = ccxt.bybit({
            "apiKey": BYBIT_API_KEY,
            "secret": BYBIT_API_SECRET,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",  # линейные USDT-деривативы
            },
        })
        # Testnet
        exchange.set_sandbox_mode(BYBIT_TESTNET)
        print(f"🚀 Trading enabled | Bybit sandbox={BYBIT_TESTNET}")
    except Exception as e:
        print(f"⚠️ Не удалось инициализировать биржу: {e}")
        exchange = None
        ENABLE_TRADING = False

app = FastAPI(title="TV → Bybit Webhook Bot", version="1.0.0")


# =========================
#   UTILS
# =========================
def mask_token(tok: str) -> str:
    if not tok:
        return ""
    if len(tok) <= 6:
        return "***"
    return tok[:3] + "..." + tok[-3:]


async def read_json_body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")


def ensure_token(passed: Optional[str]) -> None:
    if not passed or passed != WEBHOOK_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid webhook token")


# =========================
#   HEALTH / INFO
# =========================
@app.get("/")
def root():
    return {
        "ok": True,
        "service": "tv-bybit-bot",
        "trading_enabled": ENABLE_TRADING,
        "exchange": "bybit" if ENABLE_TRADING else None,
        "testnet": BYBIT_TESTNET if ENABLE_TRADING else None,
    }


@app.get("/ping")
def ping():
    return {"pong": True}


@app.get("/info")
def info():
    return {
        "ok": True,
        "how_to_use": {
            "option_1": "POST /webhook/{token}",
            "option_2": "POST /tv_webhook?token=...  OR  header X-Webhook-Token: ...",
            "expected_json": {
                "symbol": "CYBERUSDT",
                "side": "buy | sell | close",
                "qty": 1,
                "reason": "text"
            },
        },
        "token_env": "WEBHOOK_TOKEN",
        "current_token_masked": mask_token(WEBHOOK_TOKEN),
        "trading_enabled": ENABLE_TRADING,
        "bybit": {
            "testnet": BYBIT_TESTNET,
            "needs_keys": ENABLE_TRADING
        }
    }


# =========================
#   CORE WEBHOOKS
# =========================
@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):
    # токен в пути
    ensure_token(token)

    data = await read_json_body(request)
    return await _handle_signal(data)


@app.post("/tv_webhook")
async def tv_webhook(
    request: Request,
    token_q: Optional[str] = Query(default=None, alias="token"),
    token_h: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
):
    # токен в query или в заголовке
    token = token_q or token_h
    ensure_token(token)

    data = await read_json_body(request)
    return await _handle_signal(data)


# =========================
#   SIGNAL HANDLER
# =========================
async def _handle_signal(data: dict) -> JSONResponse:
    """
    Единая логика обработки сигналов от TradingView.
    Поддерживаем поля:
      symbol (обязательно), side (buy|sell|close), qty (число), reason (строка)
    """
    # Лог входящих данных
    print("📩 Пришел сигнал:", data)

    symbol = str(data.get("symbol", "CYBERUSDT")).strip().upper()
    side   = (data.get("side") or "").strip().lower()
    qty    = data.get("qty", 0.01)
    reason = str(data.get("reason", "")).strip() or "signal"

    # Валидация базовых полей
    if side not in ("buy", "sell", "close"):
        raise HTTPException(status_code=400, detail="side must be buy|sell|close")
    try:
        qty = float(qty)
        if qty <= 0:
            raise ValueError()
    except Exception:
        raise HTTPException(status_code=400, detail="qty must be positive number")

    # Печать для наглядности (работает всегда)
    print(f"✅ SIGNAL: side={side} qty={qty} symbol={symbol} reason={reason}")

    # Если торги выключены — просто подтверждаем прием
    if not ENABLE_TRADING or exchange is None:
        return JSONResponse({
            "status": "received",
            "trading_enabled": False,
            "echo": {
                "symbol": symbol, "side": side, "qty": qty, "reason": reason
            }
        })

    # ====== РЕАЛЬНАЯ ТОРГОВЛЯ (опционально) ======
    try:
        # Внимание к символам Bybit/ccxt:
        # Для линейных USDT-свопов чаще всего ccxt принимает "CYBERUSDT".
        # Если вдруг будет ошибка "symbol not found", попробуй формат "CYBER/USDT:USDT".
        place_symbol = symbol

        order = None
        if side == "buy":
            order = exchange.create_market_buy_order(place_symbol, qty)
        elif side == "sell":
            order = exchange.create_market_sell_order(place_symbol, qty)
        elif side == "close":
            # Простейшая реализация: закрытие через встречный рыночный ордер на указанный qty
            # (Продвинуто: можно смотреть текущую позицию и закрывать весь размер)
            # Для тестнета этого достаточно для проверки контура.
            # Попробуем закрыть в обе стороны по минимуму: сначала sell, если не получится — buy.
            try:
                order = exchange.create_market_sell_order(place_symbol, qty)
            except Exception:
                order = exchange.create_market_buy_order(place_symbol, qty)

        print("🧾 Bybit order response:", order)
        return JSONResponse({"status": "ok", "order": order})

    except Exception as e:
        print("❌ Trade error:", e)
        raise HTTPException(status_code=500, detail=f"trade error: {e}")
