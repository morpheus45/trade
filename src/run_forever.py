"""
Watchdog — garde le bot vivant 24/7.

Ce script lance bot_trading.py et dashboard.py en sous-processus.
Si l'un d'eux crashe, il le redémarre automatiquement apres un delai.

Usage :
    python run_forever.py
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

# ── Fix encodage Windows (cp1252 ne supporte pas les caracteres Unicode) ──────
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Empecher Windows de se mettre en veille (SetThreadExecutionState) ─────────
def _keep_awake_loop():
    """
    Appelle SetThreadExecutionState toutes les 30s pour signaler a Windows
    que le systeme est actif. Fonctionne sans droits administrateur.
    ES_CONTINUOUS (0x80000000) + ES_SYSTEM_REQUIRED (0x00000001) = 0x80000001
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ES_CONTINUOUS      = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        KEEP_AWAKE_FLAGS   = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        kernel32 = ctypes.windll.kernel32
        # Activer en permanence
        kernel32.SetThreadExecutionState(KEEP_AWAKE_FLAGS)
        while True:
            kernel32.SetThreadExecutionState(KEEP_AWAKE_FLAGS)
            time.sleep(30)
    except Exception as e:
        pass  # Ne jamais crasher le watchdog pour ca

_awake_thread = threading.Thread(target=_keep_awake_loop, daemon=True, name="keep-awake")
_awake_thread.start()

# ─── Config ───────────────────────────────────────────────────────────────────
SRC_DIR       = Path(__file__).parent
PYTHON        = sys.executable
BOT_SCRIPT    = SRC_DIR / "bot_trading.py"
DASH_SCRIPT   = SRC_DIR / "dashboard.py"
LOG_DIR       = SRC_DIR.parent / "logs"
WATCHDOG_LOG  = LOG_DIR / "watchdog.log"

RESTART_DELAY      = 15    # Secondes avant redemerrage apres crash
MAX_RESTARTS       = 20    # Nombre max de redemarrages
RESTART_WINDOW     = 300   # Fenetre en secondes
HEALTH_CHECK_EVERY = 60
DASHBOARD_PORT     = 5000

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
_processes  = {}


def _signal_handler(sig, frame):
    global _running
    logger.info("Signal recu -- arret propre de tous les processus...")
    _running = False
    for name, proc in _processes.items():
        if proc and proc.poll() is None:
            logger.info(f"Arret de {name} (PID {proc.pid})")
            proc.terminate()
    sys.exit(0)


signal.signal(signal.SIGINT,  _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def _open_log(log_file: Path):
    """Ouvre le fichier log en ecriture, avec retry si verrou Windows."""
    for attempt in range(5):
        try:
            return open(log_file, "a", encoding="utf-8", errors="replace", buffering=1)
        except PermissionError:
            logger.warning(f"Log {log_file.name} verrouille, attente {attempt+1}s...")
            time.sleep(attempt + 1)
    # Dernier recours : nouveau fichier horodate
    ts = time.strftime("%H%M%S")
    alt = log_file.parent / f"{log_file.stem}_{ts}.log"
    logger.warning(f"Utilisation fichier alternatif: {alt.name}")
    return open(alt, "a", encoding="utf-8", errors="replace", buffering=1)


def _start_process(name: str, script: Path) -> subprocess.Popen:
    """Lance un processus Python et retourne le handle."""
    log_file = LOG_DIR / f"{name}_stdout.log"
    log_fh   = _open_log(log_file)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"   # force utf-8 dans les sous-processus

    kwargs = dict(
        cwd=str(SRC_DIR),
        stdout=log_fh,
        stderr=log_fh,
        env=env,
    )
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen([PYTHON, str(script)], **kwargs)
    proc._log_fh = log_fh   # garde la reference pour fermeture propre
    logger.info(f"[{name}] Demarre (PID {proc.pid}) -> log: {log_file.name}")
    return proc


def _dashboard_healthy() -> bool:
    try:
        r = requests.get(f"http://127.0.0.1:{DASHBOARD_PORT}/api/data", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _watch(name: str, script: Path) -> None:
    restart_times = []
    restarts      = 0

    logger.info(f"[{name}] Surveillance demarree.")
    proc = _start_process(name, script)
    _processes[name] = proc

    while _running:
        ret = proc.poll()

        if ret is not None:
            # Fermer proprement le fichier log avant de rouvrir
            lh = getattr(proc, "_log_fh", None)
            if lh:
                try:
                    lh.flush()
                    lh.close()
                except Exception:
                    pass

            now = time.time()
            restart_times = [t for t in restart_times if now - t < RESTART_WINDOW]

            if len(restart_times) >= MAX_RESTARTS:
                logger.error(
                    f"[{name}] {MAX_RESTARTS} crashs en {RESTART_WINDOW}s. "
                    "Abandon. Verifier les logs."
                )
                break

            restarts += 1
            logger.warning(
                f"[{name}] Crash (code={ret}) -- redemerrage #{restarts} "
                f"dans {RESTART_DELAY}s..."
            )
            time.sleep(RESTART_DELAY)

            if not _running:
                break

            proc = _start_process(name, script)
            _processes[name] = proc
            restart_times.append(time.time())

        time.sleep(5)

    logger.info(f"[{name}] Surveillance terminee.")


def _health_monitor() -> None:
    while _running:
        time.sleep(HEALTH_CHECK_EVERY)
        parts = []
        for name, proc in _processes.items():
            if proc:
                parts.append(f"{name}={'UP' if proc.poll() is None else 'DOWN'}")
        parts.append(f"dashboard_api={'OK' if _dashboard_healthy() else 'KO'}")
        logger.info("Health: " + " | ".join(parts))


def main():
    logger.info("=" * 50)
    logger.info("  WATCHDOG demarre")
    logger.info(f"  Bot:       {BOT_SCRIPT.name}")
    logger.info(f"  Dashboard: {DASH_SCRIPT.name}")
    logger.info(f"  Restart auto: max {MAX_RESTARTS} en {RESTART_WINDOW}s")
    logger.info("=" * 50)

    threads = [
        threading.Thread(target=_watch, args=("bot",       BOT_SCRIPT),  daemon=True, name="watch-bot"),
        threading.Thread(target=_watch, args=("dashboard", DASH_SCRIPT), daemon=True, name="watch-dash"),
        threading.Thread(target=_health_monitor, daemon=True, name="health"),
    ]

    threads[0].start()
    time.sleep(3)
    threads[1].start()
    threads[2].start()

    logger.info("Tous les processus demarres. Ctrl+C pour arreter.")

    try:
        while _running:
            time.sleep(1)
    except KeyboardInterrupt:
        _signal_handler(None, None)


if __name__ == "__main__":
    main()
