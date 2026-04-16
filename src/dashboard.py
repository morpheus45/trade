"""
Dashboard Flask — PWA Trading Bot.

Endpoints :
  GET /           → Dashboard HTML (PWA installable sur Android)
  GET /api/data   → JSON temps réel (capital, positions, trades, stats)
  GET /static/... → Assets PWA (manifest, service worker, icônes)

Le dashboard se connecte au bot via les fichiers CSV de logs.
Pour avoir les données des positions en temps réel, le bot peut aussi
injecter une référence via `set_bot_instance()`.
"""
import logging
import json
import math
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, send_from_directory
import config

logger = logging.getLogger(__name__)

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)

# Référence optionnelle au bot (injectée depuis bot_trading.py si lancé ensemble)
_bot_instance = None


def set_bot_instance(bot) -> None:
    global _bot_instance
    _bot_instance = bot


# ─────────────────────────────────────────────────────────────────────────────
# Lecture des fichiers de logs
# ─────────────────────────────────────────────────────────────────────────────

def _load_trades() -> pd.DataFrame:
    if not config.TRADES_CSV.exists():
        return pd.DataFrame(columns=[
            "pair", "side", "quantity", "entry_price", "exit_price",
            "pnl_usdt", "pnl_pct", "reason", "opened_at", "closed_at",
            "duration_min", "partial_done"
        ])
    try:
        df = pd.read_csv(config.TRADES_CSV)
        df["closed_at"] = pd.to_datetime(df["closed_at"], errors="coerce")
        df = df.sort_values("closed_at", ascending=False)
        # Exclure les TP partiels du calcul des stats finales
        return df
    except Exception as e:
        logger.error(f"Erreur lecture trades: {e}")
        return pd.DataFrame()


def _load_portfolio_history() -> list[dict]:
    if not config.PORTFOLIO_CSV.exists():
        return []
    try:
        df = pd.read_csv(config.PORTFOLIO_CSV)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp", "total_value"])
        # Sous-échantillonner si trop de points (max 200 pour le graphique)
        if len(df) > 200:
            step = len(df) // 200
            df = df.iloc[::step]
        return [
            {"time": row["timestamp"].strftime("%d/%m %H:%M"), "value": round(float(row["total_value"]), 2)}
            for _, row in df.iterrows()
        ]
    except Exception as e:
        logger.error(f"Erreur lecture portfolio: {e}")
        return []


def _compute_stats(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {
            "trades": 0, "wins": 0, "losses": 0,
            "win_rate_pct": 0, "total_pnl": 0, "roi_pct": 0,
            "profit_factor": 0, "avg_win": 0, "avg_loss": 0, "expectancy": 0,
        }

    # Exclure les TP partiels des stats globales
    closed = trades[trades.get("reason", pd.Series()) != "partial_tp"] if "reason" in trades.columns else trades
    if closed.empty:
        closed = trades

    pnls   = closed["pnl_usdt"].dropna()
    wins   = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    total  = len(pnls)

    win_rate = len(wins) / total * 100 if total else 0
    avg_win  = float(wins.mean()) if len(wins) else 0
    avg_loss = float(losses.mean()) if len(losses) else 0
    pf_denom = abs(float(losses.sum())) if len(losses) else 0
    pf       = abs(float(wins.sum())) / pf_denom if pf_denom > 0 else 0
    exp      = (win_rate/100) * avg_win + (1 - win_rate/100) * avg_loss

    return {
        "trades":        total,
        "wins":          int(len(wins)),
        "losses":        int(len(losses)),
        "win_rate_pct":  round(win_rate, 1),
        "total_pnl":     round(float(pnls.sum()), 2),
        "profit_factor": round(pf, 2),
        "avg_win":       round(avg_win, 4),
        "avg_loss":      round(avg_loss, 4),
        "expectancy":    round(exp, 4),
        "roi_pct":       0,  # calculé dans /api/data avec capital
    }


# ─────────────────────────────────────────────────────────────────────────────
# API JSON
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/data")
def api_data():
    """
    Point de données principal pour le dashboard.
    Retourne tout ce dont le front-end a besoin en une seule requête.
    """
    trades    = _load_trades()
    portfolio = _load_portfolio_history()
    stats     = _compute_stats(trades)

    # Capital et positions depuis le bot si disponible, sinon depuis les logs
    initial_capital = 1000.0
    total_value     = 1000.0
    open_positions  = []
    trailing_states = {}
    paused          = False
    ml_loaded       = False
    claude_active   = False

    if _bot_instance:
        try:
            prices = {p: _bot_instance.exchange.get_price(p) or 0 for p in config.TRADE_PAIRS}
            total_value    = _bot_instance.portfolio.total_value(prices)
            initial_capital = _bot_instance.portfolio.initial_capital

            for pair, pos in _bot_instance.portfolio.positions.items():
                price = prices.get(pair, pos.entry_price)
                pnl   = pos.unrealized_pnl(price)
                pct   = pos.unrealized_pnl_pct(price)
                trail = _bot_instance.trailing.is_active(pair)
                open_positions.append({
                    "pair":              pair,
                    "qty_remaining":     round(pos.qty_remaining, 6),
                    "entry_price":       pos.entry_price,
                    "stop_price":        pos.stop_price,
                    "tp_price":          pos.tp_price,
                    "unrealized_pnl":    round(pnl, 4),
                    "unrealized_pnl_pct": round(pct, 2),
                    "trailing_active":   trail,
                    "partial_done":      pos.partial_done,
                })

            # Stats live du portfolio manager
            live_stats = _bot_instance.portfolio.stats()
            if live_stats.get("trades", 0) > 0:
                stats = live_stats

            paused       = getattr(__import__("bot_trading"), "_PAUSED", False)
            ml_loaded    = _bot_instance.ai_model.model is not None
            claude_active = _bot_instance.claude.enabled

        except Exception as e:
            logger.debug(f"Erreur lecture bot instance: {e}")

    roi_pct = (total_value - initial_capital) / initial_capital * 100 if initial_capital > 0 else 0
    stats["roi_pct"] = round(roi_pct, 2)

    # Sérialisation sécurisée (NaN → None)
    def safe(v):
        if v is None:
            return None
        try:
            if math.isnan(v) or math.isinf(v):
                return None
        except Exception:
            pass
        return v

    recent_trades = []
    if not trades.empty:
        for _, row in trades.head(30).iterrows():
            recent_trades.append({
                "pair":         row.get("pair", ""),
                "pnl_usdt":     safe(row.get("pnl_usdt", 0)) or 0,
                "pnl_pct":      safe(row.get("pnl_pct", 0)) or 0,
                "reason":       row.get("reason", ""),
                "duration_min": safe(row.get("duration_min", 0)) or 0,
                "closed_at":    str(row.get("closed_at", "")),
            })

    return jsonify({
        "paper":             config.PAPER_TRADING,
        "paused":            paused,
        "pairs":             config.TRADE_PAIRS,
        "risk_pct":          config.RISK_PER_TRADE_PCT,
        "initial_capital":   initial_capital,
        "total_value":       round(total_value, 2),
        "open_positions":    len(open_positions),
        "max_positions":     config.MAX_OPEN_POSITIONS,
        "positions":         open_positions,
        "stats":             stats,
        "recent_trades":     recent_trades,
        "portfolio_history": portfolio,
        "ml_loaded":         ml_loaded,
        "claude_active":     claude_active,
        "timestamp":         datetime.now(timezone.utc).isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Pages
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


# ─────────────────────────────────────────────────────────────────────────────
# Lancement
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Accessible sur le réseau local → port sur Android via IP du PC
    app.run(debug=False, host="0.0.0.0", port=5000, threaded=True)
