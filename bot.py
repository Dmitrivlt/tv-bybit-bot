import os
import json
import ccxt
from fastapi import FastAPI, Request
from dotenv import load_dotenv

# Загружаем ключи из .env
load_dotenv()
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")

# Подключаемся к Bybit (Testnet)
exchange = ccxt.bybit({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
    "options": {"defaultType": "swap"},  # деривативы USDT
})
exchange.set_sandbox_mode(True)  # <<< ВАЖНО: используем testnet

app = FastAPI()

@app.get("/")
def home():
    return {"status": "running"}

@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):
    body = await request.body()
    data = json.loads(body.decode())

    symbol = data.get("symbol", "CYBERUSDT")
    side   = data.get("side")
    qty    = float(data.get("qty", 0.01))
    reason = data.get("reason", "signal")

    try:
        if side == "buy":
            order = exchange.create_market_buy_order(symbol, qty)
        elif side == "sell":
            order = exchange.create_market_sell_order(symbol, qty)
        elif side == "close":
            # закрываем все позиции (рыночный ордер в противоположную сторону)
            pos = exchange.fetch_positions([symbol])
            if pos and float(pos[0]["contracts"]) != 0:
                if pos[0]["side"] == "long":
                    order = exchange.create_market_sell_order(symbol, qty)
                elif pos[0]["side"] == "short":
                    order = exchange.create_market_buy_order(symbol, qty)
                else:
                    order = {"info": "no open position"}
            else:
                order = {"info": "flat"}
        else:
            order = {"error": f"Unknown side: {side}"}

        print(f"✅ {side.upper()} {qty} {symbol} | reason: {reason}")
        return {"status": "success", "order": order}

    except Exception as e:
        print(f"❌ ERROR: {e}")
        return {"status": "error", "message": str(e)}
