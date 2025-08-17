# bot.py
import os
import json
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

# Загружаем .env из текущей папки
load_dotenv()

WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "mysecret123").strip()

app = FastAPI(title="TV-Bybit Bot", version="1.0.0")

# ================== ЛОГИРОВАНИЕ КАЖДОГО ЗАПРОСА ==================
@app.middleware("http")
async def log_requests(request: Request, call_next):
    try:
        body = await request.body()
        print(f"➡️  {request.method} {request.url}")
        if body:
            print(f"    body: {body.decode('utf-8', 'ignore')}")
        response = await call_next(request)
        print(f"⬅️  {response.status_code} {request.url.path}")
        return response
    except Exception as e:
        print(f"💥 Middleware error: {e}")
        return JSONResponse({"detail": "Middleware error"}, status_code=500)

# ================== ВСПОМОГАТЕЛЬНОЕ ==================
def mask_token(tok: str) -> str:
    if len(tok) <= 4:
        return "***"
    return tok[:2] + "*" * (len(tok) - 4) + tok[-2:]

# ================== РОУТЫ ДЛЯ ПРОВЕРКИ ==================
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

# ================== ВЕБХУКИ ==================
@app.post("/tv_webhook")
async def tv_webhook(request: Request):
    # Токен через query-параметр ?token=...
    token = request.query_params.get("token", "")
    if token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid webhook token")

    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty body")

    try:
        data = json.loads(raw.decode())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    print("📩 Пришел сигнал (query-token):", data)

    symbol = data.get("symbol", "CYBERUSDT")
    side   = data.get("side")
    qty    = float(data.get("qty", 0.01))
    reason = data.get("reason", "signal")

    print(f"✅ SIGNAL: side={side} qty={qty} symbol={symbol} reason={reason}")
    return {"status": "success", "received": data}

@app.post("/webhook/{token}")
async def webhook_path(token: str, request: Request):
    # Токен в path
    if token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid webhook token")

    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty body")

    try:
        data = json.loads(raw.decode())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    print("📩 Пришел сигнал (path-token):", data)

    symbol = data.get("symbol", "CYBERUSDT")
    side   = data.get("side")
    qty    = float(data.get("qty", 0.01))
    reason = data.get("reason", "signal")

    print(f"✅ SIGNAL: side={side} qty={qty} symbol={symbol} reason={reason}")
    return {"status": "success", "received": data}
