"""
claude_analysis.py  —  RÉÉCRITURE COMPLÈTE
Intelligence Claude intégrée — version 3 (AutonomousBrain + mémoire + web).

Cette version orchestre AutonomousBrain, MarketMemory et WebResearcher pour
des décisions enrichies tout en conservant l'interface publique existante
(compatible avec bot_trading.py sans modification).

Interface publique conservée :
  ClaudeAnalyst.enabled                            → bool
  ClaudeAnalyst.get_market_sentiment(pair)         → dict
  ClaudeAnalyst.validate_trade(pair, signal, ind, sentiment) → (bool, str)
  ClaudeAnalyst.daily_market_briefing(pairs, stats) → str
  ClaudeAnalyst.emergency_analysis(reason, stats, positions) → str  (inchangé)

Modèles utilisés :
  claude-sonnet-4-5  → décisions trading (via AutonomousBrain)
  claude-haiku-4-5   → sentiment simple (économie de tokens)
  claude-opus-4-6    → urgences critiques (circuit breaker)
"""

import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ─── Modèles ──────────────────────────────────────────────────────────────────
MODEL_FAST  = "claude-haiku-4-5"    # Sentiment simple, rapide et économique
MODEL_DEEP  = "claude-sonnet-4-5"   # Validation de trade (via AutonomousBrain)
MODEL_CRIT  = "claude-opus-4-6"     # Analyses d'urgence critiques

# ─── TTL caches ───────────────────────────────────────────────────────────────
SENTIMENT_CACHE_TTL = 300   # 5 min
FNG_CACHE_TTL       = 600   # 10 min


# ─────────────────────────────────────────────────────────────────────────────
# Imports optionnels avec dégradation gracieuse
# ─────────────────────────────────────────────────────────────────────────────

try:
    from anthropic import Anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    logger.warning("claude_analysis : package 'anthropic' introuvable")
    _ANTHROPIC_AVAILABLE = False

try:
    from groq import Groq as _GroqClient
    _GROQ_AVAILABLE = True
except ImportError:
    _GROQ_AVAILABLE = False

try:
    from autonomous_brain import AutonomousBrain
    _BRAIN_AVAILABLE = True
except ImportError:
    logger.warning("claude_analysis : autonomous_brain.py introuvable")
    _BRAIN_AVAILABLE = False

try:
    from market_memory import MarketMemory
    _MEMORY_AVAILABLE = True
except ImportError:
    logger.warning("claude_analysis : market_memory.py introuvable")
    _MEMORY_AVAILABLE = False

try:
    from web_researcher import WebResearcher
    _RESEARCHER_AVAILABLE = True
except ImportError:
    logger.warning("claude_analysis : web_researcher.py introuvable")
    _RESEARCHER_AVAILABLE = False

try:
    import config as _config
    _API_KEY      = getattr(_config, "ANTHROPIC_API_KEY", os.getenv("ANTHROPIC_API_KEY", ""))
    _GROQ_API_KEY = getattr(_config, "GROQ_API_KEY", os.getenv("GROQ_API_KEY", ""))
except Exception:
    _API_KEY      = os.getenv("ANTHROPIC_API_KEY", "")
    _GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers données externes (conservés pour compatibilité + fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _get_fear_greed_index() -> dict:
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        r.raise_for_status()
        data = r.json()["data"][0]
        return {"value": data["value"], "classification": data["value_classification"]}
    except Exception as e:
        logger.debug("Fear & Greed indisponible: %s", e)
        return {"value": "50", "classification": "Neutral"}


def _fetch_rss_headlines(feed_url: str, limit: int = 8) -> list:
    try:
        r = requests.get(feed_url, timeout=6, headers={"User-Agent": "TradingBot/3.0"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
        titles = []
        for item in root.findall(".//item")[:limit]:
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                titles.append(title_el.text.strip())
        return titles
    except Exception as e:
        logger.debug("RSS indisponible (%s): %s", feed_url, e)
        return []


def _get_crypto_headlines(pair: str) -> list:
    coin = pair.split("/")[0].upper()
    coindesk      = _fetch_rss_headlines("https://www.coindesk.com/arc/outboundfeeds/rss/")
    cointelegraph = _fetch_rss_headlines("https://cointelegraph.com/rss")
    all_headlines = coindesk + cointelegraph

    relevant = [
        h for h in all_headlines
        if coin in h.upper() or any(
            k in h.lower()
            for k in ["crypto", "bitcoin", "market", "bull", "bear", "fed", "macro"]
        )
    ]
    return relevant[:12] if relevant else all_headlines[:8]


def _get_global_market_data() -> dict:
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=6,
            headers={"User-Agent": "TradingBot/3.0"},
        )
        r.raise_for_status()
        data = r.json().get("data", {})
        return {
            "total_market_cap_usd":  data.get("total_market_cap", {}).get("usd", 0),
            "btc_dominance":         round(data.get("market_cap_percentage", {}).get("btc", 0), 1),
            "market_cap_change_24h": round(data.get("market_cap_change_percentage_24h_usd", 0), 2),
        }
    except Exception as e:
        logger.debug("CoinGecko global indisponible: %s", e)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Classe principale
# ─────────────────────────────────────────────────────────────────────────────

class ClaudeAnalyst:
    """
    Couche d'intelligence Claude pour le bot de trading.

    Orchestre AutonomousBrain (décisions profondes), MarketMemory et
    WebResearcher tout en conservant l'interface publique existante.
    """

    def __init__(self):
        self._client    = None
        self._use_groq  = False

        # Groq en priorité (gratuit)
        if _GROQ_AVAILABLE and _GROQ_API_KEY:
            try:
                self._client   = _GroqClient(api_key=_GROQ_API_KEY)
                self._use_groq = True
                logger.info("ClaudeAnalyst : Groq (llama-3.3-70b) actif — gratuit")
            except Exception as e:
                logger.warning("ClaudeAnalyst : Groq indisponible (%s)", e)

        # Fallback Anthropic
        if self._client is None and _ANTHROPIC_AVAILABLE and _API_KEY and not _API_KEY.startswith("REMPLACE"):
            try:
                self._client   = Anthropic(api_key=_API_KEY)
                self._use_groq = False
            except Exception as e:
                logger.warning("ClaudeAnalyst : Anthropic indisponible (%s)", e)

        if self._client is None:
            logger.warning("ClaudeAnalyst : aucun client IA — desactive")
            self._enabled = False
            self.brain = None
            self.memory = None
            self.researcher = None
            return

        self._enabled = True

        # AutonomousBrain — cerveau principal
        if _BRAIN_AVAILABLE:
            try:
                self.brain = AutonomousBrain()
                logger.info("ClaudeAnalyst : AutonomousBrain chargé")
            except Exception as e:
                logger.warning("ClaudeAnalyst : AutonomousBrain indisponible (%s)", e)
                self.brain = None
        else:
            self.brain = None

        # MarketMemory — pour contexte enrichi du sentiment
        if _MEMORY_AVAILABLE:
            try:
                self.memory = MarketMemory()
                logger.info("ClaudeAnalyst : MarketMemory chargée")
            except Exception as e:
                logger.warning("ClaudeAnalyst : MarketMemory indisponible (%s)", e)
                self.memory = None
        else:
            self.memory = None

        # WebResearcher — pour données temps réel
        if _RESEARCHER_AVAILABLE:
            try:
                self.researcher = WebResearcher()
                logger.info("ClaudeAnalyst : WebResearcher chargé")
            except Exception as e:
                logger.warning("ClaudeAnalyst : WebResearcher indisponible (%s)", e)
                self.researcher = None
        else:
            self.researcher = None

        # Caches
        self._sentiment_cache: dict = {}   # pair → (timestamp, result_dict)
        self._global_cache:    tuple = (0.0, {})

        logger.info(
            "ClaudeAnalyst v3 activé — fast: %s | deep: %s | crit: %s",
            MODEL_FAST, MODEL_DEEP, MODEL_CRIT,
        )

    # ─── Propriété enabled ───────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ─── Appel Claude direct (pour fallback / sentiment / urgence) ────────────

    def _call(self, prompt: str, model: str = None, max_tokens: int = 512) -> str:
        """Appel IA unifié : Groq ou Anthropic."""
        if self._client is None:
            raise RuntimeError("Client IA non initialise")
        if self._use_groq:
            resp = self._client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content.strip()
        else:
            model = model or MODEL_FAST
            response = self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text.strip()

    def _parse_json(self, text: str) -> dict:
        """Extrait le JSON d'une réponse Claude même si entouré de texte."""
        import re
        # Bloc ```json
        match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        # JSON brut
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError(f"Pas de JSON trouvé dans: {text[:100]}")
        return json.loads(text[start:end])

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Sentiment marché (enrichi avec WebResearcher + MarketMemory)
    # ─────────────────────────────────────────────────────────────────────────

    def get_market_sentiment(self, pair: str) -> dict:
        """
        Analyse le sentiment du marché pour une paire.
        Utilise WebResearcher (données riches) + MarketMemory (contexte historique).
        Fallback sur RSS + Fear & Greed si les modules sont indisponibles.

        Retourne :
          sentiment, confidence, fng_value, fng_label, summary, regime
        """
        if not self._enabled:
            return {
                "sentiment":  "neutral",
                "confidence": 0.5,
                "fng_value":  50,
                "fng_label":  "Neutral",
                "summary":    "Claude désactivé",
                "regime":     "unknown",
            }

        now = time.time()
        # Cache 5 min
        if pair in self._sentiment_cache:
            ts, cached = self._sentiment_cache[pair]
            if now - ts < SENTIMENT_CACHE_TTL:
                return cached

        coin = pair.split("/")[0]

        # ── Source principale : WebResearcher ─────────────────────────────────
        fng_value = 50
        fng_label = "Neutral"
        headlines_text = ""
        global_text = ""

        if self.researcher:
            try:
                global_market = self.researcher.get_global_market()
                fg = global_market.get("fear_greed", {})
                if fg:
                    fng_value = int(fg.get("value", 50))
                    fng_label = fg.get("classification", "Neutral")

                btcd    = global_market.get("btc_dominance_pct")
                mc_ch   = global_market.get("market_cap_change_pct_24h")
                mc_usd  = global_market.get("total_market_cap_usd")

                if btcd:
                    mc_b = mc_usd / 1e9 if mc_usd else 0
                    global_text = (
                        f"\nMarché global : Cap {mc_b:.0f}B$ | "
                        f"BTC dom {btcd:.1f}% | "
                        f"24h {mc_ch:+.2f}%"
                        if mc_ch is not None else
                        f"\nMarché global : BTC dom {btcd:.1f}%"
                    )

                # News via WebResearcher
                pair_data = self.researcher.research_pair(pair)
                news = pair_data.get("news_headlines", [])
                if news:
                    headlines_text = "\n".join(
                        f"- [{n.get('sentiment','?').upper()}] {n.get('title','')[:80]}"
                        for n in news[:8]
                    )
                    # Reddit
                    reddit = pair_data.get("reddit_sentiment", {})
                    if reddit and reddit.get("relevant_posts", 0) > 0:
                        headlines_text += (
                            f"\nReddit ({reddit.get('relevant_posts', 0)} posts) : "
                            f"{reddit.get('positive',0)}+ / {reddit.get('negative',0)}-"
                        )
            except Exception as e:
                logger.debug("get_market_sentiment WebResearcher: %s", e)

        # ── Fallback : RSS direct ─────────────────────────────────────────────
        if not headlines_text:
            fng_data = _get_fear_greed_index()
            fng_value = int(fng_data["value"])
            fng_label = fng_data["classification"]

            headlines = _get_crypto_headlines(pair)
            headlines_text = "\n".join(f"- {h}" for h in headlines) or "Aucun titre"

            # Données macro fallback
            if now - self._global_cache[0] > FNG_CACHE_TTL:
                self._global_cache = (now, _get_global_market_data())
            gdata = self._global_cache[1]
            if gdata:
                mc_b  = gdata.get("total_market_cap_usd", 0) / 1e9
                btc_d = gdata.get("btc_dominance", 0)
                mc_ch = gdata.get("market_cap_change_24h", 0)
                global_text = (
                    f"\nMarché global : Cap {mc_b:.0f}B$ | "
                    f"BTC dominance {btc_d:.1f}% | "
                    f"Variation 24h : {mc_ch:+.2f}%"
                )

        # ── Contexte mémoire ──────────────────────────────────────────────────
        memory_hint = ""
        if self.memory:
            try:
                recent_events = self.memory.recall_recent_events(hours=72)
                if recent_events:
                    memory_hint = "\nÉvénements récents mémorisés :\n"
                    for ev in recent_events[:3]:
                        sign = "+" if ev.get("impact_pct", 0) >= 0 else ""
                        memory_hint += (
                            f"  [{ev.get('date','?')}] {ev.get('pair','?')} "
                            f"{sign}{ev.get('impact_pct',0):.1f}% — "
                            f"{ev.get('event','')[:60]}\n"
                        )
            except Exception as e:
                logger.debug("get_market_sentiment mémoire: %s", e)

        # ── Prompt Claude Haiku (économique pour sentiment) ───────────────────
        prompt = f"""Tu es un analyste crypto professionnel. Analyse le sentiment du marché pour {coin}.

Fear & Greed Index : {fng_value}/100 ({fng_label}){global_text}{memory_hint}

Actualités récentes :
{headlines_text}

Identifie le régime de marché :
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
            text   = self._call(prompt, model=MODEL_FAST)
            data   = self._parse_json(text)
            result = {
                "sentiment":  data.get("sentiment", "neutral"),
                "confidence": float(data.get("confidence", 0.5)),
                "fng_value":  fng_value,
                "fng_label":  fng_label,
                "regime":     data.get("regime", "consolidation"),
                "summary":    data.get("summary", ""),
            }
        except Exception as e:
            logger.warning("Erreur sentiment Claude %s: %s", pair, e)
            result = {
                "sentiment":  "neutral",
                "confidence": 0.5,
                "fng_value":  fng_value,
                "fng_label":  fng_label,
                "regime":     "consolidation",
                "summary":    "Analyse indisponible",
            }

        self._sentiment_cache[pair] = (now, result)
        logger.info(
            "[ClaudeAnalyst Sentiment] %s: %s (conf=%.2f) | régime: %s | F&G=%d",
            pair, result["sentiment"], result["confidence"],
            result["regime"], result["fng_value"],
        )
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Validation de trade (via AutonomousBrain si disponible)
    # ─────────────────────────────────────────────────────────────────────────

    def validate_trade(
        self,
        pair: str,
        signal: str,
        indicators: dict,
        sentiment: Optional[dict] = None,
    ) -> tuple:
        """
        Valide un setup de trading via AutonomousBrain (décision profonde).
        Fallback sur validation Claude directe si le brain est indisponible.

        Args:
            pair       : ex. "BTC/USDT"
            signal     : ex. "BUY"
            indicators : dict des indicateurs techniques
            sentiment  : dict optionnel retourné par get_market_sentiment()

        Returns:
            (should_trade: bool, reasoning: str)
        """
        if not self._enabled:
            return True, "Claude désactivé — signal accepté"

        # ── Chemin principal : AutonomousBrain ────────────────────────────────
        if self.brain:
            try:
                # Injecter le sentiment dans les indicateurs si fourni
                enriched_indicators = dict(indicators)
                if sentiment:
                    enriched_indicators["_sentiment_regime"]  = sentiment.get("regime", "unknown")
                    enriched_indicators["_sentiment_value"]   = sentiment.get("sentiment", "neutral")
                    enriched_indicators["_fng_value"]         = sentiment.get("fng_value", 50)
                    enriched_indicators["_sentiment_conf"]    = sentiment.get("confidence", 0.5)

                # Signal score depuis les indicateurs si disponible
                signal_score = float(indicators.get("signal_score", 2.5))

                brain_decision = self.brain.decide(
                    pair=pair,
                    technical_signal=signal,
                    signal_score=signal_score,
                    indicators=enriched_indicators,
                )

                decision    = brain_decision.get("decision", "WAIT")
                confidence  = brain_decision.get("confidence", 0.5)
                reasoning   = brain_decision.get("reasoning", "")
                risk_adj    = brain_decision.get("risk_adjustment", 1.0)

                should_trade = (decision == "ENTER")

                full_reason = (
                    f"[{decision}] conf={confidence:.2f} risk×{risk_adj:.1f} — {reasoning}"
                )

                logger.info(
                    "[ClaudeAnalyst Validate] %s: %s conf=%.2f — %s",
                    pair,
                    "TRADE" if should_trade else "SKIP",
                    confidence,
                    reasoning[:80],
                )
                return should_trade, full_reason

            except Exception as e:
                logger.warning(
                    "validate_trade AutonomousBrain error (%s) — fallback direct", e
                )

        # ── Fallback : validation Claude directe (haiku) ──────────────────────
        return self._validate_trade_direct(pair, signal, indicators, sentiment)

    def _validate_trade_direct(
        self,
        pair: str,
        signal: str,
        indicators: dict,
        sentiment: Optional[dict],
    ) -> tuple:
        """
        Validation directe via Claude Haiku (fallback sans AutonomousBrain).
        Conserve la logique de l'ancienne version pour compatibilité.
        """
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

Le setup vaut-il une entrée LONG ?

Réponds UNIQUEMENT en JSON :
{{
  "trade": true | false,
  "confidence": 0.0 à 1.0,
  "reason": "1-2 phrases maximum"
}}"""

        try:
            text = self._call(prompt, model=MODEL_FAST)
            data = self._parse_json(text)

            should_trade = bool(data.get("trade", False))
            confidence   = float(data.get("confidence", 0.5))
            reason       = data.get("reason", "")

            logger.info(
                "[ClaudeAnalyst Fallback Validate] %s: %s conf=%.2f — %s",
                pair,
                "TRADE" if should_trade else "SKIP",
                confidence,
                reason,
            )
            return should_trade, reason

        except Exception as e:
            logger.warning("Erreur validation directe Claude %s: %s", pair, e)
            return True, f"Validation indisponible ({e}), signal accepté"

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Briefing journalier (via AutonomousBrain.reflect_daily)
    # ─────────────────────────────────────────────────────────────────────────

    def daily_market_briefing(self, pairs: list, portfolio_stats: dict) -> str:
        """
        Génère le briefing journalier complet pour Telegram.
        Utilise AutonomousBrain.reflect_daily() si disponible,
        sinon fallback sur la version directe.

        Args:
            pairs          : liste de paires tradées
            portfolio_stats: dict avec trades, win_rate_pct, total_pnl, roi_pct...

        Returns:
            str : texte du briefing journalier
        """
        if not self._enabled:
            return "Briefing indisponible"

        # ── Chemin principal : AutonomousBrain.reflect_daily ──────────────────
        if self.brain:
            try:
                return self.brain.reflect_daily(
                    portfolio_stats=portfolio_stats,
                    pairs=pairs,
                )
            except Exception as e:
                logger.warning("daily_market_briefing brain error (%s) — fallback direct", e)

        # ── Fallback : briefing simple ────────────────────────────────────────
        return self._daily_briefing_direct(pairs, portfolio_stats)

    def _daily_briefing_direct(self, pairs: list, portfolio_stats: dict) -> str:
        """Briefing quotidien direct Claude Haiku (fallback sans brain)."""
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
            return self._call(prompt, model=MODEL_FAST, max_tokens=300)
        except Exception as e:
            logger.warning("Erreur briefing direct: %s", e)
            return f"F&G: {fng['value']}/100 ({fng['classification']})"

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Analyse d'urgence (Opus — circuit breaker)
    # ─────────────────────────────────────────────────────────────────────────

    def emergency_analysis(
        self,
        reason: str,
        portfolio_stats: dict,
        open_positions: list,
    ) -> str:
        """
        Analyse approfondie déclenchée lors d'un circuit breaker.
        Utilise claude-opus-4-6 pour une réflexion critique.
        Conserve l'interface et le comportement de l'ancienne version.

        Args:
            reason          : raison du déclenchement
            portfolio_stats : état du portefeuille
            open_positions  : liste des paires en position

        Returns:
            str : recommandations en texte
        """
        if not self._enabled:
            return "Analyse d'urgence indisponible"

        fng = _get_fear_greed_index()

        # Enrichir avec contexte web si disponible
        market_context = ""
        if self.researcher:
            try:
                gm = self.researcher.get_global_market()
                fg = gm.get("fear_greed", {})
                btcd = gm.get("btc_dominance_pct")
                mc_ch = gm.get("market_cap_change_pct_24h")
                if btcd:
                    market_context = (
                        f"\nBTC Dominance : {btcd:.1f}% | "
                        f"Market cap 24h : {mc_ch:+.2f}%"
                        if mc_ch is not None else
                        f"\nBTC Dominance : {btcd:.1f}%"
                    )
            except Exception:
                pass

        prompt = f"""SITUATION D'URGENCE — Bot de trading.

Déclencheur: {reason}
Fear & Greed: {fng['value']}/100 ({fng['classification']}){market_context}
Positions ouvertes: {', '.join(open_positions) if open_positions else 'Aucune'}
Stats: {portfolio_stats}

En tant qu'expert en gestion du risque crypto :
1. Analyse la situation (2 phrases)
2. Recommandation immédiate : conserver les positions ou fermer tout ?
3. Stratégie pour les prochaines 24h

Réponse concise en français, max 6 lignes."""

        try:
            return self._call(prompt, model=MODEL_CRIT, max_tokens=400)
        except Exception as e:
            logger.warning("Erreur analyse urgence: %s", e)
            return f"Analyse urgence indisponible. Raison circuit breaker: {reason}"

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Analyse régime de marché (délégation au brain)
    # ─────────────────────────────────────────────────────────────────────────

    def get_market_regime(self, pairs: list, df_dict: Optional[dict] = None) -> dict:
        """
        Analyse le régime de marché global via AutonomousBrain.

        Returns:
            dict avec regime, confidence, fng_value, btc_dominance, reasoning
        """
        default = {
            "regime": "uncertain", "confidence": 0.5,
            "btc_trend": "unknown", "fng_value": 50,
            "btc_dominance": 50.0, "reasoning": "Indisponible",
        }

        if not self._enabled:
            return default

        if self.brain:
            try:
                return self.brain.get_market_regime(pairs=pairs, df_dict=df_dict)
            except Exception as e:
                logger.warning("get_market_regime brain error: %s", e)

        return default

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers publics
    # ─────────────────────────────────────────────────────────────────────────

    def invalidate_cache(self, pair: Optional[str] = None) -> None:
        """Vide le cache sentiment pour une paire ou tout."""
        if pair:
            self._sentiment_cache.pop(pair, None)
            if self.brain:
                self.brain.invalidate_cache(pair)
        else:
            self._sentiment_cache.clear()
            if self.brain:
                self.brain.invalidate_cache()
        logger.debug("Cache ClaudeAnalyst invalidé : %s", pair or "tout")
