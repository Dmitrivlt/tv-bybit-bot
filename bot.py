import os
import json
from fastapi import FastAPI, Request
from dotenv import load_dotenv

# Загружаем переменные из .env (если есть)
load_dotenv()
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "mysecret123")

app = FastAPI()

# ==========================
# Утилита маскировки токена
# ==========================
def mask_token(token: str) -> str:
    if not token:
        return None
    if len(token) <= 4:
        return "*" * len(token)
    return token[:2] + "*" * (len(token) - 4) + token[-2:]

# ==========================
# Базовые эндпоинты
# ==========================
@app.get("/")
def root():
    return {
        "ok": True,
        "hint": "См. /info и /docs для инструкций",
    }

@app.get("/info")
def info():
    return {
        "ok": True,
        "endpoints": {
            "webhook_query": "/tv_webhook?token=<YOUR_TOKEN>",
            "webhook_path": "/webhook/<YOUR_TOKEN>",
            "docs": "/docs"
        },
        "token_masked": mask_token(WEBHOOK_TOKEN)
    }

# ==========================
# Вебхуки
# ==========================
@app.post("/tv_webhook")
async def tv_webhook(request: Request, token: str = None):
    """Приём вебхука через query-параметр ?token=..."""
    if token != WEBHOOK_TOKEN:
        return {"status": "error", "message": "Invalid webhook token"}

    body = await request.body()
    try:
        data = json.loads(body.decode())
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}

    print("📩 Получен сигнал:", data)

    symbol = data.get("symbol", "CYBERUSDT")
    side   = data.get("side", "buy")
    qty    = float(data.get("qty", 0.01))
    reason = data.get("reason", "signal")

    print(f"✅ {side.upper()} {qty} {symbol} | reason: {reason}")

    return {"status": "success", "received": data}

@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):
    """Приём вебхука через путь /webhook/<token>"""
    if token != WEBHOOK_TOKEN:
        return {"status": "error", "message": "Invalid webhook token"}

    body = await request.body()
    try:
        data = json.loads(body.decode())
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}

    print("📩 Получен сигнал:", data)

    symbol = data.get("symbol", "CYBERUSDT")
    side   = data.get("side", "buy")
    qty    = float(data.get("qty", 0.01))
    reason = data.get("reason", "signal")

    print(f"✅ {side.upper()} {qty} {symbol} | reason: {reason}")

    return {"status": "success", "received": data}
