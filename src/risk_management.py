"""
Gestion du risque avancée :
 - Calcul de la taille de position (risk fixe + sizing adaptatif)
 - Stop-loss basé sur ATR
 - Trailing stop manager (monte avec le prix, ne descend jamais)
 - Take-profit partiel (vend 50% au premier TP, laisse le reste traîler)
 - Circuit breaker journalier + drawdown
"""
import logging
import time
import config

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Taille de position
# ─────────────────────────────────────────────────────────────────────────────

def calculate_position_size(
    capital: float,
    entry_price: float,
    stop_price: float,
    risk_pct: float = None,
) -> float:
    """
    Taille de position basée sur le risque fixe.

    Formule : qty = (capital × risk_pct) / (entry - stop)

    Plafond de sécurité à 10% du capital en valeur totale.
    """
    risk_pct = risk_pct or config.RISK_PER_TRADE_PCT
    risk_amount = capital * risk_pct
    price_risk = abs(entry_price - stop_price)

    if price_risk <= 0:
        logger.error("Stop-loss identique au prix d'entrée — position ignorée")
        return 0.0

    qty = risk_amount / price_risk

    max_pct = getattr(config, "MAX_POSITION_PCT", 0.90)
    max_qty = (capital * max_pct) / entry_price
    if qty > max_qty:  # plafond MAX_POSITION_PCT
        logger.warning(f"Position reduite (plafond {max_pct*100:.0f}%)")
        qty = max_qty


    # Verification valeur minimale ordre Binance (refuse < MIN_ORDER_USDT)
    min_order = getattr(config, "MIN_ORDER_USDT", 5.0)
    notional   = qty * entry_price
    if notional < min_order:
        logger.warning(f"Ordre trop petit ({notional:.2f} USDT < {min_order} USDT) — ignore")
        return 0.0
    return qty


def calculate_adaptive_size(
    capital: float,
    entry_price: float,
    stop_price: float,
    ml_confidence: float,
    claude_validated: bool,
    signal_score: int,
) -> float:
    """
    Sizing adaptatif : augmente la mise quand tous les signaux convergent.

    Conditions pour taille maximale :
      - ML confidence ≥ ADAPTIVE_SIZE_ML_THRESHOLD
      - Claude a validé le trade
      - Score de signal ≥ 3 indicateurs concordants

    Retourne la quantité (avec risk_pct potentiellement augmenté).
    """
    risk_pct = config.RISK_PER_TRADE_PCT

    # Boost si ML confiant + Claude valide + signal fort
    if (ml_confidence >= config.ADAPTIVE_SIZE_ML_THRESHOLD
            and claude_validated
            and signal_score >= 3):
        risk_pct = min(risk_pct * config.ADAPTIVE_SIZE_FACTOR, config.ADAPTIVE_SIZE_MAX_PCT)
        logger.info(
            f"[Adaptive Sizing] Signal haute qualité "
            f"(ML={ml_confidence:.2f}, Claude=✅, score={signal_score}) "
            f"→ risk_pct={risk_pct*100:.2f}%"
        )

    return calculate_position_size(capital, entry_price, stop_price, risk_pct)


# ─────────────────────────────────────────────────────────────────────────────
# Prix stop / take-profit
# ─────────────────────────────────────────────────────────────────────────────

def calculate_stop_price(entry_price: float, atr: float, side: str) -> float:
    """Stop-loss basé sur ATR."""
    mult = config.STOP_LOSS_ATR_MULT
    if side == "buy":
        return entry_price - atr * mult
    return entry_price + atr * mult


def calculate_take_profit(entry_price: float, atr: float, side: str) -> float:
    """Take-profit complet basé sur ATR (R:R = 1:2)."""
    mult = config.TAKE_PROFIT_ATR_MULT
    if side == "buy":
        return entry_price + atr * mult
    return entry_price - atr * mult


def calculate_partial_tp(entry_price: float, atr: float, side: str) -> float:
    """Premier TP partiel (50% de la position) — à mi-chemin du TP final."""
    mult = config.PARTIAL_TP_ATR_MULT
    if side == "buy":
        return entry_price + atr * mult
    return entry_price - atr * mult


def should_stop_loss(current_price: float, stop_price: float, side: str) -> bool:
    if side == "buy":
        return current_price <= stop_price
    return current_price >= stop_price


def should_take_profit(current_price: float, tp_price: float, side: str) -> bool:
    if side == "buy":
        return current_price >= tp_price
    return current_price <= tp_price


# ─────────────────────────────────────────────────────────────────────────────
# Trailing Stop Manager
# ─────────────────────────────────────────────────────────────────────────────

class TrailingStopManager:
    """
    Gère les trailing stops pour les positions ouvertes.

    Logique :
    1. Position ouverte → stop initial basé sur ATR
    2. Dès que profit flottant ≥ TRAILING_STOP_ACTIVATION :
       → Activation du trailing
       → Stop remonte à (prix_courant × (1 - TRAILING_STOP_DISTANCE))
    3. Le stop monte avec le prix mais ne descend JAMAIS
    4. Permet de capturer des tendances longues tout en protégeant les gains
    """

    def __init__(self):
        # pair → {"active": bool, "highest_price": float, "trail_stop": float}
        self._states: dict[str, dict] = {}

    def init_position(self, pair: str, entry_price: float, initial_stop: float) -> None:
        """Initialise le trailing stop lors de l'ouverture d'une position."""
        self._states[pair] = {
            "active":        False,
            "highest_price": entry_price,
            "trail_stop":    initial_stop,
            "initial_stop":  initial_stop,
            "entry_price":   entry_price,
        }

    def update(self, pair: str, current_price: float) -> float:
        """
        Met à jour le trailing stop avec le prix courant.
        Retourne le nouveau niveau de stop.
        """
        state = self._states.get(pair)
        if state is None:
            return 0.0

        entry = state["entry_price"]
        profit_pct = (current_price - entry) / entry

        # Met à jour le plus haut atteint
        if current_price > state["highest_price"]:
            state["highest_price"] = current_price

        # Activation du trailing dès que profit ≥ seuil
        if not state["active"] and profit_pct >= config.TRAILING_STOP_ACTIVATION:
            state["active"] = True
            logger.info(
                f"[Trailing Stop] {pair} activé "
                f"(profit={profit_pct*100:.1f}%) "
                f"@ {current_price:.4f}"
            )

        # Si trailing actif : monter le stop avec le prix
        if state["active"]:
            new_trail = state["highest_price"] * (1 - config.TRAILING_STOP_DISTANCE)
            if new_trail > state["trail_stop"]:
                old_stop = state["trail_stop"]
                state["trail_stop"] = new_trail
                logger.debug(
                    f"[Trailing Stop] {pair} stop: {old_stop:.4f} → {new_trail:.4f} "
                    f"(highest={state['highest_price']:.4f})"
                )

        return state["trail_stop"]

    def get_stop(self, pair: str) -> float:
        """Retourne le stop courant (initial si trailing pas encore actif)."""
        state = self._states.get(pair)
        if state is None:
            return 0.0
        return state["trail_stop"]

    def is_active(self, pair: str) -> bool:
        state = self._states.get(pair)
        return state["active"] if state else False

    def remove(self, pair: str) -> None:
        """Supprime le trailing stop lors de la fermeture de position."""
        self._states.pop(pair, None)


# ─────────────────────────────────────────────────────────────────────────────
# Circuit Breaker
# ─────────────────────────────────────────────────────────────────────────────

class CircuitBreaker:
    """
    Protège le capital en stoppant le trading si :
     1. Perte journalière ≥ DAILY_LOSS_LIMIT_PCT (-5%)
     2. Drawdown depuis pic ≥ MAX_DRAWDOWN_PCT (-15%)
     3. Auto-reset après CB_AUTO_RESET_HOURS heures de pause
    """

    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.peak_capital    = initial_capital
        self.daily_start_cap = initial_capital
        self._triggered      = False
        self._reason         = ""
        self._triggered_at   = 0.0

    def update(self, current_capital: float) -> None:
        if current_capital > self.peak_capital:
            self.peak_capital = current_capital

    def reset_daily(self, current_capital: float) -> None:
        self.daily_start_cap = current_capital
        logger.info(f"[CircuitBreaker] Reset journalier — capital: {current_capital:.2f} USDT")

    def is_triggered(self, current_capital: float) -> bool:
        # Auto-reset après N heures
        if self._triggered:
            hours_since = (time.time() - self._triggered_at) / 3600
            if hours_since >= config.CB_AUTO_RESET_HOURS:
                logger.info(
                    f"[CircuitBreaker] Auto-reset après {hours_since:.1f}h de pause"
                )
                self.reset_trigger()
            else:
                return True

        # Perte journalière
        daily_loss = (self.daily_start_cap - current_capital) / max(self.daily_start_cap, 1)
        if daily_loss >= config.DAILY_LOSS_LIMIT_PCT:
            self._trigger(
                f"Perte journalière {daily_loss*100:.1f}% ≥ limite {config.DAILY_LOSS_LIMIT_PCT*100:.0f}%"
            )
            return True

        # Drawdown depuis pic
        drawdown = (self.peak_capital - current_capital) / max(self.peak_capital, 1)
        if drawdown >= config.MAX_DRAWDOWN_PCT:
            self._trigger(
                f"Drawdown {drawdown*100:.1f}% ≥ limite {config.MAX_DRAWDOWN_PCT*100:.0f}%"
            )
            return True

        return False

    def _trigger(self, reason: str) -> None:
        self._triggered    = True
        self._reason       = reason
        self._triggered_at = time.time()
        logger.error(f"🚨 CIRCUIT BREAKER: {reason}")

    @property
    def reason(self) -> str:
        return self._reason

    def reset_trigger(self) -> None:
        self._triggered = False
        self._reason    = ""
        logger.warning("[CircuitBreaker] Réinitialisation")
