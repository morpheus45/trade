"""
Bot de trading principal — version améliorée.

Pipeline complet :
1. Filtre de tendance 1h + confirmation 4h
2. Filtre ADX (marchés directionnels uniquement)
3. Score de signal [0-5] basé sur 5 indicateurs
4. Filtre ML XGBoost (confidence requise)
5. Sentiment marché Claude (Fear & Greed + actualités)
6. Validation du setup Claude (analyse indicateurs)
7. Sizing adaptatif (×1.5 si signal haute qualité)
8. TP partiel à 50% (premier objectif atteint)
9. Trailing stop (protège les gains sur tendances longues)

Lancer avec :
    python bot_trading.py
"""
import logging
import signal
import sys
import time
import threading
from datetime import datetime, timezone

import pandas as pd
import config
from logger import setup_logging, log_trade, log_portfolio_snapshot
from exchange import Exchange
from portfolio_manager import PortfolioManager
from risk_management import (
    CircuitBreaker,
    TrailingStopManager,
    calculate_adaptive_size,
    calculate_stop_price,
    calculate_take_profit,
    calculate_partial_tp,
    should_stop_loss,
    should_take_profit,
)
from strategy import generate_signal, get_features_for_ml
from ai_model import AIModel
from claude_analysis import ClaudeAnalyst
from indicators import add_all_indicators
import telegram_alerts as tg
import telegram_controller as tg_ctrl
from github_reporter import GitHubReporter

setup_logging()
logger = logging.getLogger(__name__)

_RUNNING  = True
_PAUSED   = False   # Contrôle Telegram : pause sans arrêt total
_LOCK     = threading.Lock()


def _handle_sigint(sig, frame):
    global _RUNNING
    logger.info("Interruption — arrêt propre...")
    _RUNNING = False


signal.signal(signal.SIGINT,  _handle_sigint)
signal.signal(signal.SIGTERM, _handle_sigint)


class TradingBot:
    def __init__(self):
        logger.info("=" * 60)
        logger.info("  TRADING BOT v2 — Démarrage")
        logger.info(f"  Mode: {'PAPER TRADING' if config.PAPER_TRADING else 'LIVE TRADING'}")
        logger.info(f"  Paires: {config.TRADE_PAIRS}")
        logger.info(f"  Risque/trade: {config.RISK_PER_TRADE_PCT*100:.1f}% | "
                    f"Max positions: {config.MAX_OPEN_POSITIONS}")
        logger.info("=" * 60)

        self.exchange   = Exchange()
        self.portfolio  = PortfolioManager(initial_capital=self._get_initial_capital())
        self.ai_model   = AIModel()
        self.claude     = ClaudeAnalyst()
        self.cb         = CircuitBreaker(self.portfolio.initial_capital)
        self.trailing   = TrailingStopManager()
        self.reporter = GitHubReporter(config.BASE_DIR, self.portfolio)
        self.reporter.start()

        self._last_day          = datetime.now(timezone.utc).day
        self._last_stats_hour   = -1
        self._last_scan: dict = {}

        claude_status = "actif" if self.claude.enabled else "désactivé"
        logger.info(f"Claude: {claude_status}")

        # Lancer le contrôleur Telegram (commandes /status /pause etc.)
        self._tg_ctrl_thread = tg_ctrl.start_controller(self)

    def _get_initial_capital(self) -> float:
        if config.PAPER_TRADING:
            try:
                cap = float(open(config.BASE_DIR / "initial_capital.txt").read().strip())
            except Exception:
                cap = 1000.0
            logger.info(f"Capital initial (paper): {cap:.2f} USDT")
            return cap
        else:
            for attempt in range(6):
                bal = self.exchange.get_balance("USDT")
                if bal > 0:
                    logger.info(f"Capital initial (live): {bal:.2f} USDT")
                    return bal
                eur = self.exchange.get_balance("EUR")
                if eur > 0:
                    logger.warning(f"Solde EUR: {eur:.2f} EUR. Convertis en USDT sur Binance -> tentative {attempt+1}/6 dans 30s...")
                    tg._send(f"Solde en EUR ({eur:.2f} EUR). Va sur Binance -> Actifs -> Convertir -> EUR vers USDT puis reviens.")
                else:
                    logger.warning(f"Solde USDT nul (tentative {attempt+1}/6). Verifier cles API. Attente 30s...")
                time.sleep(30)
            logger.error("Impossible de recuperer le solde USDT. Arret.")
            sys.exit(1)

    # ─── Gestion des positions ouvertes ──────────────────────────────────────

    def _manage_open_positions(self, prices: dict[str, float]) -> None:
        """
        Pour chaque position ouverte :
        1. Met à jour le trailing stop
        2. Vérifie le TP partiel (50%)
        3. Vérifie le stop-loss / TP final
        """
        for pair in list(self.portfolio.positions.keys()):
            pos   = self.portfolio.get_position(pair)
            price = prices.get(pair)
            if pos is None or price is None:
                continue

            # ── Trailing stop : monte avec le prix ────────────────────────
            new_stop = self.trailing.update(pair, price)
            self.portfolio.update_stop(pair, new_stop)

            # ── TP partiel ────────────────────────────────────────────────
            if (not pos.partial_done
                    and should_take_profit(price, pos.partial_tp, pos.side)):
                qty_to_sell = pos.quantity * config.PARTIAL_TP_RATIO
                order       = self.exchange.place_market_order(pair, "sell", qty_to_sell)
                exit_price  = (order.get("average") or order.get("price") or price) if order else price
                trade       = self.portfolio.execute_partial_tp(
                    pair, exit_price, order_id=order["id"] if order else ""
                )
                if trade:
                    log_trade(trade)
                    tg._send(
                        f"🎯 *TP Partiel* {pair} — vendu 50% @ {exit_price:.4f} | "
                        f"PnL: {trade['pnl_usdt']:+.2f} USDT"
                    )
                    logger.info(f"[TP Partiel] {pair} — trailing actif sur le reste")
                continue

            # ── Stop-loss ou TP final ─────────────────────────────────────
            reason = None
            if should_stop_loss(price, pos.stop_price, pos.side):
                reason = "stop_loss"
            elif should_take_profit(price, pos.tp_price, pos.side):
                reason = "take_profit"

            if reason:
                order      = self.exchange.place_market_order(pair, "sell", pos.qty_remaining)
                exit_price = (order.get("average") or order.get("price") or price) if order else price
                trade      = self.portfolio.close_position(
                    pair, exit_price, reason=reason,
                    order_id=order["id"] if order else ""
                )
                if trade:
                    log_trade(trade)
                    tg.alert_sell_close(pair, trade["pnl_usdt"], trade["pnl_pct"],
                                        reason, config.PAPER_TRADING)
                self.trailing.remove(pair)

    # ─── Recherche de nouvelles entrées ──────────────────────────────────────

    def _scan_for_entries(self, prices: dict[str, float]) -> None:
        """
        Pipeline de filtrage complet pour chaque paire.
        """
        if not self.portfolio.can_open_position():
            return

        for pair in config.TRADE_PAIRS:
            # Throttle: scan each pair at most once every 5 minutes
            if time.time() - self._last_scan.get(pair, 0) < 300:
                continue
            self._last_scan[pair] = time.time()
            if not self.portfolio.can_open_position():
                break
            if self.portfolio.has_position(pair):
                continue

            # ── Étape 1 : données + indicateurs ───────────────────────────
            df_1h = self.exchange.fetch_ohlcv(
                pair, config.TIMEFRAME_PRIMARY, limit=config.LOOKBACK_CANDLES
            )
            if df_1h.empty or len(df_1h) < 60:
                logger.warning(f"Données 1h insuffisantes pour {pair}")
                continue

            # Timeframe 4h pour confirmation
            df_4h = self.exchange.fetch_ohlcv(
                pair, config.TIMEFRAME_TREND, limit=100
            )

            # ── Étape 2 : signal + score ───────────────────────────────────
            signal_val, signal_score = generate_signal(df_1h, df_4h)
            if signal_val != "BUY":
                continue

            # ── Étape 3 : filtre ML ────────────────────────────────────────
            features    = get_features_for_ml(df_1h)
            ml_conf     = 0.5
            ml_ok       = True

            if features:
                from ai_model import CONFIDENCE_THRESHOLD
                ml_signal, ml_conf = self.ai_model.predict(features)
                ml_ok = (ml_signal in ("BUY", "BYPASS"))
                if not ml_ok:
                    logger.info(f"[ML] Signal rejeté pour {pair} (conf={ml_conf:.2f})")
                    continue

            # ── Indicateurs pour Claude et sizing ─────────────────────────
            df_ind = add_all_indicators(df_1h)
            last   = df_ind.iloc[-1]
            price  = prices.get(pair) or float(last["close"])
            atr_raw = last.get("atr")
            atr = float(atr_raw) if (atr_raw is not None and pd.notna(atr_raw)) else price * 0.01

            indicators_snapshot = {
                "rsi":            float(last.get("rsi", 50)),
                "macd_hist":      float(last.get("macd_hist", 0)),
                "bb_position":    float(last.get("bb_position", 0.5)),
                "dist_ema_fast":  float(last.get("dist_ema_fast", 0)),
                "dist_ema_slow":  float(last.get("dist_ema_slow", 0)),
                "dist_ema_trend": float(last.get("dist_ema_trend", 0)),
                "atr_pct":        float(last.get("atr_pct", 0.01)),
                "volume_ratio":   float(last.get("volume_ratio", 1)),
                "adx":            float(last.get("adx", 20)),
                "roc":            float(last.get("roc", 0)),
                "vwap_dev":       float(last.get("vwap_dev", 0)),
                "return_1":       float(last.get("return_1", 0)),
                "return_3":       float(last.get("return_3", 0)),
                "signal_score":   signal_score,
            }

            # ── Étape 4 : sentiment marché (Claude) ───────────────────────
            sentiment = self.claude.get_market_sentiment(pair)
            if sentiment["sentiment"] == "bearish" and sentiment["confidence"] >= 0.75:
                logger.info(
                    f"[Claude Sentiment] {pair} bloqué — "
                    f"bearish {sentiment['confidence']:.0%}: {sentiment['summary']}"
                )
                continue

            # ── Étape 5 : validation du setup (Claude) ────────────────────
            should_trade, claude_reason = self.claude.validate_trade(
                pair, signal_val, indicators_snapshot, sentiment
            )
            if not should_trade:
                logger.info(f"[Claude Validation] {pair} rejeté: {claude_reason}")
                continue

            # ── Calcul de la taille (adaptatif) ───────────────────────────
            stop      = calculate_stop_price(price, atr, "buy")
            tp        = calculate_take_profit(price, atr, "buy")
            partial   = calculate_partial_tp(price, atr, "buy")

            qty = calculate_adaptive_size(
                capital       = self.portfolio.quote_balance,
                entry_price   = price,
                stop_price    = stop,
                ml_confidence = ml_conf,
                claude_validated = should_trade,
                signal_score  = signal_score,
            )

            if qty <= 0:
                logger.warning(f"Taille nulle pour {pair}, skip")
                continue

            # ── Passage de l'ordre ─────────────────────────────────────────
            order      = self.exchange.place_market_order(pair, "buy", qty)
            if order is None:
                continue

            fill_price = order.get("price", price)
            stop       = calculate_stop_price(fill_price, atr, "buy")
            tp         = calculate_take_profit(fill_price, atr, "buy")
            partial    = calculate_partial_tp(fill_price, atr, "buy")

            opened = self.portfolio.open_position(
                pair=pair, side="buy", quantity=qty,
                entry_price=fill_price, stop_price=stop, tp_price=tp,
                partial_tp=partial, atr=atr,
                order_id=order.get("id", ""),
            )

            if opened:
                self.trailing.init_position(pair, fill_price, stop)
                tg.alert_buy(pair, qty, fill_price, stop, tp, config.PAPER_TRADING)
                detail = (
                    f"Score: {signal_score}/5 | ML: {ml_conf:.0%} | "
                    f"TP partiel: {partial:.4f}"
                )
                if claude_reason:
                    detail += f"\nClaude: {claude_reason}"
                tg._send(f"_{detail}_")

    # ─── Tâches périodiques ───────────────────────────────────────────────────

    def _daily_reset(self) -> None:
        today = datetime.now(timezone.utc).day
        if today != self._last_day:
            self._last_day = today
            prices = {p: self.exchange.get_price(p) or 0 for p in config.TRADE_PAIRS}
            total  = self.portfolio.total_value(prices)
            self.cb.reset_daily(total)
            stats  = self.portfolio.stats()
            tg.alert_stats(stats, config.PAPER_TRADING)

            if self.claude.enabled:
                briefing = self.claude.daily_market_briefing(config.TRADE_PAIRS, stats)
                tg._send(f"📰 *Briefing du jour*\n{briefing}")
                self.reporter.update_context(
                    {},
                    claude_analysis=briefing if briefing else "",
                )

            logger.info(f"[DAILY RESET] Stats: {stats}")

    def _hourly_snapshot(self, prices: dict[str, float]) -> None:
        hour = datetime.now(timezone.utc).hour
        if hour != self._last_stats_hour:
            self._last_stats_hour = hour
            total = self.portfolio.total_value(prices)
            log_portfolio_snapshot(self.portfolio.quote_balance, total,
                                   len(self.portfolio.positions))
            self.cb.update(total)
            self.reporter.update_context(prices)

    # ─── Boucle principale ────────────────────────────────────────────────────

    def run(self) -> None:
        global _RUNNING, _PAUSED
        logger.info("Boucle démarrée.")

        while _RUNNING:
            loop_start = time.time()
            try:
                with _LOCK:
                    paused = _PAUSED

                if paused:
                    logger.debug("Bot en pause (commande Telegram)...")
                    time.sleep(10)
                    continue

                prices = {}
                for pair in config.TRADE_PAIRS:
                    p = self.exchange.get_price(pair)
                    if p:
                        prices[pair] = p

                total_cap = self.portfolio.total_value(prices)

                if self.cb.is_triggered(total_cap):
                    logger.error(f"Circuit breaker: {self.cb.reason}")
                    tg.alert_circuit_breaker(self.cb.reason)
                    time.sleep(60)
                    continue

                self._manage_open_positions(prices)
                self._scan_for_entries(prices)
                self._daily_reset()
                self._hourly_snapshot(prices)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.exception(f"Erreur boucle: {e}")
                time.sleep(10)

            elapsed    = time.time() - loop_start
            sleep_time = max(0, config.LOOP_INTERVAL_SECONDS - elapsed)
            logger.debug(f"Itération {elapsed:.1f}s. Pause {sleep_time:.1f}s.")
            time.sleep(sleep_time)

        # Arrêt propre
        prices = {p: self.exchange.get_price(p) or 0 for p in config.TRADE_PAIRS}
        stats  = self.portfolio.stats()
        logger.info(f"Stats finales: {stats}")
        tg.alert_stop("Arrêt manuel")
        logger.info("Bot arrêté.")


def set_paused(state: bool) -> None:
    """Permet au contrôleur Telegram de mettre en pause/reprendre."""
    global _PAUSED
    with _LOCK:
        _PAUSED = state


if __name__ == "__main__":
    bot = TradingBot()
    bot.run()
