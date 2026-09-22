"""
Service HTTP du dashboard.

Le serveur de developpement de Flask (`app.run()`) n'est pas concu pour une
exposition permanente : mono-thread par defaut, pas de limite sur les
connexions, arret brutal sur exception. On utilise Waitress, un serveur WSGI de
production qui fonctionne a l'identique sous Windows et sous Linux (contrairement
a gunicorn, qui ne tourne pas sous Windows).

Ce module porte aussi le garde-fou principal du projet : refuser d'ecouter sur
une interface reseau si aucun mot de passe n'est configure.
"""
import ipaddress
import logging
import sys

import config

logger = logging.getLogger(__name__)


def _is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_exposure() -> None:
    """
    Verifie qu'on n'expose pas le dashboard sans authentification.

    Le dashboard donne acces au capital, aux positions, au chat IA (qui consomme
    des credits API) et a des endpoints qui lancent des processus. Ouvrir cela
    sur le reseau sans mot de passe reviendrait a le publier.
    Arrete le programme plutot que de demarrer dans cette configuration.
    """
    host = config.DASHBOARD_HOST

    if config.DASHBOARD_PASSWORD:
        if len(config.DASHBOARD_PASSWORD) < 8:
            logger.error(
                "DASHBOARD_PASSWORD fait moins de 8 caracteres. "
                "Choisis un mot de passe plus long."
            )
            sys.exit(2)
        return

    if _is_loopback(host):
        logger.warning(
            "DASHBOARD_PASSWORD non defini : le dashboard n'ecoute que sur "
            f"{host} (accessible uniquement depuis cette machine). "
            "Definis un mot de passe pour pouvoir y acceder depuis l'exterieur."
        )
        return

    logger.error(
        "=" * 70 + "\n"
        f"  REFUS DE DEMARRER : DASHBOARD_HOST={host} expose le dashboard sur le\n"
        "  reseau, mais DASHBOARD_PASSWORD n'est pas defini.\n\n"
        "  Sans mot de passe, n'importe qui pouvant joindre cette machine verrait\n"
        "  ton capital et tes positions, et pourrait utiliser tes credits API.\n\n"
        "  Corrige en definissant DASHBOARD_PASSWORD dans src/.env,\n"
        "  ou repasse DASHBOARD_HOST a 127.0.0.1.\n"
        + "=" * 70
    )
    sys.exit(2)


def serve(app, host: str | None = None, port: int | None = None) -> None:
    """Sert l'application WSGI (bloquant)."""
    host = host or config.DASHBOARD_HOST
    port = port or config.DASHBOARD_PORT
    check_exposure()

    try:
        from waitress import serve as waitress_serve
    except ImportError:
        logger.error(
            "waitress n'est pas installe. Lance : pip install -r requirements.txt"
        )
        sys.exit(2)

    logger.info(f"Dashboard en ecoute sur http://{host}:{port}")
    try:
        waitress_serve(
            app,
            host=host,
            port=port,
            threads=8,
            # Un client lent ou une connexion morte ne doit pas monopoliser un thread.
            channel_timeout=60,
            ident="trading-bot",
        )
    except OSError as exc:
        # Cas frequent sous Windows : le port 5000 est deja pris par un service
        # systeme (svchost, Hyper-V) ou par une ancienne instance du bot.
        # Le message brut de Windows ne dit pas quoi faire.
        detail = [
            "======================================================================",
            f"  Impossible d'ouvrir le port {port} sur {host}.",
            f"  Cause systeme : {exc}",
            "",
            "  Ce port est deja utilise par un autre programme.",
            "  Pour savoir lequel :",
            f"      Windows   netstat -ano | findstr :{port}",
            f"      Linux     sudo ss -lptn 'sport = :{port}'",
            "",
            "  Corrige en choisissant un autre port dans src/.env :",
            "      DASHBOARD_PORT=5057",
            "======================================================================",
        ]
        logger.error("\n" + "\n".join(detail))
        raise
