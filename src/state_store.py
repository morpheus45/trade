"""
Persistance de l'état du bot sur disque.

Sans ce module, un redémarrage (crash, reboot, coupure de courant, mise à jour)
fait oublier au bot ses positions ouvertes. En mode LIVE, cela signifie une
position réelle laissée sur l'exchange sans stop-loss ni take-profit — le bot
repart de zéro et ne la revend jamais.

L'état complet est écrit dans data/state.json à chaque changement, de façon
atomique (écriture dans un fichier temporaire puis remplacement), afin qu'une
coupure pendant l'écriture ne puisse pas corrompre le fichier.
"""
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import config

logger = logging.getLogger(__name__)

SCHEMA_VERSION    = 1
MAX_TRADE_HISTORY = 2000   # Garde les N derniers trades dans l'état persisté


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value):
    """Reconstruit un datetime depuis une chaîne ISO (tolérant)."""
    if isinstance(value, datetime):
        return value
    try:
        dt = datetime.fromisoformat(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


class StateStore:
    """
    Sauvegarde et restaure l'état complet du bot :
      - solde en quote + capital initial
      - positions ouvertes (avec stop, TP, TP partiel déjà exécuté)
      - historique des trades
      - trailing stops (plus haut atteint, stop courant, activé ou non)
      - circuit breaker (pic de capital, base journalière, déclenchement)
      - état de pause
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else (config.DATA_DIR / "state.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last_save = 0.0

    # ─── Écriture ────────────────────────────────────────────────────────────

    def _atomic_write(self, payload: dict) -> None:
        """
        Écrit le JSON dans un fichier temporaire du même dossier, force le flush
        sur le disque, puis remplace l'ancien fichier. os.replace est atomique
        sur Windows comme sur Linux : le fichier final est soit l'ancien, soit
        le nouveau, jamais un mélange des deux.
        """
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".state-", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except Exception:
            # Ne jamais laisser traîner un .tmp en cas d'échec
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def save(self, bot, force: bool = False, min_interval: float = 0.0) -> bool:
        """
        Capture l'état du bot et l'écrit sur disque.

        `min_interval` permet d'appeler save() à chaque tour de boucle sans
        écrire à chaque fois ; `force=True` ignore ce throttle (à utiliser après
        toute ouverture/fermeture de position).
        Retourne True si une écriture a eu lieu.
        """
        with self._lock:
            now = time.time()
            if not force and min_interval and (now - self._last_save) < min_interval:
                return False
            try:
                payload = self._snapshot(bot)
                self._atomic_write(payload)
                self._last_save = now
                return True
            except Exception as exc:
                logger.error(f"[StateStore] Échec de la sauvegarde : {exc}")
                return False

    def _snapshot(self, bot) -> dict:
        pf = bot.portfolio
        cb = bot.cb

        positions = {}
        for pair, pos in pf.positions.items():
            positions[pair] = {
                "pair":          pos.pair,
                "side":          pos.side,
                "quantity":      pos.quantity,
                "entry_price":   pos.entry_price,
                "stop_price":    pos.stop_price,
                "tp_price":      pos.tp_price,
                "partial_tp":    pos.partial_tp,
                "atr_at_entry":  pos.atr_at_entry,
                "opened_at":     pos.opened_at.isoformat(),
                "order_id":      pos.order_id,
                "partial_done":  pos.partial_done,
                "qty_remaining": pos.qty_remaining,
            }

        return {
            "schema":          SCHEMA_VERSION,
            "saved_at":        _utcnow_iso(),
            "paper_trading":   config.PAPER_TRADING,
            "quote_currency":  config.QUOTE_CURRENCY,
            "portfolio": {
                "initial_capital": pf.initial_capital,
                "quote_balance":   pf.quote_balance,
                "positions":       positions,
                "trade_history":   pf.trade_history[-MAX_TRADE_HISTORY:],
            },
            "trailing":        dict(bot.trailing._states),
            "circuit_breaker": {
                "initial_capital": cb.initial_capital,
                "peak_capital":    cb.peak_capital,
                "daily_start_cap": cb.daily_start_cap,
                "triggered":       cb._triggered,
                "reason":          cb._reason,
                "triggered_at":    cb._triggered_at,
            },
            "paused":          bot.is_paused(),
            "last_day":        bot._last_day,
            "last_stats_hour": bot._last_stats_hour,
        }

    # ─── Lecture ─────────────────────────────────────────────────────────────

    def load(self) -> dict | None:
        """Lit l'état sauvegardé. Retourne None si absent ou illisible."""
        if not self.path.exists():
            return None
        try:
            with self.path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            logger.error(f"[StateStore] Fichier d'état illisible ({exc}) — mis de côté.")
            self._quarantine()
            return None

        if not isinstance(data, dict) or "portfolio" not in data:
            logger.error("[StateStore] Fichier d'état invalide — mis de côté.")
            self._quarantine()
            return None

        schema = data.get("schema", 0)
        if schema > SCHEMA_VERSION:
            logger.warning(
                f"[StateStore] État écrit par une version plus récente "
                f"(schema {schema} > {SCHEMA_VERSION}) — chargement tenté quand même."
            )
        return data

    def _quarantine(self) -> None:
        """Déplace un état corrompu au lieu de l'écraser silencieusement."""
        try:
            backup = self.path.with_suffix(f".corrupt-{int(time.time())}.json")
            shutil.move(str(self.path), str(backup))
            logger.warning(f"[StateStore] État corrompu conservé ici : {backup.name}")
        except Exception:
            pass

    def restore(self, bot) -> bool:
        """
        Réinjecte l'état sauvegardé dans le bot.
        Retourne True si un état a été restauré.
        """
        data = self.load()
        if data is None:
            logger.info("[StateStore] Aucun état précédent — démarrage à neuf.")
            return False

        # Refuser de mélanger un état paper avec une session live (et inversement) :
        # les soldes n'ont pas la même signification.
        saved_paper = data.get("paper_trading")
        if saved_paper is not None and bool(saved_paper) != bool(config.PAPER_TRADING):
            logger.warning(
                f"[StateStore] État sauvegardé en mode "
                f"{'PAPER' if saved_paper else 'LIVE'} alors que le bot démarre en mode "
                f"{'PAPER' if config.PAPER_TRADING else 'LIVE'} — état ignoré."
            )
            self._quarantine()
            return False

        from portfolio_manager import Position

        pf = data.get("portfolio", {})
        bot.portfolio.initial_capital = float(pf.get("initial_capital", bot.portfolio.initial_capital))
        bot.portfolio.quote_balance   = float(pf.get("quote_balance", bot.portfolio.quote_balance))
        bot.portfolio.trade_history   = list(pf.get("trade_history", []))

        bot.portfolio.positions = {}
        for pair, raw in (pf.get("positions") or {}).items():
            try:
                bot.portfolio.positions[pair] = Position(
                    pair          = raw["pair"],
                    side          = raw["side"],
                    quantity      = float(raw["quantity"]),
                    entry_price   = float(raw["entry_price"]),
                    stop_price    = float(raw["stop_price"]),
                    tp_price      = float(raw["tp_price"]),
                    partial_tp    = float(raw["partial_tp"]),
                    atr_at_entry  = float(raw.get("atr_at_entry", 0.0)),
                    opened_at     = _parse_dt(raw.get("opened_at")),
                    order_id      = raw.get("order_id", ""),
                    partial_done  = bool(raw.get("partial_done", False)),
                    qty_remaining = float(raw.get("qty_remaining") or raw["quantity"]),
                )
            except Exception as exc:
                logger.error(f"[StateStore] Position {pair} illisible, ignorée : {exc}")

        bot.trailing._states = dict(data.get("trailing") or {})

        cb_data = data.get("circuit_breaker") or {}
        bot.cb.initial_capital = float(cb_data.get("initial_capital", bot.cb.initial_capital))
        bot.cb.peak_capital    = float(cb_data.get("peak_capital", bot.cb.peak_capital))
        bot.cb.daily_start_cap = float(cb_data.get("daily_start_cap", bot.cb.daily_start_cap))
        bot.cb._triggered      = bool(cb_data.get("triggered", False))
        bot.cb._reason         = cb_data.get("reason", "")
        bot.cb._triggered_at   = float(cb_data.get("triggered_at", 0.0))

        if data.get("last_day") is not None:
            bot._last_day = data["last_day"]
        if data.get("last_stats_hour") is not None:
            bot._last_stats_hour = data["last_stats_hour"]

        n_pos = len(bot.portfolio.positions)
        logger.info(
            f"[StateStore] État restauré (sauvegardé le {data.get('saved_at', '?')}) : "
            f"solde {bot.portfolio.quote_balance:.2f} {config.QUOTE_CURRENCY}, "
            f"{n_pos} position(s) ouverte(s), "
            f"{len(bot.portfolio.trade_history)} trade(s) en historique."
        )
        for pair, pos in bot.portfolio.positions.items():
            logger.info(
                f"[StateStore]   -> {pair} : {pos.qty_remaining:.6f} @ {pos.entry_price:.4f} "
                f"| SL {pos.stop_price:.4f} | TP {pos.tp_price:.4f}"
                f"{' | TP partiel deja pris' if pos.partial_done else ''}"
            )
        if bot.cb._triggered:
            logger.warning(f"[StateStore] Circuit breaker toujours actif : {bot.cb._reason}")
        return True
