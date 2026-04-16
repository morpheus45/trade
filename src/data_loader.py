import requests
import logging

# Nouvelle API Binance pour Order Book
BINANCE_API = "https://api.binance.com/api/v3/depth"

# Nouvelle API Glassnode pour On-Chain Data (ajoute ta clé API)
GLASSNODE_API = "https://api.glassnode.com/v1/metrics"

# Order Book Binance
def get_order_book(symbol="BTCUSDC"):
    try:
        response = requests.get(BINANCE_API, params={"symbol": symbol, "limit": 5})
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Erreur Order Book Binance : {e}")
        return None

# Données On-Chain Glassnode
def get_onchain_data(symbol="BTC"):
    API_KEY = "TA_CLE_GLASSNODE"  # Remplace par ta vraie clé Glassnode
    try:
        response = requests.get(GLASSNODE_API, params={"a": symbol, "api_key": API_KEY})
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Erreur On-Chain Glassnode : {e}")
        return None
