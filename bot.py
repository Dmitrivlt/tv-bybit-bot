import os
import json
from fastapi import FastAPI, Request
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "mysecret123")

app = FastAPI()

@app.get("/")
def home():
    return {"status": "running"}

@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):
    # Проверяем секрет
    if token != WEBHOOK_TOKEN:
        return {"status": "error", "message": "Invalid token"}

    # Читаем тело запроса
    body = await request.body()
    data = json.loads(body.decode())

    # Логируем полученный сигнал
    print("📩 Получен сигнал:", data)

    # Достаём значения
    symbol = data.get("symbol", "CYBERUSDT")
    side   = data.get("side")
    qty    = float(data.get("qty", 0.01))
    reason = data.get("reason", "signal")

    # На этом этапе мы пока не торгуем — только печать
    print(f"✅ {side.upper()} {qty} {symbol} | reason: {reason}")

    return {"status": "success", "received": data}
