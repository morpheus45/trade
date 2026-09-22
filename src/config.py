"""
Configuration centrale du bot de trading.
Toutes les credentials sont lues depuis le fichier .env (jamais en dur dans le code).
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

# ─── Credentials Exchange ────────────────────────────────────────────────────
# Accepte BINANCE_API_KEY ou API_KEY (compatibilité avec les deux nommages)
BINANCE_API_KEY    = (os.getenv("BINANCE_API_KEY") or
                      os.getenv("BINANCE_API_KEY_1") or
                      os.getenv("API_KEY", ""))
BINANCE_API_SECRET = (os.getenv("BINANCE_API_SECRET") or
                      os.getenv("BINANCE_API_SECRET_1") or
                      os.getenv("API_SECRET", ""))

# ─── Claude AI / Groq ────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")

# ─── Telegram ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── Mode de fonctionnement ──────────────────────────────────────────────────
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"

# ─── Capital de depart en mode PAPER ─────────────────────────────────────────
# En LIVE, le capital est lu sur le compte Binance. En PAPER, il vient d'ici.
# (Ordre de priorite : variable INITIAL_CAPITAL, puis initial_capital.txt.)
try:
    INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "") or 0) or None
except ValueError:
    INITIAL_CAPITAL = None

# ─── Dashboard : reseau et securite ──────────────────────────────────────────
# Mot de passe d'acces au dashboard. Sans lui, le dashboard refuse d'ecouter sur
# une interface autre que la boucle locale (voir main.py).
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")

# Interface d'ecoute. 127.0.0.1 = accessible uniquement depuis la machine ;
# 0.0.0.0 = accessible depuis le reseau (a reserver aux cas ou un tunnel ou un
# reverse proxy se charge de l'exposition).
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.getenv("PORT") or os.getenv("DASHBOARD_PORT") or 5000)

# A activer uniquement derriere un reverse proxy de confiance (Cloudflare Tunnel,
# Caddy, nginx). Fait lire l'IP client dans X-Forwarded-For ; sans proxy devant,
# cet en-tete est falsifiable et permettrait de contourner le blocage anti-brute-force.
TRUST_PROXY = os.getenv("TRUST_PROXY", "false").lower() == "true"

# Duree de validite d'une session ouverte (en jours).
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))

# Autorise le bouton "Mise a jour" du dashboard (git pull + redemarrage).
# Desactive par defaut : executer du code recupere sur le reseau depuis une
# interface web est un vecteur d'attaque si le mot de passe fuit.
ALLOW_REMOTE_UPDATE = os.getenv("ALLOW_REMOTE_UPDATE", "false").lower() == "true"

# Autorise le declenchement de l'entrainement ML depuis le dashboard.
ALLOW_REMOTE_TRAIN = os.getenv("ALLOW_REMOTE_TRAIN", "true").lower() == "true"

# ─── Devise de cotation ──────────────────────────────────────────────────────
# EUR obligatoire pour Binance France (MiCA — USDT restreint)
QUOTE_CURRENCY = "EUR"

# ─── Paires tradées ──────────────────────────────────────────────────────────
# Paires EUR disponibles sur Binance France
TRADE_PAIRS = [
    "BTC/EUR",    # Roi du marché — signal de tendance macro
    "ETH/EUR",    # DeFi / altseason leader
    "BNB/EUR",    # BNB Chain ecosystem
    "SOL/EUR",    # Layer 1 haute performance
    "XRP/EUR",    # Haute liquidité, corrélation modérée BTC
    "DOGE/EUR",   # Momentum / sentiment driven
    "ADA/EUR",    # Cardano
    "LTC/EUR",    # Litecoin
]

# ─── Timeframes ───────────────────────────────────────────────────────────────
TIMEFRAME_PRIMARY = "1h"    # Signal d'entrée — indicateurs techniques
TIMEFRAME_TREND   = "4h"    # Confirmation tendance macro (filtre supérieur)

# ─── Frais Binance ───────────────────────────────────────────────────────────
BINANCE_FEE_PCT       = 0.001  # 0.1% par ordre (entrée + sortie = 0.2% aller-retour)
# Un trade doit gagner > 2 × FEE_PCT pour être rentable après frais

# ─── Gestion du risque ───────────────────────────────────────────────────────
RISK_PER_TRADE_PCT    = 0.05   # 5% du capital par trade
STOP_LOSS_ATR_MULT    = 1.5    # Stop = 1.5 × ATR
TAKE_PROFIT_ATR_MULT  = 3.0    # TP = 3.0 × ATR → ~2-3% gain typique >> 0.2% fees
MAX_OPEN_POSITIONS    = 1      # Petit capital : 1 position à la fois
# Binance minimum order value (EUR)
MIN_ORDER_EUR         = 5.0    # Refuser les ordres < 5 EUR notional
MIN_ORDER_USDT        = 5.0    # Alias compat (ne pas supprimer)
MAX_POSITION_PCT      = 0.90   # Max 90% du capital par trade (petit compte)
# Profit minimum net de frais pour valider un trade
MIN_PROFIT_AFTER_FEES = 2 * BINANCE_FEE_PCT  # 0.2% minimum absolu

# ─── Trailing stop ───────────────────────────────────────────────────────────
# Active le trailing stop dès que la position est profitable à x%
# puis maintient le stop à y% sous le plus haut atteint
TRAILING_STOP_ACTIVATION = 0.015   # Active après +1.5% de profit flottant
TRAILING_STOP_DISTANCE   = 0.012   # Trail à 1.2% sous le pic (serré = préserve profit)

# ─── Prise de profit partielle ───────────────────────────────────────────────
# À 50% du TP : vendre la moitié de la position, laisser le reste traîler
PARTIAL_TP_RATIO       = 0.50   # Vendre 50% à PARTIAL_TP_ATR_MULT × ATR
PARTIAL_TP_ATR_MULT    = 1.5    # Premier TP à 1.5 × ATR (= 50% de TAKE_PROFIT)

# ─── Sizing adaptatif ────────────────────────────────────────────────────────
# Quand ML + Claude sont en accord fort → augmenter la mise
ADAPTIVE_SIZE_FACTOR        = 1.5   # ×1.5 sur signaux de haute qualité
ADAPTIVE_SIZE_ML_THRESHOLD  = 0.75  # ML confidence requise
ADAPTIVE_SIZE_MAX_PCT       = 0.07  # Plafond : 7% du capital (petit capital)

# ─── Circuit breaker ─────────────────────────────────────────────────────────
DAILY_LOSS_LIMIT_PCT  = 0.05   # -5% en 1 jour → pause trading
MAX_DRAWDOWN_PCT      = 0.15   # -15% depuis le pic → arrêt d'urgence
CB_AUTO_RESET_HOURS   = 6      # Auto-reset du circuit breaker après 6h de pause

# ─── Paramètres des indicateurs ──────────────────────────────────────────────
RSI_PERIOD        = 14
RSI_OVERSOLD      = 35
RSI_OVERBOUGHT    = 65
MACD_FAST         = 12
MACD_SLOW         = 26
MACD_SIGNAL       = 9
BB_PERIOD         = 20
BB_STD            = 2.0
EMA_FAST          = 9
EMA_SLOW          = 21
EMA_TREND         = 200
ATR_PERIOD        = 14
VOLUME_MA_PERIOD  = 20
ADX_PERIOD        = 14          # ADX pour la force de tendance
ADX_TRENDING      = 25          # ADX > 25 → marché directionnel (signal fiable)
ADX_WEAK          = 15          # ADX < 15 → marché sans direction (skip)
ROC_PERIOD        = 10          # Rate of Change sur 10 bougies
OBV_EMA_PERIOD    = 20          # EMA de l'OBV pour filtrer le bruit

# ─── Paramètres généraux ─────────────────────────────────────────────────────
LOOKBACK_CANDLES       = 300    # Bougies chargées (250 → 300 pour ADX)
LOOP_INTERVAL_SECONDS  = 30     # Boucle toutes les 30s (60s → 30s)

# ─── Chemins fichiers ─────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent.parent
MODEL_PATH    = BASE_DIR / "models" / "xgboost_model.json"
LOGS_DIR      = BASE_DIR / "logs"
DATA_DIR      = BASE_DIR / "data"
TRADES_CSV    = LOGS_DIR / "trades.csv"
PORTFOLIO_CSV = LOGS_DIR / "portfolio.csv"
