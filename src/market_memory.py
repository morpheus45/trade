"""
market_memory.py
Système de mémoire persistante SQLite pour le bot de trading crypto.

- Stocke les observations et trades dans data/market_memory.db
- Pré-chargé avec une connaissance historique des marchés crypto
- Permet de retrouver des situations similaires passées
- Apprend des trades exécutés (quels setups ont marché)
"""

import sqlite3
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import config
    DATA_DIR = config.DATA_DIR
except Exception:
    DATA_DIR = Path(__file__).parent.parent / "data"

logger = logging.getLogger(__name__)

DB_PATH = DATA_DIR / "market_memory.db"

# ─── Données historiques ──────────────────────────────────────────────────────

HISTORICAL_EVENTS = [
    # (date, event, pair, impact_pct, type, lesson)
    ("2017-12-17", "ATH historique BTC $20k — premier grand cycle bull", "BTC/USDT", 1800.0, "ath", "Les ATH provoquent une euphorie extrême suivie d'un bear market prolongé"),
    ("2018-01-16", "Flash crash -65% BTC après ATH", "BTC/USDT", -65.0, "crash", "Après un ATH euphorique, consolidation violente quasi-systématique"),
    ("2018-12-15", "Bear bottom $3150 — capitulation finale 2018", "BTC/USDT", -84.0, "bear_bottom", "Les capitulations finales se font dans un désintérêt total des médias"),
    ("2019-06-26", "BTC rebond $13800 — recovery post-bear", "BTC/USDT", 340.0, "recovery", "Les recoveries post-bear sont rapides une fois la liquidité revenue"),
    ("2020-03-12", "COVID Black Thursday — BTC -48% en 24h", "BTC/USDT", -48.0, "black_swan", "Les black swans macro frappent crypto plus fort que TradFi, recovery rapide ensuite"),
    ("2020-03-13", "Rebond violent +25% après Black Thursday", "BTC/USDT", 25.0, "recovery", "Les overshoots baissiers dans un bull market sont des opportunités d'achat"),
    ("2020-05-11", "BTC Halving #3 — réduction récompense 12.5→6.25 BTC", "BTC/USDT", 5.0, "halving", "Le halving réduit l'offre — l'effet prix se ressent 6-12 mois après"),
    ("2020-09-01", "DeFi summer — ETH +150% en 2 mois", "ETH/USDT", 150.0, "altseason", "L'altseason DeFi précède souvent un bull run BTC plus large"),
    ("2020-10-01", "BTC break résistance $10k après 2 ans", "BTC/USDT", 8.0, "breakout", "La cassure d'une résistance majeure après accumulation longue est bullish"),
    ("2020-11-30", "BTC break ATH 2017 $20k — bull run confirmé", "BTC/USDT", 12.0, "ath_break", "La cassure d'un ATH historique ouvre la voie à une découverte de prix libre"),
    ("2020-12-16", "BTC ATH $20k brisé pour la première fois", "BTC/USDT", 15.0, "ath", "Première cassure ATH signale le début de la phase parabolique du cycle"),
    ("2021-01-08", "BTC $40k en 3 jours — accélération parabolique", "BTC/USDT", 40.0, "parabolic", "Les moves paraboliques sont difficiles à shorter — laisser courir les longs"),
    ("2021-02-08", "Tesla achète 1.5B$ BTC — adoption institutionnelle", "BTC/USDT", 20.0, "institutional", "L'adoption institutionnelle est un catalyseur de bull run multi-mois"),
    ("2021-03-13", "BTC $60k première fois", "BTC/USDT", 15.0, "ath", "Les multiples ATH successifs en peu de temps = phase euphorie avancée"),
    ("2021-04-14", "ATH $64k — Coinbase IPO au NASDAQ", "BTC/USDT", 10.0, "ath", "Coinbase IPO = top local probable — vendre la nouvelle"),
    ("2021-05-19", "China crypto ban — crash -50% en 3 semaines", "BTC/USDT", -50.0, "regulatory", "Les bans Chine sont devenus récurrents — impact diminue à chaque annonce"),
    ("2021-07-20", "BTC rebond $30k après correction -50%", "BTC/USDT", 30.0, "recovery", "Le rebond post-correction mid-cycle test souvent le support $30k"),
    ("2021-09-07", "El Salvador adopte BTC comme monnaie légale", "BTC/USDT", -12.0, "adoption", "Buy the rumor, sell the news — l'adoption d'un pays = top local"),
    ("2021-10-15", "ETF BTC Futures ProShares approuvé USA", "BTC/USDT", 8.0, "etf", "Approbation ETF Futures = signal positif mais pas autant que ETF Spot"),
    ("2021-11-10", "ATH historique BTC $69k — sommet cycle 2021", "BTC/USDT", 5.0, "ath", "L'ATH absolu du cycle se forme souvent sur un volume décroissant = divergence"),
    ("2021-11-25", "BTC -15% après ATH $69k — début retournement", "BTC/USDT", -15.0, "reversal", "La distribution après ATH s'accompagne de lower highs et volumes en baisse"),
    ("2022-01-21", "Krach crypto -40% en janvier — risk-off macro", "BTC/USDT", -40.0, "macro", "La corrélation BTC/Nasdaq monte en bear market — macro pilote"),
    ("2022-03-28", "Rebond $48k — recovery spring 2022", "BTC/USDT", 30.0, "recovery", "Les rebonds bear market peuvent être violents — ne pas confondre avec bull"),
    ("2022-05-09", "Terra/LUNA collapse -99% — contagion DeFi", "LUNA/USDT", -99.0, "collapse", "Les stablecoins algo sont des bombes — la contagion peut durer des semaines"),
    ("2022-05-12", "BTC sous $30k après contagion LUNA", "BTC/USDT", -25.0, "contagion", "La contagion d'un effondrement majeur se propage à tout l'écosystème"),
    ("2022-06-12", "Celsius suspend les retraits — contagion DeFi 2", "BTC/USDT", -15.0, "defi_contagion", "Les plateformes de yield insolvables amplifient les bear markets"),
    ("2022-06-18", "BTC sous $20k — support majeur 2017 ATH cassé", "BTC/USDT", -35.0, "support_break", "La perte du support ATH précédent confirme le bear market profond"),
    ("2022-07-13", "BTC bottom local $17600 — oversold extrême", "BTC/USDT", -5.0, "bottom", "Les bottoms locaux se forment sur RSI < 20 et capitulation volume"),
    ("2022-11-08", "FTX collapse annoncé — CZ vend ses FTT", "BTC/USDT", -18.0, "exchange_collapse", "La vente de FTT par Binance déclenche une bank run sur FTX"),
    ("2022-11-11", "FTX banqueroute déclarée — BTC -25% en 48h", "BTC/USDT", -25.0, "black_swan", "L'effondrement d'un exchange majeur est la pire contagion crypto possible"),
    ("2022-11-21", "BTC $15500 — bottom cycle bear 2022", "BTC/USDT", -12.0, "bear_bottom", "Le bottom FTX marque la capitulation finale du cycle bear 2022"),
    ("2023-01-01", "Bear bottom confirmé ~$15k — accumulation début", "BTC/USDT", 25.0, "accumulation", "Après la capitulation finale, l'accumulation silencieuse des baleines commence"),
    ("2023-03-10", "Silicon Valley Bank collapse — BTC rebond paradoxal", "BTC/USDT", 15.0, "macro", "La crise bancaire TradFi renforce la narrative Bitcoin comme refuge"),
    ("2023-03-28", "BTC $28k — recovery post-FTX accélère", "BTC/USDT", 70.0, "recovery", "Les recoveries post-capitulation sont parmi les plus rapides et violentes"),
    ("2023-06-15", "BlackRock dépose dossier ETF BTC Spot SEC", "BTC/USDT", 10.0, "etf_filing", "Le dossier BlackRock = signal fort d'intérêt institutionnel massif"),
    ("2023-10-23", "BTC $35k — rally anticipation ETF spot", "BTC/USDT", 30.0, "etf_anticipation", "Les anticipations ETF alimentent un rally multi-mois avant approbation"),
    ("2024-01-10", "ETF BTC Spot approuvé USA — 11 ETF simultanés", "BTC/USDT", 5.0, "etf_approval", "Buy the rumor sell the news — dip -15% juste après approbation puis recovery"),
    ("2024-02-28", "BTC $62k — cassure ATH 2021 anticipée", "BTC/USDT", 20.0, "ath_approach", "L'approche de l'ATH précédent s'accompagne d'une forte résistance"),
    ("2024-03-14", "Nouveau ATH $73k pre-halving", "BTC/USDT", 15.0, "ath", "ATH pre-halving inhabituel — indique une demande institutionnelle ETF massive"),
    ("2024-04-20", "BTC Halving #4 — récompense 6.25→3.125 BTC", "BTC/USDT", 2.0, "halving", "4ème halving — consolidation post-halving typique avant bull run"),
    ("2024-06-05", "BTC consolidation $60-70k post-halving", "BTC/USDT", -8.0, "consolidation", "La consolidation post-halving dure typiquement 6 mois avant l'explosion"),
    ("2024-08-05", "Flash crash global — Yen carry trade unwind, BTC -30%", "BTC/USDT", -30.0, "macro", "Les unwinds de carry trade Yen impactent durement les assets risqués"),
    ("2024-11-06", "Élection Trump — BTC +30% en 48h", "BTC/USDT", 30.0, "political", "Une administration crypto-friendly déclenche un rally majeur"),
    ("2024-12-05", "BTC $100k — franchissement niveau psychologique", "BTC/USDT", 8.0, "milestone", "Les niveaux psychologiques ronds sont des aimants de prix et zones de prise de profit"),
    ("2025-01-20", "Inauguration Trump — BTC ATH $109k", "BTC/USDT", 15.0, "ath", "Les événements politiques pro-crypto catalysent des ATH dans un bull market"),
    ("2025-02-03", "BTC correction -20% après ATH $109k", "BTC/USDT", -20.0, "correction", "Corrections -20% normales dans un bull market — opportunités d'achat"),
]

MARKET_WISDOM = [
    # (topic, knowledge, category)
    (
        "halving_cycle",
        "Les halvings BTC se produisent tous les ~4 ans (2012, 2016, 2020, 2024). "
        "Le cycle typique : accumulation pré-halving → halving → consolidation 6 mois → "
        "bull run 12-18 mois → ATH → bear market 2 ans. "
        "Historique des gains post-halving : 2012→8000%, 2016→2800%, 2020→700%, 2024→?. "
        "Ne jamais shorter BTC dans les 18 mois post-halving.",
        "cycle"
    ),
    (
        "fear_greed_interpretation",
        "Fear & Greed Index de 0 à 100. "
        "0-15 = Peur Extrême → signal d'achat fort (capitulation). "
        "15-30 = Peur → achat progressif (DCA). "
        "30-50 = Neutre → attendre confirmation. "
        "50-70 = Avidité → prudence, gérer le risque. "
        "70-85 = Avidité Forte → prendre des profits partiels. "
        "85-100 = Avidité Extrême → vendre massivement, top probable proche. "
        "Règle : acheter quand les autres ont peur, vendre quand les autres sont avides.",
        "sentiment"
    ),
    (
        "altcoin_season",
        "L'altseason démarre typiquement quand BTC.D (dominance) commence à baisser après un ATH BTC. "
        "Indicateurs : ETH/BTC en hausse, BTC.D < 45%, funding rates ETH positifs. "
        "Ordre typique : BTC ATH → ETH × 2-3 → Large Caps × 3-5 → Mid Caps × 5-10 → Small Caps ×10-50. "
        "L'altseason dure 2-4 mois et se termine toujours par un crash brutal. "
        "Stratégie : entrer sur la baisse BTC.D, sortir quand Fear&Greed > 85.",
        "altseason"
    ),
    (
        "funding_rates",
        "Les funding rates mesurent le coût de maintien des positions futures perpetuelles. "
        "Taux positif élevé (>0.1%) = longs surchargés = correction imminente possible. "
        "Taux négatif (<-0.05%) = shorts surchargés = short squeeze potentiel. "
        "Taux neutre (±0.01%) = marché équilibré. "
        "Funding rates très élevés (>0.2%) = signal de vente à court terme. "
        "Signal le plus fiable en combinaison avec RSI overbought et liquidités décroissantes.",
        "derivatives"
    ),
    (
        "volume_patterns",
        "Le volume valide les mouvements de prix. "
        "Règles clés : Volume faible avant breakout = coil → explosion à venir. "
        "Volume × 2-3 sur la cassure = confirmation forte. "
        "Prix monte + volume baisse = divergence baissière (distribution). "
        "Prix baisse + volume monte = capitulation (opportunité d'achat). "
        "Prix baisse + volume baisse = correction saine dans un bull. "
        "OBV (On Balance Volume) montant = accumulation institutionnelle.",
        "technical"
    ),
    (
        "bitcoin_dominance",
        "BTC.D = part de marché Bitcoin dans la capitalisation totale crypto. "
        "BTC.D monte → fuite des investisseurs vers BTC (risk-off crypto). "
        "BTC.D baisse → argent qui se déplace vers les alts (altseason). "
        "Niveaux clés : >60% = dominance forte BTC, 40-55% = zone mixte, <40% = altseason. "
        "Stratégie : acheter BTC quand BTC.D monte en bear, acheter alts quand BTC.D baisse en bull. "
        "Le pivot BTC.D est souvent l'un des meilleurs indicateurs d'altseason.",
        "dominance"
    ),
    (
        "support_resistance",
        "Les niveaux psychologiques ronds sont des zones de résistance/support majeures. "
        "Niveaux BTC critiques : $10k, $20k (ATH 2017), $30k, $40k, $50k, $60k, $69k (ATH 2021), $100k, $200k. "
        "La cassure d'une résistance la transforme en support (flip). "
        "Un retest du niveau cassé est souvent une opportunité d'entrée. "
        "Plus un niveau a été testé, plus sa cassure est significative. "
        "Les ATH précédents sont toujours des résistances majeures.",
        "technical"
    ),
    (
        "trend_following",
        "La tendance est ton amie — ne jamais trader contre la tendance macro. "
        "EMA 200 : prix au-dessus = bull trend, prix en-dessous = bear trend. "
        "Golden Cross (EMA50 croise EMA200 à la hausse) = signal bull fort. "
        "Death Cross (EMA50 croise EMA200 à la baisse) = signal bear fort. "
        "Dans un bull market : acheter les dips sur EMA50 ou EMA200. "
        "Dans un bear market : shorter les rallys sur EMA50 ou vendre les positions longues. "
        "L'ADX > 25 confirme une tendance établie, < 15 = marché sans direction.",
        "technical"
    ),
    (
        "bear_market_signs",
        "Signaux d'un bear market qui commence : "
        "1. Death Cross EMA50 / EMA200 sur chart hebdomadaire. "
        "2. Volume en baisse sur les rallys, en hausse sur les baisses. "
        "3. OBV en tendance baissière persistante (distribution). "
        "4. BTC casse support majeur avec volume (ex: ATH précédent). "
        "5. Fear & Greed reste < 30 pendant 2+ semaines. "
        "6. Open Interest futures chute (fuite des traders professionnels). "
        "7. Exchange inflows augmentent (whales envoient du BTC sur exchange pour vendre). "
        "Stratégie bear : réduire exposition, raccourcir durée des trades, utiliser des stablecoins.",
        "bear_market"
    ),
    (
        "bull_market_signs",
        "Signaux d'un bull market confirmé : "
        "1. Golden Cross EMA50 / EMA200 sur chart hebdomadaire. "
        "2. Higher Highs et Higher Lows consécutifs. "
        "3. Accumulation on-chain : exchange outflows nets (retraits crypto des exchanges). "
        "4. Whale wallets accumulent (augmentation des gros portefeuilles). "
        "5. Hash rate BTC au plus haut historique (confiance des mineurs). "
        "6. Open Interest futures en hausse régulière. "
        "7. Fear & Greed > 60 pendant 2+ semaines. "
        "Stratégie bull : acheter les dips, laisser courir les gagnants, HODL partiel.",
        "bull_market"
    ),
    (
        "news_impact",
        "L'impact des news dépend du contexte de marché. "
        "Bull market : mauvaises nouvelles = opportunité d'achat (buy the dip). "
        "Bear market : mauvaises nouvelles = continuer à vendre (sell the news). "
        "Règle 'buy the rumor, sell the news' : le prix monte en anticipation, chute à la confirmation. "
        "Exceptions : news vraiment majeures (FTX collapse, LUNA crash) = impact durable. "
        "Les bans Chine sont récurrents et leur impact diminue à chaque annonce. "
        "Les approbations ETF Spot = buy anticipation, légère correction à l'approbation.",
        "news"
    ),
    (
        "correlation_macro",
        "BTC est de plus en plus corrélé aux marchés macro depuis 2020. "
        "DXY (Dollar Index) inverse : DXY monte → BTC/alts baissent. "
        "Taux Fed : hausse des taux → risk-off → BTC baisse. Baisse des taux → BTC monte. "
        "SPX (S&P 500) : corrélation positive en bear, plus décorrélé en bull crypto. "
        "Yen Carry Trade : unwind du carry trade Yen → chutes violentes tous assets. "
        "Or : souvent précurseur — si or monte, BTC suit 4-6 semaines après. "
        "Risque macro à surveiller : CPI, FOMC meetings, décisions Fed.",
        "macro"
    ),
    (
        "seasonal_patterns",
        "Patterns saisonniers historiques BTC : "
        "Q1 (Jan-Mar) : Effet janvier souvent positif après un Q4 fort. "
        "Q2 (Avr-Jun) : Souvent volatil — halving en avril, correction possible. "
        "Q3 (Jul-Sep) : Historiquement le plus faible — 'Crypto summer lull'. "
        "Q4 (Oct-Déc) : Historiquement le plus haussier — 'Uptober', 'No Nut November bull'. "
        "Septembre est statistiquement le pire mois pour BTC. "
        "Décembre est souvent fort mais se termine par une consolidation en janvier.",
        "seasonal"
    ),
    (
        "on_chain_signals",
        "Les données on-chain donnent une vue directe du comportement des whales. "
        "Exchange outflows nets (retraits > dépôts) = accumulation → bullish. "
        "Exchange inflows nets (dépôts > retraits) = distribution → bearish. "
        "NUPL (Net Unrealized Profit/Loss) : < 0 = bear bottom, 0.5-0.75 = bull fort, > 0.75 = euphorie top. "
        "SOPR (Spent Output Profit Ratio) : > 1 en hausse = bull, < 1 en baisse = bear. "
        "Addresses actives en hausse = adoption croissante → bullish long terme. "
        "Miner outflows : si les mineurs vendent massivement = pression baissière.",
        "on_chain"
    ),
    (
        "whale_behavior",
        "Les baleines (wallets > 1000 BTC) dictent les tendances crypto. "
        "Comportement typique : accumulation dans le silence et la baisse, distribution dans l'euphorie. "
        "Wall Buy / Bid walls importants sur orderbook = support artificiel temporaire. "
        "Wall Sell / Ask walls importants = résistance artificielle temporaire. "
        "Les whales créent souvent des fakeouts pour liquider les retail avant le vrai move. "
        "Stop hunting : chutes rapides sous un niveau de support clé puis rebond immédiat. "
        "La liquidité se trouve là où les stops sont placés — surveiller les niveaux ronds.",
        "market_microstructure"
    ),
    (
        "position_sizing",
        "La gestion de taille de position est la compétence la plus importante en trading. "
        "Règle de base : ne jamais risquer plus de 1-2% du capital par trade. "
        "En bull fort : jusqu'à 2.5% sur signaux haute conviction (ML + Claude alignés). "
        "En incertitude ou bear : réduire à 0.5-1% max. "
        "Corrélation des positions : si 5 alts sont corrélées à BTC, le risque réel est ×5. "
        "Kelly Criterion : taille optimale = (edge × odds - (1-edge)) / odds. "
        "Préférer des trades moins nombreux mais de meilleure qualité.",
        "risk_management"
    ),
    (
        "entry_timing",
        "Le timing d'entrée peut faire la différence entre un bon et mauvais trade. "
        "Attendre la confirmation : ne pas anticiper la cassure, attendre la clôture de bougie. "
        "Volume confirmation : la cassure doit s'accompagner d'un volume > moyenne 20 bougies. "
        "Retest entry : entrer sur le retest du niveau cassé (moins risqué que la cassure). "
        "Limit orders : placer des limites sur les zones de support/résistance identifiées. "
        "Éviter les entrées sur un move déjà fait de +10% — attendre consolidation. "
        "Le meilleur moment d'entrée est souvent quand le trade fait peur.",
        "execution"
    ),
]


class MarketMemory:
    """
    Mémoire persistante SQLite pour le bot de trading.
    Stocke les événements historiques, la sagesse des marchés,
    les résultats de trades et les observations en temps réel.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._seed_if_empty()
        logger.info("MarketMemory initialisée : %s", self.db_path)

    # ─── Init ─────────────────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        """Crée les tables si elles n'existent pas."""
        ddl = """
        CREATE TABLE IF NOT EXISTS market_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT    NOT NULL,
            event       TEXT    NOT NULL,
            pair        TEXT    NOT NULL DEFAULT 'BTC/USDT',
            impact_pct  REAL    NOT NULL DEFAULT 0.0,
            type        TEXT    NOT NULL DEFAULT 'other',
            lesson      TEXT    NOT NULL DEFAULT '',
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS market_wisdom (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            topic       TEXT    NOT NULL UNIQUE,
            knowledge   TEXT    NOT NULL,
            category    TEXT    NOT NULL DEFAULT 'general',
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS trade_outcomes (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp           TEXT    NOT NULL,
            pair                TEXT    NOT NULL,
            signal              TEXT    NOT NULL,
            indicators_json     TEXT    NOT NULL DEFAULT '{}',
            outcome_pnl_pct     REAL    NOT NULL DEFAULT 0.0,
            ml_confidence       REAL    NOT NULL DEFAULT 0.0,
            claude_validated    INTEGER NOT NULL DEFAULT 0,
            rsi                 REAL,
            adx                 REAL,
            trend               TEXT,
            pattern_key         TEXT,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS market_observations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT    NOT NULL,
            pair            TEXT    NOT NULL,
            price           REAL,
            rsi             REAL,
            adx             REAL,
            trend           TEXT,
            volume_ratio    REAL,
            notes           TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS pattern_stats (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_key     TEXT    NOT NULL UNIQUE,
            total_trades    INTEGER NOT NULL DEFAULT 0,
            winning_trades  INTEGER NOT NULL DEFAULT 0,
            total_pnl_pct   REAL    NOT NULL DEFAULT 0.0,
            avg_ml_conf     REAL    NOT NULL DEFAULT 0.0,
            last_updated    TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_events_date       ON market_events(date);
        CREATE INDEX IF NOT EXISTS idx_events_type       ON market_events(type);
        CREATE INDEX IF NOT EXISTS idx_trades_pair       ON trade_outcomes(pair);
        CREATE INDEX IF NOT EXISTS idx_trades_signal     ON trade_outcomes(signal);
        CREATE INDEX IF NOT EXISTS idx_trades_ts         ON trade_outcomes(timestamp);
        CREATE INDEX IF NOT EXISTS idx_obs_pair          ON market_observations(pair);
        CREATE INDEX IF NOT EXISTS idx_obs_ts            ON market_observations(timestamp);
        """
        try:
            with self._get_conn() as conn:
                for stmt in ddl.strip().split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        conn.execute(stmt)
        except Exception:
            logger.exception("Erreur lors de l'initialisation de la DB")

    def _seed_if_empty(self) -> None:
        """Pré-charge les données historiques uniquement si les tables sont vides."""
        try:
            with self._get_conn() as conn:
                events_count = conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]
                wisdom_count = conn.execute("SELECT COUNT(*) FROM market_wisdom").fetchone()[0]
            if events_count == 0 or wisdom_count == 0:
                logger.info("Pré-chargement de la connaissance historique...")
                self._seed_historical_knowledge()
        except Exception:
            logger.exception("Erreur lors du seed")

    def _seed_historical_knowledge(self) -> None:
        """Insère les données historiques pré-chargées."""
        try:
            with self._get_conn() as conn:
                # Événements historiques
                conn.executemany(
                    """INSERT OR IGNORE INTO market_events
                       (date, event, pair, impact_pct, type, lesson)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    HISTORICAL_EVENTS,
                )
                # Sagesse des marchés
                conn.executemany(
                    """INSERT OR REPLACE INTO market_wisdom
                       (topic, knowledge, category, updated_at)
                       VALUES (?, ?, ?, datetime('now'))""",
                    MARKET_WISDOM,
                )
            logger.info(
                "Seed terminé : %d événements, %d sagesses",
                len(HISTORICAL_EVENTS),
                len(MARKET_WISDOM),
            )
        except Exception:
            logger.exception("Erreur lors du seed historical knowledge")

    # ─── Méthodes publiques ───────────────────────────────────────────────────

    def remember_trade(
        self,
        pair: str,
        signal: str,
        indicators_snapshot: dict,
        outcome_pnl_pct: float,
        ml_conf: float,
        claude_validated: bool,
    ) -> None:
        """
        Enregistre le résultat d'un trade exécuté.
        Met à jour les statistiques du pattern correspondant.
        """
        try:
            rsi = indicators_snapshot.get("rsi")
            adx = indicators_snapshot.get("adx")
            trend = indicators_snapshot.get("trend", "unknown")
            rsi_bucket = _bucket_rsi(rsi)
            pattern_key = f"{pair}|{signal}|{trend}|rsi:{rsi_bucket}"

            ts = datetime.now(timezone.utc).isoformat()

            with self._get_conn() as conn:
                conn.execute(
                    """INSERT INTO trade_outcomes
                       (timestamp, pair, signal, indicators_json,
                        outcome_pnl_pct, ml_confidence, claude_validated,
                        rsi, adx, trend, pattern_key)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ts, pair, signal,
                        json.dumps(indicators_snapshot, default=str),
                        outcome_pnl_pct, ml_conf,
                        1 if claude_validated else 0,
                        rsi, adx, trend, pattern_key,
                    ),
                )
                # Mise à jour pattern_stats (upsert)
                conn.execute(
                    """INSERT INTO pattern_stats
                           (pattern_key, total_trades, winning_trades, total_pnl_pct, avg_ml_conf, last_updated)
                       VALUES (?, 1, ?, ?, ?, datetime('now'))
                       ON CONFLICT(pattern_key) DO UPDATE SET
                           total_trades  = total_trades + 1,
                           winning_trades = winning_trades + excluded.winning_trades,
                           total_pnl_pct = total_pnl_pct + excluded.total_pnl_pct,
                           avg_ml_conf   = (avg_ml_conf * total_trades + excluded.avg_ml_conf) / (total_trades + 1),
                           last_updated  = datetime('now')""",
                    (
                        pattern_key,
                        1 if outcome_pnl_pct > 0 else 0,
                        outcome_pnl_pct,
                    ),
                )
            logger.debug("Trade mémorisé : %s %s pnl=%.2f%%", pair, signal, outcome_pnl_pct)
        except Exception:
            logger.exception("Erreur remember_trade")

    def remember_market_event(
        self,
        event_text: str,
        pair: str,
        price_before: float,
        price_after: float,
    ) -> None:
        """Enregistre un événement de marché observé en temps réel."""
        try:
            if price_before and price_before != 0:
                impact_pct = ((price_after - price_before) / price_before) * 100.0
            else:
                impact_pct = 0.0

            event_type = "positive" if impact_pct > 0 else ("negative" if impact_pct < 0 else "neutral")
            ts = datetime.now(timezone.utc).isoformat()

            with self._get_conn() as conn:
                conn.execute(
                    """INSERT INTO market_events (date, event, pair, impact_pct, type, lesson)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (ts[:10], event_text, pair, impact_pct, event_type, ""),
                )
                conn.execute(
                    """INSERT INTO market_observations
                       (timestamp, pair, price, notes)
                       VALUES (?, ?, ?, ?)""",
                    (ts, pair, price_after, event_text),
                )
            logger.debug("Événement mémorisé : %s (%.2f%%)", event_text[:60], impact_pct)
        except Exception:
            logger.exception("Erreur remember_market_event")

    def recall_similar_conditions(
        self,
        current_indicators: dict,
        limit: int = 5,
    ) -> list:
        """
        Retrouve des trades passés dans des conditions similaires.
        Similarité basée sur : signal, trend, bucket RSI.
        """
        try:
            signal = current_indicators.get("signal", "")
            trend = current_indicators.get("trend", "")
            rsi = current_indicators.get("rsi")
            rsi_bucket = _bucket_rsi(rsi)

            with self._get_conn() as conn:
                rows = conn.execute(
                    """SELECT pair, signal, trend, rsi, adx, outcome_pnl_pct,
                              ml_confidence, claude_validated, timestamp
                       FROM trade_outcomes
                       WHERE signal = ?
                         AND trend  = ?
                         AND (rsi IS NULL OR ABS(rsi - COALESCE(?, rsi)) < 10)
                       ORDER BY timestamp DESC
                       LIMIT ?""",
                    (signal, trend, rsi, limit),
                ).fetchall()

                if not rows and signal:
                    # Fallback : même signal uniquement
                    rows = conn.execute(
                        """SELECT pair, signal, trend, rsi, adx, outcome_pnl_pct,
                                  ml_confidence, claude_validated, timestamp
                           FROM trade_outcomes
                           WHERE signal = ?
                           ORDER BY timestamp DESC
                           LIMIT ?""",
                        (signal, limit),
                    ).fetchall()

            return [dict(r) for r in rows]
        except Exception:
            logger.exception("Erreur recall_similar_conditions")
            return []

    def recall_recent_events(self, hours: int = 48) -> list:
        """Retourne les événements de marché récents (dernières N heures)."""
        try:
            with self._get_conn() as conn:
                rows = conn.execute(
                    """SELECT date, event, pair, impact_pct, type, lesson
                       FROM market_events
                       WHERE created_at >= datetime('now', ? || ' hours')
                       ORDER BY created_at DESC""",
                    (f"-{hours}",),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            logger.exception("Erreur recall_recent_events")
            return []

    def get_wisdom(self, topic: Optional[str] = None) -> list:
        """
        Retourne la sagesse pré-chargée.
        topic=None → tout retourner.
        """
        try:
            with self._get_conn() as conn:
                if topic:
                    rows = conn.execute(
                        "SELECT topic, knowledge, category FROM market_wisdom WHERE topic = ?",
                        (topic,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT topic, knowledge, category FROM market_wisdom ORDER BY category, topic"
                    ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            logger.exception("Erreur get_wisdom")
            return []

    def get_performance_by_pattern(self) -> dict:
        """
        Retourne les statistiques de performance par pattern.
        Clé = pattern_key, valeur = {win_rate, avg_pnl, total_trades, ...}
        """
        try:
            with self._get_conn() as conn:
                rows = conn.execute(
                    """SELECT pattern_key, total_trades, winning_trades,
                              total_pnl_pct, avg_ml_conf
                       FROM pattern_stats
                       WHERE total_trades >= 3
                       ORDER BY total_pnl_pct DESC"""
                ).fetchall()

            result = {}
            for r in rows:
                win_rate = r["winning_trades"] / r["total_trades"] if r["total_trades"] > 0 else 0.0
                avg_pnl = r["total_pnl_pct"] / r["total_trades"] if r["total_trades"] > 0 else 0.0
                result[r["pattern_key"]] = {
                    "total_trades": r["total_trades"],
                    "win_rate": round(win_rate, 3),
                    "avg_pnl_pct": round(avg_pnl, 3),
                    "total_pnl_pct": round(r["total_pnl_pct"], 3),
                    "avg_ml_conf": round(r["avg_ml_conf"], 3),
                }
            return result
        except Exception:
            logger.exception("Erreur get_performance_by_pattern")
            return {}

    def get_market_context_summary(self) -> str:
        """
        Génère un résumé textuel de la mémoire du marché pour Claude.
        Inclut : événements récents, sagesse pertinente, performance des patterns.
        """
        lines = ["=== MÉMOIRE MARCHÉ ===\n"]

        try:
            # Événements récents (48h)
            recent = self.recall_recent_events(hours=48)
            if recent:
                lines.append("--- Événements récents (48h) ---")
                for ev in recent[:10]:
                    sign = "+" if ev["impact_pct"] >= 0 else ""
                    lines.append(
                        f"  [{ev['date']}] {ev['pair']} {sign}{ev['impact_pct']:.1f}% — {ev['event'][:80]}"
                    )
            else:
                lines.append("--- Aucun événement récent enregistré ---")

            lines.append("")

            # Événements historiques importants récents (6 mois)
            try:
                with self._get_conn() as conn:
                    major = conn.execute(
                        """SELECT date, event, pair, impact_pct, lesson
                           FROM market_events
                           WHERE date >= date('now', '-180 days')
                             AND ABS(impact_pct) >= 10
                           ORDER BY date DESC
                           LIMIT 5"""
                    ).fetchall()
                if major:
                    lines.append("--- Événements majeurs (6 mois, impact >10%) ---")
                    for ev in major:
                        sign = "+" if ev["impact_pct"] >= 0 else ""
                        lines.append(
                            f"  [{ev['date']}] {ev['pair']} {sign}{ev['impact_pct']:.1f}% — {ev['event'][:80]}"
                        )
                    lines.append("")
            except Exception:
                pass

            # Performance des patterns
            perf = self.get_performance_by_pattern()
            if perf:
                lines.append("--- Top patterns (win rate) ---")
                sorted_perf = sorted(perf.items(), key=lambda x: x[1]["win_rate"], reverse=True)
                for pattern, stats in sorted_perf[:8]:
                    lines.append(
                        f"  {pattern[:50]} : WR={stats['win_rate']:.0%} "
                        f"avgPnL={stats['avg_pnl_pct']:+.2f}% "
                        f"({stats['total_trades']} trades)"
                    )
                lines.append("")

            # Sagesse clé
            wisdom = self.get_wisdom()
            if wisdom:
                lines.append("--- Sagesse clé disponible ---")
                for w in wisdom[:5]:
                    lines.append(f"  [{w['topic']}] {w['knowledge'][:120]}...")
                lines.append(f"  ... et {len(wisdom) - 5} autres topics disponibles via get_wisdom()")

        except Exception:
            logger.exception("Erreur get_market_context_summary")
            lines.append("(Erreur lors de la génération du résumé)")

        return "\n".join(lines)

    def save_observation(
        self,
        pair: str,
        price: float,
        rsi: Optional[float] = None,
        adx: Optional[float] = None,
        trend: Optional[str] = None,
        volume_ratio: Optional[float] = None,
        notes: str = "",
    ) -> None:
        """Sauvegarde une observation de marché en temps réel."""
        try:
            ts = datetime.now(timezone.utc).isoformat()
            with self._get_conn() as conn:
                conn.execute(
                    """INSERT INTO market_observations
                       (timestamp, pair, price, rsi, adx, trend, volume_ratio, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ts, pair, price, rsi, adx, trend, volume_ratio, notes),
                )
        except Exception:
            logger.exception("Erreur save_observation")


# ─── Helpers internes ─────────────────────────────────────────────────────────

def _bucket_rsi(rsi: Optional[float]) -> str:
    """Convertit un RSI en bucket pour la comparaison de similarité."""
    if rsi is None:
        return "unknown"
    if rsi < 20:
        return "extreme_oversold"
    if rsi < 35:
        return "oversold"
    if rsi < 50:
        return "below_mid"
    if rsi < 65:
        return "above_mid"
    if rsi < 80:
        return "overbought"
    return "extreme_overbought"
