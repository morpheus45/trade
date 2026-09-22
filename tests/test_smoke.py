"""
Test de fumee — a lancer apres toute modification, avant de laisser tourner le bot.

    python tests/test_smoke.py

Ne passe aucun ordre, ne contacte ni Binance ni aucune API. Verifie :
  1. que tous les modules s'importent (un import mort a deja casse le bot)
  2. que l'etat survit a un redemarrage (positions, solde, historique)
  3. qu'un etat corrompu est mis de cote au lieu de faire crasher le bot
  4. que le dashboard refuse l'acces sans mot de passe
  5. que le bot refuse de s'exposer sur le reseau sans mot de passe
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Environnement de test isole : aucune cle reelle, aucun ordre possible.
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("DASHBOARD_PASSWORD", "motdepasse-de-test-1234")
os.environ.setdefault("BINANCE_API_KEY", "")
os.environ.setdefault("BINANCE_API_SECRET", "")

_passed: list[str] = []
_failed: list[tuple[str, str]] = []


def test(name):
    def decorator(fn):
        try:
            fn()
            _passed.append(name)
            print(f"  [OK]   {name}")
        except Exception:
            _failed.append((name, traceback.format_exc()))
            print(f"  [ECHEC] {name}")
        return fn
    return decorator


# ─────────────────────────────────────────────────────────────────────────────
print("\n1. Imports")
# ─────────────────────────────────────────────────────────────────────────────

@test("tous les modules du bot s'importent")
def _():
    import config          # noqa: F401
    import indicators      # noqa: F401
    import strategy        # noqa: F401
    import portfolio_manager  # noqa: F401
    import risk_management # noqa: F401
    import exchange        # noqa: F401
    import ai_model        # noqa: F401
    import claude_analysis # noqa: F401
    import telegram_alerts # noqa: F401
    import telegram_controller  # noqa: F401
    import state_store     # noqa: F401
    import auth            # noqa: F401
    import server          # noqa: F401
    import bot_trading     # noqa: F401
    import dashboard       # noqa: F401
    import main            # noqa: F401


# ─────────────────────────────────────────────────────────────────────────────
print("\n2. Persistance de l'etat")
# ─────────────────────────────────────────────────────────────────────────────

class _FakeBot:
    """Juste ce que StateStore manipule, sans toucher au reseau."""

    def __init__(self):
        from portfolio_manager import PortfolioManager
        from risk_management import CircuitBreaker, TrailingStopManager
        self.portfolio = PortfolioManager(initial_capital=500.0)
        self.trailing  = TrailingStopManager()
        self.cb        = CircuitBreaker(500.0)
        self._last_day = 14
        self._last_stats_hour = 9
        self._paused = False

    def is_paused(self):
        return self._paused


@test("une position ouverte survit a un redemarrage")
def _():
    from state_store import StateStore

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"

        # ── Session 1 : le bot ouvre une position puis « meurt » ──────────────
        bot = _FakeBot()
        bot.portfolio.open_position(
            pair="BTC/EUR", side="buy", quantity=0.002,
            entry_price=50000.0, stop_price=49000.0, tp_price=53000.0,
            partial_tp=51500.0, atr=700.0, order_id="test-1",
        )
        bot.trailing.init_position("BTC/EUR", 50000.0, 49000.0)
        balance_avant = bot.portfolio.quote_balance

        store = StateStore(path)
        assert store.save(bot, force=True), "la sauvegarde a echoue"

        # ── Session 2 : nouveau process, etat vierge ─────────────────────────
        bot2 = _FakeBot()
        assert not bot2.portfolio.positions, "le bot neuf devrait etre vide"
        assert StateStore(path).restore(bot2), "la restauration a echoue"

        pos = bot2.portfolio.positions.get("BTC/EUR")
        assert pos is not None, "la position ouverte a ete perdue au redemarrage"
        assert pos.entry_price == 50000.0, f"prix d'entree altere : {pos.entry_price}"
        assert pos.stop_price == 49000.0, f"stop-loss altere : {pos.stop_price}"
        assert pos.tp_price == 53000.0, f"take-profit altere : {pos.tp_price}"
        assert abs(pos.quantity - 0.002) < 1e-12, f"quantite alteree : {pos.quantity}"
        assert abs(bot2.portfolio.quote_balance - balance_avant) < 1e-9, \
            "le solde n'a pas ete restaure"
        assert "BTC/EUR" in bot2.trailing._states, "trailing stop perdu"


@test("l'historique des trades et le circuit breaker survivent")
def _():
    from state_store import StateStore

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        bot = _FakeBot()
        # 0.1 ETH a 2000 EUR = 200 EUR, soit moins que les 500 EUR de capital.
        opened = bot.portfolio.open_position(
            pair="ETH/EUR", side="buy", quantity=0.1,
            entry_price=2000.0, stop_price=1950.0, tp_price=2150.0,
            partial_tp=2075.0, atr=33.0,
        )
        assert opened, "la position de test n'a pas pu etre ouverte"
        trade = bot.portfolio.close_position("ETH/EUR", 2150.0, reason="take_profit")
        assert trade is not None, "la fermeture n'a renvoye aucun trade"
        bot.cb.peak_capital = 612.34
        bot.cb._triggered = True
        bot.cb._reason = "drawdown de test"

        StateStore(path).save(bot, force=True)

        bot2 = _FakeBot()
        StateStore(path).restore(bot2)
        assert len(bot2.portfolio.trade_history) == 1, "historique perdu"
        assert bot2.portfolio.trade_history[0]["reason"] == "take_profit"
        assert bot2.cb.peak_capital == 612.34, "pic de capital perdu"
        assert bot2.cb._triggered, "le circuit breaker declenche a ete oublie"


@test("un fichier d'etat corrompu ne fait pas crasher le bot")
def _():
    from state_store import StateStore

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        path.write_text("{ ceci n'est pas du JSON", encoding="utf-8")

        bot = _FakeBot()
        assert StateStore(path).restore(bot) is False, \
            "un etat corrompu ne doit pas etre declare restaure"
        assert not path.exists(), "l'etat corrompu aurait du etre mis de cote"
        quarantined = list(Path(tmp).glob("state.corrupt-*.json"))
        assert quarantined, "l'etat corrompu doit etre conserve pour analyse"


@test("un etat paper n'est pas charge dans une session live")
def _():
    import config
    from state_store import StateStore

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        bot = _FakeBot()
        StateStore(path).save(bot, force=True)   # ecrit avec PAPER_TRADING=true

        original = config.PAPER_TRADING
        try:
            config.PAPER_TRADING = False          # le bot redemarre en LIVE
            bot2 = _FakeBot()
            assert StateStore(path).restore(bot2) is False, \
                "un solde simule ne doit jamais etre repris en mode reel"
        finally:
            config.PAPER_TRADING = original


@test("l'ecriture de l'etat est atomique")
def _():
    from state_store import StateStore
    import json

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        store = StateStore(path)
        bot = _FakeBot()
        for _ in range(5):
            store.save(bot, force=True)
        # Aucun fichier temporaire ne doit subsister, et le JSON doit etre lisible.
        leftovers = list(Path(tmp).glob(".state-*.tmp"))
        assert not leftovers, f"fichiers temporaires abandonnes : {leftovers}"
        json.loads(path.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
print("\n3. Authentification du dashboard")
# ─────────────────────────────────────────────────────────────────────────────

@test("les endpoints sensibles renvoient 401 sans session")
def _():
    import dashboard
    dashboard.app.config["TESTING"] = True
    client = dashboard.app.test_client()

    for path in ("/api/data", "/api/chat/history", "/api/train/status"):
        r = client.get(path)
        assert r.status_code == 401, f"{path} a repondu {r.status_code} au lieu de 401"

    for path in ("/api/chat", "/api/update", "/api/train"):
        r = client.post(path)
        assert r.status_code == 401, f"{path} a repondu {r.status_code} au lieu de 401"


@test("la page d'accueil redirige vers /login sans session")
def _():
    import dashboard
    client = dashboard.app.test_client()
    r = client.get("/")
    assert r.status_code == 302, f"attendu 302, recu {r.status_code}"
    assert "/login" in r.headers.get("Location", ""), r.headers.get("Location")


@test("/healthz reste accessible sans mot de passe")
def _():
    import dashboard
    client = dashboard.app.test_client()
    r = client.get("/healthz")
    assert r.status_code == 200, f"attendu 200, recu {r.status_code}"
    body = r.get_json()
    # La sonde ne doit divulguer aucun chiffre financier.
    interdits = {"total_value", "initial_capital", "positions", "stats", "balance"}
    fuite = interdits & set(body)
    assert not fuite, f"/healthz expose des donnees financieres : {fuite}"


@test("un mauvais mot de passe est refuse, le bon ouvre la session")
def _():
    import dashboard
    client = dashboard.app.test_client()

    r = client.post("/login", data={"password": "mauvais"})
    assert r.status_code == 401, f"un mauvais mot de passe a repondu {r.status_code}"
    assert client.get("/api/data").status_code == 401, "session ouverte a tort"

    r = client.post("/login", data={"password": os.environ["DASHBOARD_PASSWORD"]})
    assert r.status_code == 302, f"le bon mot de passe a repondu {r.status_code}"
    assert client.get("/api/data").status_code == 200, "session non ouverte"

    client.get("/logout")
    assert client.get("/api/data").status_code == 401, "la deconnexion n'a pas ferme la session"


@test("la redirection apres connexion ne peut pas pointer vers un site externe")
def _():
    import dashboard
    client = dashboard.app.test_client()
    r = client.post("/login?next=https://exemple-malveillant.test/phishing",
                    data={"password": os.environ["DASHBOARD_PASSWORD"]})
    dest = r.headers.get("Location", "")
    assert "exemple-malveillant" not in dest, f"redirection externe acceptee : {dest}"


@test("les tentatives repetees finissent par etre bloquees")
def _():
    import auth
    auth._failures.clear()
    auth._locked.clear()
    import dashboard
    client = dashboard.app.test_client()

    for _ in range(auth.MAX_ATTEMPTS):
        client.post("/login", data={"password": "mauvais"})

    # Meme le bon mot de passe doit etre refuse pendant le blocage.
    r = client.post("/login", data={"password": os.environ["DASHBOARD_PASSWORD"]})
    assert r.status_code == 401, "l'IP aurait du etre bloquee apres les echecs"
    assert b"Trop de tentatives" in r.data, "message de blocage absent"

    auth._failures.clear()
    auth._locked.clear()


# ─────────────────────────────────────────────────────────────────────────────
print("\n4. Garde-fou d'exposition reseau")
# ─────────────────────────────────────────────────────────────────────────────

@test("refus de demarrer si expose sur le reseau sans mot de passe")
def _():
    import config
    import server

    pw, host = config.DASHBOARD_PASSWORD, config.DASHBOARD_HOST
    try:
        config.DASHBOARD_PASSWORD = ""
        config.DASHBOARD_HOST = "0.0.0.0"
        try:
            server.check_exposure()
        except SystemExit as exc:
            assert exc.code == 2, f"code de sortie inattendu : {exc.code}"
        else:
            raise AssertionError(
                "le dashboard accepterait de s'exposer sur 0.0.0.0 sans mot de passe"
            )

        # Sans mot de passe mais en local : autorise, avec un avertissement.
        config.DASHBOARD_HOST = "127.0.0.1"
        server.check_exposure()
    finally:
        config.DASHBOARD_PASSWORD, config.DASHBOARD_HOST = pw, host


@test("un mot de passe trop court est refuse")
def _():
    import config
    import server

    pw = config.DASHBOARD_PASSWORD
    try:
        config.DASHBOARD_PASSWORD = "court"
        try:
            server.check_exposure()
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("un mot de passe de 5 caracteres a ete accepte")
    finally:
        config.DASHBOARD_PASSWORD = pw


# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 62)
total = len(_passed) + len(_failed)
if _failed:
    print(f"  {len(_passed)}/{total} tests passes — {len(_failed)} ECHEC(S)")
    print("=" * 62)
    for name, tb in _failed:
        print(f"\n--- {name} ---\n{tb}")
    sys.exit(1)

print(f"  {total}/{total} tests passes")
print("=" * 62)
sys.exit(0)
