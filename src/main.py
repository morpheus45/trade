"""
Point d'entree unique : bot de trading + dashboard dans un seul processus.

Pourquoi un seul processus ?

  1. Le dashboard lit les positions ouvertes directement dans l'objet du bot
     (`set_bot_instance`). Dans l'ancienne architecture a deux processus, cette
     reference restait vide : le dashboard affichait en permanence 0 position
     ouverte et un capital lu dans un CSV, jamais les donnees reelles.

  2. Bot et dashboard echangent aussi par fichiers (logs/trades.csv,
     logs/portfolio.csv). Deux services separes chez un hebergeur n'ont pas le
     meme disque : le dashboard n'aurait jamais rien affiche.

  3. Un seul processus se supervise trivialement : systemd, une tache planifiee
     Windows ou Docker le relancent en cas d'arret, sans watchdog maison.

Lancement :
    python src/main.py
"""
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

# Permet de lancer le fichier depuis la racine du depot comme depuis src/
sys.path.insert(0, str(Path(__file__).parent))

# Force l'UTF-8 en sortie : sous Windows, la console est en cp1252 et les
# messages contenant des accents ou des emojis feraient crasher le logger.
if sys.platform == "win32":
    import io
    for stream in ("stdout", "stderr"):
        buf = getattr(getattr(sys, stream), "buffer", None)
        if buf is not None:
            setattr(sys, stream, io.TextIOWrapper(buf, encoding="utf-8", errors="replace"))

import config                      # noqa: E402
from logger import setup_logging   # noqa: E402

setup_logging()
logger = logging.getLogger("main")

_shutdown = threading.Event()


def _keep_windows_awake() -> None:
    """
    Empeche Windows de se mettre en veille tant que le bot tourne.
    Une machine endormie ne surveille plus ses stop-loss.
    Sans effet (et sans erreur) sur les autres systemes.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ES_CONTINUOUS      = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        kernel32 = ctypes.windll.kernel32
        while not _shutdown.is_set():
            kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            _shutdown.wait(30)
    except Exception as exc:
        logger.debug(f"Maintien eveille indisponible : {exc}")


def _banner() -> None:
    mode = "PAPER (simulation)" if config.PAPER_TRADING else "LIVE (fonds reels)"
    logger.info("=" * 62)
    logger.info("  TRADING BOT — demarrage")
    logger.info(f"  Mode          : {mode}")
    logger.info(f"  Paires        : {', '.join(config.TRADE_PAIRS)}")
    logger.info(f"  Dashboard     : http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT}")
    logger.info(f"  Mot de passe  : {'defini' if config.DASHBOARD_PASSWORD else 'ABSENT'}")
    logger.info(f"  Etat persiste : {config.DATA_DIR / 'state.json'}")
    logger.info("=" * 62)


def main() -> int:
    _banner()

    import server
    # Verifie avant toute chose qu'on n'est pas sur le point d'exposer le
    # dashboard sans mot de passe. Sort en erreur si c'est le cas.
    server.check_exposure()

    import dashboard
    from bot_trading import TradingBot

    threading.Thread(target=_keep_windows_awake, daemon=True, name="keep-awake").start()

    # ── Le bot d'abord : s'il ne peut pas demarrer, inutile de servir une page ──
    try:
        bot = TradingBot()
    except SystemExit:
        raise
    except Exception:
        logger.exception("Le bot n'a pas pu demarrer.")
        return 1

    # Donne au dashboard l'acces direct au bot : positions, capital et etat de
    # pause deviennent enfin des donnees temps reel et non des CSV relus.
    dashboard.set_bot_instance(bot)

    def _serve() -> None:
        try:
            server.serve(dashboard.app)
        except Exception:
            logger.exception("Le dashboard s'est arrete.")
        # Un bot qui trade sans dashboard joignable trade en aveugle : plus
        # aucun moyen de voir ses positions ni de l'arreter depuis le telephone.
        # On arrete tout, et le superviseur (systemd, tache planifiee, Docker)
        # relance proprement.
        logger.error("Arret du bot : le dashboard n'est plus disponible.")
        _shutdown.set()
        import bot_trading
        bot_trading._RUNNING = False

    threading.Thread(target=_serve, daemon=True, name="dashboard").start()

    # Laisse au serveur le temps de se lier au port avant de lancer la boucle :
    # en cas d'echec de bind, on sort tout de suite au lieu de commencer a trader.
    time.sleep(3)
    if _shutdown.is_set():
        logger.error("Le dashboard n'a pas demarre — arret.")
        return 1

    # ── Arret propre : laisse le bot sauvegarder son etat avant de sortir ──────
    def _on_signal(signum, _frame):
        logger.info(f"Signal {signum} recu — arret en cours...")
        _shutdown.set()
        import bot_trading
        bot_trading._RUNNING = False

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass   # signal indisponible sur certaines plateformes

    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Interruption clavier.")
    finally:
        _shutdown.set()
        # Derniere sauvegarde, meme si bot.run() est sorti sur une exception.
        try:
            bot.state.save(bot, force=True)
            logger.info("Etat sauvegarde.")
        except Exception:
            logger.exception("Echec de la sauvegarde finale.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
