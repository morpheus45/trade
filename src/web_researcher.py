"""
web_researcher.py
Module de recherche internet en temps réel pour le bot de trading crypto.

Sources utilisées (100% gratuites, sans clé API) :
  - CoinGecko API        : prix, volume, sentiment, trending
  - Fear & Greed Index   : alternative.me
  - RSS News             : CoinDesk, Cointelegraph, Decrypt
  - Reddit r/CryptoCurrency : sentiment des titres (JSON public)
  - Binance Public API   : funding rates, open interest futures
"""

import logging
import time
import re
from datetime import datetime, timezone
from typing import Optional

import requests

try:
    import feedparser
    _FEEDPARSER_AVAILABLE = True
except ImportError:
    _FEEDPARSER_AVAILABLE = False

try:
    import config as _config
except Exception:
    _config = None

logger = logging.getLogger(__name__)

# ─── Constantes ───────────────────────────────────────────────────────────────

_TIMEOUT = 5          # secondes par requête
_CACHE_TTL = 600      # 10 minutes

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

_RSS_FEEDS = {
    "CoinDesk":      "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt":       "https://decrypt.co/feed",
}

# Mapping paire Binance → id CoinGecko (EUR + USDT pour compatibilité)
_PAIR_TO_COINGECKO = {
    # Paires EUR (Binance France / MiCA)
    "BTC/EUR":   "bitcoin",
    "ETH/EUR":   "ethereum",
    "BNB/EUR":   "binancecoin",
    "SOL/EUR":   "solana",
    "XRP/EUR":   "ripple",
    "DOGE/EUR":  "dogecoin",
    "ADA/EUR":   "cardano",
    "LTC/EUR":   "litecoin",
    # Fallback USDT (pour market_memory et données historiques)
    "BTC/USDT":  "bitcoin",
    "ETH/USDT":  "ethereum",
    "BNB/USDT":  "binancecoin",
    "SOL/USDT":  "solana",
    "AVAX/USDT": "avalanche-2",
    "XRP/USDT":  "ripple",
    "DOGE/USDT": "dogecoin",
    "ADA/USDT":  "cardano",
    "LTC/USDT":  "litecoin",
}

# Mots clés positifs / négatifs pour l'analyse de sentiment basique
_POSITIVE_WORDS = {
    "surge", "rally", "bull", "breakout", "ath", "high", "gain", "rise",
    "soar", "pump", "boost", "adoption", "approve", "etf", "institutional",
    "moon", "record", "growth", "buy", "uptrend", "recovery",
}
_NEGATIVE_WORDS = {
    "crash", "plunge", "bear", "dump", "sell", "ban", "collapse", "hack",
    "scam", "fraud", "fear", "drop", "fall", "down", "loss", "bankruptcy",
    "liquidation", "contagion", "attack", "exploit", "warning", "lawsuit",
}


# ─── Cache ────────────────────────────────────────────────────────────────────

class _Cache:
    """Cache en mémoire avec TTL."""

    def __init__(self):
        self._store: dict = {}

    def get(self, key: str):
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value, ttl: int = _CACHE_TTL) -> None:
        self._store[key] = (value, time.monotonic() + ttl)

    def clear(self) -> None:
        self._store.clear()


# ─── Classe principale ────────────────────────────────────────────────────────

class WebResearcher:
    """
    Recherche internet en temps réel sur les marchés crypto.
    Toutes les sources sont gratuites et ne nécessitent pas de clé API.
    """

    def __init__(self):
        self._cache = _Cache()
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        logger.info("WebResearcher initialisé (cache TTL=%ds)", _CACHE_TTL)

    # ─── Méthodes publiques ───────────────────────────────────────────────────

    def research_pair(self, pair: str) -> dict:
        """
        Recherche complète sur une paire crypto.
        Retourne : prix, variation, volume, sentiment, news, funding.
        """
        cache_key = f"research_pair:{pair}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        result = {
            "pair": pair,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "coingecko": {},
            "funding": {},
            "news_headlines": [],
            "reddit_sentiment": {},
            "error": None,
        }

        cg_id = _PAIR_TO_COINGECKO.get(pair)
        if cg_id:
            result["coingecko"] = self._fetch_coingecko_coin(cg_id)

        binance_symbol = _pair_to_binance_symbol(pair)
        if binance_symbol:
            result["funding"] = self._fetch_funding_data(binance_symbol)

        base_currency = pair.split("/")[0].lower()
        result["news_headlines"] = self._fetch_news_headlines(base_currency, max_items=5)
        result["reddit_sentiment"] = self._fetch_reddit_sentiment(base_currency)

        self._cache.set(cache_key, result)
        return result

    def get_global_market(self) -> dict:
        """
        Retourne les données de marché globales :
        BTC dominance, market cap total, Fear & Greed Index.
        """
        cache_key = "global_market"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "btc_dominance_pct": None,
            "total_market_cap_usd": None,
            "total_volume_24h_usd": None,
            "active_cryptocurrencies": None,
            "fear_greed": {},
            "error": None,
        }

        # CoinGecko global
        cg_global = self._fetch_coingecko_global()
        result.update(cg_global)

        # Fear & Greed
        result["fear_greed"] = self._fetch_fear_greed()

        self._cache.set(cache_key, result)
        return result

    def get_trending_coins(self) -> list:
        """
        Retourne les cryptos tendance sur CoinGecko (top 7 trending).
        """
        cache_key = "trending_coins"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        result = self._fetch_coingecko_trending()
        self._cache.set(cache_key, result)
        return result

    def get_funding_rates(self, pair: str) -> dict:
        """
        Retourne le funding rate et l'open interest pour une paire futures.
        """
        cache_key = f"funding:{pair}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        symbol = _pair_to_binance_symbol(pair)
        if not symbol:
            return {"pair": pair, "error": "Symbol non reconnu"}

        result = self._fetch_funding_data(symbol)
        result["pair"] = pair
        self._cache.set(cache_key, result)
        return result

    def get_full_market_report(self, pairs: list) -> str:
        """
        Génère un rapport texte complet sur le marché pour Claude.
        Agrège toutes les sources disponibles.
        """
        lines = ["=== RAPPORT MARCHÉ EN TEMPS RÉEL ===\n"]
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"Généré le : {ts}\n")

        # Marché global
        try:
            gm = self.get_global_market()
            lines.append("--- MARCHÉ GLOBAL ---")
            if gm.get("btc_dominance_pct") is not None:
                lines.append(f"  BTC Dominance     : {gm['btc_dominance_pct']:.1f}%")
            if gm.get("total_market_cap_usd") is not None:
                cap_b = gm["total_market_cap_usd"] / 1e9
                lines.append(f"  Market Cap Total  : ${cap_b:,.0f}B")
            if gm.get("total_volume_24h_usd") is not None:
                vol_b = gm["total_volume_24h_usd"] / 1e9
                lines.append(f"  Volume 24h        : ${vol_b:,.0f}B")

            fg = gm.get("fear_greed", {})
            if fg:
                lines.append(
                    f"  Fear & Greed      : {fg.get('value', '?')}/100 "
                    f"({fg.get('classification', '?')})"
                )
                hist = fg.get("history", [])
                if hist:
                    hist_str = ", ".join(
                        f"[{h.get('date','')}]{h.get('value','?')}"
                        for h in hist[:5]
                    )
                    lines.append(f"  Historique F&G    : {hist_str}")
        except Exception:
            logger.exception("Erreur rapport global")
            lines.append("  (Erreur marché global)")

        lines.append("")

        # Trending
        try:
            trending = self.get_trending_coins()
            if trending:
                names = ", ".join(
                    f"{c.get('name', '?')} ({c.get('symbol', '?').upper()})"
                    for c in trending[:7]
                )
                lines.append(f"--- TRENDING CoinGecko ---\n  {names}\n")
        except Exception:
            pass

        # Paires demandées
        lines.append("--- PAIRES ---")
        for pair in pairs:
            try:
                data = self.research_pair(pair)
                cg = data.get("coingecko", {})
                fd = data.get("funding", {})

                price = cg.get("current_price")
                chg24 = cg.get("price_change_pct_24h")
                vol24 = cg.get("volume_24h")
                sent  = cg.get("sentiment_votes_up_pct")
                fr    = fd.get("funding_rate")
                oi    = fd.get("open_interest_usd")

                parts = [f"  {pair}"]
                if price is not None:
                    parts.append(f"${price:,.4f}")
                if chg24 is not None:
                    sign = "+" if chg24 >= 0 else ""
                    parts.append(f"{sign}{chg24:.2f}%/24h")
                if vol24 is not None:
                    parts.append(f"Vol=${vol24/1e6:.0f}M")
                if sent is not None:
                    parts.append(f"Sent={sent:.0f}%↑")
                if fr is not None:
                    parts.append(f"FR={fr*100:.4f}%")
                if oi is not None:
                    parts.append(f"OI=${oi/1e6:.0f}M")
                lines.append(" | ".join(parts))
            except Exception:
                lines.append(f"  {pair} : (erreur récupération)")

        lines.append("")

        # News
        try:
            all_headlines = self._fetch_news_headlines("crypto", max_items=10)
            if all_headlines:
                lines.append("--- DERNIÈRES NEWS ---")
                for h in all_headlines:
                    sent_tag = f"[{h.get('sentiment', '?').upper()}]"
                    src = h.get("source", "?")
                    title = h.get("title", "")[:100]
                    lines.append(f"  {sent_tag} [{src}] {title}")
        except Exception:
            pass

        lines.append("")

        # Reddit
        try:
            reddit = self._fetch_reddit_posts("CryptoCurrency", limit=15)
            if reddit:
                pos = sum(1 for p in reddit if p.get("sentiment") == "positive")
                neg = sum(1 for p in reddit if p.get("sentiment") == "negative")
                neu = len(reddit) - pos - neg
                lines.append(
                    f"--- REDDIT r/CryptoCurrency (top {len(reddit)} posts) ---\n"
                    f"  Sentiment : {pos} positifs / {neg} négatifs / {neu} neutres"
                )
                top_posts = sorted(reddit, key=lambda x: x.get("score", 0), reverse=True)[:3]
                for p in top_posts:
                    lines.append(f"  ↑{p.get('score',0):,} {p.get('title','')[:90]}")
        except Exception:
            pass

        return "\n".join(lines)

    # ─── Fetchers privés ──────────────────────────────────────────────────────

    def _get(self, url: str, params: Optional[dict] = None) -> Optional[dict]:
        """GET JSON avec timeout et gestion d'erreur."""
        try:
            resp = self._session.get(url, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            logger.warning("Timeout: %s", url)
        except requests.exceptions.HTTPError as exc:
            logger.warning("HTTP %s: %s", exc.response.status_code if exc.response else "?", url)
        except requests.exceptions.ConnectionError:
            logger.warning("Connexion impossible: %s", url)
        except Exception:
            logger.exception("Erreur GET: %s", url)
        return None

    def _fetch_coingecko_coin(self, coin_id: str) -> dict:
        """Récupère les données d'une crypto via CoinGecko."""
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
        params = {
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "false",
        }
        data = self._get(url, params=params)
        if not data:
            return {}
        try:
            md = data.get("market_data", {})
            result = {
                "id": coin_id,
                "name": data.get("name"),
                "symbol": data.get("symbol", "").upper(),
                "current_price": _safe_get(md, "current_price", "usd"),
                "market_cap_usd": _safe_get(md, "market_cap", "usd"),
                "volume_24h": _safe_get(md, "total_volume", "usd"),
                "price_change_pct_24h": md.get("price_change_percentage_24h"),
                "price_change_pct_7d": md.get("price_change_percentage_7d"),
                "price_change_pct_30d": md.get("price_change_percentage_30d"),
                "ath_usd": _safe_get(md, "ath", "usd"),
                "ath_change_pct": _safe_get(md, "ath_change_percentage", "usd"),
                "high_24h": _safe_get(md, "high_24h", "usd"),
                "low_24h": _safe_get(md, "low_24h", "usd"),
                "sentiment_votes_up_pct": data.get("sentiment_votes_up_percentage"),
                "sentiment_votes_down_pct": data.get("sentiment_votes_down_percentage"),
            }
            return {k: v for k, v in result.items() if v is not None}
        except Exception:
            logger.exception("Erreur parse CoinGecko coin %s", coin_id)
            return {}

    def _fetch_coingecko_global(self) -> dict:
        """Récupère les données globales du marché via CoinGecko."""
        data = self._get("https://api.coingecko.com/api/v3/global")
        if not data:
            return {}
        try:
            gd = data.get("data", {})
            mcp = gd.get("market_cap_percentage", {})
            return {
                "btc_dominance_pct": mcp.get("btc"),
                "eth_dominance_pct": mcp.get("eth"),
                "total_market_cap_usd": _safe_get(gd, "total_market_cap", "usd"),
                "total_volume_24h_usd": _safe_get(gd, "total_volume", "usd"),
                "active_cryptocurrencies": gd.get("active_cryptocurrencies"),
                "market_cap_change_pct_24h": gd.get("market_cap_change_percentage_24h_usd"),
            }
        except Exception:
            logger.exception("Erreur parse CoinGecko global")
            return {}

    def _fetch_fear_greed(self) -> dict:
        """Récupère le Fear & Greed Index (7 derniers jours)."""
        data = self._get("https://api.alternative.me/fng/", params={"limit": 7})
        if not data:
            return {}
        try:
            entries = data.get("data", [])
            if not entries:
                return {}
            latest = entries[0]
            history = []
            for entry in entries[1:6]:
                ts_val = int(entry.get("timestamp", 0))
                date_str = datetime.fromtimestamp(ts_val, tz=timezone.utc).strftime("%Y-%m-%d") if ts_val else ""
                history.append({
                    "date": date_str,
                    "value": int(entry.get("value", 0)),
                    "classification": entry.get("value_classification", ""),
                })
            ts_latest = int(latest.get("timestamp", 0))
            date_latest = datetime.fromtimestamp(ts_latest, tz=timezone.utc).strftime("%Y-%m-%d") if ts_latest else ""
            return {
                "value": int(latest.get("value", 0)),
                "classification": latest.get("value_classification", ""),
                "date": date_latest,
                "history": history,
            }
        except Exception:
            logger.exception("Erreur parse Fear & Greed")
            return {}

    def _fetch_coingecko_trending(self) -> list:
        """Récupère les cryptos tendance sur CoinGecko."""
        data = self._get("https://api.coingecko.com/api/v3/search/trending")
        if not data:
            return []
        try:
            coins = data.get("coins", [])
            result = []
            for item in coins:
                coin = item.get("item", {})
                result.append({
                    "id": coin.get("id", ""),
                    "name": coin.get("name", ""),
                    "symbol": coin.get("symbol", ""),
                    "market_cap_rank": coin.get("market_cap_rank"),
                    "score": coin.get("score", 0),
                })
            return result
        except Exception:
            logger.exception("Erreur parse CoinGecko trending")
            return []

    def _fetch_funding_data(self, binance_symbol: str) -> dict:
        """Récupère le funding rate et l'open interest Binance Futures."""
        result = {"symbol": binance_symbol, "funding_rate": None, "open_interest_usd": None}

        # Funding rate
        fr_data = self._get(
            "https://fapi.binance.com/fapi/v1/premiumIndex",
            params={"symbol": binance_symbol},
        )
        if fr_data:
            try:
                result["funding_rate"] = float(fr_data.get("lastFundingRate", 0) or 0)
                result["mark_price"] = float(fr_data.get("markPrice", 0) or 0)
                result["index_price"] = float(fr_data.get("indexPrice", 0) or 0)
            except (ValueError, TypeError):
                pass

        # Open interest
        oi_data = self._get(
            "https://fapi.binance.com/fapi/v1/openInterest",
            params={"symbol": binance_symbol},
        )
        if oi_data:
            try:
                oi_qty = float(oi_data.get("openInterest", 0) or 0)
                mark = result.get("mark_price") or 0
                if mark and oi_qty:
                    result["open_interest_usd"] = oi_qty * mark
                result["open_interest_qty"] = oi_qty
            except (ValueError, TypeError):
                pass

        return result

    def _fetch_news_headlines(self, keyword: str = "bitcoin", max_items: int = 10) -> list:
        """
        Récupère les dernières news des flux RSS.
        Analyse le sentiment basique de chaque titre.
        """
        headlines = []

        if _FEEDPARSER_AVAILABLE:
            headlines = self._fetch_news_feedparser(keyword, max_items)
        else:
            headlines = self._fetch_news_requests(keyword, max_items)

        # Si les deux méthodes échouent, retourner liste vide sans lever d'exception
        return headlines[:max_items]

    def _fetch_news_feedparser(self, keyword: str, max_items: int) -> list:
        """Parse les RSS avec feedparser."""
        results = []
        kw_lower = keyword.lower()
        for source_name, feed_url in _RSS_FEEDS.items():
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:20]:
                    title = entry.get("title", "")
                    summary = entry.get("summary", "")
                    published = entry.get("published", "")
                    link = entry.get("link", "")

                    title_lower = title.lower()
                    if kw_lower not in title_lower and "crypto" not in title_lower and "bitcoin" not in title_lower and "btc" not in title_lower:
                        continue

                    sentiment = _score_sentiment(title + " " + summary)
                    results.append({
                        "title": title,
                        "source": source_name,
                        "published": published,
                        "link": link,
                        "sentiment": sentiment,
                    })
                    if len(results) >= max_items * 2:
                        break
            except Exception:
                logger.debug("Erreur RSS feedparser %s", source_name)

        return results[:max_items]

    def _fetch_news_requests(self, keyword: str, max_items: int) -> list:
        """
        Fallback RSS sans feedparser : parse XML minimal avec regex.
        """
        results = []
        kw_lower = keyword.lower()
        for source_name, feed_url in _RSS_FEEDS.items():
            try:
                resp = self._session.get(feed_url, timeout=_TIMEOUT)
                resp.raise_for_status()
                xml = resp.text

                # Extraction des <title> dans les <item>
                items = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)
                for item_xml in items[:15]:
                    title_match = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item_xml, re.DOTALL)
                    if not title_match:
                        continue
                    title = title_match.group(1).strip()
                    title_lower = title.lower()
                    if kw_lower not in title_lower and "crypto" not in title_lower and "bitcoin" not in title_lower:
                        continue
                    pub_match = re.search(r"<pubDate>(.*?)</pubDate>", item_xml)
                    link_match = re.search(r"<link>(.*?)</link>", item_xml, re.DOTALL)
                    sentiment = _score_sentiment(title)
                    results.append({
                        "title": title,
                        "source": source_name,
                        "published": pub_match.group(1).strip() if pub_match else "",
                        "link": link_match.group(1).strip() if link_match else "",
                        "sentiment": sentiment,
                    })
                    if len(results) >= max_items * 2:
                        break
            except Exception:
                logger.debug("Erreur RSS requests %s", source_name)

        return results[:max_items]

    def _fetch_reddit_sentiment(self, coin_keyword: str) -> dict:
        """Analyse le sentiment des posts Reddit r/CryptoCurrency liés à une crypto."""
        posts = self._fetch_reddit_posts("CryptoCurrency", limit=25)
        kw = coin_keyword.lower()

        relevant = [
            p for p in posts
            if kw in p.get("title", "").lower()
            or p.get("flair", "").lower() in (kw, "markets")
        ]

        if not relevant:
            return {"relevant_posts": 0, "sentiment": "neutral", "details": []}

        pos = sum(1 for p in relevant if p.get("sentiment") == "positive")
        neg = sum(1 for p in relevant if p.get("sentiment") == "negative")

        if pos > neg * 1.5:
            overall = "positive"
        elif neg > pos * 1.5:
            overall = "negative"
        else:
            overall = "neutral"

        return {
            "relevant_posts": len(relevant),
            "positive": pos,
            "negative": neg,
            "neutral": len(relevant) - pos - neg,
            "sentiment": overall,
            "top_posts": [
                {"title": p["title"][:80], "score": p.get("score", 0), "sentiment": p.get("sentiment")}
                for p in sorted(relevant, key=lambda x: x.get("score", 0), reverse=True)[:3]
            ],
        }

    def _fetch_reddit_posts(self, subreddit: str, limit: int = 25) -> list:
        """Récupère les posts hot d'un subreddit via l'API JSON publique."""
        cache_key = f"reddit:{subreddit}:{limit}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        url = f"https://www.reddit.com/r/{subreddit}/hot.json"
        params = {"limit": min(limit, 100)}
        try:
            resp = self._session.get(url, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            posts_data = data.get("data", {}).get("children", [])
            posts = []
            for child in posts_data:
                p = child.get("data", {})
                title = p.get("title", "")
                posts.append({
                    "title": title,
                    "score": p.get("score", 0),
                    "upvote_ratio": p.get("upvote_ratio", 0.5),
                    "num_comments": p.get("num_comments", 0),
                    "flair": p.get("link_flair_text", ""),
                    "sentiment": _score_sentiment(title),
                })
            self._cache.set(cache_key, posts)
            return posts
        except Exception:
            logger.debug("Erreur Reddit r/%s", subreddit)
            return []

    def clear_cache(self) -> None:
        """Vide le cache complet."""
        self._cache.clear()
        logger.info("Cache WebResearcher vidé")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _pair_to_binance_symbol(pair: str) -> Optional[str]:
    """Convertit 'BTC/USDT' → 'BTCUSDT'."""
    try:
        return pair.replace("/", "").upper()
    except Exception:
        return None


def _safe_get(obj: dict, *keys) -> Optional[float]:
    """Navigue dans un dict imbriqué et retourne None si la clé n'existe pas."""
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if current is None:
        return None
    try:
        return float(current)
    except (ValueError, TypeError):
        return None


def _score_sentiment(text: str) -> str:
    """
    Analyse de sentiment basique par mots clés.
    Retourne 'positive', 'negative' ou 'neutral'.
    """
    if not text:
        return "neutral"
    text_lower = text.lower()
    words = set(re.findall(r"\b\w+\b", text_lower))
    pos_hits = len(words & _POSITIVE_WORDS)
    neg_hits = len(words & _NEGATIVE_WORDS)
    if pos_hits > neg_hits:
        return "positive"
    if neg_hits > pos_hits:
        return "negative"
    return "neutral"
