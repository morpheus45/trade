"""
Authentification du dashboard.

Le dashboard expose le capital, les positions, l'historique des trades, un chat
IA qui consomme des credits API, et des endpoints qui lancent des processus sur
la machine. Rien de tout cela ne doit etre accessible sans mot de passe des lors
que le dashboard est joignable depuis l'exterieur.

Mecanisme : mot de passe unique (DASHBOARD_PASSWORD), verifie en temps constant,
puis cookie de session signe par Flask. Les tentatives ratees sont ralenties par
IP pour rendre une attaque par force brute impraticable.
"""
import hashlib
import hmac
import logging
import os
import secrets
import time
from collections import defaultdict
from functools import wraps
from pathlib import Path

from flask import (
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

import config

logger = logging.getLogger(__name__)

SESSION_KEY = "authenticated"

#: Longueur minimale d'un jeton machine (32 caracteres aleatoires).
MIN_TOKEN_LENGTH = 32

# Anti-force-brute : au-dela de MAX_ATTEMPTS echecs dans FAIL_WINDOW secondes,
# l'IP est bloquee pendant LOCKOUT secondes.
MAX_ATTEMPTS = 5
FAIL_WINDOW  = 300
LOCKOUT      = 900

_failures: dict[str, list[float]] = defaultdict(list)
_locked:   dict[str, float] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Cle de signature des sessions
# ─────────────────────────────────────────────────────────────────────────────

def get_secret_key() -> bytes:
    """
    Cle utilisee pour signer les cookies de session.

    Prise dans FLASK_SECRET_KEY si fournie, sinon generee une fois et conservee
    dans data/secret_key. Le fichier persiste pour que les sessions ouvertes
    survivent a un redemarrage du bot.
    """
    env_key = os.getenv("FLASK_SECRET_KEY", "").strip()
    if env_key:
        return env_key.encode("utf-8")

    key_file = Path(config.DATA_DIR) / "secret_key"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if key_file.exists():
        data = key_file.read_bytes().strip()
        if len(data) >= 32:
            return data

    key = secrets.token_bytes(48)
    key_file.write_bytes(key)
    try:
        os.chmod(key_file, 0o600)   # sans effet sur Windows, utile sous Linux
    except OSError:
        pass
    logger.info("[auth] Nouvelle cle de session generee (data/secret_key).")
    return key


# ─────────────────────────────────────────────────────────────────────────────
# Verification du mot de passe
# ─────────────────────────────────────────────────────────────────────────────

def password_configured() -> bool:
    return bool(config.DASHBOARD_PASSWORD)


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def api_token_configured() -> bool:
    return bool(config.DASHBOARD_API_TOKEN)


def check_api_token(candidate: str) -> bool:
    """
    Verifie un jeton machine, en temps constant.

    Un jeton trop court serait devinable : on refuse plutot que d'offrir une
    authentification de facade a un programme qui se croit protege.
    """
    token = config.DASHBOARD_API_TOKEN
    if not token or len(token) < MIN_TOKEN_LENGTH:
        return False
    return hmac.compare_digest(_digest(candidate), _digest(token))


def _bearer_token() -> str:
    """Extrait le jeton de l'en-tete Authorization, vide s'il n'y en a pas."""
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    return header[len(prefix):].strip() if header.startswith(prefix) else ""


def check_password(candidate: str) -> bool:
    """
    Comparaison en temps constant : compare_digest ne s'arrete pas au premier
    caractere different, ce qui evite de reveler le mot de passe par la mesure
    du temps de reponse.
    """
    if not password_configured():
        return False
    return hmac.compare_digest(_digest(candidate), _digest(config.DASHBOARD_PASSWORD))


# ─────────────────────────────────────────────────────────────────────────────
# Limitation des tentatives
# ─────────────────────────────────────────────────────────────────────────────

def _client_ip() -> str:
    """
    IP du client. Derriere un reverse proxy (Cloudflare Tunnel, Caddy, nginx),
    l'IP reelle est dans X-Forwarded-For ; on ne lit cet en-tete que si
    TRUST_PROXY est active, sinon n'importe qui pourrait l'usurper pour
    contourner le blocage.
    """
    if config.TRUST_PROXY:
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.remote_addr or "inconnu"


def is_locked(ip: str) -> float:
    """Retourne le nombre de secondes de blocage restantes (0 si libre)."""
    until = _locked.get(ip, 0)
    remaining = until - time.time()
    if remaining <= 0:
        _locked.pop(ip, None)
        return 0
    return remaining


def record_failure(ip: str) -> None:
    now = time.time()
    attempts = [t for t in _failures[ip] if now - t < FAIL_WINDOW]
    attempts.append(now)
    _failures[ip] = attempts
    if len(attempts) >= MAX_ATTEMPTS:
        _locked[ip] = now + LOCKOUT
        _failures[ip] = []
        logger.warning(
            f"[auth] {len(attempts)} echecs de connexion depuis {ip} — "
            f"bloquee {LOCKOUT // 60} minutes."
        )


def record_success(ip: str) -> None:
    _failures.pop(ip, None)
    _locked.pop(ip, None)


# ─────────────────────────────────────────────────────────────────────────────
# Decorateur de protection
# ─────────────────────────────────────────────────────────────────────────────

def login_required(view):
    """
    Refuse l'acces tant que la session n'est pas authentifiee.
    Les appels API repondent 401 en JSON, les pages redirigent vers /login.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        if session.get(SESSION_KEY):
            return view(*args, **kwargs)

        # Clients machine : un jeton porteur ouvre les endpoints /api/, jamais
        # les pages. Un programme n'a pas besoin de l'interface, et limiter la
        # portee du jeton reduit ce qu'une fuite permettrait.
        if request.path.startswith("/api/") and api_token_configured():
            presente = _bearer_token()
            if presente:
                ip = _client_ip()
                if is_locked(ip):
                    return jsonify({"error": "Trop de tentatives"}), 429
                if check_api_token(presente):
                    return view(*args, **kwargs)
                # Un jeton errone est compte comme un echec : sans cela, le
                # blocage anti-force-brute ne couvrirait que le formulaire.
                record_failure(ip)
                return jsonify({"error": "Jeton invalide"}), 401

        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentification requise"}), 401
        return redirect(url_for("login", next=request.path))
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# Page de connexion
# ─────────────────────────────────────────────────────────────────────────────

LOGIN_PAGE = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Trading Bot</title>
  <style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100vh; display: grid; place-items: center;
      background: #0d1117; color: #e6edf3; padding: 24px;
      font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
    }
    form {
      width: 100%; max-width: 340px; background: #161b22;
      border: 1px solid #30363d; border-radius: 12px; padding: 28px;
    }
    h1 { margin: 0 0 4px; font-size: 19px; }
    p.sub { margin: 0 0 22px; color: #8b949e; font-size: 13px; }
    label { display: block; font-size: 13px; color: #8b949e; margin-bottom: 6px; }
    input {
      width: 100%; padding: 11px 12px; border-radius: 8px; font-size: 16px;
      background: #0d1117; border: 1px solid #30363d; color: #e6edf3;
    }
    input:focus { outline: 2px solid #1f6feb; outline-offset: -1px; border-color: #1f6feb; }
    button {
      width: 100%; margin-top: 16px; padding: 11px; border: 0; border-radius: 8px;
      background: #238636; color: #fff; font-size: 15px; font-weight: 600; cursor: pointer;
    }
    button:hover { background: #2ea043; }
    .err {
      margin-top: 16px; padding: 10px 12px; border-radius: 8px; font-size: 13px;
      background: #2d1214; border: 1px solid #6e2932; color: #ff9c9c;
    }
  </style>
</head>
<body>
  <form method="post" autocomplete="off">
    <h1>Trading Bot</h1>
    <p class="sub">Acces protege</p>
    <label for="pw">Mot de passe</label>
    <input id="pw" type="password" name="password" autofocus required>
    <button type="submit">Se connecter</button>
    {% if error %}<div class="err">{{ error }}</div>{% endif %}
  </form>
</body>
</html>
"""


def register(app) -> None:
    """Branche /login et /logout sur l'application Flask."""

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None

        if not password_configured():
            return (
                "<h1>Dashboard non configure</h1>"
                "<p>La variable <code>DASHBOARD_PASSWORD</code> n'est pas definie. "
                "Le dashboard refuse de s'ouvrir sans mot de passe.</p>",
                503,
            )

        if session.get(SESSION_KEY):
            return redirect(url_for("index"))

        if request.method == "POST":
            ip = _client_ip()
            remaining = is_locked(ip)
            if remaining:
                error = f"Trop de tentatives. Reessaye dans {int(remaining // 60) + 1} min."
            elif check_password(request.form.get("password", "")):
                session.clear()
                session[SESSION_KEY] = True
                session.permanent = True
                record_success(ip)
                logger.info(f"[auth] Connexion reussie depuis {ip}.")
                nxt = request.args.get("next", "")
                # N'accepter qu'un chemin interne : evite une redirection forcee
                # vers un site externe via ?next=https://...
                if nxt.startswith("/") and not nxt.startswith("//"):
                    return redirect(nxt)
                return redirect(url_for("index"))
            else:
                record_failure(ip)
                error = "Mot de passe incorrect."

        return render_template_string(LOGIN_PAGE, error=error), (401 if error else 200)

    @app.route("/logout", methods=["GET", "POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))
