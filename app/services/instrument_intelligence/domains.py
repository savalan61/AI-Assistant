"""Shared fundamental-factor vocabulary (READ-ONLY, deterministic, no LLM).

A fundamental news item or economic-calendar event does not have to name an
instrument to be about that instrument. "Federal Reserve officials discuss
interest rates" never mentions XAUUSD, yet it concerns a factor the instrument
is exposed to. Deciding that is this module's job: it defines the *domains* - the
documented fundamental factors - and matches a piece of text against them.

Deliberate properties:

* **One vocabulary.** The calendar relevance layer and the news relevance layer
  both resolve their text through this table, so "US CPI" (an event) and "US
  inflation accelerates" (an item) land in the same domain. There is exactly one
  place to add a factor, and no second, divergent keyword list.
* **Facts only.** A domain names a *subject area* ("US inflation", "geopolitical
  risk"). Nothing here expresses a direction, a magnitude, an expectation or a
  probability, and a match is never a price claim: matching a domain means "this
  text is about a factor this instrument is documented to be exposed to", not
  "this is good/bad for it". See the forbidden-vocabulary test.
* **No score.** Matching is boolean per domain: a domain either appears in the
  text or it does not. No weights, no counts and no numeric relevance anywhere.
* **Deterministic.** Normalization is fixed (lower-case, hyphens treated as
  spaces, whitespace collapsed) and matching is whole-word/phrase only, so the
  same text always yields the same domains in the same order. Domains are
  returned in table order, never in hash or set order.
* **Extensible by data, not by code.** Adding a factor is a table edit here plus
  a profile entry in profiles.py; no engine, no rules DSL, no database.
"""
import re
from enum import StrEnum
from typing import Final

# Domain labels and keywords are authored in this normalized form: lower-case,
# hyphens written as spaces (the text is normalized the same way before
# matching, so "10-year yield" and "10 year yield" are one keyword and
# "interest-rate" still matches "interest rate").
_WHITESPACE: Final[re.Pattern[str]] = re.compile(r"\s+")
_HYPHENS: Final[re.Pattern[str]] = re.compile(r"[-–—]")


class FundamentalDomain(StrEnum):
    """A documented fundamental factor a piece of financial text can concern.

    The value is a stable machine token (it can travel as data); the human label
    lives in ``DOMAIN_LABELS`` and is what an explanation quotes.
    """

    PRECIOUS_METALS = "precious_metals"
    CRUDE_OIL = "crude_oil"
    ENERGY_SUPPLY = "energy_supply"
    US_DOLLAR = "us_dollar"
    MONETARY_POLICY = "monetary_policy"
    INFLATION = "inflation"
    LABOR_AND_GROWTH = "labor_and_growth"
    RATES_AND_YIELDS = "rates_and_yields"
    GEOPOLITICAL_RISK = "geopolitical_risk"
    TECHNOLOGY_SECTOR = "technology_sector"
    MAJOR_TECHNOLOGY_COMPANIES = "major_technology_companies"
    TRADE_AND_TARIFFS = "trade_and_tariffs"


# Human labels used in every explanation this layer produces. They are scoped
# honestly: "central-bank monetary policy" covers the Fed and other central
# banks, so a Bank of Japan item is never labelled as US monetary policy.
DOMAIN_LABELS: dict[FundamentalDomain, str] = {
    FundamentalDomain.PRECIOUS_METALS: "precious metals",
    FundamentalDomain.CRUDE_OIL: "crude oil and refined product prices",
    FundamentalDomain.ENERGY_SUPPLY: "energy supply and production",
    FundamentalDomain.US_DOLLAR: "the US dollar",
    FundamentalDomain.MONETARY_POLICY: "central-bank monetary policy",
    FundamentalDomain.INFLATION: "inflation and price data",
    FundamentalDomain.LABOR_AND_GROWTH: "labor market and economic growth",
    FundamentalDomain.RATES_AND_YIELDS: "interest rates and bond yields",
    FundamentalDomain.GEOPOLITICAL_RISK: "geopolitical risk",
    FundamentalDomain.TECHNOLOGY_SECTOR: "the technology sector",
    FundamentalDomain.MAJOR_TECHNOLOGY_COMPANIES: "major technology companies",
    FundamentalDomain.TRADE_AND_TARIFFS: "trade restrictions and tariffs",
}

# Single source of truth for what text belongs to which domain. Keywords are
# whole words or whole phrases; nothing is matched as a substring, so
# "inflationary" is not "inflation" and "oil" is not "boil". Lists stay short and
# specific on purpose: a keyword that is not clearly tied to one domain is not
# listed (an over-eager table would mark everything relevant, which is worse than
# missing an item, because it makes the relevance layer untrustworthy).
_DOMAIN_KEYWORDS: tuple[tuple[FundamentalDomain, tuple[str, ...]], ...] = (
    (
        FundamentalDomain.PRECIOUS_METALS,
        (
            "gold",
            "gold price",
            "gold prices",
            "gold demand",
            "gold reserves",
            "gold etf",
            "bullion",
            "xau",
            "precious metal",
            "precious metals",
        ),
    ),
    (
        FundamentalDomain.CRUDE_OIL,
        (
            "crude",
            "crude oil",
            "wti",
            "brent",
            "petroleum",
            "oil",
            "oil price",
            "oil prices",
            "oil market",
            "oil demand",
            "oil supply",
            "oil production",
            "oil exports",
            "crude exports",
            "energy prices",
            "fuel prices",
            "gasoline",
            "diesel",
        ),
    ),
    (
        FundamentalDomain.ENERGY_SUPPLY,
        (
            "opec",
            "production cut",
            "production cuts",
            "production increase",
            "output cut",
            "output increase",
            "crude inventories",
            "oil inventories",
            "energy inventories",
            "stockpiles",
            "eia",
            "drilling",
            "pipeline",
            "pipelines",
            "refinery",
            "refineries",
            "supply disruption",
            "supply disruptions",
            "shipping disruption",
            "tanker",
            "tankers",
        ),
    ),
    (
        FundamentalDomain.US_DOLLAR,
        (
            "dollar",
            "dollars",
            "usd",
            "dxy",
            "dollar index",
            "greenback",
            "reserve currency",
        ),
    ),
    (
        FundamentalDomain.MONETARY_POLICY,
        (
            "federal reserve",
            "fed",
            "fomc",
            "monetary policy",
            "interest rate",
            "interest rates",
            "policy rate",
            "rate decision",
            "rate cut",
            "rate cuts",
            "rate hike",
            "rate hikes",
            "rate path",
        ),
    ),
    (
        FundamentalDomain.INFLATION,
        (
            "inflation",
            "core inflation",
            "disinflation",
            "deflation",
            "cpi",
            "pce",
            "ppi",
            "consumer price index",
            "producer price index",
            "price pressures",
        ),
    ),
    (
        FundamentalDomain.LABOR_AND_GROWTH,
        (
            "employment",
            "unemployment",
            "nonfarm payrolls",
            "non farm payrolls",
            "payrolls",
            "nfp",
            "jobless claims",
            "jobs report",
            "wages",
            "gdp",
            "recession",
            "economic growth",
            "economic activity",
            "industrial production",
            "industrial activity",
            "manufacturing",
            "consumer spending",
            "retail sales",
        ),
    ),
    (
        FundamentalDomain.RATES_AND_YIELDS,
        (
            "treasury yield",
            "treasury yields",
            "bond yield",
            "bond yields",
            "real yield",
            "real yields",
            "10 year yield",
            "2 year yield",
            "yield curve",
            "government bond yields",
            "sovereign yields",
        ),
    ),
    (
        FundamentalDomain.GEOPOLITICAL_RISK,
        (
            "war",
            "conflict",
            "military escalation",
            "escalation",
            "escalates",
            "escalating",
            "sanctions",
            "iran",
            "israel",
            "ukraine",
            "russia",
            "persian gulf",
            "strait of hormuz",
            "middle east",
            "red sea",
            "geopolitical",
            "safe haven",
            "invasion",
            "blockade",
        ),
    ),
    (
        FundamentalDomain.TECHNOLOGY_SECTOR,
        (
            "nasdaq",
            "nas100",
            "technology",
            "technology sector",
            "tech stocks",
            "tech sector",
            "semiconductor",
            "semiconductors",
            "chip",
            "chips",
            "software",
            "cloud",
            "cloud computing",
            "artificial intelligence",
            "ai",
            "data center",
            "data centers",
        ),
    ),
    (
        FundamentalDomain.MAJOR_TECHNOLOGY_COMPANIES,
        (
            # A fixed, documented list of large listed technology companies.
            # Extending it is a data edit here, not a code change.
            "apple",
            "microsoft",
            "nvidia",
            "amazon",
            "alphabet",
            "google",
            "meta",
            "tesla",
            "netflix",
            "broadcom",
            "adobe",
            "intel",
            "amd",
            "oracle",
            "salesforce",
            "qualcomm",
            "micron",
        ),
    ),
    (
        FundamentalDomain.TRADE_AND_TARIFFS,
        (
            "tariff",
            "tariffs",
            "trade war",
            "trade restriction",
            "trade restrictions",
            "trade tensions",
            "export restriction",
            "export restrictions",
            "export control",
            "export controls",
            "semiconductor export",
            "chip export",
            "technology sanctions",
            "trade sanctions",
            "us china",
            "china trade",
        ),
    ),
)

# Compiled once, paired with the keywords they were built from. Domain order is
# the table order above, so results are stable.
_DOMAIN_PATTERNS: tuple[
    tuple[FundamentalDomain, tuple[tuple[str, re.Pattern[str]], ...]], ...
] = tuple(
    (
        domain,
        tuple(
            (keyword, re.compile(rf"\b{re.escape(keyword)}\b")) for keyword in keywords
        ),
    )
    for domain, keywords in _DOMAIN_KEYWORDS
)

# The domain table's own order, used whenever domains must be presented in a
# stable, table-defined sequence instead of set or hash order.
DOMAIN_ORDER: tuple[FundamentalDomain, ...] = tuple(domain for domain, _ in _DOMAIN_KEYWORDS)


def normalize_text(text: str) -> str:
    """Normalized matching form of ``text`` (lower-case, hyphens as spaces)."""
    return _WHITESPACE.sub(" ", _HYPHENS.sub(" ", text.lower())).strip()


def matched_domain_terms(text: str) -> tuple[tuple[FundamentalDomain, tuple[str, ...]], ...]:
    """Domains found in ``text`` with the keyword(s) that matched, in table order.

    Whole-word/phrase matching only, so a partial word never counts. An empty
    tuple means the text carries no documented fundamental factor at all - an
    explicit "nothing matched" rather than a guess.
    """
    normalized = normalize_text(text)
    found: list[tuple[FundamentalDomain, tuple[str, ...]]] = []
    for domain, keywords in _DOMAIN_PATTERNS:
        hits = tuple(
            keyword for keyword, pattern in keywords if pattern.search(normalized)
        )
        if hits:
            found.append((domain, hits))
    return tuple(found)


def match_domains(text: str) -> tuple[FundamentalDomain, ...]:
    """Domains ``text`` concerns, in table order (empty when none match)."""
    return tuple(domain for domain, _ in matched_domain_terms(text))


def domain_labels(domains: tuple[FundamentalDomain, ...]) -> tuple[str, ...]:
    """Human labels for ``domains``, deduplicated in the order given."""
    labels: list[str] = []
    for domain in domains:
        label = DOMAIN_LABELS[domain]
        if label not in labels:
            labels.append(label)
    return tuple(labels)


def order_domains(domains: tuple[FundamentalDomain, ...]) -> tuple[FundamentalDomain, ...]:
    """``domains`` deduplicated and ordered by the domain table (deterministic)."""
    unique = set(domains)
    return tuple(domain for domain in DOMAIN_ORDER if domain in unique)
