import os
import json
from fastapi import FastAPI, Request
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "mysecret123")

app = FastAPI()

# ==========================
# Базовые эндпоинты
# ==========================
@app.get("/")
def root():
    return {"ok": True, "hint": "См. /info и /docs"}

@app.get("/info")
def info():
    return {
        "ok": True,
        "endpoints": {
            "home": "/",
            "info": "/info",
            "swagger": "/docs",
            "webhook_query": "/tv_webhook?token=<YOUR_TOKEN>",
            "webhook_path": "/webhook/<YOUR_TOKEN>"
        },
        "your_token": WEBHOOK_TOKEN
    }

# ==========================
# Вебхуки
# ==========================
@app.post("/tv_webhook")
async def tv_webhook(request: Request, token: str = None):
    if token != WEBHOOK_TOKEN:
        return {"status": "error", "message": "Invalid webhook token"}

    data = await request.json()
    print("📩 Получен сигнал:", data)

    symbol = data.get("symbol", "CYBERUSDT")
    side   = data.get("side", "buy")
    qty    = float(data.get("qty", 0.01))
    reason = data.get("reason", "signal")

    print(f"✅ {side.upper()} {qty} {symbol} | reason: {reason}")
    return {"status": "success", "received": data}

@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):
    if token != WEBHOOK_TOKEN:
        return {"status": "error", "message": "Invalid webhook token"}

    data = await request.json()
    print("📩 Получен сигнал:", data)

    symbol = data.get("symbol", "CYBERUSDT")
    side   = data.get("side", "buy")
    qty    = float(data.get("qty", 0.01))
    reason = data.get("reason", "signal")

    print(f"✅ {side.upper()} {qty} {symbol} | reason: {reason}")
    return {"status": "success", "received": data}
