import os
import json
from fastapi import FastAPI, Request, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# ---------- Загрузка .env ----------
load_dotenv()
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "mysecret123")

# ---------- FastAPI ----------
app = FastAPI(title="TV-Bybit Bot", version="1.0.0")

# Разрешим CORS на всякий (Swagger, тесты и т.д.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Вспомогательные ----------
def log_signal(data: dict):
    print("📩 Получен сигнал:", data)
    symbol = data.get("symbol", "CYBERUSDT")
    side   = data.get("side")
    qty    = data.get("qty", 0.01)
    reason = data.get("reason", "signal")
    try:
        qty = float(qty)
    except Exception:
        qty = 0.01
    side_str = str(side).upper() if side else "UNKNOWN"
    print(f"✅ {side_str} {qty} {symbol} | reason: {reason}")

# ---------- Service endpoints ----------
@app.get("/")
def root():
    return {"ok": True, "exchange": "bybit", "market_type": "linear", "testnet": True}

@app.get("/info")
def info():
    return {"ok": True, "expects": {
        "url_1": "/webhook/{token}",
        "url_2": "/tv_webhook (token via header X-Webhook-Token or ?token=)",
        "token_env": "WEBHOOK_TOKEN",
        "example_body": {"symbol":"CYBERUSDT","side":"buy","qty":1,"reason":"test"}
    }}

# ---------- Вариант 1: токен в пути ----------
@app.post("/webhook/{token}")
async def webhook_with_token(token: str, request: Request):
    if token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid webhook token (path)")

    raw = await request.body()
    try:
        data = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")

    log_signal(data)
    # Здесь пока только лог, без торговли.
    return {"status": "success", "received": data, "token_mode": "path"}

# ---------- Вариант 2: токен в заголовке или как ?token= ----------
@app.post("/tv_webhook")
async def tv_webhook(
    request: Request,
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
    token_q: str | None = Query(default=None, alias="token"),
):
    # Примем токен либо из заголовка, либо из query-параметра
    token = x_webhook_token or token_q
    if token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid webhook token")

    raw = await request.body()
    try:
        data = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")

    log_signal(data)
    # Здесь пока только лог, без торговли.
    return {"status": "success", "received": data, "token_mode": "header_or_query"}
