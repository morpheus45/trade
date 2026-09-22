"""
Couche de decision Jev (TypeSafe AI) — https://docs.typesafe.ai

Pourquoi Jev ici plutot qu'un LLM
---------------------------------
Le pipeline d'entree demandait jusqu'ici a un LLM (Groq/Anthropic) d'ecrire un
texte d'analyse, que le code reparsait ensuite pour en extraire une decision.
Cela cumule trois defauts sur un bot de trading : plusieurs secondes de latence
par paire, un parsing fragile, et aucune mesure exploitable de l'incertitude.

Jev est un "System One model" : on lui envoie un etat et des questions typees,
il renvoie directement des valeurs structurees avec des probabilites calibrees
(~120 ms). Pas de generation de texte, pas de parsing.

Principes de conception suivis (doc TypeSafe)
--------------------------------------------
1. Questions atomiques, composees dans le code. Plutot qu'un unique
   "faut-il prendre ce trade ?", on pose des questions independantes et bien
   delimitees, puis on les combine avec une formule explicite ici. Quand les
   priorites changent, on modifie un coefficient — pas un prompt.
2. L'etat est du TEXTE. Jev n'accepte que chaines, objets JSON et tableaux de
   valeurs textuelles. Les indicateurs numeriques sont donc rendus en texte
   ANNOTE ("RSI 38.2 — below the 50 midline, approaching oversold at 35")
   plutot qu'en flottants bruts : le seuil compte autant que la valeur.
3. Les questions sont redigees en ANGLAIS. La langue d'entrainement principale
   de Jev est l'anglais ; les autres langues sont acceptees mais moins precises.
4. La confiance pilote l'action. Seuls Choice et Score renvoient `confidence`
   (pas Noul). En dessous du seuil, on s'abstient au lieu de deviner — les
   seuils sont d'autant plus hauts que l'enjeu est eleve, et ici il s'agit
   d'argent reel.
"""
import json
import logging
import time
from dataclasses import dataclass, field

import config

logger = logging.getLogger(__name__)

# Le SDK est optionnel : le bot doit rester installable et fonctionnel sans lui,
# exactement comme pour groq et anthropic.
try:
    from typesafe_sdk import (
        Choice,
        Noul,
        Score,
        TypeSafeAuthenticationError,
        TypeSafeBadRequestError,
        TypeSafeClient,
        TypeSafePermissionDeniedError,
        TypeSafeUnprocessableEntityError,
    )
    _SDK_AVAILABLE = True

    # Une cle invalide ou une requete malformee ne se repareront pas toutes
    # seules : inutile de reessayer toutes les 5 minutes. On distingue donc les
    # pannes permanentes des pannes transitoires (rate limit, timeout, 5xx).
    _PERMANENT_ERRORS = (
        TypeSafeAuthenticationError,
        TypeSafePermissionDeniedError,
        TypeSafeBadRequestError,
        TypeSafeUnprocessableEntityError,
    )
except ImportError:
    _SDK_AVAILABLE = False
    _PERMANENT_ERRORS = ()
    logger.debug("jev_decision : paquet 'typesafe-sdk' absent — couche Jev inactive")


# ─────────────────────────────────────────────────────────────────────────────
# Rendu des indicateurs en texte annote
# ─────────────────────────────────────────────────────────────────────────────

def _band(value: float, bands: list[tuple[float, str]], fmt: str = "{:.2f}") -> str:
    """
    Rend une valeur numerique accompagnee de sa lecture.

    Jev ne lit que du texte : "38.2" seul ne dit rien, "38.2 — below the 50
    midline, approaching oversold at 35" porte le seuil avec la valeur.
    `bands` est une liste (borne_haute, libelle) parcourue dans l'ordre.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "unavailable"
    for upper, label in bands:
        if v < upper:
            return f"{fmt.format(v)} — {label}"
    return f"{fmt.format(v)} — {bands[-1][1]}"


def _pct(value: float, label_low: str, label_high: str, pivot: float = 0.0) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "unavailable"
    side = label_high if v > pivot else label_low
    return f"{v*100:+.2f}% — {side}"


def build_state(
    pair: str,
    signal_score: float,
    indicators: dict,
    sentiment: dict | None = None,
    portfolio: dict | None = None,
) -> dict:
    """
    Construit l'etat soumis a Jev.

    La doc TypeSafe le formule ainsi : l'etat est le dossier qu'on presenterait
    a un panel d'experts avant de leur demander un avis. On y met donc le
    contexte et les faits — jamais la question elle-meme.
    """
    rsi = indicators.get("rsi", 50)
    adx = indicators.get("adx", 20)

    state: dict = {
        "instrument": f"{pair} spot on Binance",
        "proposed_action": (
            "Open a LONG (buy) spot position now, held until either a "
            "stop-loss or a take-profit level is reached."
        ),
        "timeframes": (
            "Entry signal computed on 1-hour candles; "
            "trend confirmation on 4-hour candles."
        ),
        "technical_signal": {
            "direction": "BUY",
            "indicator_agreement": (
                f"{signal_score:.0f} out of 5 entry indicators agree on a long"
            ),
        },
        "momentum": {
            "rsi_14": _band(rsi, [
                (30, "deeply oversold, often a reversal zone but also a falling knife"),
                (45, "below the 50 midline, leaning oversold"),
                (55, "neutral, no momentum edge either way"),
                (70, "above the midline, leaning overbought"),
                (101, "deeply overbought, stretched to the upside"),
            ], "{:.1f}"),
            "macd_histogram": _pct(
                indicators.get("macd_hist", 0),
                "negative, bearish momentum", "positive, bullish momentum",
            ),
            "rate_of_change_10": _pct(
                indicators.get("roc", 0) / 100 if abs(float(indicators.get("roc", 0) or 0)) > 1
                else indicators.get("roc", 0),
                "price declining over the last 10 candles",
                "price rising over the last 10 candles",
            ),
            "return_last_candle": _pct(
                indicators.get("return_1", 0), "down", "up"),
            "return_last_3_candles": _pct(
                indicators.get("return_3", 0), "down", "up"),
        },
        "trend": {
            "adx_14": _band(adx, [
                (15, "no directional trend, choppy range-bound market"),
                (25, "weak or forming trend, direction not established"),
                (40, "established directional trend"),
                (1000, "very strong trend, possibly climactic and late"),
            ], "{:.1f}"),
            "distance_to_fast_ema_9": _pct(
                indicators.get("dist_ema_fast", 0),
                "price below its short-term average", "price above its short-term average"),
            "distance_to_slow_ema_21": _pct(
                indicators.get("dist_ema_slow", 0),
                "price below its medium-term average", "price above its medium-term average"),
            "distance_to_trend_ema_200": _pct(
                indicators.get("dist_ema_trend", 0),
                "price below its long-term average, macro downtrend",
                "price above its long-term average, macro uptrend"),
        },
        "volatility_and_position": {
            "bollinger_band_position": _band(
                indicators.get("bb_position", 0.5), [
                    (0.1, "at or below the lower band, statistically stretched downward"),
                    (0.4, "in the lower half of the band"),
                    (0.6, "mid-band, no stretch"),
                    (0.9, "in the upper half of the band"),
                    (99, "at or above the upper band, statistically stretched upward"),
                ]),
            "atr_percent_of_price": _band(
                float(indicators.get("atr_pct", 0.01)) * 100, [
                    (0.5, "unusually quiet, moves may be too small to clear fees"),
                    (2.0, "normal volatility for crypto"),
                    (5.0, "elevated volatility, wider stops needed"),
                    (1000, "extreme volatility, stop-outs are likely"),
                ], "{:.2f}") + " of price",
            "vwap_deviation": _pct(
                indicators.get("vwap_dev", 0),
                "trading below the session volume-weighted average price",
                "trading above the session volume-weighted average price"),
        },
        "volume": {
            "volume_vs_20_candle_average": _band(
                indicators.get("volume_ratio", 1), [
                    (0.7, "well below average, move lacks participation"),
                    (1.2, "around average participation"),
                    (2.0, "above average, move is being confirmed by volume"),
                    (1000, "far above average, possible climax or news event"),
                ], "{:.2f}x"),
        },
        "costs": {
            "round_trip_fee": (
                f"{config.BINANCE_FEE_PCT * 2 * 100:.2f}% of notional in exchange fees "
                "on entry plus exit; a trade must clear this before it is profitable"
            ),
        },
    }

    if sentiment:
        state["market_sentiment"] = {
            "fear_and_greed_index": _band(
                sentiment.get("fng_value", 50), [
                    (25, "extreme fear, crowd is capitulating"),
                    (45, "fear"),
                    (55, "neutral"),
                    (75, "greed"),
                    (101, "extreme greed, crowd is euphoric and complacent"),
                ], "{:.0f}"),
            "assessed_regime": str(sentiment.get("regime", "unknown")),
            "assessed_direction": str(sentiment.get("sentiment", "neutral")),
            "summary": str(sentiment.get("summary", ""))[:600],
        }

    if portfolio:
        state["account_context"] = {
            "open_positions": (
                f"{portfolio.get('open_positions', 0)} of "
                f"{portfolio.get('max_positions', 1)} allowed slots currently used"
            ),
            "capital_at_risk_per_trade": (
                f"{config.RISK_PER_TRADE_PCT * 100:.1f}% of account equity"
            ),
            "recent_performance": str(portfolio.get("recent", "no recent trades")),
        }

    return state


# ─────────────────────────────────────────────────────────────────────────────
# Les questions
# ─────────────────────────────────────────────────────────────────────────────
# Chacune porte sur UN aspect que l'on peut trancher d'un coup d'oeil expert.
# La doc TypeSafe est explicite : si une question demande un raisonnement long
# ou pese plusieurs facteurs independants, il faut la decomposer. C'est pour
# cela qu'il n'y a pas de question "faut-il acheter ?" : cette synthese est
# faite plus bas, dans du code lisible et modifiable.

def build_questions() -> dict:
    """
    Les questions posees a Jev.

    Trois techniques de fiabilite documentees par TypeSafe sont appliquees ici :

    1. Questions atomiques. Chacune porte sur UN aspect tranchable d'un coup
       d'oeil. La doc en fait "le concept le plus important" : une question large
       cache plusieurs jugements derriere une seule reponse, une question etroite
       les expose et permet de les ponderer dans le code.

    2. Criteres contrastifs. Pour un Choice, chaque option est decrite par un
       OBJET avec les memes champs (`covers`, `not_for`, `example`), ce qui
       permet au modele de comparer les options terme a terme au lieu
       d'interpreter des phrases de longueurs differentes.

    3. References par chemin. Les questions designent les valeurs de l'etat par
       leur chemin entre backticks (`trend.adx_14`). La doc recommande
       explicitement cette notation pour lever l'ambiguite sur ce qui est juge.
    """
    return {
        # ── Contexte de marche ────────────────────────────────────────────────
        "regime": Choice(
            instructions=(
                "Using `trend.adx_14`, `trend.distance_to_trend_ema_200` and "
                "`momentum.rate_of_change_10`, which description best fits the "
                "current market structure of this instrument?"
            ),
            criteria={
                "uptrend": {
                    "covers": "A sustained move upward with higher highs; pullbacks are bought",
                    "not_for": "A single green candle inside an otherwise flat or falling market",
                    "example": "ADX above 25, price above its 200-period average, positive rate of change",
                },
                "downtrend": {
                    "covers": "A sustained move downward with lower lows; rallies are sold",
                    "not_for": "A brief dip inside an intact uptrend",
                    "example": "ADX above 25, price below its 200-period average, negative rate of change",
                },
                "range": {
                    "covers": "No clear direction; price oscillates between stable levels",
                    "not_for": "A market that is trending but pausing briefly",
                    "example": "ADX below 20, price oscillating around its averages",
                },
                "choppy": {
                    "covers": "Erratic high-volatility movement with no reliable structure",
                    "not_for": "An orderly range with clean boundaries",
                    "example": "Low ADX combined with unusually high ATR and conflicting indicators",
                },
            },
        ),

        # ── Qualite du setup ──────────────────────────────────────────────────
        "setup_quality": Score(
            instructions=(
                "Judging only the technical evidence in `momentum`, `trend`, "
                "`volatility_and_position` and `volume`, how well does this "
                "evidence support buying this instrument at the current price?"
            ),
            criteria=[
                "Poor — the evidence contradicts a long entry",
                "Weak — little supporting evidence, essentially a coin flip",
                "Fair — some support but notable conflicting signals",
                "Good — several independent indicators align on a long",
                "Excellent — trend, momentum and volume all confirm the entry",
            ],
        ),

        "entry_timing": Score(
            instructions=(
                "Ignoring whether the direction is right, judge from "
                "`volatility_and_position.bollinger_band_position`, "
                "`trend.distance_to_fast_ema_9` and `momentum.return_last_3_candles` "
                "how much of the move has already happened."
            ),
            criteria=[
                "Late — the move is already extended, most of it is behind us",
                "Acceptable — somewhat extended but not exhausted",
                "Early — the move appears to be starting, room remains",
            ],
        ),

        # ── Vetos ─────────────────────────────────────────────────────────────
        "trend_alignment": Noul(
            instructions=(
                "The short-term direction in `trend.distance_to_slow_ema_21` and "
                "the long-term direction in `trend.distance_to_trend_ema_200` "
                "both support a long position, rather than pointing opposite ways."
            ),
        ),

        "overextended": Noul(
            instructions=(
                "Judging from `volatility_and_position.bollinger_band_position` "
                "and `trend.distance_to_fast_ema_9`, price is stretched far "
                "enough above its recent averages that a pullback is a material "
                "near-term risk."
            ),
        ),

        "volume_confirms": Noul(
            instructions=(
                "`volume.volume_vs_20_candle_average` indicates that participation "
                "supports this move, rather than the move fading on thin volume."
            ),
        ),

        "elevated_risk": Noul(
            instructions=(
                "Conditions are unusually risky for opening a new position right "
                "now: extreme volatility in `volatility_and_position.atr_percent_of_price`, "
                "a possible climax move, or indicators that contradict each other."
            ),
        ),

        # ── Question de controle ──────────────────────────────────────────────
        # Formulee a l'envers de `setup_quality` a dessein. Si Jev juge le setup
        # bon ET juge aussi qu'il vaudrait mieux attendre, les deux reponses se
        # contredisent : le signal n'est pas fiable et on s'abstient. C'est une
        # verification de coherence interne, pas une question de plus.
        "better_to_wait": Noul(
            instructions=(
                "Taken as a whole, the evidence suggests that waiting for a "
                "clearer setup would be wiser than buying this instrument now."
            ),
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Verdict
# ─────────────────────────────────────────────────────────────────────────────

def _usage_dict(usage) -> dict:
    """`usage` est un modele pydantic (input_tokens / output_tokens), pas un dict."""
    if usage is None:
        return {}
    dump = getattr(usage, "model_dump", None)
    if callable(dump):
        try:
            return dump()
        except Exception:
            pass
    return {
        "input_tokens":  getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
    }


@dataclass
class JevVerdict:
    """
    Resultat compose, pret a etre consomme par le bot.

    `outcome` a trois etats et non deux :
        approve   les conditions sont reunies
        reject    une condition disqualifiante est etablie de facon fiable
        abstain   Jev n'est pas assez sur pour qu'on agisse sur sa reponse

    La distinction entre `reject` et `abstain` compte : le premier est une
    information exploitable, le second signale que le modele n'a rien d'utile a
    dire sur ce cas. Les confondre reviendrait a traiter « je ne sais pas »
    comme « non », et a croire le filtre plus informatif qu'il ne l'est.
    """
    available:       bool  = False   # Jev a-t-il repondu ?
    approved:        bool  = True    # raccourci : outcome == "approve"
    outcome:         str   = "approve"
    size_multiplier: float = 1.0     # modulation de la taille de position
    confidence:      float = 0.0     # confiance sur la qualite du setup
    regime:          str   = "unknown"
    setup_quality:   float = 0.0     # 0..4
    uncertain:       list  = field(default_factory=list)  # questions non fiables
    reasons:         list  = field(default_factory=list)
    latency_ms:      int   = 0
    usage:           dict  = field(default_factory=dict)
    samples:         int   = 1       # nombre d'appels ayant produit ce verdict

    def summary(self) -> str:
        etiquette = {"approve": "ACCEPTE", "reject": "REFUSE",
                     "abstain": "ABSTENTION"}.get(self.outcome, self.outcome)
        parts = [
            f"Jev {etiquette}",
            f"regime={self.regime}",
            f"qualite={self.setup_quality:.1f}/4 (conf {self.confidence:.0%})",
        ]
        if self.outcome == "approve":
            parts.append(f"taille x{self.size_multiplier:.2f}")
        if self.samples > 1:
            parts.append(f"{self.samples} tirages")
        parts.append(f"{self.latency_ms} ms")
        ligne = " | ".join(parts)
        return ligne + (" | " + " ; ".join(self.reasons) if self.reasons else "")


class JevDecider:
    """
    Interroge Jev et compose un verdict d'entree.

    Utilisation :
        jev = JevDecider()
        if jev.enabled:
            verdict = jev.evaluate_entry(pair, score, indicators, sentiment)
    """

    def __init__(self):
        self._client = None
        self._fail_count = 0
        self._disabled_until = 0.0

        if not config.JEV_API_KEY:
            logger.info("Jev : aucune cle TYPESAFE_API_KEY — couche desactivee")
            return
        if not _SDK_AVAILABLE:
            logger.warning(
                "Jev : TYPESAFE_API_KEY fournie mais le paquet 'typesafe-sdk' "
                "est absent. Lance : pip install typesafe-sdk"
            )
            return

        try:
            self._client = TypeSafeClient(api_key=config.JEV_API_KEY)
            logger.info(
                f"Jev : actif (modele {config.JEV_MODEL}, mode {config.JEV_MODE})"
            )
        except Exception as exc:
            logger.error(f"Jev : initialisation impossible ({exc}) — couche desactivee")
            self._client = None

    @property
    def enabled(self) -> bool:
        if self._client is None or config.JEV_MODE == "off":
            return False
        # Coupe-circuit : apres plusieurs echecs d'affilee, on cesse d'appeler
        # pendant un moment plutot que d'ajouter de la latence a chaque scan.
        if time.time() < self._disabled_until:
            return False
        return True

    # ─── Appel ───────────────────────────────────────────────────────────────

    def evaluate_entry(
        self,
        pair: str,
        signal_score: float,
        indicators: dict,
        sentiment: dict | None = None,
        portfolio: dict | None = None,
    ) -> JevVerdict:
        """
        Evalue une entree longue. Ne leve jamais : en cas de probleme, renvoie
        un verdict `available=False` que l'appelant traite selon JEV_ON_ERROR.

        Avec JEV_CONSENSUS_SAMPLES > 1, la meme question est posee plusieurs
        fois et l'accord est exige. TypeSafe mesure que Jev rejoue son label
        majoritaire ~90 % du temps sur les cas limites : rejouer la question est
        donc le moyen le plus direct de reperer les cas ou la reponse n'est pas
        stable. Un desaccord entre tirages vaut abstention — c'est precisement
        la situation ou il ne faut pas agir.
        """
        if not self.enabled:
            return JevVerdict(available=False, outcome="abstain",
                              reasons=["couche Jev inactive"])

        state = build_state(pair, signal_score, indicators, sentiment, portfolio)
        tirages = max(1, config.JEV_CONSENSUS_SAMPLES)

        verdicts: list[JevVerdict] = []
        for _ in range(tirages):
            v = self._evaluate_once(state)
            if not v.available:
                return v          # panne : inutile d'insister
            verdicts.append(v)

        verdict = verdicts[0] if tirages == 1 else self._consensus(verdicts)
        logger.info(f"[Jev] {pair} — {verdict.summary()}")
        return verdict

    def _evaluate_once(self, state: dict) -> JevVerdict:
        """Un appel, un verdict. Ne leve jamais."""
        started = time.perf_counter()
        try:
            response = self._client.system_one(
                state=state,
                questions=build_questions(),
                model=config.JEV_MODEL,
                # La boucle du bot tourne toutes les 30 s sur 8 paires : un appel
                # qui traine est pire qu'un appel qui echoue.
                timeout=config.JEV_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            self._register_failure(exc)
            return JevVerdict(
                available=False, outcome="abstain",
                reasons=[f"appel Jev echoue : {type(exc).__name__}"],
            )

        self._fail_count = 0
        latency_ms = int((time.perf_counter() - started) * 1000)

        try:
            return self._compose(response, latency_ms)
        except Exception as exc:
            logger.error(f"Jev : reponse inexploitable ({exc})")
            return JevVerdict(available=False, outcome="abstain",
                              reasons=["reponse Jev inexploitable"])

    @staticmethod
    def _consensus(verdicts: list[JevVerdict]) -> JevVerdict:
        """
        Fusionne plusieurs tirages.

        Regle : l'accord unanime est exige pour agir. Des tirages qui divergent
        signalent un cas limite, ou la reponse depend du tirage plutot que des
        donnees — le seul comportement defendable est de s'abstenir.

        Un `reject` unanime reste un `reject` : quand tous les tirages
        s'accordent pour ecarter le trade, l'information est fiable.
        """
        issues = {v.outcome for v in verdicts}
        base = verdicts[0]
        base.samples = len(verdicts)
        base.latency_ms = sum(v.latency_ms for v in verdicts)
        base.usage = {
            "input_tokens":  sum((v.usage or {}).get("input_tokens") or 0 for v in verdicts),
            "output_tokens": sum((v.usage or {}).get("output_tokens") or 0 for v in verdicts),
        }

        if len(issues) > 1:
            base.outcome  = "abstain"
            base.approved = False
            base.size_multiplier = 1.0
            base.reasons = [
                "tirages divergents : "
                + ", ".join(f"{v.outcome}" for v in verdicts)
                + " — reponse instable, abstention"
            ]
            return base

        # Accord unanime. Sur une acceptation, on retient la mise la plus
        # prudente des tirages plutot que la plus genereuse.
        if base.outcome == "approve":
            base.size_multiplier = min(v.size_multiplier for v in verdicts)
            base.confidence = min(v.confidence for v in verdicts)
            base.setup_quality = min(v.setup_quality for v in verdicts)
        base.reasons = list(base.reasons) + [f"{len(verdicts)} tirages concordants"]
        return base

    @staticmethod
    def _describe(exc: Exception) -> str:
        """
        Decrit une exception sans jamais lever.

        Formater une exception n'est pas toujours sur : le __str__ de certaines
        erreurs du SDK lit des attributs qui peuvent manquer. Une exception ici
        remonterait dans la boucle de scan et interromprait le tour complet.
        """
        try:
            return f"{type(exc).__name__}: {exc}"
        except Exception:
            return type(exc).__name__

    def _register_failure(self, exc: Exception) -> None:
        # Panne permanente : cle invalide, droits insuffisants, requete refusee.
        # Reessayer n'y changera rien — on coupe la couche et on le dit clairement.
        if _PERMANENT_ERRORS and isinstance(exc, _PERMANENT_ERRORS):
            logger.error(
                f"Jev : erreur permanente ({self._describe(exc)}). "
                f"Verifie TYPESAFE_API_KEY. Couche desactivee jusqu'au prochain "
                f"redemarrage ; le bot continue avec le pipeline habituel."
            )
            self._client = None
            return

        self._fail_count += 1
        logger.warning(f"Jev : appel echoue ({self._describe(exc)})")
        if self._fail_count >= config.JEV_MAX_FAILURES:
            self._disabled_until = time.time() + config.JEV_COOLDOWN_SECONDS
            self._fail_count = 0
            logger.error(
                f"Jev : {config.JEV_MAX_FAILURES} echecs consecutifs — "
                f"couche suspendue {config.JEV_COOLDOWN_SECONDS // 60} min. "
                f"Le bot continue avec le pipeline habituel."
            )

    # ─── Composition ─────────────────────────────────────────────────────────

    @staticmethod
    def _answers(response) -> dict:
        """
        Normalise l'acces aux reponses.

        Le SDK expose `response.answers[...]` ainsi que des collections typees
        (`nouls`, `choices`, `scores`). On passe par `answers`, qui couvre les
        trois primitives, avec repli sur les collections typees.
        """
        answers = getattr(response, "answers", None)
        if answers:
            return dict(answers)
        merged = {}
        for attr in ("choices", "scores", "nouls"):
            merged.update(dict(getattr(response, attr, {}) or {}))
        return merged

    # ─── Lecture fiable des reponses ─────────────────────────────────────────
    # TypeSafe mesure lui-meme que Jev n'est pas deterministe : sur un cas
    # limite, il rejoue son label majoritaire 90,8 % du temps et change d'avis
    # sur 2 questions sur 8. Le correctif qu'ils documentent et mesurent est de
    # ne PAS agir sur une reponse dont la masse de probabilite est trop diffuse :
    # avec un plancher de 0,60 sur la probabilite de l'option retenue, l'accord
    # entre executions passe a 99,2 %.
    #
    # Les trois lecteurs ci-dessous appliquent ce principe. Chacun renvoie
    # (valeur, fiable) ; une reponse non fiable ne sert JAMAIS a decider.

    @staticmethod
    def _read_choice(answers: dict, key: str) -> tuple[str, float, float, bool]:
        """(option, probabilite_de_l_option, confiance, fiable)"""
        item = answers.get(key)
        if item is None:
            return "unknown", 0.0, 0.0, False
        option = str(getattr(item, "choice", "unknown"))
        conf   = float(getattr(item, "confidence", 0.0) or 0.0)
        probs  = getattr(item, "probabilities", None) or {}
        try:
            top = float(probs.get(option, 0.0))
        except (TypeError, ValueError):
            top = 0.0
        fiable = top >= config.JEV_MIN_TOP_PROBABILITY
        return option, top, conf, fiable

    @staticmethod
    def _read_score(answers: dict, key: str) -> tuple[float, float, float, bool]:
        """(niveau, probabilite_du_niveau, confiance, fiable)"""
        item = answers.get(key)
        if item is None:
            return 0.0, 0.0, 0.0, False
        niveau = float(getattr(item, "score", 0.0) or 0.0)
        conf   = float(getattr(item, "confidence", 0.0) or 0.0)
        probs  = getattr(item, "probabilities", None) or {}
        top = 0.0
        # Les cles de `probabilities` sont des entiers cote SDK, des chaines
        # dans le JSON brut : on accepte les deux.
        for cle in (int(niveau), str(int(niveau)), niveau):
            if cle in probs:
                try:
                    top = float(probs[cle])
                except (TypeError, ValueError):
                    top = 0.0
                break
        fiable = (top >= config.JEV_MIN_TOP_PROBABILITY
                  and conf >= config.JEV_MIN_CONFIDENCE)
        return niveau, top, conf, fiable

    @staticmethod
    def _read_noul(answers: dict, key: str) -> tuple[float, bool]:
        """
        (valeur, fiable)

        Un Noul ne porte pas de `confidence` : c'est la valeur elle-meme qui
        exprime l'incertitude. La doc TypeSafe transforme explicitement la plage
        0,30–0,70 en resultat « incertain ». Une valeur dans cette bande ne dit
        ni oui ni non, et ne doit donc rien declencher.
        """
        item = answers.get(key)
        if item is None:
            return 0.5, False
        valeur = float(getattr(item, "noul", 0.5) or 0.5)
        fiable = not (config.JEV_NOUL_UNCERTAIN_LOW
                      <= valeur
                      <= config.JEV_NOUL_UNCERTAIN_HIGH)
        return valeur, fiable

    def _compose(self, response, latency_ms: int) -> JevVerdict:
        """
        Traduit les reponses en decision.

        Toute la politique de trading tient dans cette methode : c'est ici, et
        nulle part dans un prompt, qu'on ajuste la severite du filtre.

        Le resultat est a TROIS etats et non deux :
            approve   les conditions sont reunies
            reject    une condition disqualifiante est etablie de facon fiable
            abstain   Jev n'est pas assez sur pour qu'on agisse sur sa reponse

        `abstain` n'est pas un detail : c'est ce qui evite d'agir sur une
        reponse qui aurait pu basculer a l'execution suivante.
        """
        a = self._answers(response)

        regime, regime_top, regime_conf, regime_ok = self._read_choice(a, "regime")
        quality, quality_top, quality_conf, quality_ok = self._read_score(a, "setup_quality")
        timing, _, timing_conf, timing_ok = self._read_score(a, "entry_timing")

        aligned,      aligned_ok      = self._read_noul(a, "trend_alignment")
        overextended, overext_ok      = self._read_noul(a, "overextended")
        volume_ok_val, volume_ok      = self._read_noul(a, "volume_confirms")
        risky,        risky_ok        = self._read_noul(a, "elevated_risk")
        wait,         wait_ok         = self._read_noul(a, "better_to_wait")

        v = JevVerdict(
            available=True,
            regime=regime,
            setup_quality=quality,
            confidence=quality_conf,
            latency_ms=latency_ms,
            usage=_usage_dict(getattr(response, "usage", None)),
        )

        incertaines = [
            nom for nom, ok in (
                ("regime", regime_ok), ("setup_quality", quality_ok),
                ("entry_timing", timing_ok), ("trend_alignment", aligned_ok),
                ("overextended", overext_ok), ("volume_confirms", volume_ok),
                ("elevated_risk", risky_ok), ("better_to_wait", wait_ok),
            ) if not ok
        ]
        v.uncertain = incertaines
        raisons: list[str] = []

        # ── 1. Vetos : uniquement sur des reponses fiables ────────────────────
        # Un veto declenche par une reponse instable serait aussi arbitraire
        # qu'un trade pris sur une reponse instable.
        if regime_ok and regime in ("downtrend", "choppy"):
            v.outcome = "reject"
            raisons.append(
                f"regime {regime} (p={regime_top:.0%})"
            )
        elif overext_ok and overextended >= config.JEV_VETO_THRESHOLD:
            v.outcome = "reject"
            raisons.append(f"prix sur-etendu ({overextended:.0%})")
        elif risky_ok and risky >= config.JEV_VETO_THRESHOLD:
            v.outcome = "reject"
            raisons.append(f"conditions risquees ({risky:.0%})")
        elif aligned_ok and aligned <= config.JEV_NOUL_UNCERTAIN_LOW:
            v.outcome = "reject"
            raisons.append(f"tendances opposees ({aligned:.0%})")
        elif quality_ok and quality < config.JEV_MIN_QUALITY:
            v.outcome = "reject"
            raisons.append(f"qualite {quality:.1f} < {config.JEV_MIN_QUALITY}")

        if v.outcome == "reject":
            v.reasons = raisons
            v.approved = False
            return v

        # ── 2. Coherence interne ─────────────────────────────────────────────
        # `better_to_wait` est la meme question posee a l'envers. Si Jev juge le
        # setup bon et, dans le meme souffle, qu'il vaudrait mieux attendre, ses
        # deux reponses se contredisent : rien de fiable a en tirer.
        if (quality_ok and wait_ok
                and quality >= config.JEV_MIN_QUALITY
                and wait >= config.JEV_VETO_THRESHOLD):
            v.outcome = "abstain"
            v.approved = False
            v.reasons = [
                f"incoherence : qualite {quality:.1f}/4 mais « mieux vaut "
                f"attendre » a {wait:.0%}"
            ]
            return v

        # ── 3. Fondations indispensables ─────────────────────────────────────
        # Sans lecture fiable du regime ET de la qualite, il n'y a pas de
        # decision a prendre — seulement un tirage au sort.
        manquantes = [
            nom for nom, ok in (("regime", regime_ok), ("setup_quality", quality_ok))
            if not ok
        ]
        if manquantes:
            v.outcome = "abstain"
            v.approved = False
            v.reasons = [
                "reponse trop incertaine sur : " + ", ".join(manquantes)
                + f" (plancher de probabilite {config.JEV_MIN_TOP_PROBABILITY:.0%})"
            ]
            return v

        # ── 4. Budget d'incertitude ──────────────────────────────────────────
        # Quelques questions floues sont normales ; un tableau majoritairement
        # flou signifie que l'etat ne permet pas de trancher.
        if len(incertaines) > config.JEV_MAX_UNCERTAIN:
            v.outcome = "abstain"
            v.approved = False
            v.reasons = [
                f"{len(incertaines)} reponses incertaines sur 8 "
                f"(maximum tolere : {config.JEV_MAX_UNCERTAIN}) : "
                + ", ".join(incertaines)
            ]
            return v

        # ── 5. Confirmation positive exigee ──────────────────────────────────
        # L'absence de contradiction n'est pas une confirmation. On exige que
        # l'alignement des tendances soit affirme, pas seulement non infirme.
        if not (aligned_ok and aligned >= config.JEV_VETO_THRESHOLD):
            v.outcome = "abstain"
            v.approved = False
            v.reasons = [f"alignement des tendances non confirme ({aligned:.0%})"]
            return v

        # ── 6. Accepte : modulation de la taille ─────────────────────────────
        mult = 1.0
        mult *= 1.0 + config.JEV_SIZE_QUALITY_WEIGHT * (quality - config.JEV_MIN_QUALITY)
        if timing_ok:
            if timing >= 2.0:
                mult *= 1.10          # entree jugee precoce
            elif timing <= 0.0:
                mult *= 0.80          # entree jugee tardive
        if volume_ok:
            mult *= 1.05 if volume_ok_val >= config.JEV_VETO_THRESHOLD else 0.90
        # Une reponse fiable mais peu tranchee ne merite pas la mise maximale.
        mult *= min(1.0, quality_top / config.JEV_MIN_TOP_PROBABILITY)

        v.outcome = "approve"
        v.approved = True
        v.size_multiplier = round(
            max(config.JEV_SIZE_MIN, min(config.JEV_SIZE_MAX, mult)), 3
        )
        raisons.append(
            f"regime {regime} (p={regime_top:.0%}), qualite {quality:.1f}/4 "
            f"(p={quality_top:.0%})"
        )
        if incertaines:
            raisons.append("incertain sur : " + ", ".join(incertaines))
        v.reasons = raisons
        return v


# ─────────────────────────────────────────────────────────────────────────────
# Journal des decisions
# ─────────────────────────────────────────────────────────────────────────────

def log_decision(pair: str, verdict: JevVerdict, acted: bool, mode: str) -> None:
    """
    Trace chaque evaluation dans logs/jev_decisions.jsonl.

    C'est ce qui rend le mode observation exploitable : on peut comparer apres
    coup ce que Jev aurait filtre avec ce que le bot a reellement fait.
    """
    try:
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        row = {
            "ts":              time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "pair":            pair,
            "mode":            mode,
            "available":       verdict.available,
            "outcome":         verdict.outcome,
            "approved":        verdict.approved,
            "acted":           acted,
            "uncertain":       verdict.uncertain,
            "samples":         verdict.samples,
            "regime":          verdict.regime,
            "setup_quality":   verdict.setup_quality,
            "confidence":      verdict.confidence,
            "size_multiplier": verdict.size_multiplier,
            "latency_ms":      verdict.latency_ms,
            "reasons":         verdict.reasons,
            "usage":           verdict.usage,
        }
        with open(config.LOGS_DIR / "jev_decisions.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug(f"Jev : ecriture du journal impossible ({exc})")
