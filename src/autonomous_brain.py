"""
autonomous_brain.py
Cerveau IA central du bot de trading crypto.

C'est lui qui prend TOUTES les décisions de manière autonome en combinant :
  - Claude API (claude-sonnet-4-5) pour le raisonnement profond (Chain of Thought)
  - MarketMemory (SQLite) : situations similaires passées, sagesse, statistiques
  - WebResearcher : données temps réel (prix, news, funding rates, sentiment)
  - Indicateurs techniques calculés par le bot

Décisions retournées :
  ENTER  → ouvrir une position
  WAIT   → signal insuffisant, attendre confirmation
  SKIP   → setup mauvais, ignorer complètement
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import anthropic

try:
    import config
    _API_KEY = config.ANTHROPIC_API_KEY
except AttributeError:
    import os
    _API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

try:
    from market_memory import MarketMemory
    _MEMORY_AVAILABLE = True
except ImportError:
    _MEMORY_AVAILABLE = False

try:
    from web_researcher import WebResearcher
    _RESEARCHER_AVAILABLE = True
except ImportError:
    _RESEARCHER_AVAILABLE = False

logger = logging.getLogger(__name__)

MODEL_DECISION  = "claude-sonnet-4-5"   # Décisions trading (raisonnement profond)
MODEL_REFLECT   = "claude-sonnet-4-5"   # Réflexion journalière
MODEL_REGIME    = "claude-haiku-4-5"    # Analyse régime marché (rapide)

# Décision par défaut retournée en cas d'erreur Claude
_DEFAULT_DECISION = {
    "decision": "WAIT",
    "confidence": 0.5,
    "reasoning": "Décision par défaut — analyse indisponible",
    "risk_adjustment": 1.0,
    "key_factors": ["analyse_indisponible"],
    "market_regime": "uncertain",
    "suggested_sl_adjustment": 0.0,
    "time_horizon": "intraday",
    "alert_telegram": "Analyse IA indisponible — position en attente",
}


class AutonomousBrain:
    """
    Cerveau décisionnel autonome du bot de trading.
    Orchestre mémoire, recherche web et raisonnement Claude.
    """

    def __init__(self):
        # Mémoire des marchés
        if _MEMORY_AVAILABLE:
            try:
                self.memory = MarketMemory()
                logger.info("AutonomousBrain : MarketMemory chargée")
            except Exception as e:
                logger.warning("AutonomousBrain : MarketMemory indisponible (%s)", e)
                self.memory = None
        else:
            logger.warning("AutonomousBrain : market_memory.py introuvable")
            self.memory = None

        # Recherche web
        if _RESEARCHER_AVAILABLE:
            try:
                self.researcher = WebResearcher()
                logger.info("AutonomousBrain : WebResearcher chargé")
            except Exception as e:
                logger.warning("AutonomousBrain : WebResearcher indisponible (%s)", e)
                self.researcher = None
        else:
            logger.warning("AutonomousBrain : web_researcher.py introuvable")
            self.researcher = None

        # Client Anthropic
        if not _API_KEY or _API_KEY.startswith("REMPLACE"):
            logger.warning("AutonomousBrain : ANTHROPIC_API_KEY absent — décisions désactivées")
            self.client = None
        else:
            self.client = anthropic.Anthropic(api_key=_API_KEY)
            logger.info("AutonomousBrain : Claude %s prêt", MODEL_DECISION)

        # Cache décisions (pair → (timestamp, decision_dict))
        self._decision_cache: dict = {}
        self.CACHE_TTL = 300  # 5 minutes

    # ─────────────────────────────────────────────────────────────────────────
    # Méthode principale : decide()
    # ─────────────────────────────────────────────────────────────────────────

    def decide(
        self,
        pair: str,
        technical_signal: str,
        signal_score: float,
        indicators: dict,
        df=None,
    ) -> dict:
        """
        Raisonnement complet en 4 étapes pour décider d'entrer ou non.

        Args:
            pair             : ex. "BTC/USDT"
            technical_signal : ex. "BUY", "SELL", ""
            signal_score     : score [0-5] calculé par strategy.py
            indicators       : dict des indicateurs techniques
            df               : DataFrame OHLCV (optionnel, pour contexte)

        Returns:
            dict avec keys : decision, confidence, reasoning, risk_adjustment,
                             key_factors, market_regime, suggested_sl_adjustment,
                             time_horizon, alert_telegram
        """
        if self.client is None:
            return dict(_DEFAULT_DECISION)

        # Cache hit
        now = time.time()
        cache_entry = self._decision_cache.get(pair)
        if cache_entry:
            ts, cached_decision = cache_entry
            if now - ts < self.CACHE_TTL:
                logger.debug("AutonomousBrain cache hit pour %s", pair)
                return cached_decision

        # ── Étape 1 : Recherche web ───────────────────────────────────────────
        web_context = ""
        pair_research = {}
        global_market = {}

        if self.researcher:
            try:
                pair_research = self.researcher.research_pair(pair)
                global_market = self.researcher.get_global_market()
                web_context   = self._format_web_context(pair, pair_research, global_market)
            except Exception as e:
                logger.warning("AutonomousBrain : erreur recherche web (%s)", e)
                web_context = "Données web indisponibles.\n"
        else:
            web_context = "Module de recherche web non disponible.\n"

        # ── Étape 2 : Mémoire ─────────────────────────────────────────────────
        memory_context = ""
        similar_situations = []
        recent_events = []
        wisdom_text = ""
        pattern_stats_text = ""

        if self.memory:
            try:
                # Situations similaires passées
                similar_situations = self.memory.recall_similar_conditions(
                    {
                        "signal": technical_signal,
                        "trend": "up" if indicators.get("dist_ema_trend", 0) > 0 else "down",
                        "rsi": indicators.get("rsi"),
                    },
                    limit=5,
                )

                # Événements récents (48h)
                recent_events = self.memory.recall_recent_events(hours=48)

                # Sagesse pertinente selon les conditions
                wisdom_topics = self._select_wisdom_topics(indicators, global_market)
                wisdom_items = []
                for topic in wisdom_topics:
                    items = self.memory.get_wisdom(topic)
                    wisdom_items.extend(items)

                # Statistiques patterns
                pattern_stats = self.memory.get_performance_by_pattern()

                memory_context = self._format_memory_context(
                    similar_situations, recent_events, wisdom_items, pattern_stats, indicators
                )
            except Exception as e:
                logger.warning("AutonomousBrain : erreur mémoire (%s)", e)
                memory_context = "Mémoire de marché indisponible.\n"
        else:
            memory_context = "Module de mémoire non disponible.\n"

        # ── Étape 3 : Raisonnement Claude ─────────────────────────────────────
        prompt = self._build_decision_prompt(
            pair=pair,
            technical_signal=technical_signal,
            signal_score=signal_score,
            indicators=indicators,
            web_context=web_context,
            memory_context=memory_context,
            df=df,
        )

        decision = dict(_DEFAULT_DECISION)
        try:
            response = self.client.messages.create(
                model=MODEL_DECISION,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.content[0].text.strip()
            decision = self._parse_decision(raw_text)
            logger.info(
                "[AutonomousBrain] %s → %s (conf=%.2f) | %s",
                pair,
                decision.get("decision", "?"),
                decision.get("confidence", 0),
                decision.get("reasoning", "")[:80],
            )
        except anthropic.APITimeoutError:
            logger.warning("AutonomousBrain : timeout Claude API pour %s — WAIT par défaut", pair)
        except anthropic.APIError as e:
            logger.warning("AutonomousBrain : erreur Claude API (%s) — WAIT par défaut", e)
        except Exception as e:
            logger.warning("AutonomousBrain : erreur inattendue (%s) — WAIT par défaut", e)

        # ── Étape 4 : Mise à jour mémoire ─────────────────────────────────────
        if self.memory:
            try:
                price = indicators.get("close", 0.0)
                if price == 0.0 and pair_research:
                    cg = pair_research.get("coingecko", {})
                    price = cg.get("current_price", 0.0)

                self.memory.save_observation(
                    pair=pair,
                    price=float(price),
                    rsi=indicators.get("rsi"),
                    adx=indicators.get("adx"),
                    trend="up" if indicators.get("dist_ema_trend", 0) > 0 else "down",
                    volume_ratio=indicators.get("volume_ratio"),
                    notes=(
                        f"Signal={technical_signal} score={signal_score:.1f} "
                        f"decision={decision.get('decision','?')} "
                        f"conf={decision.get('confidence', 0):.2f}"
                    ),
                )
            except Exception as e:
                logger.debug("AutonomousBrain : erreur save_observation (%s)", e)

        # Mise en cache
        self._decision_cache[pair] = (now, decision)
        return decision

    # ─────────────────────────────────────────────────────────────────────────
    # reflect_daily()
    # ─────────────────────────────────────────────────────────────────────────

    def reflect_daily(self, portfolio_stats: dict, pairs: list) -> str:
        """
        Réflexion journalière : analyse les performances, ajuste les croyances,
        sauvegarde dans la mémoire, retourne un rapport pour Telegram.

        Args:
            portfolio_stats : dict avec trades, win_rate_pct, total_pnl, roi_pct, etc.
            pairs           : liste des paires tradées

        Returns:
            str : rapport de réflexion journalière (pour Telegram)
        """
        if self.client is None:
            return "Réflexion journalière indisponible — Claude désactivé."

        # Contexte mémoire global
        memory_summary = ""
        if self.memory:
            try:
                memory_summary = self.memory.get_market_context_summary()
                # Statistiques par pattern
                pattern_stats = self.memory.get_performance_by_pattern()
                if pattern_stats:
                    worst = sorted(
                        pattern_stats.items(),
                        key=lambda x: x[1]["avg_pnl_pct"]
                    )[:3]
                    best = sorted(
                        pattern_stats.items(),
                        key=lambda x: x[1]["avg_pnl_pct"],
                        reverse=True
                    )[:3]
                    stats_detail = "Meilleurs patterns :\n"
                    for k, v in best:
                        stats_detail += f"  {k[:40]} WR={v['win_rate']:.0%} avg={v['avg_pnl_pct']:+.2f}%\n"
                    stats_detail += "Pires patterns :\n"
                    for k, v in worst:
                        stats_detail += f"  {k[:40]} WR={v['win_rate']:.0%} avg={v['avg_pnl_pct']:+.2f}%\n"
                    memory_summary += "\n" + stats_detail
            except Exception as e:
                logger.warning("reflect_daily : erreur mémoire (%s)", e)
                memory_summary = "Mémoire indisponible.\n"

        # Contexte marché global
        market_snapshot = ""
        if self.researcher:
            try:
                gm = self.researcher.get_global_market()
                fg = gm.get("fear_greed", {})
                btcd = gm.get("btc_dominance_pct")
                mcc = gm.get("market_cap_change_pct_24h")
                market_snapshot = (
                    f"BTC Dominance: {btcd:.1f}%\n"
                    f"Market cap change 24h: {mcc:+.2f}%\n"
                    f"Fear & Greed: {fg.get('value', '?')}/100 ({fg.get('classification', '?')})\n"
                ) if btcd else "Données marché indisponibles.\n"
            except Exception as e:
                logger.warning("reflect_daily : erreur researcher (%s)", e)

        # Construction du prompt de réflexion
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        trades   = portfolio_stats.get("trades", 0)
        win_rate = portfolio_stats.get("win_rate_pct", 0)
        total_pnl = portfolio_stats.get("total_pnl", 0)
        roi_pct  = portfolio_stats.get("roi_pct", 0)
        drawdown = portfolio_stats.get("max_drawdown_pct", 0)

        prompt = f"""Tu es un trader quantitatif expert qui effectue sa réflexion de fin de journée.

Date : {date_str}
Paires tradées : {', '.join(pairs)}

=== PERFORMANCE DU JOUR ===
Nombre de trades : {trades}
Win rate : {win_rate:.1f}%
PnL total : {total_pnl:+.2f} USDT
ROI : {roi_pct:+.2f}%
Drawdown max : {drawdown:.2f}%

=== ÉTAT DU MARCHÉ ===
{market_snapshot}

=== MÉMOIRE & PATTERNS ===
{memory_summary}

=== MISSION ===
Effectue une réflexion structurée en 4 parties :

1. **Ce qui a marché** : quels types de setups ont généré du profit ? Pourquoi ?
2. **Ce qui n'a pas marché** : quels setups ont échoué ? Quelle en est la cause ?
3. **Ajustements stratégiques** : quelles croyances ou règles faut-il ajuster pour demain ?
4. **Outlook demain** : contexte de marché, paires à surveiller, niveau de risque recommandé.

Sois concis, factuel et actionnable. Maximum 15 lignes. Format Telegram (pas de markdown complexe).
"""

        try:
            response = self.client.messages.create(
                model=MODEL_REFLECT,
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
            )
            report = response.content[0].text.strip()

            # Sauvegarder la réflexion dans la mémoire
            if self.memory:
                try:
                    self.memory.remember_market_event(
                        event_text=f"[Réflexion IA {date_str}] {report[:200]}",
                        pair="ALL",
                        price_before=1.0,
                        price_after=1.0,
                    )
                except Exception:
                    pass

            logger.info("reflect_daily : rapport généré (%d chars)", len(report))
            return report

        except Exception as e:
            logger.warning("reflect_daily : erreur Claude (%s)", e)
            return (
                f"Réflexion {date_str} — trades={trades} "
                f"WR={win_rate:.1f}% PnL={total_pnl:+.2f} USDT\n"
                f"(Rapport détaillé indisponible)"
            )

    # ─────────────────────────────────────────────────────────────────────────
    # get_market_regime()
    # ─────────────────────────────────────────────────────────────────────────

    def get_market_regime(self, pairs: list, df_dict: Optional[dict] = None) -> dict:
        """
        Analyse le régime de marché global.

        Args:
            pairs   : liste de paires pour l'analyse
            df_dict : dict {pair: DataFrame} optionnel

        Returns:
            {
              "regime": "bull" | "bear" | "ranging" | "uncertain",
              "confidence": float,
              "btc_trend": str,
              "fng_value": int,
              "btc_dominance": float,
              "reasoning": str,
            }
        """
        default_regime = {
            "regime": "uncertain",
            "confidence": 0.5,
            "btc_trend": "unknown",
            "fng_value": 50,
            "btc_dominance": 50.0,
            "reasoning": "Analyse régime indisponible",
        }

        if self.client is None:
            return default_regime

        # Données marché global
        global_market = {}
        btc_data = {}
        if self.researcher:
            try:
                global_market = self.researcher.get_global_market()
                btc_data      = self.researcher.research_pair("BTC/EUR")
            except Exception as e:
                logger.warning("get_market_regime : erreur researcher (%s)", e)

        # Résumé données
        fg   = global_market.get("fear_greed", {})
        btcd = global_market.get("btc_dominance_pct", 50.0) or 50.0
        fng_val = fg.get("value", 50) if fg else 50

        btc_cg  = btc_data.get("coingecko", {}) if btc_data else {}
        btc_24h = btc_cg.get("price_change_pct_24h", 0) or 0
        btc_7d  = btc_cg.get("price_change_pct_7d", 0) or 0
        btc_30d = btc_cg.get("price_change_pct_30d", 0) or 0

        # Indicateurs BTC depuis df_dict
        btc_indicators = ""
        btc_pair = next((p for p in (df_dict or {}) if p.startswith("BTC/")), None)
        if df_dict and btc_pair:
            try:
                df = df_dict[btc_pair]
                if not df.empty:
                    last = df.iloc[-1]
                    rsi  = last.get("rsi", "N/A")
                    adx  = last.get("adx", "N/A")
                    ema200 = last.get("ema_trend", None)
                    price  = last.get("close", None)
                    trend_str = ""
                    if ema200 and price:
                        trend_str = "AU-DESSUS EMA200" if price > ema200 else "EN-DESSOUS EMA200"
                    btc_indicators = (
                        f"BTC RSI={rsi:.1f} ADX={adx:.1f} {trend_str}"
                        if isinstance(rsi, float) else ""
                    )
            except Exception:
                pass

        prompt = f"""Analyse le régime actuel du marché crypto en 30 secondes.

Données :
- BTC Dominance : {btcd:.1f}%
- Fear & Greed : {fng_val}/100 ({fg.get('classification', '?') if fg else '?'})
- BTC 24h : {btc_24h:+.2f}% | 7j : {btc_7d:+.2f}% | 30j : {btc_30d:+.2f}%
{f'- Indicateurs BTC : {btc_indicators}' if btc_indicators else ''}

Régimes possibles :
- "bull" : tendance haussière confirmée, >60 F&G, BTC au-dessus EMA200
- "bear" : tendance baissière, <30 F&G, BTC sous EMA200, lower lows
- "ranging" : marché sans direction, 30-60 F&G, consolidation
- "uncertain" : données contradictoires, forte volatilité non directionnelle

Réponds UNIQUEMENT en JSON :
{{
  "regime": "bull" | "bear" | "ranging" | "uncertain",
  "confidence": 0.0 à 1.0,
  "reasoning": "1 phrase max"
}}"""

        try:
            response = self.client.messages.create(
                model=MODEL_REGIME,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            data = self._parse_json(response.content[0].text.strip())
            result = {
                "regime":        data.get("regime", "uncertain"),
                "confidence":    float(data.get("confidence", 0.5)),
                "btc_trend":     "up" if btc_7d > 0 else "down",
                "fng_value":     int(fng_val),
                "btc_dominance": float(btcd),
                "reasoning":     data.get("reasoning", ""),
            }
            logger.info(
                "get_market_regime : %s (conf=%.2f) — %s",
                result["regime"], result["confidence"], result["reasoning"]
            )
            return result
        except Exception as e:
            logger.warning("get_market_regime : erreur (%s)", e)
            return {
                **default_regime,
                "fng_value":     int(fng_val),
                "btc_dominance": float(btcd),
            }

    # ─────────────────────────────────────────────────────────────────────────
    # Méthodes internes
    # ─────────────────────────────────────────────────────────────────────────

    def _build_decision_prompt(
        self,
        pair: str,
        technical_signal: str,
        signal_score: float,
        indicators: dict,
        web_context: str,
        memory_context: str,
        df=None,
    ) -> str:
        """Construit le prompt complet pour la décision Claude."""

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Indicateurs techniques
        rsi     = indicators.get("rsi", 0)
        macd    = indicators.get("macd_hist", 0)
        bb_pos  = indicators.get("bb_position", 0.5)
        adx     = indicators.get("adx", 0)
        roc     = indicators.get("roc", 0)
        vol_r   = indicators.get("volume_ratio", 1.0)
        vwap_d  = indicators.get("vwap_dev", 0)
        ema_f   = indicators.get("dist_ema_fast", 0)
        ema_t   = indicators.get("dist_ema_trend", 0)
        atr_p   = indicators.get("atr_pct", 0)
        ret1    = indicators.get("return_1", 0)
        ret3    = indicators.get("return_3", 0)
        ml_conf = indicators.get("ml_confidence", 0)

        trend_dir = "HAUSSIERE (+{:.2f}%)".format(ema_t * 100) if ema_t > 0 \
                    else "BAISSIERE ({:.2f}%)".format(ema_t * 100)
        adx_label = "FORT" if adx >= 25 else "MODERE" if adx >= 15 else "FAIBLE"
        bb_label  = "SURACHETÉ" if bb_pos > 0.8 else "SURVENDU" if bb_pos < 0.2 else "MILIEU"

        prompt = f"""Tu es un trader quantitatif expert avec 15 ans d'expérience sur les marchés crypto.
Tu dois prendre une décision autonome sur un setup de trading.

═══════════════════════════════════════
DATE/HEURE UTC : {now_utc}
PAIRE ANALYSÉE : {pair}
SIGNAL TECHNIQUE : {technical_signal if technical_signal else 'AUCUN'}
SCORE SIGNAL : {signal_score:.1f}/5
CONFIANCE ML (XGBoost) : {ml_conf:.1%}
═══════════════════════════════════════

=== INDICATEURS TECHNIQUES ===
Tendance EMA200  : {trend_dir}
ADX(14)          : {adx:.1f} ({adx_label})
RSI(14)          : {rsi:.1f}
MACD Histogram   : {macd:.6f}
Bollinger        : {bb_pos*100:.0f}% ({bb_label})
ROC(10)          : {roc:+.2f}%
Volume ratio     : {vol_r:.2f}x moyenne
VWAP déviation   : {vwap_d*100:+.2f}%
EMA rapide dist  : {ema_f*100:+.2f}%
ATR%             : {atr_p*100:.2f}%
Retour 1 bougie  : {ret1*100:+.2f}%
Retour 3 bougies : {ret3*100:+.2f}%

=== CONTEXTE WEB (TEMPS RÉEL) ===
{web_context}

=== MÉMOIRE & SITUATIONS SIMILAIRES ===
{memory_context}

═══════════════════════════════════════
QUESTION : Dois-je entrer en position LONG sur {pair} ?
Analyse tous les éléments disponibles. Pense étape par étape.
Considère : confluence des indicateurs, contexte macro, situations passées similaires,
niveau de risque actuel du marché, sagesse des marchés crypto.
═══════════════════════════════════════

Réponds UNIQUEMENT avec un bloc JSON valide dans ```json ... ``` :
```json
{{
  "decision": "ENTER" | "WAIT" | "SKIP",
  "confidence": 0.0 à 1.0,
  "reasoning": "Explication claire en 2-3 phrases",
  "risk_adjustment": 0.5 à 2.0,
  "key_factors": ["facteur1", "facteur2", "facteur3"],
  "market_regime": "bull" | "bear" | "ranging" | "uncertain",
  "suggested_sl_adjustment": -0.5 à 0.5,
  "time_horizon": "scalp" | "intraday" | "swing",
  "alert_telegram": "Message court 1 ligne pour Telegram"
}}
```

Règles de décision :
- ENTER : signal technique fort + contexte favorable + confluence indicateurs
- WAIT  : signal présent mais conditions incertaines, attendre confirmation
- SKIP  : setup mauvais, contre-tendance, risque élevé, éviter
- confidence > 0.7 requis pour ENTER
- risk_adjustment < 1.0 si marché incertain, > 1.0 si setup exceptionnel
- suggested_sl_adjustment en % (ex: 0.2 = élargir SL de 20%, -0.2 = resserrer)
"""
        return prompt

    def _format_web_context(self, pair: str, pair_research: dict, global_market: dict) -> str:
        """Formate le contexte web pour le prompt."""
        lines = []

        # Marché global
        fg = global_market.get("fear_greed", {})
        btcd = global_market.get("btc_dominance_pct")
        mc_change = global_market.get("market_cap_change_pct_24h")

        if fg:
            lines.append(
                f"Fear & Greed Index : {fg.get('value', '?')}/100 "
                f"({fg.get('classification', '?')})"
            )
            # Historique F&G sur 5 jours
            hist = fg.get("history", [])
            if hist:
                hist_str = " → ".join(
                    f"{h.get('value','?')}" for h in hist[:5]
                )
                lines.append(f"  Historique 5j F&G : {hist_str}")

        if btcd:
            lines.append(f"BTC Dominance : {btcd:.1f}%")
        if mc_change is not None:
            lines.append(f"Market cap change 24h : {mc_change:+.2f}%")

        # Données paire
        cg = pair_research.get("coingecko", {})
        fd = pair_research.get("funding", {})

        if cg:
            price  = cg.get("current_price")
            chg24  = cg.get("price_change_pct_24h")
            chg7d  = cg.get("price_change_pct_7d")
            ath_ch = cg.get("ath_change_pct")
            sent   = cg.get("sentiment_votes_up_pct")

            if price:
                lines.append(f"\nPrix {pair} : ${price:,.4f}")
            if chg24 is not None:
                lines.append(f"  Variation 24h : {chg24:+.2f}%")
            if chg7d is not None:
                lines.append(f"  Variation 7j  : {chg7d:+.2f}%")
            if ath_ch is not None:
                lines.append(f"  Distance ATH  : {ath_ch:.1f}%")
            if sent is not None:
                lines.append(f"  Sentiment CG  : {sent:.0f}% haussier")

        if fd:
            fr = fd.get("funding_rate")
            oi = fd.get("open_interest_usd")
            if fr is not None:
                fr_pct = fr * 100
                fr_label = "ÉLEVÉ (longs surchargés)" if fr_pct > 0.1 \
                           else "NÉGATIF (shorts surchargés)" if fr_pct < -0.05 \
                           else "neutre"
                lines.append(f"  Funding rate  : {fr_pct:.4f}% ({fr_label})")
            if oi:
                lines.append(f"  Open Interest : ${oi/1e6:.0f}M")

        # News
        news = pair_research.get("news_headlines", [])
        if news:
            lines.append(f"\nActualités récentes :")
            for item in news[:5]:
                sent_tag = item.get("sentiment", "neutral").upper()
                src = item.get("source", "?")
                title = item.get("title", "")[:90]
                lines.append(f"  [{sent_tag}][{src}] {title}")

        # Reddit sentiment
        reddit = pair_research.get("reddit_sentiment", {})
        if reddit and reddit.get("relevant_posts", 0) > 0:
            lines.append(
                f"\nReddit sentiment : {reddit.get('sentiment', '?').upper()} "
                f"({reddit.get('positive', 0)}+ / {reddit.get('negative', 0)}-)"
            )

        return "\n".join(lines) if lines else "Données web non disponibles."

    def _format_memory_context(
        self,
        similar_situations: list,
        recent_events: list,
        wisdom_items: list,
        pattern_stats: dict,
        indicators: dict,
    ) -> str:
        """Formate le contexte mémoire pour le prompt."""
        lines = []

        # Situations similaires passées
        if similar_situations:
            lines.append("Situations similaires passées (même signal + tendance + RSI) :")
            for s in similar_situations[:5]:
                outcome = s.get("outcome_pnl_pct", 0)
                sign = "+" if outcome >= 0 else ""
                claude_ok = "Claude OK" if s.get("claude_validated") else "non validé Claude"
                lines.append(
                    f"  [{s.get('timestamp', '?')[:10]}] {s.get('pair', '?')} "
                    f"RSI={s.get('rsi', '?'):.0f} ADX={s.get('adx', '?'):.0f} "
                    f"→ PnL={sign}{outcome:.2f}% ({claude_ok})"
                )
        else:
            lines.append("Aucune situation similaire dans la mémoire.")

        # Événements récents
        if recent_events:
            lines.append("\nÉvénements récents (48h) :")
            for ev in recent_events[:5]:
                sign = "+" if ev.get("impact_pct", 0) >= 0 else ""
                lines.append(
                    f"  [{ev.get('date', '?')}] {ev.get('pair', '?')} "
                    f"{sign}{ev.get('impact_pct', 0):.1f}% — {ev.get('event', '')[:70]}"
                )
        else:
            lines.append("\nAucun événement récent enregistré.")

        # Sagesse pertinente
        if wisdom_items:
            lines.append("\nSagesse de marché pertinente :")
            for w in wisdom_items[:4]:
                knowledge = w.get("knowledge", "")[:200]
                lines.append(f"  [{w.get('topic', '?')}] {knowledge}...")

        # Statistiques patterns similaires
        if pattern_stats:
            rsi = indicators.get("rsi")
            rsi_bucket = ""
            if rsi:
                if rsi < 35: rsi_bucket = "oversold"
                elif rsi < 50: rsi_bucket = "below_mid"
                elif rsi < 65: rsi_bucket = "above_mid"
                else: rsi_bucket = "overbought"

            relevant_patterns = {
                k: v for k, v in pattern_stats.items()
                if rsi_bucket in k
            }
            if relevant_patterns:
                lines.append(f"\nStatistiques patterns (RSI bucket={rsi_bucket}) :")
                for pattern, stats in list(relevant_patterns.items())[:3]:
                    lines.append(
                        f"  {pattern[:50]} : "
                        f"WR={stats['win_rate']:.0%} "
                        f"avgPnL={stats['avg_pnl_pct']:+.2f}% "
                        f"({stats['total_trades']} trades)"
                    )

        return "\n".join(lines) if lines else "Mémoire de marché vide."

    def _select_wisdom_topics(self, indicators: dict, global_market: dict) -> list:
        """Sélectionne les topics de sagesse les plus pertinents selon le contexte."""
        topics = ["trend_following"]  # Toujours inclus

        # F&G
        fg = global_market.get("fear_greed", {})
        fng = fg.get("value", 50) if fg else 50
        if fng < 30:
            topics.append("fear_greed_interpretation")
        elif fng > 70:
            topics.append("fear_greed_interpretation")

        # BTC dominance
        btcd = global_market.get("btc_dominance_pct", 50) or 50
        if btcd < 45:
            topics.append("altcoin_season")
        elif btcd > 60:
            topics.append("bitcoin_dominance")

        # Indicateurs techniques
        adx = indicators.get("adx", 0)
        if adx < 15:
            topics.append("entry_timing")
        elif adx > 30:
            topics.append("trend_following")

        rsi = indicators.get("rsi", 50)
        if rsi < 30 or rsi > 70:
            topics.append("fear_greed_interpretation")

        vol_r = indicators.get("volume_ratio", 1.0)
        if vol_r > 2.0:
            topics.append("volume_patterns")

        topics.append("position_sizing")  # Toujours inclus

        return list(dict.fromkeys(topics))  # Dédupliqué, ordre préservé

    def _parse_decision(self, raw_text: str) -> dict:
        """
        Extrait et valide la décision JSON de la réponse Claude.
        Cherche le JSON dans un bloc ```json ... ``` ou directement.
        """
        # Chercher dans un bloc ```json
        import re
        pattern = r"```json\s*(.*?)\s*```"
        match = re.search(pattern, raw_text, re.DOTALL)
        if match:
            json_str = match.group(1)
        else:
            # Fallback : chercher {} directement
            start = raw_text.find("{")
            end   = raw_text.rfind("}") + 1
            if start == -1 or end == 0:
                logger.warning("_parse_decision : pas de JSON trouvé dans : %s", raw_text[:150])
                return dict(_DEFAULT_DECISION)
            json_str = raw_text[start:end]

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning("_parse_decision : JSON invalide (%s) — texte: %s", e, json_str[:150])
            return dict(_DEFAULT_DECISION)

        # Validation et normalisation
        decision_raw = str(data.get("decision", "WAIT")).upper()
        if decision_raw not in ("ENTER", "WAIT", "SKIP"):
            decision_raw = "WAIT"

        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        risk_adj = float(data.get("risk_adjustment", 1.0))
        risk_adj = max(0.1, min(3.0, risk_adj))

        sl_adj = float(data.get("suggested_sl_adjustment", 0.0))
        sl_adj = max(-1.0, min(1.0, sl_adj))

        horizon = str(data.get("time_horizon", "intraday")).lower()
        if horizon not in ("scalp", "intraday", "swing"):
            horizon = "intraday"

        regime = str(data.get("market_regime", "uncertain")).lower()
        if regime not in ("bull", "bear", "ranging", "uncertain"):
            regime = "uncertain"

        key_factors = data.get("key_factors", [])
        if not isinstance(key_factors, list):
            key_factors = [str(key_factors)]

        return {
            "decision":               decision_raw,
            "confidence":             confidence,
            "reasoning":              str(data.get("reasoning", ""))[:500],
            "risk_adjustment":        risk_adj,
            "key_factors":            key_factors[:8],
            "market_regime":          regime,
            "suggested_sl_adjustment": sl_adj,
            "time_horizon":           horizon,
            "alert_telegram":         str(data.get("alert_telegram", ""))[:200],
        }

    def _parse_json(self, text: str) -> dict:
        """Extrait un JSON d'une chaîne de texte."""
        import re
        # Bloc ```json
        match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        # JSON brut
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError(f"Pas de JSON dans : {text[:100]}")
        return json.loads(text[start:end])

    def invalidate_cache(self, pair: Optional[str] = None) -> None:
        """Invalide le cache pour une paire ou tout le cache."""
        if pair:
            self._decision_cache.pop(pair, None)
        else:
            self._decision_cache.clear()
        logger.debug("Cache AutonomousBrain invalidé : %s", pair or "tout")
