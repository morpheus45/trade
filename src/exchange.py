"""
Interface avec l'exchange Binance via CCXT.
Supporte le mode PAPER TRADING (simulation sans ordres réels).
"""
import logging
import time
import pandas as pd
import ccxt
import config

logger = logging.getLogger(__name__)


class Exchange:
    def __init__(self):
        self.paper_trading = config.PAPER_TRADING

        # Connexion CCXT (utilisée même en paper pour les prix et candles)
        self._exchange = ccxt.binance({
            "apiKey": config.BINANCE_API_KEY,
            "secret": config.BINANCE_API_SECRET,
            "options": {"defaultType": "spot"},
            "enableRateLimit": True,
        })

        self._markets_cache: dict = {}

        if self.paper_trading:
            logger.info("⚠️  MODE PAPER TRADING activé — aucun ordre réel ne sera passé")
        else:
            logger.warning("🔴 MODE LIVE — les ordres seront exécutés avec de VRAIS fonds")

    # ─── Données de marché ────────────────────────────────────────────────────

    def fetch_ohlcv(self, pair: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        """
        Récupère les bougies OHLCV.
        Retourne un DataFrame avec colonnes : timestamp, open, high, low, close, volume.
        """
        try:
            raw = self._exchange.fetch_ohlcv(pair, timeframe, limit=limit)
            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            return df
        except ccxt.NetworkError as e:
            logger.error(f"Erreur réseau pour {pair}: {e}")
            return pd.DataFrame()
        except ccxt.ExchangeError as e:
            logger.error(f"Erreur exchange pour {pair}: {e}")
            return pd.DataFrame()

    def fetch_ticker(self, pair: str) -> dict:
        """Récupère le ticker courant (prix, volume 24h, etc.)."""
        try:
            return self._exchange.fetch_ticker(pair)
        except Exception as e:
            logger.error(f"Erreur fetch_ticker {pair}: {e}")
            return {}

    def get_price(self, pair: str) -> float | None:
        """Retourne le dernier prix mid."""
        ticker = self.fetch_ticker(pair)
        return ticker.get("last") if ticker else None

    def get_balance(self, asset: str = "EUR") -> float:
        """Solde disponible pour un actif."""
        if self.paper_trading:
            return 0.0  # Géré par PortfolioManager en paper mode
        try:
            bal = self._exchange.fetch_balance()
            return float(bal.get(asset, {}).get("free", 0))
        except Exception as e:
            logger.error(f"Erreur get_balance {asset}: {e}")
            return 0.0

    # ─── Passage d'ordres ────────────────────────────────────────────────────

    def place_market_order(self, pair: str, side: str, amount: float) -> dict | None:
        """
        Passe un ordre market.
        En paper mode, simule l'ordre et retourne un faux order dict.
        `side` : 'buy' ou 'sell'
        `amount` : quantité de la base (ex: BTC pour BTC/USDT)
        """
        price = self.get_price(pair)
        if price is None:
            logger.error(f"Impossible d'obtenir le prix pour {pair}, ordre annulé")
            return None

        if self.paper_trading:
            order = {
                "id": f"paper_{int(time.time()*1000)}",
                "symbol": pair,
                "side": side,
                "type": "market",
                "amount": amount,
                "price": price,
                "cost": amount * price,
                "status": "closed",
                "paper": True,
            }
            logger.info(f"📄 [PAPER] {side.upper()} {amount:.6f} {pair} @ {price:.4f}")
            return order

        # ─── Ordre réel ───────────────────────────────────────────────────
        try:
            if not self._markets_cache:
                self._markets_cache = self._exchange.load_markets()
            markets = self._markets_cache
            market = markets.get(pair, {})
            limits = market.get("limits", {})

            min_amount = limits.get("amount", {}).get("min", 0) or 0
            min_cost   = limits.get("cost", {}).get("min", 0) or 0

            if amount < min_amount:
                logger.warning(f"Montant {amount} < min {min_amount} pour {pair}, ajustement")
                amount = min_amount

            if amount * price < min_cost:
                logger.warning(f"Coût total < min_cost {min_cost} pour {pair}, ajustement")
                amount = min_cost / price

            # Arrondir selon la précision de l'exchange
            amount = self._exchange.amount_to_precision(pair, amount)

            order = self._exchange.create_order(pair, "market", side, float(amount))
            logger.info(f"✅ Ordre RÉEL {side.upper()} {amount} {pair} @ ~{price:.4f} — id: {order['id']}")
            return order

        except ccxt.InsufficientFunds as e:
            logger.error(f"Fonds insuffisants pour {side} {pair}: {e}")
            return None
        except ccxt.ExchangeError as e:
            logger.error(f"Erreur exchange ordre {side} {pair}: {e}")
            return None
        except Exception as e:
            logger.error(f"Erreur inattendue ordre {side} {pair}: {e}")
            return None
