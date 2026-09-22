"""
Tests de la couche de decision Jev.

    python tests/test_jev.py

Aucun appel reseau, aucune cle API requise. Les reponses simulees sont
construites avec les VRAIS modeles pydantic du SDK (ChoiceAnswer, ScoreAnswer,
NoulAnswer, SystemOneResponse) : si TypeSafe change la forme de sa reponse, ces
tests cassent au lieu de valider un stub qui ne correspond plus a rien.

Ce qui est verifie :
  1. l'etat envoye a Jev ne contient que du texte (Jev n'accepte rien d'autre)
  2. les questions sont acceptees par le SDK
  3. la composition du verdict : vetos, plancher de confiance, taille
  4. la gestion des pannes : permanente vs transitoire, coupe-circuit
"""
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("DASHBOARD_PASSWORD", "motdepasse-de-test-1234")

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


try:
    from typesafe_sdk import (
        ChoiceAnswer, NoulAnswer, ScoreAnswer, SystemOneResponse, Usage,
    )
    SDK = True
except ImportError:
    SDK = False
    print("\n  typesafe-sdk absent — tests dependant du SDK ignores.")
    print("  Installe-le avec : pip install typesafe-sdk\n")

import config              # noqa: E402
import jev_decision        # noqa: E402
from jev_decision import JevDecider, build_questions, build_state  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Donnees de reference
# ─────────────────────────────────────────────────────────────────────────────

INDICATORS = {
    "rsi": 38.2, "macd_hist": 0.0021, "bb_position": 0.28,
    "dist_ema_fast": -0.004, "dist_ema_slow": 0.011, "dist_ema_trend": 0.085,
    "atr_pct": 0.018, "volume_ratio": 1.65, "adx": 28.4, "roc": 1.2,
    "vwap_dev": 0.003, "return_1": 0.004, "return_3": 0.019,
    "signal_score": 4,
}
SENTIMENT = {
    "sentiment": "bullish", "confidence": 0.7, "regime": "risk_on",
    "fng_value": 62, "summary": "Marche en phase d'appetit pour le risque.",
}


def _choice_probs(retenue: str, top: float, options=("uptrend", "downtrend", "range", "choppy")):
    """Distribution concentree sur l'option retenue, le reste reparti."""
    autres = [o for o in options if o != retenue]
    reste = max(0.0, 1.0 - top) / max(1, len(autres))
    d = {o: reste for o in autres}
    d[retenue] = top
    return d


def _score_probs(niveau: float, top: float, n: int):
    """Distribution concentree sur le niveau retenu."""
    i = int(niveau)
    autres = [k for k in range(n) if k != i]
    reste = max(0.0, 1.0 - top) / max(1, len(autres))
    d = {k: reste for k in autres}
    d[i] = top
    return d


def _answer_set(
    regime="uptrend", regime_top=0.85, regime_conf=0.82,
    quality=3.0, quality_top=0.78, quality_conf=0.78,
    timing=2.0, timing_top=0.72, timing_conf=0.70,
    aligned=0.95, overextended=0.05, volume=0.90, risky=0.05, wait=0.05,
):
    """
    Construit une reponse Jev plausible avec les vrais modeles du SDK.

    Les distributions de probabilite sont CONCENTREES sur la valeur retenue,
    comme dans une vraie reponse. Une distribution plate signifierait que le
    modele hesite — c'est precisement ce que le code doit refuser d'exploiter,
    et cela se teste explicitement plus bas.
    """
    return SystemOneResponse(
        model="jev-1.13.0",
        usage=Usage(input_tokens=640, output_tokens=71),
        answers={
            "regime": ChoiceAnswer(
                type="choice", choice=regime, confidence=regime_conf,
                probabilities=_choice_probs(regime, regime_top),
            ),
            "setup_quality": ScoreAnswer(
                type="score", score=quality, confidence=quality_conf,
                # Le modele pydantic du SDK attend des cles ENTIERES pour
                # les niveaux d'un Score, pas les chaines du JSON brut.
                legend={i: f"level {i}" for i in range(5)},
                probabilities=_score_probs(quality, quality_top, 5),
            ),
            "entry_timing": ScoreAnswer(
                type="score", score=timing, confidence=timing_conf,
                legend={0: "late", 1: "acceptable", 2: "early"},
                probabilities=_score_probs(timing, timing_top, 3),
            ),
            "trend_alignment": NoulAnswer(type="noul", noul=aligned),
            "overextended":    NoulAnswer(type="noul", noul=overextended),
            "volume_confirms": NoulAnswer(type="noul", noul=volume),
            "elevated_risk":   NoulAnswer(type="noul", noul=risky),
            "better_to_wait":  NoulAnswer(type="noul", noul=wait),
        },
    )


def _decider() -> JevDecider:
    """Un JevDecider dont le client est neutralise (aucun appel reseau)."""
    d = JevDecider.__new__(JevDecider)
    d._client = object()
    d._fail_count = 0
    d._disabled_until = 0.0
    return d


# ─────────────────────────────────────────────────────────────────────────────
print("\n1. Etat envoye a Jev")
# ─────────────────────────────────────────────────────────────────────────────

@test("l'etat ne contient que du texte")
def _():
    state = build_state("BTC/EUR", 4, INDICATORS, SENTIMENT,
                        {"open_positions": 0, "max_positions": 1, "recent": "n/a"})

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif not isinstance(node, str):
            raise AssertionError(
                f"{path} = {node!r} ({type(node).__name__}) : Jev n'accepte "
                f"que des valeurs textuelles"
            )

    walk(state)


@test("les valeurs numeriques sont annotees, pas brutes")
def _():
    state = build_state("BTC/EUR", 4, INDICATORS, SENTIMENT)
    rsi = state["momentum"]["rsi_14"]
    assert "38.2" in rsi, f"la valeur du RSI a disparu : {rsi}"
    assert "—" in rsi, f"le RSI n'est pas annote : {rsi}"
    adx = state["trend"]["adx_14"]
    assert "28.4" in adx and "trend" in adx.lower(), adx


@test("un indicateur manquant ne fait pas echouer la construction")
def _():
    state = build_state("ETH/EUR", 3, {}, None)
    assert isinstance(state, dict) and state["instrument"].startswith("ETH/EUR")


@test("les frais aller-retour sont transmis a Jev")
def _():
    state = build_state("BTC/EUR", 4, INDICATORS)
    fee = state["costs"]["round_trip_fee"]
    assert "%" in fee, fee


# ─────────────────────────────────────────────────────────────────────────────
print("\n2. Questions")
# ─────────────────────────────────────────────────────────────────────────────

@test("les questions sont valides pour le SDK")
def _():
    if not SDK:
        return
    from typesafe_sdk import Choice, Noul, Score
    q = build_questions()
    assert q, "aucune question definie"
    for nom, question in q.items():
        assert isinstance(question, (Choice, Score, Noul)), f"{nom} : type inattendu"
        assert question.instructions, f"{nom} : instructions vides"


@test("les questions sont redigees en anglais")
def _():
    # La langue d'entrainement principale de Jev est l'anglais ; du francais
    # degraderait la precision sans que rien ne le signale.
    accents = set("éèêëàâäîïôöûüçÉÈÀÂÎÔÛÇ")
    for nom, question in build_questions().items():
        texte = question.instructions
        fautifs = accents & set(texte)
        assert not fautifs, f"{nom} : caracteres accentues {fautifs} dans « {texte[:60]} »"


@test("chaque question porte sur un seul aspect")
def _():
    q = build_questions()
    for nom in ("regime", "setup_quality", "entry_timing", "trend_alignment",
                "overextended", "volume_confirms", "elevated_risk"):
        assert nom in q, f"question attendue absente : {nom}"


# ─────────────────────────────────────────────────────────────────────────────
print("\n3. Composition du verdict")
# ─────────────────────────────────────────────────────────────────────────────

@test("un bon setup est accepte et la mise augmentee")
def _():
    if not SDK:
        return
    v = _decider()._compose(_answer_set(), latency_ms=118)
    assert v.available and v.approved, f"refuse a tort : {v.reasons}"
    assert v.size_multiplier > 1.0, f"mise non augmentee : {v.size_multiplier}"
    assert v.regime == "uptrend"
    assert v.usage.get("input_tokens") == 640, v.usage


@test("une confiance insuffisante fait renoncer")
def _():
    if not SDK:
        return
    faible = config.JEV_MIN_CONFIDENCE - 0.15
    v = _decider()._compose(_answer_set(quality_conf=faible), latency_ms=100)
    assert not v.approved, "a agi malgre une confiance sous le plancher"
    # Une confiance insuffisante n'est pas un refus motive : c'est l'aveu que
    # le modele n'a rien de fiable a dire. D'ou une abstention, pas un rejet.
    assert v.outcome == "abstain", f"attendu abstain, recu {v.outcome}"
    assert "setup_quality" in " ".join(v.reasons), v.reasons


@test("un regime baissier oppose un veto")
def _():
    if not SDK:
        return
    v = _decider()._compose(
        _answer_set(regime="downtrend", regime_top=0.88), latency_ms=100)
    assert not v.approved, "a achete dans une tendance baissiere"
    assert v.outcome == "reject", f"attendu reject, recu {v.outcome}"
    assert any("downtrend" in r for r in v.reasons), v.reasons


@test("un prix sur-etendu oppose un veto")
def _():
    if not SDK:
        return
    v = _decider()._compose(_answer_set(overextended=0.85), latency_ms=100)
    assert not v.approved, "a achete un prix sur-etendu"


@test("des tendances non alignees opposent un veto")
def _():
    if not SDK:
        return
    v = _decider()._compose(_answer_set(aligned=0.1), latency_ms=100)
    assert not v.approved, "a achete malgre des tendances opposees"


@test("des conditions risquees opposent un veto")
def _():
    if not SDK:
        return
    v = _decider()._compose(_answer_set(risky=0.9), latency_ms=100)
    assert not v.approved, "a achete en conditions risquees"


@test("un setup mediocre est refuse")
def _():
    if not SDK:
        return
    v = _decider()._compose(_answer_set(quality=0.0), latency_ms=100)
    assert not v.approved, "a accepte un setup de qualite nulle"


@test("la mise reste dans ses bornes")
def _():
    if not SDK:
        return
    haut = _decider()._compose(
        _answer_set(quality=4.0, quality_conf=1.0, timing=2.0,
                    timing_conf=1.0, volume=1.0), latency_ms=100)
    assert haut.size_multiplier <= config.JEV_SIZE_MAX, haut.size_multiplier

    bas = _decider()._compose(
        _answer_set(quality=2.0, timing=0.0, timing_conf=1.0, volume=0.0),
        latency_ms=100)
    if bas.approved:
        assert bas.size_multiplier >= config.JEV_SIZE_MIN, bas.size_multiplier


@test("un trade refuse ne modifie jamais la mise")
def _():
    if not SDK:
        return
    v = _decider()._compose(_answer_set(regime="downtrend", regime_conf=0.9),
                            latency_ms=100)
    assert not v.approved
    assert v.size_multiplier == 1.0, (
        f"un refus a modifie la mise ({v.size_multiplier}) — sans effet ici, "
        f"mais dangereux si le veto sautait un jour"
    )


@test("une reponse incomplete ne fait pas crasher")
def _():
    if not SDK:
        return
    partielle = SystemOneResponse(
        model="jev-1.13.0",
        usage=Usage(input_tokens=10, output_tokens=2),
        answers={"regime": ChoiceAnswer(
            type="choice", choice="uptrend", confidence=0.9,
            probabilities={"uptrend": 0.9, "downtrend": 0.1})},
    )
    v = _decider()._compose(partielle, latency_ms=50)
    # Sans score de qualite, la confiance vaut 0 : le trade doit etre refuse.
    assert not v.approved, "a accepte un trade sans evaluation de qualite"


# ─────────────────────────────────────────────────────────────────────────────
print("\n4. Gestion des pannes")
# ─────────────────────────────────────────────────────────────────────────────

@test("une cle invalide desactive la couche sans reessayer")
def _():
    if not SDK:
        return
    import httpx2
    from typesafe_sdk import TypeSafeAuthenticationError
    d = _decider()
    exc = TypeSafeAuthenticationError(
        status=401,
        body={"error": "invalid api key"},
        headers=httpx2.Headers(),
        message="invalid api key",
    )
    d._register_failure(exc)
    assert d._client is None, "la couche reste active malgre une cle invalide"
    assert d._fail_count == 0, "une panne permanente ne doit pas incrementer le compteur"


@test("les pannes transitoires declenchent le coupe-circuit")
def _():
    d = _decider()
    for _ in range(config.JEV_MAX_FAILURES):
        d._register_failure(TimeoutError("reseau indisponible"))
    assert d._disabled_until > 0, "coupe-circuit non arme"
    assert not d.enabled, "la couche repond encore alors qu'elle est suspendue"


@test("une couche desactivee renvoie un verdict indisponible, sans lever")
def _():
    d = _decider()
    d._client = None
    v = d.evaluate_entry("BTC/EUR", 4, INDICATORS, SENTIMENT)
    assert v.available is False and v.approved is True, (
        "le verdict par defaut doit etre neutre : c'est l'appelant qui tranche "
        "selon JEV_ON_ERROR"
    )


@test("le journal des decisions s'ecrit sans lever")
def _():
    from jev_decision import JevVerdict
    jev_decision.log_decision(
        "BTC/EUR",
        JevVerdict(available=True, approved=True, regime="uptrend",
                   setup_quality=3.0, confidence=0.8, size_multiplier=1.2),
        acted=True, mode="shadow",
    )
    journal = config.LOGS_DIR / "jev_decisions.jsonl"
    assert journal.exists(), "journal non cree"
    import json
    derniere = json.loads(journal.read_text(encoding="utf-8").strip().split("\n")[-1])
    assert derniere["pair"] == "BTC/EUR" and derniere["mode"] == "shadow"



# ─────────────────────────────────────────────────────────────────────────────
print("\n5. Routage des modes (_entry_gate)")
# ─────────────────────────────────────────────────────────────────────────────
# C'est le code qui decide reellement si un ordre part. Il est teste ici sans
# reseau ni exchange : on reutilise la vraie methode de TradingBot, greffee sur
# un objet minimal.

class _FakeClaude:
    """Validation LLM simulee, avec compteur d'appels."""
    def __init__(self, verdict=True, raison="LLM OK"):
        self.verdict, self.raison, self.appels = verdict, raison, 0

    def validate_trade(self, *a, **k):
        self.appels += 1
        return self.verdict, self.raison


class _FakeJev:
    def __init__(self, verdict, enabled=True):
        self._verdict, self._enabled, self.appels = verdict, enabled, 0

    @property
    def enabled(self):
        return self._enabled

    def evaluate_entry(self, *a, **k):
        self.appels += 1
        return self._verdict


def _gate(mode, jev_verdict, llm_ok=True, on_error="fallback", jev_enabled=True):
    """Execute le vrai _entry_gate dans le mode demande."""
    from bot_trading import TradingBot
    from portfolio_manager import PortfolioManager

    ancien_mode, ancienne_err = config.JEV_MODE, config.JEV_ON_ERROR
    config.JEV_MODE, config.JEV_ON_ERROR = mode, on_error
    try:
        bot = object.__new__(TradingBot)
        bot.claude    = _FakeClaude(verdict=llm_ok)
        bot.jev       = _FakeJev(jev_verdict, enabled=jev_enabled)
        bot.portfolio = PortfolioManager(initial_capital=500.0)
        resultat = TradingBot._entry_gate(
            bot, "BTC/EUR", "BUY", 4, INDICATORS, SENTIMENT)
        return resultat, bot.claude, bot.jev
    finally:
        config.JEV_MODE, config.JEV_ON_ERROR = ancien_mode, ancienne_err


@test("mode off : Jev n'est jamais appele")
def _():
    from jev_decision import JevVerdict
    (ok, _, mult), claude, jev = _gate(
        "off", JevVerdict(available=True, approved=False))
    assert jev.appels == 0, "Jev a ete appele alors que le mode est off"
    assert claude.appels == 1 and ok and mult == 1.0


@test("mode shadow : Jev est appele mais ne change pas la decision")
def _():
    from jev_decision import JevVerdict
    refus = JevVerdict(available=True, approved=False, reasons=["test"],
                       size_multiplier=0.7)
    (ok, _, mult), claude, jev = _gate("shadow", refus, llm_ok=True)
    assert jev.appels == 1, "Jev n'a pas ete interroge en mode observation"
    assert ok is True, "le refus de Jev a modifie la decision en mode observation"
    assert mult == 1.0, "la mise a ete modifiee en mode observation"
    assert claude.appels == 1, "le pipeline habituel doit rester aux commandes"


@test("mode filter : Jev peut opposer son veto a un OK du LLM")
def _():
    from jev_decision import JevVerdict
    refus = JevVerdict(available=True, approved=False, reasons=["sur-etendu"])
    (ok, raison, mult), _, _ = _gate("filter", refus, llm_ok=True)
    assert ok is False, "le veto de Jev a ete ignore"
    assert "sur-etendu" in raison, raison
    assert mult == 1.0


@test("mode filter : un refus du LLM l'emporte meme si Jev accepte")
def _():
    from jev_decision import JevVerdict
    accord = JevVerdict(available=True, approved=True, size_multiplier=1.3)
    (ok, _, _), _, _ = _gate("filter", accord, llm_ok=False)
    assert ok is False, "le refus du LLM a ete ignore en mode filter"


@test("mode primary : Jev decide et le LLM n'est plus appele")
def _():
    from jev_decision import JevVerdict
    accord = JevVerdict(available=True, approved=True, size_multiplier=1.25)
    (ok, _, mult), claude, jev = _gate("primary", accord, llm_ok=False)
    assert ok is True, "la decision de Jev n'a pas ete suivie"
    assert mult == 1.25, f"le multiplicateur de taille est perdu : {mult}"
    assert claude.appels == 0, (
        "le LLM a ete appele en mode primary — c'est precisement la latence "
        "que ce mode doit supprimer"
    )


@test("Jev injoignable : repli sur le pipeline habituel")
def _():
    from jev_decision import JevVerdict
    panne = JevVerdict(available=False, reasons=["appel echoue"])
    (ok, raison, mult), claude, _ = _gate(
        "primary", panne, llm_ok=True, on_error="fallback")
    assert ok is True and claude.appels == 1, "pas de repli sur le LLM"
    assert "indisponible" in raison, raison
    assert mult == 1.0


@test("Jev injoignable avec JEV_ON_ERROR=skip : aucun trade")
def _():
    from jev_decision import JevVerdict
    panne = JevVerdict(available=False)
    (ok, raison, _), claude, _ = _gate(
        "primary", panne, llm_ok=True, on_error="skip")
    assert ok is False, "un trade est passe alors que Jev etait injoignable"
    assert claude.appels == 0
    assert "skip" in raison


@test("couche Jev desactivee : comportement identique au mode off")
def _():
    from jev_decision import JevVerdict
    (ok, _, mult), claude, jev = _gate(
        "primary", JevVerdict(available=True, approved=False), jev_enabled=False)
    assert jev.appels == 0 and claude.appels == 1
    assert ok is True and mult == 1.0



@test("mode filter : une abstention de Jev ne bloque pas le trade")
def _():
    from jev_decision import JevVerdict
    sans_avis = JevVerdict(available=True, approved=False, outcome="abstain",
                           reasons=["reponse trop incertaine"])
    (ok, raison, mult), claude, _ = _gate("filter", sans_avis, llm_ok=True)
    assert ok is True, (
        "une abstention a bloque un trade — une non-information n'est pas un veto"
    )
    assert claude.appels == 1 and mult == 1.0
    assert "sans avis" in raison, raison


@test("mode filter : un refus motive de Jev bloque bien le trade")
def _():
    from jev_decision import JevVerdict
    refus = JevVerdict(available=True, approved=False, outcome="reject",
                       reasons=["regime downtrend"])
    (ok, _, _), _, _ = _gate("filter", refus, llm_ok=True)
    assert ok is False, "un refus motive doit bloquer le trade"


@test("mode primary : une abstention replie sur le LLM")
def _():
    from jev_decision import JevVerdict
    sans_avis = JevVerdict(available=True, approved=False, outcome="abstain")
    (ok, raison, _), claude, _ = _gate(
        "primary", sans_avis, llm_ok=True, on_error="fallback")
    assert ok is True and claude.appels == 1, "pas de repli sur le LLM"
    assert "sans avis" in raison, raison


@test("mode primary : abstention + skip = aucun trade")
def _():
    from jev_decision import JevVerdict
    sans_avis = JevVerdict(available=True, approved=False, outcome="abstain")
    (ok, _, _), claude, _ = _gate(
        "primary", sans_avis, llm_ok=True, on_error="skip")
    assert ok is False, "un trade est passe malgre une abstention en mode skip"
    assert claude.appels == 0
# ─────────────────────────────────────────────────────────────────────────────
print("\n6. Fiabilite : ne pas agir sur une reponse instable")
# ─────────────────────────────────────────────────────────────────────────────
# TypeSafe mesure que Jev rejoue son label majoritaire ~90,8 % du temps sur un
# cas limite. Ces tests verifient que le code refuse d'exploiter une reponse
# dont la masse de probabilite est trop diffuse pour etre stable.

@test("une probabilite diffuse sur le regime provoque une abstention")
def _():
    if not SDK:
        return
    sous_seuil = config.JEV_MIN_TOP_PROBABILITY - 0.15
    v = _decider()._compose(_answer_set(regime_top=sous_seuil), latency_ms=100)
    assert v.outcome == "abstain", f"attendu abstain, recu {v.outcome} ({v.reasons})"
    assert not v.approved
    assert "regime" in " ".join(v.reasons), v.reasons


@test("une probabilite diffuse sur la qualite provoque une abstention")
def _():
    if not SDK:
        return
    sous_seuil = config.JEV_MIN_TOP_PROBABILITY - 0.20
    v = _decider()._compose(_answer_set(quality_top=sous_seuil), latency_ms=100)
    assert v.outcome == "abstain", f"attendu abstain, recu {v.outcome}"


@test("un veto ne se declenche pas sur une reponse non fiable")
def _():
    if not SDK:
        return
    # « downtrend » choisi, mais avec une probabilite trop faible pour etre sure.
    v = _decider()._compose(
        _answer_set(regime="downtrend", regime_top=0.35), latency_ms=100)
    assert v.outcome == "abstain", (
        f"un veto a ete oppose sur une reponse instable ({v.outcome}) — "
        f"aussi arbitraire qu'un trade pris sur une reponse instable"
    )


@test("un veto se declenche bien sur une reponse fiable")
def _():
    if not SDK:
        return
    v = _decider()._compose(
        _answer_set(regime="downtrend", regime_top=0.88), latency_ms=100)
    assert v.outcome == "reject", f"attendu reject, recu {v.outcome}"
    assert any("downtrend" in r for r in v.reasons), v.reasons


@test("un Noul dans la bande d'incertitude ne declenche rien")
def _():
    if not SDK:
        return
    milieu = (config.JEV_NOUL_UNCERTAIN_LOW + config.JEV_NOUL_UNCERTAIN_HIGH) / 2
    v = _decider()._compose(_answer_set(overextended=milieu), latency_ms=100)
    assert v.outcome != "reject", (
        "un Noul a 0.5 ne dit ni oui ni non et ne doit rien declencher"
    )
    assert "overextended" in v.uncertain, v.uncertain


@test("l'alignement des tendances doit etre confirme, pas seulement non infirme")
def _():
    if not SDK:
        return
    # Valeur dans la bande d'incertitude : ni confirme ni infirme.
    v = _decider()._compose(_answer_set(aligned=0.5), latency_ms=100)
    assert v.outcome == "abstain", (
        f"a trade sans confirmation d'alignement ({v.outcome}) — "
        f"l'absence de contradiction n'est pas une confirmation"
    )


@test("une contradiction interne provoque une abstention")
def _():
    if not SDK:
        return
    # Setup juge bon ET « mieux vaut attendre » juge vrai : incoherent.
    v = _decider()._compose(_answer_set(quality=4.0, wait=0.9), latency_ms=100)
    assert v.outcome == "abstain", f"attendu abstain, recu {v.outcome}"
    assert any("incoherence" in r for r in v.reasons), v.reasons


@test("trop de reponses incertaines provoquent une abstention")
def _():
    if not SDK:
        return
    flou = (config.JEV_NOUL_UNCERTAIN_LOW + config.JEV_NOUL_UNCERTAIN_HIGH) / 2
    v = _decider()._compose(
        _answer_set(aligned=flou, overextended=flou, volume=flou,
                    risky=flou, wait=flou, timing_top=0.30),
        latency_ms=100)
    assert v.outcome == "abstain", f"attendu abstain, recu {v.outcome}"


@test("une reponse franche reste acceptee")
def _():
    if not SDK:
        return
    v = _decider()._compose(_answer_set(), latency_ms=118)
    assert v.outcome == "approve", f"refuse a tort : {v.reasons}"
    assert v.size_multiplier > 1.0
    assert not v.uncertain, f"reponse franche jugee incertaine : {v.uncertain}"


# ─────────────────────────────────────────────────────────────────────────────
print("\n7. Fiabilite : accord entre tirages")
# ─────────────────────────────────────────────────────────────────────────────

@test("des tirages concordants produisent une decision")
def _():
    if not SDK:
        return
    from jev_decision import JevVerdict
    trois = [JevVerdict(available=True, approved=True, outcome="approve",
                        size_multiplier=m, confidence=0.8, setup_quality=3.0,
                        latency_ms=120)
             for m in (1.2, 1.3, 1.25)]
    v = JevDecider._consensus(trois)
    assert v.outcome == "approve"
    assert v.samples == 3
    assert v.size_multiplier == 1.2, (
        f"le consensus doit retenir la mise la plus prudente, pas {v.size_multiplier}"
    )
    assert v.latency_ms == 360


@test("des tirages divergents provoquent une abstention")
def _():
    if not SDK:
        return
    from jev_decision import JevVerdict
    melange = [
        JevVerdict(available=True, approved=True,  outcome="approve", latency_ms=120),
        JevVerdict(available=True, approved=False, outcome="reject",  latency_ms=120),
        JevVerdict(available=True, approved=True,  outcome="approve", latency_ms=120),
    ]
    v = JevDecider._consensus(melange)
    assert v.outcome == "abstain", (
        f"attendu abstain sur des tirages divergents, recu {v.outcome} — "
        f"c'est exactement le cas ou il ne faut pas agir"
    )
    assert not v.approved
    assert v.size_multiplier == 1.0


@test("un refus unanime reste un refus")
def _():
    if not SDK:
        return
    from jev_decision import JevVerdict
    refus = [JevVerdict(available=True, approved=False, outcome="reject",
                        reasons=["regime downtrend"], latency_ms=100)
             for _ in range(3)]
    v = JevDecider._consensus(refus)
    assert v.outcome == "reject", (
        "un refus unanime est une information fiable et doit le rester"
    )


@test("le consensus additionne la consommation de tokens")
def _():
    if not SDK:
        return
    from jev_decision import JevVerdict
    lot = [JevVerdict(available=True, approved=True, outcome="approve",
                      usage={"input_tokens": 640, "output_tokens": 70})
           for _ in range(3)]
    v = JevDecider._consensus(lot)
    assert v.usage["input_tokens"] == 1920, v.usage

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 62)
total = len(_passed) + len(_failed)
if _failed:
    print(f"  {len(_passed)}/{total} tests passes — {len(_failed)} ECHEC(S)")
    print("=" * 62)
    for nom, tb in _failed:
        print(f"\n--- {nom} ---\n{tb}")
    sys.exit(1)

print(f"  {total}/{total} tests passes")
print("=" * 62)
sys.exit(0)
