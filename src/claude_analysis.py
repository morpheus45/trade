"""
Intelligence Claude intégrée — version améliorée.

Capacités :
1. SENTIMENT MARCHÉ (claude-haiku-4-5)
   - Fear & Greed Index
   - Actualités RSS (CoinDesk + Cointelegraph)
   - Analyse du régime de marché (bull run, bear market, consolidation, alt season)
   - Cache 5 minutes pour économiser les tokens

2. VALIDATION DE TRADE (claude-haiku-4-5)
   - Tous les indicateurs techniques (RSI, MACD, BB, ADX, ROC, VWAP...)
   - Score de signal
   - Contexte de sentiment
   - Décision + explication courte

3. BRIEFING JOURNALIER (claude-haiku-4-5)
   - Résumé marché + performance bot → Telegram

4. ANALYSE D'URGENCE (claude-opus-4-6)
   - Déclenché si circuit breaker ou drawdown important
   - Recommandations tactiques pour protéger le capital
"""
import json
import logging
import os
import time
import xml.etree.ElementTree as ET

import requests
from anthropic import Anthropic

import config

logger = logging.getLogger(__name__)

MODEL_FAST  = "claude-haiku-4-5"    # Opérations courantes (économique)
MODEL_DEEP  = "claude-opus-4-6"     # Analyses critiques (urgence, stratégie)

SENTIMENT_CACHE_TTL  = 300   # 5 min
FNG_CACHE_TTL        = 600   # 10 min


# ─────────────────────────────────────────────────────────────────────────────
# Données externes (gratuites)
# ─────────────────────────────────────────────────────────────────────────────

def _get_fear_greed_index() -> dict:
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        r.raise_for_status()
        data = r.json()["data"][0]
        return {"value": data["value"], "classification": data["value_classification"]}
    except Exception as e:
        logger.debug(f"Fear & Greed indisponible: {e}")
        return {"value": "50", "classification": "Neutral"}


def _fetch_rss_headlines(feed_url: str, limit: int = 8) -> list[str]:
    try:
        r = requests.get(feed_url, timeout=6, headers={"User-Agent": "TradingBot/2.0"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
        titles = []
        for item in root.findall(".//item")[:limit]:
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                titles.append(title_el.text.strip())
        return titles
    except Exception as e:
        logger.debug(f"RSS indisponible ({feed_url}): {e}")
        return []


def _get_crypto_headlines(pair: str) -> list[str]:
    coin = pair.split("/")[0].upper()
    coindesk      = _fetch_rss_headlines("https://www.coindesk.com/arc/outboundfeeds/rss/")
    cointelegraph = _fetch_rss_headlines("https://cointelegraph.com/rss")
    all_headlines = coindesk + cointelegraph

    relevant = [
        h for h in all_headlines
        if coin in h.upper() or any(
            k in h.lower() for k in ["crypto", "bitcoin", "market", "bull", "bear", "fed", "macro"]
        )
    ]
    return relevant[:12] if relevant else all_headlines[:8]


def _get_global_market_data() -> dict:
    """
    Données macro crypto depuis CoinGecko (gratuit, sans clé).
    Retourne : market_cap total, BTC dominance, 24h change global.
    """
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=6,
            headers={"User-Agent": "TradingBot/2.0"}
        )
        r.raise_for_status()
        data = r.json().get("data", {})
        return {
            "total_market_cap_usd":  data.get("total_market_cap", {}).get("usd", 0),
            "btc_dominance":         round(data.get("market_cap_percentage", {}).get("btc", 0), 1),
            "market_cap_change_24h": round(data.get("market_cap_change_percentage_24h_usd", 0), 2),
        }
    except Exception as e:
        logger.debug(f"CoinGecko global indisponible: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Classe principale
# ─────────────────────────────────────────────────────────────────────────────

class ClaudeAnalyst:
    """Couche d'intelligence Claude pour le bot de trading."""

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key or api_key.startswith("REMPLACE"):
            logger.warning("ANTHROPIC_API_KEY absent — Claude désactivé")
            self._enabled = False
            return

        self._client  = Anthropic(api_key=api_key)
        self._enabled = True
        self._sentiment_cache: dict[str, tuple[float, dict]] = {}
        self._global_cache:    tuple[float, dict] = (0.0, {})
        logger.info(f"Claude activé — fast: {MODEL_FAST} | deep: {MODEL_DEEP}")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _call(self, prompt: str, model: str = None, max_tokens: int = 512) -> str:
        """Appel Claude avec gestion d'erreur centralisée."""
        model = model or MODEL_FAST
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    def _parse_json(self, text: str) -> dict:
        """Extrait le JSON d'une réponse Claude même si entouré de texte."""
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError(f"Pas de JSON trouvé dans: {text[:100]}")
        return json.loads(text[start:end])

    # ─── 1. Sentiment marché ──────────────────────────────────────────────────

    def get_market_sentiment(self, pair: str) -> dict:
        """
        Analyse le sentiment du marché pour une paire.

        Retourne :
          sentiment, confidence, fng_value, fng_label, summary, regime
        """
        if not self._enabled:
            return {
                "sentiment": "neutral", "confidence": 0.5,
                "fng_value": 50, "fng_label": "Neutral",
                "summary": "Claude désactivé", "regime": "unknown",
            }

        now = time.time()
        if pair in self._sentiment_cache:
            ts, cached = self._sentiment_cache[pair]
            if now - ts < SENTIMENT_CACHE_TTL:
                return cached

        fng       = _get_fear_greed_index()
        headlines = _get_crypto_headlines(pair)
        coin      = pair.split("/")[0]

        # Données macro globales (cache 10 min)
        if now - self._global_cache[0] > FNG_CACHE_TTL:
            self._global_cache = (now, _get_global_market_data())
        global_data = self._global_cache[1]

        global_text = ""
        if global_data:
            mc  = global_data.get("total_market_cap_usd", 0)
        if global_data:
            mc_b  = global_data.get("total_market_cap_usd", 0) / 1e9
            btc_d = global_data.get("btc_dominance", 0)
            mc_ch = global_data.get("market_cap_change_24h", 0)
            global_text = (
                f"\nMarché global: Cap totale {mc_b:.0f}B$ | "
                f"BTC dominance {btc_d:.1f}% | "
                f"Variation 24h: {mc_ch:+.2f}%"
            )

        headlines_text = "\n".join(f"- {h}" for h in headlines) or "Aucun titre"

        prompt = f"""Tu es un analyste crypto professionnel. Analyse le sentiment du marché pour {coin}.

Fear & Greed Index: {fng['value']}/100 ({fng['classification']}){global_text}

Titres récents:
{headlines_text}

Identifie aussi le régime de marché :
- "bull_run" : hausse soutenue, euphorie
- "bear_market" : baisse structurelle, capitulation
- "consolidation" : range horizontal, incertitude
- "alt_season" : BTC dominance baisse, altcoins surperforment
- "recovery" : rebond après baisse

Réponds UNIQUEMENT en JSON valide :
{{
  "sentiment": "bullish" | "bearish" | "neutral",
  "confidence": 0.0 à 1.0,
  "regime": "bull_run" | "bear_market" | "consolidation" | "alt_season" | "recovery",
  "summary": "une phrase max"
}}"""

        try:
            text   = self._call(prompt)
            data   = self._parse_json(text)
            result = {
                "sentiment":  data.get("sentiment", "neutral"),
                "confidence": float(data.get("confidence", 0.5)),
                "fng_value":  int(fng["value"]),
                "fng_label":  fng["classification"],
                "regime":     data.get("regime", "consolidation"),
                "summary":    data.get("summary", ""),
            }
        except Exception as e:
            logger.warning(f"Erreur sentiment Claude {pair}: {e}")
            result = {
                "sentiment": "neutral", "confidence": 0.5,
                "fng_value": int(fng["value"]), "fng_label": fng["classification"],
                "regime": "consolidation", "summary": "Analyse indisponible",
            }

        self._sentiment_cache[pair] = (now, result)
        logger.info(
            f"[Claude Sentiment] {pair}: {result['sentiment']} "
            f"(conf={result['confidence']:.2f}) | régime: {result['regime']} | "
            f"F&G={result['fng_value']}"
        )
        return result

    # ─── 2. Validation de trade ───────────────────────────────────────────────

    def validate_trade(
        self,
        pair: str,
        signal: str,
        indicators: dict,
        sentiment: dict | None = None,
    ) -> tuple[bool, str]:
        """
        Valide un setup de trading.
        Retourne (should_trade, reasoning).
        """
        if not self._enabled:
            return True, "Claude désactivé — signal accepté"

        ind = indicators
        trend_dir = "HAUSSIÈRE" if ind.get("dist_ema_trend", 0) > 0 else "BAISSIÈRE"

        sentiment_line = ""
        if sentiment:
            sentiment_line = (
                f"\nSentiment: {sentiment['sentiment'].upper()} "
                f"(conf={sentiment['confidence']:.0%}) | "
                f"Régime: {sentiment.get('regime', '?')} | "
                f"F&G: {sentiment['fng_value']}/100"
                f"\nActualités: {sentiment['summary']}"
            )

        adx_val   = ind.get("adx", 20)
        adx_label = "FORT" if adx_val >= 25 else "MODÉRÉ" if adx_val >= 15 else "FAIBLE"

        prompt = f"""Tu es un trader quantitatif. Évalue ce setup {signal} sur {pair}.

Tendance (EMA200): {trend_dir} ({ind.get('dist_ema_trend', 0)*100:.1f}%)
Score signal: {ind.get('signal_score', 0)}/5

Indicateurs:
- RSI(14): {ind.get('rsi', 0):.1f}
- MACD hist: {ind.get('macd_hist', 0):.6f}
- Bollinger: {ind.get('bb_position', 0)*100:.0f}%
- ADX: {adx_val:.1f} ({adx_label})
- ROC(10): {ind.get('roc', 0):.2f}%
- VWAP deviation: {ind.get('vwap_dev', 0)*100:.2f}%
- Volume ratio: {ind.get('volume_ratio', 1):.2f}x
- EMA fast dist: {ind.get('dist_ema_fast', 0)*100:.2f}%
- ATR%: {ind.get('atr_pct', 0)*100:.2f}%
- Retour 1 bougie: {ind.get('return_1', 0)*100:.2f}%
- Retour 3 bougies: {ind.get('return_3', 0)*100:.2f}%{sentiment_line}

Critères validés par le système: tendance UP ✓, ≥2 signaux ✓, volume ✓, ML ✓

Le setup vaut-il une entrée LONG ?

Réponds UNIQUEMENT en JSON :
{{
  "trade": true | false,
  "confidence": 0.0 à 1.0,
  "reason": "1-2 phrases maximum"
}}"""

        try:
            text  = self._call(prompt)
            data  = self._parse_json(text)

            should_trade = bool(data.get("trade", True))
            confidence   = float(data.get("confidence", 0.5))
            reason       = data.get("reason", "")

            logger.info(
                f"[Claude Validation] {pair}: "
                f"{'✅' if should_trade else '❌'} "
                f"conf={confidence:.2f} — {reason}"
            )
            return should_trade, reason

        except Exception as e:
            logger.warning(f"Erreur validation Claude {pair}: {e}")
            return True, f"Validation indisponible ({e}), signal accepté"

    # ─── 3. Briefing journalier ───────────────────────────────────────────────

    def daily_market_briefing(self, pairs: list[str], portfolio_stats: dict) -> str:
        if not self._enabled:
            return "Briefing indisponible"

        fng   = _get_fear_greed_index()
        gdata = _get_global_market_data()

        global_text = ""
        if gdata:
            mc_b  = gdata.get("total_market_cap_usd", 0) / 1e9
            btc_d = gdata.get("btc_dominance", 0)
            mc_ch = gdata.get("market_cap_change_24h", 0)
            global_text = f"\nMarchés: Cap {mc_b:.0f}B$ | BTC dom {btc_d:.1f}% | 24h {mc_ch:+.2f}%"

        stats_text = (
            f"Trades: {portfolio_stats.get('trades', 0)} | "
            f"Win rate: {portfolio_stats.get('win_rate_pct', 0):.1f}% | "
            f"PnL: {portfolio_stats.get('total_pnl', 0):+.2f} USDT | "
            f"ROI: {portfolio_stats.get('roi_pct', 0):+.2f}%"
        )

        prompt = f"""Bot de trading crypto actif sur {', '.join(pairs)}.
Fear & Greed: {fng['value']}/100 ({fng['classification']}){global_text}
Performance: {stats_text}

Briefing quotidien en 3-4 lignes MAX, en français, ton neutre et factuel.
Focus: opportunités, risques du jour, contexte macro pertinent."""

        try:
            return self._call(prompt, max_tokens=300)
        except Exception as e:
            logger.warning(f"Erreur briefing: {e}")
            return f"F&G: {fng['value']}/100 ({fng['classification']})"

    # ─── 4. Analyse d'urgence (Opus) ─────────────────────────────────────────

    def emergency_analysis(self, reason: str, portfolio_stats: dict, open_positions: list[str]) -> str:
        """
        Analyse approfondie déclenchée lors d'un circuit breaker.
        Utilise Opus pour une réflexion plus profonde.
        """
        if not self._enabled:
            return "Analyse d'urgence indisponible"

        fng = _get_fear_greed_index()

        prompt = f"""SITUATION D'URGENCE — Bot de trading.

Déclencheur: {reason}
Fear & Greed: {fng['value']}/100 ({fng['classification']})
Positions ouvertes: {', '.join(open_positions) if open_positions else 'Aucune'}
Stats: {portfolio_stats}

En tant qu'expert en gestion du risque crypto :
1. Analyse la situation (2 phrases)
2. Recommandation immédiate : conserver les positions ou fermer tout ?
3. Stratégie pour les prochaines 24h

Réponse concise en français, max 6 lignes."""

        try:
            return self._call(prompt, model=MODEL_DEEP, max_tokens=400)
        except Exception as e:
            logger.warning(f"Erreur analyse urgence: {e}")
            return f"Analyse urgence indisponible. Raison circuit breaker: {reason}"
