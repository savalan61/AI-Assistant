"""Instrument fundamental profiles (READ-ONLY, deterministic, no LLM).

An instrument is exposed to a documented set of fundamental factors, and the same
factor can reach two instruments through different relationships. This module
states that relationship per instrument:

* ``direct`` - the text concerns the instrument's own underlying asset or market
  ("Gold demand rises" for XAUUSD, "OPEC announces production cuts" for USOIL,
  "semiconductor export restrictions" for NASDAQ);
* ``macro`` - the text concerns a broad factor the instrument is documented to be
  exposed to ("Federal Reserve keeps rates unchanged" for gold, US CPI for an
  equity index);
* ``indirect`` - the text concerns a factor that reaches the instrument through a
  documented transmission ("Persian Gulf tensions disrupt crude shipments" is
  direct for oil and indirect for gold; "Saudi oil production changes" reaches
  NASDAQ only indirectly).

The relationship is a factual statement about *transmission*, never about
direction: the table says "gold is exposed to US monetary policy", never "rate
cuts are good for gold". Nothing here ranks instruments, scores factors or
predicts anything, and the tests enforce that (no directional vocabulary, and a
domain listed in exactly one tier per profile).

Matching stays small and static. A *symbol* belongs to a profile when its
normalized form begins with one of the profile's documented symbol roots, so
broker suffixes (``XAUUSD.r``, ``USOIL.cash``, ``NAS100.i``) still resolve. A
*user-typed question* is matched only against ``focus_names`` - explicit
instrument names such as ``USOIL`` or ``NAS100``. A commodity word ("gold",
"oil") is never a focus instrument, exactly as the focus-detection contract
already states, because mapping a commodity name to one broker's symbol would be
a guess.

Adding an instrument or a factor is a data edit here (or in domains.py), not an
engine change: there is no rules DSL, no scoring, no persistence and no network.
"""
import re
from enum import StrEnum
from typing import Final, NamedTuple

from app.services.instrument_intelligence.domains import (
    FundamentalDomain,
    domain_labels,
    match_domains,
    order_domains,
)

# Everything that is not a letter or digit is dropped before a symbol is matched,
# so "XAUUSD.r", "USOIL.cash" and "NAS100.i" still resolve to their profile.
_SYMBOL_NOISE: Final[re.Pattern[str]] = re.compile(r"[^A-Z0-9]")


class RelevanceKind(StrEnum):
    """How a factor reaches an instrument (never how strong or which way it goes).

    ``DIRECT`` - the instrument's own underlying asset or market;
    ``MACRO`` - a broad factor the instrument is documented to be exposed to;
    ``INDIRECT`` - a factor that reaches the instrument through a documented
    transmission.
    """

    DIRECT = "DIRECT"
    MACRO = "MACRO"
    INDIRECT = "INDIRECT"


# How each relationship is phrased in an explanation. Factual exposure only: no
# direction, no magnitude, no expectation. Shared by the news and calendar
# explanations so both describe the same relationship in the same words.
_KIND_PHRASE: dict[RelevanceKind, str] = {
    RelevanceKind.DIRECT: "a defined direct fundamental factor",
    RelevanceKind.MACRO: "a defined macro fundamental factor",
    RelevanceKind.INDIRECT: "a defined indirect transmission factor",
}


class DomainMatch(NamedTuple):
    """The strongest relationship an instrument's profile has to a piece of text."""

    kind: RelevanceKind
    domains: tuple[FundamentalDomain, ...]


class InstrumentProfile(NamedTuple):
    """One instrument's documented fundamental exposure.

    ``canonical`` is the label this layer reports for the instrument (so a
    question about "WTI" is answered about USOIL), ``symbols`` resolve a broker
    symbol to the profile, and ``focus_names`` are the explicit instrument names a
    user may type. The three tiers are the transmission relationship for each
    domain, and they never overlap.
    """

    name: str
    canonical: str
    symbols: tuple[str, ...]
    focus_names: tuple[str, ...]
    direct: tuple[FundamentalDomain, ...]
    macro: tuple[FundamentalDomain, ...]
    indirect: tuple[FundamentalDomain, ...]

    def domains(self) -> tuple[FundamentalDomain, ...]:
        """Every domain this profile is exposed to, in table order."""
        return order_domains(self.direct + self.macro + self.indirect)


# The initial profiles. Each is deliberately conservative: a factor is listed
# only where the transmission is one this project is willing to state as fact,
# and the tiers are scoped so an unrelated instrument is not swept in (a gold
# item is not a technology item, an oil-supply item is not a semiconductor item).
PROFILES: tuple[InstrumentProfile, ...] = (
    InstrumentProfile(
        name="gold (XAUUSD)",
        canonical="XAUUSD",
        # "GOLD" is a common broker symbol for spot gold, so it resolves a
        # symbol; it is deliberately NOT a focus name, because "gold" in a
        # question is a commodity word rather than an instrument name.
        symbols=("XAUUSD", "GOLD"),
        focus_names=("XAUUSD",),
        direct=(FundamentalDomain.PRECIOUS_METALS,),
        macro=(
            FundamentalDomain.MONETARY_POLICY,
            FundamentalDomain.INFLATION,
            FundamentalDomain.LABOR_AND_GROWTH,
            FundamentalDomain.RATES_AND_YIELDS,
            # Gold is priced in USD, so the dollar is a macro factor for it.
            FundamentalDomain.US_DOLLAR,
        ),
        indirect=(
            FundamentalDomain.GEOPOLITICAL_RISK,
            FundamentalDomain.CRUDE_OIL,
            FundamentalDomain.ENERGY_SUPPLY,
            FundamentalDomain.TRADE_AND_TARIFFS,
        ),
    ),
    InstrumentProfile(
        name="crude oil (USOIL/WTI)",
        canonical="USOIL",
        symbols=("USOIL", "WTI", "XTIUSD", "OILUSD", "OIL"),
        focus_names=("USOIL", "WTI", "XTIUSD", "OILUSD"),
        direct=(
            FundamentalDomain.CRUDE_OIL,
            FundamentalDomain.ENERGY_SUPPLY,
            FundamentalDomain.GEOPOLITICAL_RISK,
        ),
        macro=(
            FundamentalDomain.LABOR_AND_GROWTH,
            FundamentalDomain.MONETARY_POLICY,
            FundamentalDomain.INFLATION,
            # Crude is priced in USD: a documented macro factor for the contract.
            FundamentalDomain.US_DOLLAR,
        ),
        indirect=(
            FundamentalDomain.RATES_AND_YIELDS,
            FundamentalDomain.TRADE_AND_TARIFFS,
        ),
    ),
    InstrumentProfile(
        name="Nasdaq-100 (NAS100/NASDAQ)",
        canonical="NAS100",
        symbols=("NASDAQ", "NAS100", "US100", "USTEC", "NDX"),
        focus_names=("NASDAQ", "NAS100", "US100", "USTEC"),
        direct=(
            FundamentalDomain.TECHNOLOGY_SECTOR,
            FundamentalDomain.MAJOR_TECHNOLOGY_COMPANIES,
            # Trade and export policy is a direct sector factor for technology.
            FundamentalDomain.TRADE_AND_TARIFFS,
        ),
        macro=(
            FundamentalDomain.MONETARY_POLICY,
            FundamentalDomain.INFLATION,
            FundamentalDomain.LABOR_AND_GROWTH,
            FundamentalDomain.RATES_AND_YIELDS,
        ),
        indirect=(
            FundamentalDomain.ENERGY_SUPPLY,
            FundamentalDomain.CRUDE_OIL,
            FundamentalDomain.GEOPOLITICAL_RISK,
            # Overseas revenue translates through the dollar: indirect only.
            FundamentalDomain.US_DOLLAR,
        ),
    ),
)

# Explicit instrument names a question may contain. Built from the profiles so
# there is one place to extend when an instrument is added.
FOCUS_INSTRUMENT_NAMES: frozenset[str] = frozenset(
    name for profile in PROFILES for name in profile.focus_names
)

_FOCUS_CANONICAL: dict[str, str] = {
    name: profile.canonical for profile in PROFILES for name in profile.focus_names
}


def profile_for(symbol: str) -> InstrumentProfile | None:
    """The profile ``symbol`` belongs to, or None when no profile covers it.

    Deterministic: symbols are normalized and compared against the documented
    roots in table order, so a broker suffix never changes the answer and an
    unknown instrument (or an empty string) simply has no profile.
    """
    normalized = _SYMBOL_NOISE.sub("", symbol.upper())
    if not normalized:
        return None
    for profile in PROFILES:
        if any(normalized.startswith(root) for root in profile.symbols):
            return profile
    return None


def focus_symbol_for_token(token: str) -> str | None:
    """Canonical focus instrument for a question token, or None.

    Only explicit instrument names qualify ("USOIL", "WTI", "NAS100"): a
    commodity word such as "gold" or "oil" is not an instrument name and is
    refused, so a topic word can never silently become the user's instrument.
    """
    return _FOCUS_CANONICAL.get(_SYMBOL_NOISE.sub("", token.upper()))


def match_profile_domains(profile: InstrumentProfile, text: str) -> DomainMatch | None:
    """Strongest documented relationship between ``profile`` and ``text``.

    The tiers are tested in strength order (direct, then macro, then indirect) and
    only the strongest tier's domains are returned, in table order. None means the
    text carries no factor this instrument's profile documents - an explicit
    "nothing matched", never a guess.
    """
    matched = set(match_domains(text))
    if not matched:
        return None
    for kind, tier in (
        (RelevanceKind.DIRECT, profile.direct),
        (RelevanceKind.MACRO, profile.macro),
        (RelevanceKind.INDIRECT, profile.indirect),
    ):
        hits = order_domains(tuple(domain for domain in tier if domain in matched))
        if hits:
            return DomainMatch(kind=kind, domains=hits)
    return None


def factor_reason(
    kind: RelevanceKind,
    domains: tuple[FundamentalDomain, ...],
    instrument: str,
    *,
    subject: str,
) -> str:
    """One factual sentence explaining a profile match.

    ``subject`` is the thing that matched ("The item" / "The event"). The sentence
    states the exposure and nothing else - no direction, no forecast, no advice.
    """
    joined = ", ".join(domain_labels(order_domains(domains)))
    return f"{subject} concerns {joined}, {_KIND_PHRASE[kind]} for {instrument}."
