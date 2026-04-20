"""
Gestion du portefeuille et suivi des positions ouvertes.
Supporte : paper trading, live, take-profit partiel, trailing stop.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
import config

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Représente une position ouverte."""
    pair:           str
    side:           str            # 'buy' (long)
    quantity:       float          # Quantité totale en base (ex: BTC)
    entry_price:    float          # Prix d'entrée moyen
    stop_price:     float          # Niveau stop-loss courant (mis à jour par trailing)
    tp_price:       float          # Take-profit final (100% de la position)
    partial_tp:     float          # Premier TP partiel (50%)
    atr_at_entry:   float          # ATR au moment de l'entrée
    opened_at:      datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    order_id:       str = ""
    partial_done:   bool = False   # True si le TP partiel a déjà été exécuté
    qty_remaining:  float = 0.0    # Quantité après TP partiel (initialisée dans __post_init__)

    def __post_init__(self):
        if self.qty_remaining == 0.0:
            self.qty_remaining = self.quantity

    @property
    def cost(self) -> float:
        """Valeur d'entrée en quote (EUR)."""
        return self.entry_price * self.quantity

    def unrealized_pnl(self, current_price: float) -> float:
        """P&L non réalisé en EUR (basé sur qty_remaining)."""
        qty = self.qty_remaining
        if self.side == "buy":
            return (current_price - self.entry_price) * qty
        return (self.entry_price - current_price) * qty

    def unrealized_pnl_pct(self, current_price: float) -> float:
        cost = self.entry_price * self.qty_remaining
        if cost == 0:
            return 0.0
        return self.unrealized_pnl(current_price) / cost * 100


class PortfolioManager:
    """
    Gère :
     - Solde en quote (EUR)
     - Positions ouvertes
     - TP partiel (50% de la position vendue au premier TP)
     - Mise à jour du stop-loss (trailing)
     - Historique des trades et métriques
    """

    def __init__(self, initial_capital: float = 1000.0):
        self.initial_capital = initial_capital
        self.quote_balance   = initial_capital
        self.positions: dict[str, Position] = {}
        self.trade_history: list[dict] = []

    # ─── Valeur et P&L ───────────────────────────────────────────────────────

    def total_value(self, prices: dict[str, float]) -> float:
        val = self.quote_balance
        for pair, pos in self.positions.items():
            price = prices.get(pair, pos.entry_price)
            val += pos.qty_remaining * price
        return val

    def get_unrealized_pnl(self, prices: dict[str, float]) -> float:
        return sum(
            pos.unrealized_pnl(prices.get(pair, pos.entry_price))
            for pair, pos in self.positions.items()
        )

    # ─── Ouverture de position ────────────────────────────────────────────────

    def open_position(
        self,
        pair: str,
        side: str,
        quantity: float,
        entry_price: float,
        stop_price: float,
        tp_price: float,
        partial_tp: float,
        atr: float,
        order_id: str = "",
    ) -> bool:
        if pair in self.positions:
            logger.warning(f"Position déjà ouverte sur {pair}, ignoré")
            return False

        fee_pct = getattr(config, "BINANCE_FEE_PCT", 0.001)
        cost    = quantity * entry_price
        fee_in  = cost * fee_pct           # Frais d'entrée
        total_cost = cost + fee_in

        if total_cost > self.quote_balance * 1.01:
            logger.warning(
                f"Capital insuffisant pour {pair}: besoin {total_cost:.4f} (dont {fee_in:.4f} fees), "
                f"dispo {self.quote_balance:.4f}"
            )
            return False

        self.quote_balance -= total_cost
        logger.info(f"Frais entree {pair}: {fee_in:.4f} EUR")
        self.positions[pair] = Position(
            pair=pair,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_price=stop_price,
            tp_price=tp_price,
            partial_tp=partial_tp,
            atr_at_entry=atr,
            order_id=order_id,
        )
        logger.info(
            f"📈 OUVERTURE [{pair}] {side.upper()} "
            f"{quantity:.6f} @ {entry_price:.4f} | "
            f"SL: {stop_price:.4f} | TP partiel: {partial_tp:.4f} | TP final: {tp_price:.4f}"
        )
        return True

    # ─── TP Partiel ───────────────────────────────────────────────────────────

    def execute_partial_tp(self, pair: str, exit_price: float, order_id: str = "") -> dict | None:
        """
        Vend 50% de la position au TP partiel.
        Retourne le résumé du trade partiel.
        """
        pos = self.positions.get(pair)
        if pos is None or pos.partial_done:
            return None

        fee_pct     = getattr(config, "BINANCE_FEE_PCT", 0.001)
        qty_to_sell = pos.quantity * config.PARTIAL_TP_RATIO
        gross       = qty_to_sell * exit_price
        fee_out     = gross * fee_pct
        proceeds    = gross - fee_out
        self.quote_balance += proceeds

        pos.partial_done  = True
        pos.qty_remaining = pos.quantity - qty_to_sell

        # PnL net de frais (frais entree proportionnels + frais sortie)
        fee_in_partial = (qty_to_sell * pos.entry_price) * fee_pct
        pnl     = (exit_price - pos.entry_price) * qty_to_sell - fee_in_partial - fee_out
        pnl_pct = pnl / (pos.entry_price * qty_to_sell) * 100

        trade = {
            "pair":        pair,
            "side":        pos.side,
            "quantity":    round(qty_to_sell, 8),
            "entry_price": pos.entry_price,
            "exit_price":  exit_price,
            "pnl_usdt":    round(pnl, 4),
            "pnl_pct":     round(pnl_pct, 2),
            "reason":      "partial_tp",
            "opened_at":   pos.opened_at.isoformat(),
            "closed_at":   datetime.now(timezone.utc).isoformat(),
            "duration_min": round(
                (datetime.now(timezone.utc) - pos.opened_at).total_seconds() / 60, 1
            ),
            "order_id": order_id,
        }
        self.trade_history.append(trade)
        logger.info(
            f"🎯 TP PARTIEL [{pair}] sold {qty_to_sell:.6f} @ {exit_price:.4f} | "
            f"PnL: {pnl:+.4f} EUR | Reste: {pos.qty_remaining:.6f}"
        )
        return trade

    # ─── Fermeture complète ───────────────────────────────────────────────────

    def close_position(
        self,
        pair: str,
        exit_price: float,
        reason: str = "signal",
        order_id: str = "",
    ) -> dict | None:
        pos = self.positions.pop(pair, None)
        if pos is None:
            logger.warning(f"Aucune position ouverte sur {pair}")
            return None

        fee_pct  = getattr(config, "BINANCE_FEE_PCT", 0.001)
        gross    = pos.qty_remaining * exit_price
        fee_out  = gross * fee_pct
        proceeds = gross - fee_out
        self.quote_balance += proceeds

        # PnL net : frais entree (qty_remaining) + frais sortie
        fee_in_remaining = (pos.qty_remaining * pos.entry_price) * fee_pct
        pnl     = (exit_price - pos.entry_price) * pos.qty_remaining - fee_in_remaining - fee_out
        pnl_pct = pnl / (pos.entry_price * pos.qty_remaining) * 100

        # Si TP partiel déjà exécuté, ajouter le P&L de la première tranche
        total_pnl = pnl
        if pos.partial_done:
            partial_qty = pos.quantity * config.PARTIAL_TP_RATIO
            partial_pnl_trades = [
                t for t in self.trade_history
                if t["pair"] == pair and t["reason"] == "partial_tp"
            ]
            partial_pnl = sum(t["pnl_usdt"] for t in partial_pnl_trades)
            total_pnl = pnl + partial_pnl

        duration = datetime.now(timezone.utc) - pos.opened_at

        trade = {
            "pair":        pair,
            "side":        pos.side,
            "quantity":    round(pos.qty_remaining, 8),
            "entry_price": pos.entry_price,
            "exit_price":  exit_price,
            "pnl_usdt":    round(pnl, 4),
            "pnl_pct":     round(pnl_pct, 2),
            "total_pnl_usdt": round(total_pnl, 4),
            "reason":      reason,
            "opened_at":   pos.opened_at.isoformat(),
            "closed_at":   datetime.now(timezone.utc).isoformat(),
            "duration_min": round(duration.total_seconds() / 60, 1),
            "order_id":    order_id,
            "partial_done": pos.partial_done,
        }
        self.trade_history.append(trade)

        emoji = "✅" if pnl >= 0 else "❌"
        logger.info(
            f"{emoji} FERMETURE [{pair}] @ {exit_price:.4f} | "
            f"PnL: {pnl:+.4f} EUR ({pnl_pct:+.2f}%) | raison: {reason}"
        )
        return trade

    # ─── Mise à jour du stop (trailing) ──────────────────────────────────────

    def update_stop(self, pair: str, new_stop: float) -> None:
        """Met à jour le stop-loss d'une position (utilisé par le trailing stop)."""
        pos = self.positions.get(pair)
        if pos and new_stop > pos.stop_price:
            pos.stop_price = new_stop

    # ─── Checks ──────────────────────────────────────────────────────────────

    def has_position(self, pair: str) -> bool:
        return pair in self.positions

    def can_open_position(self) -> bool:
        return len(self.positions) < config.MAX_OPEN_POSITIONS

    def get_position(self, pair: str) -> Position | None:
        return self.positions.get(pair)

    # ─── Statistiques ────────────────────────────────────────────────────────

    def stats(self) -> dict:
        if not self.trade_history:
            return {"trades": 0}

        closed = [t for t in self.trade_history if t["reason"] != "partial_tp"]
        if not closed:
            return {"trades": 0}

        pnls   = [t["pnl_usdt"] for t in closed]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_pnl = sum(pnls)
        win_rate  = len(wins) / len(pnls) * 100 if pnls else 0
        avg_win   = sum(wins)   / len(wins)   if wins   else 0
        avg_loss  = sum(losses) / len(losses) if losses else 0
        pf_denom  = abs(sum(losses))
        profit_factor = abs(sum(wins)) / pf_denom if pf_denom > 0 else float("inf")

        # Expectancy = (win_rate × avg_win) + ((1 - win_rate) × avg_loss)
        win_r = win_rate / 100
        expectancy = win_r * avg_win + (1 - win_r) * avg_loss

        return {
            "trades":         len(pnls),
            "wins":           len(wins),
            "losses":         len(losses),
            "win_rate_pct":   round(win_rate, 1),
            "total_pnl":      round(total_pnl, 2),
            "avg_win":        round(avg_win, 4),
            "avg_loss":       round(avg_loss, 4),
            "profit_factor":  round(profit_factor, 2),
            "expectancy":     round(expectancy, 4),
            "balance":        round(self.quote_balance, 2),
            "roi_pct":        round(
                (self.quote_balance - self.initial_capital) / self.initial_capital * 100, 2
            ),
        }
