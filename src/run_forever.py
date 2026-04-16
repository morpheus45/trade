"""
Watchdog — garde le bot vivant 24/7.

Ce script lance bot_trading.py et dashboard.py en sous-processus.
Si l'un d'eux crashe, il le redémarre automatiquement après un délai.

Usage :
    python run_forever.py

C'est CE script qu'on configure en démarrage automatique Windows,
pas bot_trading.py directement.
"""
import subprocess
import sys
import time
import logging
import os
import signal
import threading
import requests
from pathlib import Path
from datetime import datetime

# ─── Config ──────────────────────────────────────────────────────────────────
SRC_DIR       = Path(__file__).parent
PYTHON        = sys.executable
BOT_SCRIPT    = SRC_DIR / "bot_trading.py"
DASH_SCRIPT   = SRC_DIR / "dashboard.py"
LOG_DIR       = SRC_DIR.parent / "logs"
WATCHDOG_LOG  = LOG_DIR / "watchdog.log"

RESTART_DELAY      = 10    # Secondes avant redémarrage après crash
MAX_RESTARTS       = 20    # Nombre max de redémarrages (évite boucle infinie)
RESTART_WINDOW     = 300   # Si MAX_RESTARTS en moins de 5 min → abandon
HEALTH_CHECK_EVERY = 60    # Vérification santé toutes les 60s
DASHBOARD_PORT     = 5000

# ─────────────────────────────────────────────────────────────────────────────

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] %(message)s",
    handlers=[
        logging.FileHandler(WATCHDOG_LOG, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

_running    = True
_processes  = {}   # name → subprocess.Popen


def _signal_handler(sig, frame):
    global _running
    logger.info("Signal reçu — arrêt propre de tous les processus...")
    _running = False
    for name, proc in _processes.items():
        if proc and proc.poll() is None:
            logger.info(f"Arrêt de {name} (PID {proc.pid})")
            proc.terminate()
    sys.exit(0)


signal.signal(signal.SIGINT,  _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def _start_process(name: str, script: Path) -> subprocess.Popen:
    """Lance un processus Python et retourne le handle."""
    log_file = LOG_DIR / f"{name}.log"
    log_fh   = open(log_file, "a", encoding="utf-8", buffering=1)

    proc = subprocess.Popen(
        [PYTHON, str(script)],
        cwd=str(SRC_DIR),
        stdout=log_fh,
        stderr=log_fh,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    logger.info(f"[{name}] Démarré (PID {proc.pid}) → log: {log_file}")
    return proc


def _dashboard_healthy() -> bool:
    """Vérifie que le dashboard répond sur /api/data."""
    try:
        r = requests.get(f"http://127.0.0.1:{DASHBOARD_PORT}/api/data", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _watch(name: str, script: Path) -> None:
    """
    Boucle de surveillance pour un processus.
    Tourne dans son propre thread.
    """
    restart_times = []
    restarts      = 0

    logger.info(f"[{name}] Surveillance démarrée.")
    proc = _start_process(name, script)
    _processes[name] = proc

    while _running:
        ret = proc.poll()

        if ret is not None:
            # Processus terminé
            now = time.time()
            restart_times = [t for t in restart_times if now - t < RESTART_WINDOW]

            if len(restart_times) >= MAX_RESTARTS:
                logger.error(
                    f"[{name}] {MAX_RESTARTS} crashs en {RESTART_WINDOW}s. "
                    "Abandon pour éviter une boucle infinie. Vérifie les logs."
                )
                break

            restarts += 1
            logger.warning(
                f"[{name}] Crashé (code={ret}) — redémarrage #{restarts} "
                f"dans {RESTART_DELAY}s..."
            )
            time.sleep(RESTART_DELAY)

            if not _running:
                break

            proc = _start_process(name, script)
            _processes[name] = proc
            restart_times.append(time.time())

        time.sleep(5)

    logger.info(f"[{name}] Surveillance terminée.")


def _health_monitor() -> None:
    """
    Moniteur de santé global — vérifie le dashboard et log l'état.
    """
    while _running:
        time.sleep(HEALTH_CHECK_EVERY)
        status_parts = []

        for name, proc in _processes.items():
            if proc:
                alive = proc.poll() is None
                status_parts.append(f"{name}={'UP' if alive else 'DOWN'}")

        dash_ok = _dashboard_healthy()
        status_parts.append(f"dashboard_api={'OK' if dash_ok else 'KO'}")

        logger.info("Health: " + " | ".join(status_parts))


def main():
    logger.info("=" * 50)
    logger.info("  WATCHDOG démarré")
    logger.info(f"  Bot: {BOT_SCRIPT}")
    logger.info(f"  Dashboard: {DASH_SCRIPT}")
    logger.info(f"  Redémarrage auto: oui (max {MAX_RESTARTS} en {RESTART_WINDOW}s)")
    logger.info("=" * 50)

    # Lancer les threads de surveillance
    threads = [
        threading.Thread(target=_watch, args=("bot",       BOT_SCRIPT),  daemon=True, name="watch-bot"),
        threading.Thread(target=_watch, args=("dashboard", DASH_SCRIPT), daemon=True, name="watch-dash"),
        threading.Thread(target=_health_monitor, daemon=True, name="health"),
    ]

    # Lancer le dashboard 3 secondes après le bot (attendre l'init)
    threads[0].start()
    time.sleep(3)
    threads[1].start()
    threads[2].start()

    logger.info("Tous les processus démarrés. Ctrl+C pour arrêter.")

    # Garder le processus principal vivant
    try:
        while _running:
            time.sleep(1)
    except KeyboardInterrupt:
        _signal_handler(None, None)


if __name__ == "__main__":
    main()
