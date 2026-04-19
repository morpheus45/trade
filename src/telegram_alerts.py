"""
Alertes Telegram pour le bot de trading.
Envoie des notifications lors des événements importants.
"""
import logging
import requests
import config

logger = logging.getLogger(__name__)

BASE_URL = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"


def _send(text: str) -> None:
    """Envoie un message Telegram. Ne lève pas d'exception en cas d'échec."""
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.debug("Telegram non configuré, alerte ignorée")
        return
    try:
        resp = requests.post(
            BASE_URL,
            data={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if not resp.ok:
            logger.warning(f"Telegram HTTP {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        logger.warning(f"Erreur envoi Telegram: {e}")


def alert_start(paper: bool) -> None:
    mode = "📄 PAPER TRADING" if paper else "🔴 TRADING RÉEL"
    _send(f"🤖 *Bot démarré* — Mode: {mode}")


def alert_stop(reason: str = "") -> None:
    _send(f"⏹ *Bot arrêté*{' — ' + reason if reason else ''}")


def alert_buy(pair: str, qty: float, price: float, stop: float, tp: float, paper: bool) -> None:
    tag = "[PAPER] " if paper else ""
    _send(
        f"📈 {tag}*ACHAT* `{pair}`\n"
        f"Qté: `{qty:.6f}` @ `{price:.4f}`\n"
        f"Stop: `{stop:.4f}` | TP: `{tp:.4f}`"
    )


def alert_sell_close(pair: str, pnl: float, pnl_pct: float, reason: str, paper: bool) -> None:
    tag = "[PAPER] " if paper else ""
    emoji = "✅" if pnl >= 0 else "❌"
    _send(
        f"{emoji} {tag}*POSITION FERMÉE* `{pair}`\n"
        f"PnL: `{pnl:+.4f} EUR` (`{pnl_pct:+.2f}%`)\n"
        f"Raison: `{reason}`"
    )


def alert_circuit_breaker(reason: str) -> None:
    _send(f"🚨 *CIRCUIT BREAKER DÉCLENCHÉ*\n{reason}\nTrading suspendu.")


def alert_stats(stats: dict, paper: bool) -> None:
    tag = "PAPER " if paper else ""
    _send(
        f"📊 *{tag}Stats journalières*\n"
        f"Trades: `{stats.get('trades', 0)}` | Win rate: `{stats.get('win_rate_pct', 0):.1f}%`\n"
        f"PnL total: `{stats.get('total_pnl', 0):+.2f} EUR`\n"
        f"Balance: `{stats.get('balance', 0):.2f} EUR` | ROI: `{stats.get('roi_pct', 0):+.2f}%`"
    )
