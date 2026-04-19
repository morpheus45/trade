"""
Contrôleur Telegram — pilotage du bot depuis Android.

Commandes disponibles :
  /status    → État du bot, positions, capital, P&L
  /pause     → Suspend le trading (positions existantes gérées, plus de nouvelles entrées)
  /resume    → Reprend le trading
  /positions → Détail des positions ouvertes
  /stats     → Statistiques complètes des trades
  /stop      → Arrêt propre du bot
  /help      → Liste des commandes

Usage :
    python telegram_controller.py   (se lance en thread séparé)
    ou importé et lancé via bot_trading.py

Sécurité :
    Répond UNIQUEMENT au TELEGRAM_CHAT_ID configuré dans .env
    Toute requête d'un autre chat_id est silencieusement ignorée.
"""
import logging
import threading
import time
import requests
import config

logger = logging.getLogger(__name__)

# État partagé — modifié par les commandes Telegram, lu par bot_trading.py
_bot_ref     = None   # Référence au TradingBot pour accéder au portefeuille
_paused      = False
_stop_flag   = False
_last_update = 0


def register_bot(bot) -> None:
    """Enregistre la référence au TradingBot principal."""
    global _bot_ref
    _bot_ref = bot


def _send(text: str) -> None:
    """Envoie un message Telegram."""
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":    config.TELEGRAM_CHAT_ID,
                "text":       text,
                "parse_mode": "Markdown",
            },
            timeout=5,
        )
    except Exception as e:
        logger.debug(f"Telegram send error: {e}")


def _get_updates(offset: int = 0) -> list[dict]:
    """Récupère les nouveaux messages Telegram."""
    if not config.TELEGRAM_TOKEN:
        return []
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 10, "allowed_updates": ["message"]},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("result", [])
    except Exception:
        return []


def _handle_command(cmd: str) -> str:
    """Traite une commande et retourne la réponse."""
    global _paused, _stop_flag

    if cmd == "/help":
        return (
            "🤖 *Commandes disponibles :*\n"
            "/status → État global du bot\n"
            "/positions → Positions ouvertes\n"
            "/stats → Statistiques des trades\n"
            "/pause → Suspendre le trading\n"
            "/resume → Reprendre le trading\n"
            "/stop → Arrêt propre du bot\n"
            "/help → Cette aide"
        )

    if cmd == "/pause":
        if _paused:
            return "⚠️ Bot déjà en pause."
        _paused = True
        # Propager l'état au bot_trading.py
        try:
            import bot_trading
            bot_trading.set_paused(True)
        except Exception:
            pass
        return "⏸ *Bot mis en pause.* Les positions existantes continuent d'être gérées.\nUtilise /resume pour reprendre."

    if cmd == "/resume":
        if not _paused:
            return "ℹ️ Bot déjà actif."
        _paused = False
        try:
            import bot_trading
            bot_trading.set_paused(False)
        except Exception:
            pass
        return "▶️ *Bot repris.* Recherche de nouvelles entrées active."

    if cmd == "/stop":
        _stop_flag = True
        try:
            import bot_trading
            bot_trading._RUNNING = False
        except Exception:
            pass
        return "🛑 *Arrêt du bot en cours...* À bientôt."

    if cmd in ("/status", "/positions", "/stats"):
        if _bot_ref is None:
            return "⚠️ Bot non initialisé."

        if cmd == "/status":
            mode   = "📄 PAPER" if config.PAPER_TRADING else "🔴 LIVE"
            paused = "⏸ EN PAUSE" if _paused else "▶️ ACTIF"
            try:
                prices = {p: _bot_ref.exchange.get_price(p) or 0 for p in config.TRADE_PAIRS}
                total  = _bot_ref.portfolio.total_value(prices)
                init   = _bot_ref.portfolio.initial_capital
                pnl    = total - init
                pnl_pct = pnl / init * 100
                n_pos  = len(_bot_ref.portfolio.positions)
                stats  = _bot_ref.portfolio.stats()
            except Exception as e:
                return f"Erreur lecture portfolio: {e}"

            return (
                f"🤖 *Trading Bot — Status*\n"
                f"Mode: {mode} | État: {paused}\n"
                f"Capital initial: {init:.2f} EUR\n"
                f"Capital actuel: {total:.2f} EUR\n"
                f"P&L total: {pnl:+.2f} EUR ({pnl_pct:+.2f}%)\n"
                f"Positions ouvertes: {n_pos}/{config.MAX_OPEN_POSITIONS}\n"
                f"Trades effectués: {stats.get('trades', 0)} | "
                f"Win rate: {stats.get('win_rate_pct', 0):.1f}%"
            )

        if cmd == "/positions":
            if not _bot_ref.portfolio.positions:
                return "📭 Aucune position ouverte."
            lines = ["📊 *Positions ouvertes :*"]
            try:
                prices = {p: _bot_ref.exchange.get_price(p) or 0 for p in config.TRADE_PAIRS}
                for pair, pos in _bot_ref.portfolio.positions.items():
                    price  = prices.get(pair, pos.entry_price)
                    pnl    = pos.unrealized_pnl(price)
                    pnl_pct = pos.unrealized_pnl_pct(price)
                    trail  = "🎯" if _bot_ref.trailing.is_active(pair) else "🔒"
                    partial = "✅TP1" if pos.partial_done else "⏳TP1"
                    lines.append(
                        f"{trail} *{pair}*: {pos.qty_remaining:.4f} @ {pos.entry_price:.4f}\n"
                        f"   Prix: {price:.4f} | PnL: {pnl:+.2f}$ ({pnl_pct:+.2f}%) | {partial}"
                    )
            except Exception as e:
                return f"Erreur lecture positions: {e}"
            return "\n".join(lines)

        if cmd == "/stats":
            stats = _bot_ref.portfolio.stats()
            if not stats.get("trades"):
                return "📭 Aucun trade terminé pour l'instant."
            return (
                f"📈 *Statistiques des trades*\n"
                f"Trades: {stats['trades']} "
                f"(✅{stats['wins']} / ❌{stats['losses']})\n"
                f"Win rate: {stats['win_rate_pct']:.1f}%\n"
                f"Profit factor: {stats['profit_factor']:.2f}\n"
                f"Avg gain: {stats['avg_win']:+.4f} EUR\n"
                f"Avg perte: {stats['avg_loss']:+.4f} EUR\n"
                f"Expectancy: {stats['expectancy']:+.4f} EUR/trade\n"
                f"P&L total: {stats['total_pnl']:+.2f} EUR\n"
                f"ROI: {stats['roi_pct']:+.2f}%"
            )

    return f"❓ Commande inconnue: `{cmd}`\nUtilise /help pour voir les commandes."


def _polling_loop() -> None:
    """Boucle de polling Telegram (tourne en thread séparé)."""
    global _last_update
    logger.info("[Telegram Controller] Démarré — polling actif")

    offset = 0
    while not _stop_flag:
        try:
            updates = _get_updates(offset)
            for upd in updates:
                offset = upd["update_id"] + 1
                msg    = upd.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text    = msg.get("text", "").strip()

                # Sécurité : ignorer les messages d'autres chats
                if chat_id != str(config.TELEGRAM_CHAT_ID):
                    continue

                if text.startswith("/"):
                    cmd      = text.split()[0].lower()
                    response = _handle_command(cmd)
                    _send(response)
                    logger.info(f"[Telegram] Commande reçue: {cmd}")

        except Exception as e:
            logger.debug(f"[Telegram] Erreur polling: {e}")

        time.sleep(2)

    logger.info("[Telegram Controller] Arrêté.")


def start_controller(bot) -> threading.Thread:
    """
    Lance le contrôleur Telegram en thread démon.
    Appeler depuis bot_trading.py après l'init du TradingBot.
    """
    register_bot(bot)
    t = threading.Thread(target=_polling_loop, daemon=True, name="TelegramController")
    t.start()
    _send(
        "🟢 *Bot de trading démarré*\n"
        f"Mode: {'📄 PAPER' if config.PAPER_TRADING else '🔴 LIVE'}\n"
        f"Paires: {', '.join(config.TRADE_PAIRS)}\n"
        "Tape /help pour les commandes."
    )
    return t
