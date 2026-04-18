"""
GitHubReporter — pousse les statistiques du bot vers GitHub toutes les 15 minutes.
Le dashboard GitHub Pages (docs/data/stats.json) reste ainsi constamment à jour.
"""
import json
import logging
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

import config
from portfolio_manager import PortfolioManager

logger = logging.getLogger(__name__)

BOT_VERSION = "2.0"
MAX_EQUITY_POINTS = 500


class GitHubReporter:
    """
    Lit les données du PortfolioManager, génère docs/data/stats.json,
    puis effectue un git add / commit / push vers origin/main.
    Tourne dans un thread daemon séparé, toutes les `interval_seconds` secondes.
    """

    def __init__(
        self,
        repo_dir: Path,
        portfolio_manager: PortfolioManager,
        interval_seconds: int = 900,
    ):
        self.repo_dir = Path(repo_dir)
        self.pm = portfolio_manager
        self.interval = interval_seconds

        # Chemins des fichiers générés
        self.stats_path = self.repo_dir / "docs" / "data" / "stats.json"
        self.equity_path = self.repo_dir / "logs" / "equity_curve.json"

        # Cache partagé (mis à jour par le bot principal)
        self._last_prices: dict = {}
        self._last_claude_analysis: str = ""
        self._market_sentiment: str = "neutral"
        self._fng_value: int = 50
        self._activity_log: list = []

        # Chargement de la equity_curve persistée
        self._equity_curve: list = self._load_equity_curve()

        self._stop_event = threading.Event()

    # ─── Persistance equity curve ──────────────────────────────────────────────

    def _load_equity_curve(self) -> list:
        """Charge la equity_curve depuis le fichier JSON de persistance."""
        try:
            if self.equity_path.exists():
                with self.equity_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data[-MAX_EQUITY_POINTS:]
        except Exception as exc:
            logger.warning(f"Impossible de charger equity_curve.json : {exc}")
        return []

    def _save_equity_curve(self) -> None:
        """Persiste la equity_curve dans logs/equity_curve.json."""
        try:
            self.equity_path.parent.mkdir(parents=True, exist_ok=True)
            with self.equity_path.open("w", encoding="utf-8") as f:
                json.dump(self._equity_curve, f)
        except Exception as exc:
            logger.warning(f"Impossible de sauvegarder equity_curve.json : {exc}")

    def _append_equity_point(self, value: float) -> None:
        """Ajoute un point (ISO timestamp + valeur) à la equity_curve (max 500)."""
        self._equity_curve.append({
            "t": datetime.now(timezone.utc).isoformat(),
            "v": round(value, 2),
        })
        if len(self._equity_curve) > MAX_EQUITY_POINTS:
            self._equity_curve = self._equity_curve[-MAX_EQUITY_POINTS:]

    # ─── Mise à jour des données contextuelles ────────────────────────────────

    def update_context(
        self,
        prices: dict,
        claude_analysis: str = "",
        sentiment: str = "neutral",
        fng: int = 50,
        activity_entry: dict | None = None,
    ) -> None:
        """
        Appelé par le bot principal pour fournir les données de contexte
        (prix, analyse Claude, sentiment, Fear & Greed, activité récente).
        """
        self._last_prices = prices
        if claude_analysis:
            self._last_claude_analysis = claude_analysis
        self._market_sentiment = sentiment
        self._fng_value = fng
        if activity_entry:
            self._activity_log.append(activity_entry)
            self._activity_log = self._activity_log[-100:]  # max 100 entrées

    # ─── Construction du JSON ─────────────────────────────────────────────────

    def build_stats_json(self, prices: dict) -> dict:
        """
        Construit le dictionnaire complet pour docs/data/stats.json.
        Ce dictionnaire est ensuite sérialisé et commité vers GitHub.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        pm = self.pm

        # Valeur totale du portefeuille
        current_capital = round(pm.total_value(prices), 2)
        roi_pct = round(
            (current_capital - pm.initial_capital) / pm.initial_capital * 100, 2
        )

        # Statistiques issues du PortfolioManager
        raw_stats = pm.stats()

        # Positions ouvertes
        open_positions = []
        for pair, pos in pm.positions.items():
            price = prices.get(pair, pos.entry_price)
            pnl_usdt = round(pos.unrealized_pnl(price), 4)
            pnl_pct = round(pos.unrealized_pnl_pct(price), 2)
            duration_h = round(
                (datetime.now(timezone.utc) - pos.opened_at).total_seconds() / 3600, 2
            )
            # Trailing stop actif si le prix a dépassé le niveau d'activation
            trailing_active = False
            if pos.side == "buy":
                activation_price = pos.entry_price * (1 + config.TRAILING_STOP_ACTIVATION)
                trailing_active = price >= activation_price

            open_positions.append({
                "pair":           pair,
                "side":           pos.side,
                "entry_price":    pos.entry_price,
                "current_price":  round(price, 8),
                "pnl_usdt":       pnl_usdt,
                "pnl_pct":        pnl_pct,
                "quantity":       pos.quantity,
                "opened_at":      pos.opened_at.isoformat(),
                "duration_hours": duration_h,
                "stop_price":     pos.stop_price,
                "tp_price":       pos.tp_price,
                "trailing_active": trailing_active,
                "partial_done":   pos.partial_done,
            })

        # 20 trades récents (excluant les partial_tp internes si souhaité)
        recent_trades = []
        for t in reversed(pm.trade_history[-50:]):
            recent_trades.append({
                "date":        t.get("closed_at", now_iso),
                "pair":        t.get("pair", ""),
                "side":        t.get("side", ""),
                "entry":       t.get("entry_price", 0),
                "exit":        t.get("exit_price", 0),
                "pnl_usdt":    t.get("pnl_usdt", 0),
                "pnl_pct":     t.get("pnl_pct", 0),
                "reason":      t.get("reason", ""),
                "duration_min": t.get("duration_min", 0),
            })
            if len(recent_trades) >= 20:
                break

        # Performance par paire
        pairs_performance: dict[str, dict] = {}
        closed_trades = [t for t in pm.trade_history if t.get("reason") != "partial_tp"]
        for t in closed_trades:
            pair = t.get("pair", "")
            if pair not in pairs_performance:
                pairs_performance[pair] = {"trades": 0, "pnl": 0.0, "wins": 0}
            pairs_performance[pair]["trades"] += 1
            pairs_performance[pair]["pnl"] += t.get("pnl_usdt", 0)
            if t.get("pnl_usdt", 0) > 0:
                pairs_performance[pair]["wins"] += 1

        pairs_perf_out = {}
        for pair, data in pairs_performance.items():
            wr = round(data["wins"] / data["trades"] * 100, 1) if data["trades"] > 0 else 0
            pairs_perf_out[pair] = {
                "trades":   data["trades"],
                "pnl":      round(data["pnl"], 2),
                "win_rate": wr,
            }

        # Best / worst trade
        pnl_pcts = [t.get("pnl_pct", 0) for t in closed_trades]
        best_trade_pct  = round(max(pnl_pcts), 2)  if pnl_pcts else 0.0
        worst_trade_pct = round(min(pnl_pcts), 2)  if pnl_pcts else 0.0

        return {
            "updated_at":          now_iso,
            "mode":                "PAPER" if config.PAPER_TRADING else "LIVE",
            "bot_version":         BOT_VERSION,
            "initial_capital":     pm.initial_capital,
            "current_capital":     current_capital,
            "roi_pct":             roi_pct,
            "win_rate":            raw_stats.get("win_rate_pct", 0.0),
            "profit_factor":       raw_stats.get("profit_factor", 0.0),
            "total_trades":        raw_stats.get("trades", 0),
            "winning_trades":      raw_stats.get("wins", 0),
            "losing_trades":       raw_stats.get("losses", 0),
            "avg_win_usdt":        raw_stats.get("avg_win", 0.0),
            "avg_loss_usdt":       raw_stats.get("avg_loss", 0.0),
            "expectancy":          raw_stats.get("expectancy", 0.0),
            "best_trade_pct":      best_trade_pct,
            "worst_trade_pct":     worst_trade_pct,
            "open_positions":      open_positions,
            "recent_trades":       recent_trades,
            "equity_curve":        self._equity_curve,
            "pairs_performance":   pairs_perf_out,
            "last_claude_analysis": self._last_claude_analysis,
            "market_sentiment":    self._market_sentiment,
            "fng_value":           self._fng_value,
            "activity_log":        self._activity_log[-50:],
        }

    # ─── Git push ─────────────────────────────────────────────────────────────

    def push_to_github(self, prices: dict) -> bool:
        """
        1. Ajoute un point à la equity_curve et la persiste.
        2. Génère docs/data/stats.json.
        3. git add / commit / push → origin/main.
        Retourne True si le push réussit, False sinon (l'erreur est loguée).
        """
        try:
            # Equity curve — snapshot du capital actuel
            current_capital = self.pm.total_value(prices)
            self._append_equity_point(current_capital)
            self._save_equity_curve()

            # Génération du JSON
            stats = self.build_stats_json(prices)
            self.stats_path.parent.mkdir(parents=True, exist_ok=True)
            with self.stats_path.open("w", encoding="utf-8") as f:
                json.dump(stats, f, indent=2, ensure_ascii=False)

            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            commit_msg = f"📊 Auto-update stats [{timestamp}]"

            # git add
            subprocess.run(
                ["git", "add",
                 str(self.stats_path.relative_to(self.repo_dir)),
                 str(self.equity_path.relative_to(self.repo_dir))],
                cwd=str(self.repo_dir),
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )

            # git commit (--allow-empty-message au cas où rien n'a changé)
            result = subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=str(self.repo_dir),
                capture_output=True,
                text=True,
                timeout=30,
            )
            # Code 1 = "nothing to commit" — pas une vraie erreur
            if result.returncode not in (0, 1):
                logger.warning(f"git commit failed (rc={result.returncode}): {result.stderr.strip()}")
                return False

            if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
                logger.debug("github_reporter: aucune modification à commiter.")
                return True

            # git push
            push_result = subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=str(self.repo_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if push_result.returncode != 0:
                logger.error(
                    f"git push échoué (rc={push_result.returncode}): "
                    f"{push_result.stderr.strip()}"
                )
                return False

            logger.info(f"github_reporter: push OK — {timestamp}")
            return True

        except subprocess.TimeoutExpired:
            logger.error("github_reporter: timeout lors du git push.")
            return False
        except FileNotFoundError:
            logger.error("github_reporter: commande 'git' introuvable dans PATH.")
            return False
        except Exception as exc:
            logger.error(f"github_reporter: erreur inattendue — {exc}")
            return False

    # ─── Thread ───────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Boucle principale du thread : push toutes les `interval` secondes."""
        logger.info(
            f"github_reporter: démarré (intervalle={self.interval}s, "
            f"repo={self.repo_dir})"
        )
        while not self._stop_event.is_set():
            try:
                self.push_to_github(self._last_prices)
            except Exception as exc:
                # Sécurité ultime — le thread ne doit jamais crasher
                logger.error(f"github_reporter: erreur dans la boucle — {exc}")
            # Attente interruptible via l'event
            self._stop_event.wait(timeout=self.interval)

    def start(self) -> threading.Thread:
        """
        Démarre le thread de reporting en arrière-plan.
        Retourne l'objet Thread (daemon=True → il s'arrête avec le processus principal).
        """
        self._stop_event.clear()
        t = threading.Thread(target=self._run_loop, name="github_reporter", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        """Arrête proprement le thread de reporting."""
        self._stop_event.set()
