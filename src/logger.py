"""
Système de logs : texte (console + fichier) + CSV pour le dashboard.
"""
import csv
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
import config


def setup_logging() -> logging.Logger:
    """Configure le logger global et retourne le logger root."""
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = config.LOGS_DIR / "bot.log"

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    # Handler fichier
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    fh.setLevel(logging.DEBUG)

    # Handler console
    import sys, io
    _stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace") if hasattr(sys.stdout, "buffer") else sys.stdout
    ch = logging.StreamHandler(_stream)
    ch.setFormatter(formatter)
    ch.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(fh)
    root.addHandler(ch)

    return root


def log_trade(trade: dict) -> None:
    """
    Enregistre un trade fermé dans le fichier CSV.
    trade : dict retourné par PortfolioManager.close_position()
    """
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = config.TRADES_CSV.exists()

    with open(config.TRADES_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "pair", "side", "quantity", "entry_price", "exit_price",
            "pnl_usdt", "pnl_pct", "reason", "opened_at", "closed_at",
            "duration_min", "order_id"
        ])
        if not file_exists:
            writer.writeheader()
        writer.writerow(trade)


def log_portfolio_snapshot(balance: float, total_value: float, open_positions: int) -> None:
    """Enregistre un snapshot horaire du portefeuille."""
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = config.PORTFOLIO_CSV.exists()

    row = {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "balance_usdt":    round(balance, 4),
        "total_value":     round(total_value, 4),
        "open_positions":  open_positions,
    }

    with open(config.PORTFOLIO_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
