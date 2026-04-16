# websocket_handler.py

import websocket
import json
import config

def on_message(ws, message):
    """Gère les messages WebSocket Binance"""
    data = json.loads(message)
    print(f"Données reçues: {data}")

def start_websocket():
    """Lance WebSocket Binance"""
    url = "wss://stream.binance.com:9443/ws/btcusdt@trade"
    ws = websocket.WebSocketApp(url, on_message=on_message)
    ws.run_forever()