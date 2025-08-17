import os, ccxt
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from typing import Optional

EXCHANGE       = os.getenv("EXCHANGE", "bybit").lower()
USE_TESTNET    = os.getenv("USE_TESTNET", "true").lower() == "true"
MARKET_TYPE    = os.getenv("MARKET_TYPE", "linear").lower()      # linear | spot
API_KEY        = os.getenv("API_KEY", "")
API_SECRET     = os.getenv("API_SECRET", "")
ACCOUNT_EQUITY = float(os.getenv("ACCOUNT_EQUITY_USDT", "10000"))
WEBHOOK_TOKEN  = os.getenv("WEBHOOK_TOKEN", "")

if not API_KEY or not API_SECRET:
    raise RuntimeError("Set API_KEY and API_SECRET env vars")

def make_exchange():
    if EXCHANGE != "bybit":
        raise RuntimeError("Only EXCHANGE=bybit supported in this template")
    ex = ccxt.bybit({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "enableRateLimit": True,
        "options": {"defaultType": "linear" if MARKET_TYPE == "linear" else "spot"}
    })
    ex.set_sandbox_mode(USE_TESTNET)  # testnet
    return ex

ex = make_exchange()
markets = ex.load_markets()

class TVPayload(BaseModel):
    strategy_id: str
    version: str
    ticker: str
    tf: str
    time: int
    price: float
    action: str                # BUY | SELL | CLOSE_LONG | CLOSE_SHORT
    signal: str
    qty_pct: float
    sl_enabled: Optional[bool] = None
    sl_pct: Optional[float] = None
    sl_price: Optional[float] = None
    position_side: Optional[str] = None
    position_size: Optional[float] = None

def normalize_symbol(ticker: str) -> str:
    base = ticker.split(":")[-1].upper()  # e.g. BTCUSDT
    if MARKET_TYPE == "spot":
        pref = base[:-4] + "/USDT"
    else:
        pref = base[:-4] + "/USDT:USDT"
    if pref in markets:
        return pref
    for s in markets:
        if MARKET_TYPE == "spot" and s.endswith("/USDT") and s.replace("/", "") == base:
            return s
        if MARKET_TYPE != "spot" and s.endswith(":USDT") and s.startswith(base[:-4] + "/USDT"):
            return s
    for s in markets:
        if base[:-4] in s and "USDT" in s:
            return s
    raise HTTPException(status_code=400, detail=f"Symbol not found on Bybit for {base}")

def calc_amount(symbol: str, price: float, qty_pct: float) -> float:
    quote_to_spend = ACCOUNT_EQUITY * (qty_pct / 100.0)
    amt = quote_to_spend / max(price, 1e-9)
    return float(ex.amount_to_precision(symbol, amt))

def close_params() -> dict:
    return {"reduceOnly": True} if MARKET_TYPE != "spot" else {}

app = FastAPI()

@app.get("/")
async def root():
    return {"ok": True, "exchange": EXCHANGE, "market_type": MARKET_TYPE, "testnet": USE_TESTNET}

@app.post("/tv_webhook")
async def tv_webhook(req: Request):
    if WEBHOOK_TOKEN:
        hdr = req.headers.get("x-webhook-token") or req.headers.get("webhook-token")
        if hdr != WEBHOOK_TOKEN:
            raise HTTPException(status_code=403, detail="Invalid webhook token")

    payload = TVPayload(**(await req.json()))
    symbol  = normalize_symbol(payload.ticker)
    price   = float(payload.price)
    action  = payload.action.upper()

    amount = None
    try:
        if action == "BUY":
            amount = calc_amount(symbol, price, payload.qty_pct)
            order = ex.create_order(symbol, "market", "buy", amount)
        elif action == "SELL":
            amount = calc_amount(symbol, price, payload.qty_pct)
            order = ex.create_order(symbol, "market", "sell", amount)
        elif action == "CLOSE_LONG":
            order = ex.create_order(symbol, "market", "sell",
                                    calc_amount(symbol, price, payload.qty_pct), None, close_params())
        elif action == "CLOSE_SHORT":
            order = ex.create_order(symbol, "market", "buy",
                                    calc_amount(symbol, price, payload.qty_pct), None, close_params())
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported action: {action}")
    except ccxt.BaseError as e:
        raise HTTPException(status_code=502, detail=f"Exchange error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Order error: {e}")

    return {"ok": True, "symbol": symbol, "action": action, "amount": amount, "order": order}
