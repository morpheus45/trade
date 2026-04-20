"""
Interface de chat IA — permet de discuter avec Claude directement
depuis le dashboard web, avec connaissance totale du bot et des marchés.

Claude a accès à :
 - L'état du portefeuille (capital, positions, P&L)
 - L'historique des trades
 - Les indicateurs de marché actuels
 - La mémoire des marchés (événements, sagesse)
 - La recherche web en temps réel
"""
import logging
import json
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import config

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5"

SYSTEM_PROMPT = """Tu es l'IA de gestion du bot de trading crypto de l'utilisateur.
Tu as accès en temps réel à toutes les données du bot : portefeuille, positions ouvertes,
historique des trades, indicateurs de marché, et ta propre mémoire des marchés.

Ton rôle :
- Répondre aux questions sur les performances du bot
- Expliquer les décisions de trading prises
- Analyser les marchés et donner ton avis
- Suggérer des ajustements de stratégie
- Former l'utilisateur sur le trading crypto

Règles :
- Sois concis et direct (pas de phrases inutiles)
- Utilise des chiffres précis quand disponibles
- Mentionne toujours les risques
- Réponds en français
- Utilise des emojis pour rendre la lecture agréable
- Si tu ne sais pas quelque chose, dis-le clairement

Tu n'es PAS un conseiller financier. Rappelle-le si l'utilisateur demande des conseils d'investissement personnels."""


class AIChat:
    """
    Gère les conversations avec Claude depuis le dashboard.
    Chaque session conserve un historique de conversation.
    """

    def __init__(self):
        self.enabled = bool(config.ANTHROPIC_API_KEY)
        if not self.enabled:
            logger.warning("AIChat désactivé — ANTHROPIC_API_KEY manquant")
            return

        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        # Historique par session (session_id → list de messages)
        self._histories: dict[str, list] = {}
        self._max_history = 20  # messages max par session

        # Référence vers le portfolio (injectée au démarrage du bot)
        self._portfolio = None
        self._prices    = {}

    def set_portfolio(self, portfolio_manager, prices: dict) -> None:
        """Injecte le portfolio manager pour accès aux données en temps réel."""
        self._portfolio = portfolio_manager
        self._prices    = prices

    def _build_context(self) -> str:
        """Construit le contexte complet du bot pour Claude."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"=== ÉTAT DU BOT — {now} ==="]
        lines.append(f"Mode: {'PAPER TRADING (simulation)' if config.PAPER_TRADING else 'LIVE TRADING'}")

        if self._portfolio:
            total = self._portfolio.total_value(self._prices)
            init  = self._portfolio.initial_capital
            roi   = (total - init) / init * 100
            lines.append(f"\n💰 PORTEFEUILLE:")
            lines.append(f"  Capital initial : {init:.2f} EUR")
            lines.append(f"  Capital actuel  : {total:.2f} EUR")
            lines.append(f"  ROI             : {roi:+.2f}%")
            lines.append(f"  Cash disponible : {self._portfolio.quote_balance:.2f} EUR")

            # Positions ouvertes
            positions = self._portfolio.positions
            if positions:
                lines.append(f"\n📊 POSITIONS OUVERTES ({len(positions)}) :")
                for pair, pos in positions.items():
                    price = self._prices.get(pair, pos.entry_price)
                    pnl   = pos.unrealized_pnl(price)
                    pct   = pos.unrealized_pnl_pct(price)
                    lines.append(
                        f"  {pair}: {pos.side.upper()} {pos.qty_remaining:.6f} "
                        f"@ {pos.entry_price:.4f} | Actuel: {price:.4f} | "
                        f"P&L: {pnl:+.4f} USDT ({pct:+.2f}%) | "
                        f"SL: {pos.stop_price:.4f} | TP: {pos.tp_price:.4f}"
                    )
            else:
                lines.append("\n📊 POSITIONS OUVERTES: Aucune")

            # Stats
            stats = self._portfolio.stats()
            if stats.get("trades", 0) > 0:
                lines.append(f"\n📈 STATISTIQUES:")
                lines.append(f"  Trades total  : {stats['trades']}")
                lines.append(f"  Win rate      : {stats['win_rate_pct']}%")
                lines.append(f"  Profit factor : {stats['profit_factor']}")
                lines.append(f"  Expectancy    : {stats['expectancy']:.4f} USDT/trade")
                lines.append(f"  P&L total     : {stats['total_pnl']:+.2f} USDT")

            # Derniers trades
            recent = self._portfolio.trade_history[-5:] if self._portfolio.trade_history else []
            if recent:
                lines.append(f"\n🕐 5 DERNIERS TRADES:")
                for t in reversed(recent):
                    emoji = "✅" if t["pnl_usdt"] >= 0 else "❌"
                    lines.append(
                        f"  {emoji} {t['pair']} {t['side'].upper()} "
                        f"@ {t['entry_price']:.4f}→{t['exit_price']:.4f} "
                        f"| P&L: {t['pnl_usdt']:+.4f} USDT | {t['reason']}"
                    )

        # Mémoire des marchés (contexte rapide)
        try:
            from market_memory import MarketMemory
            mem = MarketMemory()
            wisdom_items = mem.get_wisdom()
            events = mem.recall_recent_events(hours=48)
            if events:
                lines.append(f"\n📰 ÉVÉNEMENTS RÉCENTS (48h):")
                for ev in events[:3]:
                    lines.append(f"  • {ev.get('event_text', '')}")
        except Exception:
            pass

        # Prix actuels
        if self._prices:
            lines.append(f"\n💹 PRIX ACTUELS:")
            for pair, price in list(self._prices.items())[:5]:
                lines.append(f"  {pair}: {price:.4f} USDT")

        lines.append("\n=== FIN CONTEXTE ===")
        return "\n".join(lines)

    def chat(self, session_id: str, user_message: str) -> str:
        """
        Envoie un message à Claude et retourne la réponse.

        Args:
            session_id   : Identifiant de session (ex: IP du client)
            user_message : Message de l'utilisateur

        Returns:
            Réponse de Claude en texte
        """
        if not self.enabled:
            return "❌ Chat IA non disponible (clé API manquante)."

        # Initialiser l'historique de session
        if session_id not in self._histories:
            self._histories[session_id] = []

        history = self._histories[session_id]

        # Contexte du bot injecté dans le premier message système
        context = self._build_context()
        system  = f"{SYSTEM_PROMPT}\n\n{context}"

        # Ajouter le message utilisateur
        history.append({"role": "user", "content": user_message})

        # Limiter l'historique
        if len(history) > self._max_history * 2:
            history = history[-self._max_history * 2:]
            self._histories[session_id] = history

        try:
            response = self.client.messages.create(
                model   = MODEL,
                max_tokens = 1024,
                system  = system,
                messages = history,
            )
            reply = response.content[0].text

            # Sauvegarder la réponse dans l'historique
            history.append({"role": "assistant", "content": reply})

            return reply

        except anthropic.APITimeoutError:
            return "⏱️ Délai dépassé. Réessaie dans quelques secondes."
        except anthropic.RateLimitError:
            return "⚠️ Limite de tokens atteinte. Attends quelques secondes."
        except Exception as e:
            logger.error(f"Erreur chat IA: {e}")
            return f"❌ Erreur: {str(e)}"

    def clear_history(self, session_id: str) -> None:
        """Efface l'historique d'une session."""
        self._histories.pop(session_id, None)

    def get_history(self, session_id: str) -> list:
        """Retourne l'historique d'une session."""
        return self._histories.get(session_id, [])


# Instance globale (partagée avec le dashboard Flask)
_chat_instance: AIChat | None = None


def get_chat() -> AIChat:
    global _chat_instance
    if _chat_instance is None:
        _chat_instance = AIChat()
    return _chat_instance
