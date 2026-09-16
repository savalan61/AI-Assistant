"""Tests for instrument-aware fundamental relevance (READ-ONLY, no LLM).

Covers the Step 48 architecture: an item or event does NOT have to name the
instrument to be relevant, relevance is graded per instrument through that
instrument's documented fundamental profile, the news and calendar layers share
one domain vocabulary, and nothing anywhere produces a direction, a score or a
recommendation.

Offline and deterministic: no MT5, no database, no network, no LLM, no news
vendor. Titles are fixed strings.
"""
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.providers.economic_calendar import EconomicEvent, EventImpact
from app.providers.news import NewsItem
from app.providers.position import Position, PositionType
from app.services.economic_intelligence import (
    RelevanceLevel,
    classify_relevance,
)
from app.services.fundamental_intelligence import classify_instrument_relevance
from app.services.instrument_intelligence import FundamentalDomain, RelevanceKind, match_domains

PUBLISHED = datetime(2026, 9, 16, 7, 5, tzinfo=UTC)

RELEVANT = RelevanceLevel.RELEVANT
POTENTIALLY = RelevanceLevel.POTENTIALLY_RELEVANT
NOT_OBVIOUS = RelevanceLevel.NOT_OBVIOUSLY_RELEVANT

# Wording that would turn a factual relatedness statement into advice or a
# prediction. No reason this layer produces may contain any of it.
_FORBIDDEN_WORDS = (
    "buy",
    "sell",
    "long",
    "short",
    "expect",
    "forecast",
    "predict",
    "target",
    "probability",
    "recommend",
    "should",
    "bullish",
    "bearish",
    "will rise",
    "will fall",
)


def news(title: str, *, instruments: tuple[str, ...] = (), currencies: tuple[str, ...] = ()) -> NewsItem:
    return NewsItem(
        item_id="news-1",
        published_at=PUBLISHED,
        publisher="Example Newswire (placeholder)",
        title=title,
        summary="Placeholder excerpt.",
        url=None,
        instruments=instruments,
        currencies=currencies,
        categories=(),
    )


def position(symbol: str, ticket: int = 1) -> Position:
    return Position(
        ticket=ticket,
        symbol=symbol,
        type=PositionType.BUY,
        volume=Decimal("0.10"),
        open_price=Decimal("1.0"),
        current_price=Decimal("1.0"),
        profit=Decimal("0.0"),
    )


def event(title: str, currency: str = "USD", impact: EventImpact = EventImpact.HIGH) -> EconomicEvent:
    return EconomicEvent(
        event_id="evt-1",
        timestamp=datetime(2026, 9, 16, 12, 30, tzinfo=UTC),
        currency=currency,
        title=title,
        impact=impact,
        forecast="3.1%",
        previous="3.2%",
        actual=None,
    )


# --- the scenario matrix -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "symbol", "level", "kind", "domains"),
    [
        # XAUUSD: direct, macro and indirect evidence, then an unrelated item.
        (
            "Gold demand rises as central banks increase purchases",
            "XAUUSD",
            RELEVANT,
            "DIRECT",
            ("precious_metals",),
        ),
        (
            "Federal Reserve officials discuss interest rates",
            "XAUUSD",
            POTENTIALLY,
            "MACRO",
            ("monetary_policy",),
        ),
        ("US CPI inflation accelerates", "XAUUSD", POTENTIALLY, "MACRO", ("inflation",)),
        ("US employment growth slows", "XAUUSD", POTENTIALLY, "MACRO", ("labor_and_growth",)),
        ("US Treasury yields rise", "XAUUSD", POTENTIALLY, "MACRO", ("rates_and_yields",)),
        ("Persian Gulf tensions escalate", "XAUUSD", POTENTIALLY, "INDIRECT", ("geopolitical_risk",)),
        (
            "Persian Gulf tensions disrupt crude oil shipments",
            "XAUUSD",
            POTENTIALLY,
            "INDIRECT",
            ("crude_oil", "geopolitical_risk"),
        ),
        ("OPEC announces a production cut", "XAUUSD", POTENTIALLY, "INDIRECT", ("energy_supply",)),
        ("Apple launches a new consumer device", "XAUUSD", NOT_OBVIOUS, None, ()),
        # USOIL: supply and geopolitics are direct; demand and policy are macro.
        (
            "Major pipeline disruption affects crude exports",
            "USOIL",
            RELEVANT,
            "DIRECT",
            ("crude_oil", "energy_supply"),
        ),
        ("OPEC+ announces production cuts", "USOIL", RELEVANT, "DIRECT", ("energy_supply",)),
        (
            "Persian Gulf shipping routes face disruption",
            "USOIL",
            RELEVANT,
            "DIRECT",
            ("geopolitical_risk",),
        ),
        ("China industrial activity slows", "USOIL", POTENTIALLY, "MACRO", ("labor_and_growth",)),
        (
            "Federal Reserve changes interest-rate policy",
            "USOIL",
            POTENTIALLY,
            "MACRO",
            ("monetary_policy",),
        ),
        ("Apple announces a new iPhone", "USOIL", NOT_OBVIOUS, None, ()),
        # NASDAQ: the sector is direct; rates, inflation and growth are macro.
        ("Federal Reserve signals higher rates", "NAS100", POTENTIALLY, "MACRO", ("monetary_policy",)),
        ("US Treasury yields rise sharply", "NAS100", POTENTIALLY, "MACRO", ("rates_and_yields",)),
        ("US CPI inflation surprises", "NAS100", POTENTIALLY, "MACRO", ("inflation",)),
        (
            "Major semiconductor export restrictions announced",
            "NAS100",
            RELEVANT,
            "DIRECT",
            ("technology_sector", "trade_and_tariffs"),
        ),
        ("AI regulation changes in the United States", "NAS100", RELEVANT, "DIRECT", ("technology_sector",)),
        (
            "Apple announces a major product launch",
            "NAS100",
            RELEVANT,
            "DIRECT",
            ("major_technology_companies",),
        ),
        # Reaches an equity index only indirectly, and is deliberately not
        # overstated beyond that.
        ("Saudi oil production changes", "NAS100", POTENTIALLY, "INDIRECT", ("crude_oil",)),
    ],
)
def test_relevance_matrix(
    title: str,
    symbol: str,
    level: RelevanceLevel,
    kind: str | None,
    domains: tuple[str, ...],
) -> None:
    relevance = classify_instrument_relevance(news(title), symbol)

    # The instrument symbol never appears in any of these titles.
    assert symbol.lower() not in title.lower()
    assert relevance.symbol == symbol
    assert relevance.level is level
    assert (relevance.kind.value if relevance.kind else None) == kind
    assert tuple(domain.value for domain in relevance.domains) == domains
    assert relevance.reason


def test_relevance_matrix_titles_are_never_named_after_an_instrument() -> None:
    # Guards the matrix itself: it must exercise inference, not tag matching.
    for title in ("Gold demand rises as central banks increase purchases", "OPEC+ announces production cuts"):
        assert "XAUUSD" not in title
        assert "USOIL" not in title


# --- cross-instrument behaviour -------------------------------------------------------------


def test_the_same_item_reaches_two_instruments_at_different_levels() -> None:
    item = news("Persian Gulf tensions disrupt crude oil shipments")

    oil = classify_instrument_relevance(item, "USOIL")
    gold = classify_instrument_relevance(item, "XAUUSD")
    index = classify_instrument_relevance(item, "NAS100")

    # Direct for the underlying commodity, indirect for the others.
    assert oil.level is RELEVANT
    assert gold.level is POTENTIALLY
    assert index.level is POTENTIALLY
    assert oil.kind is RelevanceKind.DIRECT
    assert gold.kind is RelevanceKind.INDIRECT
    assert oil.domains == index.domains == (FundamentalDomain.CRUDE_OIL, FundamentalDomain.GEOPOLITICAL_RISK)


@pytest.mark.parametrize(
    ("title", "symbols"),
    [
        # Purely metal-specific: never highly relevant to the others.
        ("Gold demand rises as central banks increase purchases", ("USOIL", "NAS100")),
        # Purely semiconductor-specific: never highly relevant to the others.
        ("Major semiconductor export restrictions announced", ("XAUUSD", "USOIL")),
        # Purely oil-production-specific: never highly relevant to an index.
        ("OPEC+ announces production cuts", ("XAUUSD", "NAS100")),
        # A company launch is not a gold or oil story.
        ("Apple announces a major product launch", ("XAUUSD", "USOIL")),
    ],
)
def test_no_instrument_is_highly_relevant_to_another_market_story(
    title: str, symbols: tuple[str, ...]
) -> None:
    for symbol in symbols:
        relevance = classify_instrument_relevance(news(title), symbol)

        assert relevance.level is not RELEVANT, (symbol, title)
        # No direct transmission is claimed either: a different market's story is
        # at most a documented macro or indirect factor.
        assert relevance.kind is None or relevance.kind.value != "DIRECT", (symbol, title)


def test_each_instrument_gets_its_own_classification_of_one_item() -> None:
    item = news("US CPI inflation surprises")

    levels = {
        symbol: classify_instrument_relevance(item, symbol).level
        for symbol in ("XAUUSD", "USOIL", "NAS100")
    }

    assert set(levels.values()) == {POTENTIALLY}


# --- the symbol-string view is preserved ------------------------------------------------------


def test_an_instrument_without_a_profile_keeps_the_symbol_string_behaviour() -> None:
    # EURUSD has no fundamental profile: the established currency-leg rules decide.
    relevance = classify_instrument_relevance(news("Quarterly statement", currencies=("EUR",)), "EURUSD")

    assert relevance.level is POTENTIALLY
    assert relevance.kind is None
    assert relevance.domains == ()
    assert "EUR is the base currency of EURUSD" in relevance.reason


def test_a_symbol_without_a_profile_and_without_a_currency_leg_stays_unclassified() -> None:
    relevance = classify_instrument_relevance(news("Quarterly statement", currencies=("USD",)), "US30")

    assert relevance.level is NOT_OBVIOUS
    assert "No currency leg could be identified" in relevance.reason


def test_a_declared_instrument_tag_is_not_a_profile_match() -> None:
    # Documented boundary: a source's own tag is a labelling claim, so it keeps
    # the conservative symbol-string level and carries no factor attribution.
    relevance = classify_instrument_relevance(
        news("Quarterly statement", instruments=("XAUUSD",)), "XAUUSD"
    )

    assert relevance.level is POTENTIALLY
    assert relevance.kind is None
    assert relevance.domains == ()
    assert "named directly" in relevance.reason


def test_a_profile_item_that_names_the_instrument_is_rule_free() -> None:
    relevance = classify_instrument_relevance(
        news("Gold prices steady", instruments=("XAUUSD",)), "XAUUSD"
    )

    assert relevance.level is RELEVANT
    assert relevance.domains == (FundamentalDomain.PRECIOUS_METALS,)


def test_classification_is_deterministic() -> None:
    item = news("Gold demand rises as central banks increase purchases")

    first = classify_instrument_relevance(item, "XAUUSD")
    second = classify_instrument_relevance(item, "XAUUSD")

    assert first == second


# --- the calendar layer shares the same vocabulary ----------------------------------------------


def test_a_calendar_event_is_attributed_the_same_domain_as_a_news_item() -> None:
    event_domains = match_domains("US Consumer Price Index (CPI) YoY")
    item_domains = match_domains("US CPI inflation accelerates")

    assert FundamentalDomain.INFLATION in event_domains
    assert FundamentalDomain.INFLATION in item_domains


def test_a_currency_qualified_event_gets_its_documented_factor_attributed() -> None:
    relevance = classify_relevance(event("US Consumer Price Index (CPI) YoY"), position("XAUUSD"))

    # The currency-scoped level contract is unchanged...
    assert relevance.relevance is POTENTIALLY
    assert "quote currency" in relevance.reason
    # ...and the shared vocabulary explains WHICH factor the event concerns.
    assert relevance.kind is not None
    assert relevance.kind.value == "MACRO"
    assert relevance.domains == (FundamentalDomain.INFLATION,)
    assert "inflation and price data" in relevance.reason


def test_a_monetary_policy_event_and_item_share_one_domain() -> None:
    event_relevance = classify_relevance(event("FOMC Rate Decision", currency="USD"), position("XAUUSD"))
    item_relevance = classify_instrument_relevance(
        news("Fed officials signal fewer rate cuts"), "XAUUSD"
    )

    assert event_relevance.domains == (FundamentalDomain.MONETARY_POLICY,)
    assert item_relevance.domains == (FundamentalDomain.MONETARY_POLICY,)
    assert event_relevance.relevance is POTENTIALLY
    assert item_relevance.level is POTENTIALLY


def test_an_unrelated_currency_event_keeps_its_currency_scoped_verdict() -> None:
    # A JPY event against a gold position is not escalated by the domain layer:
    # the event's currency is not a leg of the instrument, which is the calendar
    # layer's documented and tested scoping rule.
    relevance = classify_relevance(
        event("Japan BoJ Interest Rate Decision", currency="JPY"), position("XAUUSD")
    )

    assert relevance.relevance is NOT_OBVIOUS
    assert relevance.domains == ()
    assert relevance.kind is None
    assert "not a currency leg" in relevance.reason


def test_a_symbol_with_no_currency_leg_uses_its_profile_for_calendar_events() -> None:
    cpi = classify_relevance(event("US Consumer Price Index (CPI) YoY"), position("NAS100"))
    boj = classify_relevance(
        event("Japan BoJ Interest Rate Decision", currency="JPY"), position("NAS100")
    )

    # An index CFD has no currency leg, so the documented profile is the evidence.
    assert cpi.relevance is POTENTIALLY
    assert cpi.domains == (FundamentalDomain.INFLATION,)
    assert "fundamental profile" in cpi.reason
    # A foreign central-bank decision is a macro factor, never a direct one.
    assert boj.relevance is POTENTIALLY
    assert boj.domains == (FundamentalDomain.MONETARY_POLICY,)
    assert boj.kind is not None and boj.kind.value == "MACRO"


def test_a_symbol_with_no_currency_leg_and_no_profile_is_unchanged() -> None:
    relevance = classify_relevance(event("US Consumer Price Index (CPI) YoY"), position("US30"))

    assert relevance.relevance is NOT_OBVIOUS
    assert "No currency leg could be identified" in relevance.reason


def test_a_direct_profile_match_is_the_only_route_to_the_strongest_level() -> None:
    # An event about the instrument's own market, for a symbol with no currency
    # leg, is the one case where a calendar classification may assert RELEVANT.
    relevance = classify_relevance(
        event("Nasdaq 100 technology sector index rebalance", currency="USD"), position("NAS100")
    )

    assert relevance.relevance is RELEVANT
    assert relevance.kind is not None and relevance.kind.value == "DIRECT"


# --- broker-decorated spellings reach their profile ------------------------------------------


def test_a_brent_position_s_forward_separator_spelling_reaches_the_oil_profile() -> None:
    # The regression this fix exists for: UKOIL. (a broker's Brent spelling,
    # trailing separator and all) has no currency leg, and a USD FOMC decision
    # used to be NOT_OBVIOUSLY_RELEVANT merely because the crude-oil profile
    # did not document the Brent spellings. The profile is a data table: Brent
    # is the same commodity, so the documented macro tier decides.
    relevance = classify_relevance(event("FOMC Rate Decision"), position("UKOIL."))

    assert relevance.relevance is POTENTIALLY
    assert relevance.kind is not None and relevance.kind.value == "MACRO"
    assert relevance.domains == (FundamentalDomain.MONETARY_POLICY,)
    assert "fundamental profile" in relevance.reason
    assert relevance.symbol == "UKOIL."  # the broker's spelling stays visible


def test_brent_spellings_reach_direct_oil_factors() -> None:
    for symbol in ("UKOIL", "BRENT"):
        relevance = classify_relevance(
            event("Persian Gulf tensions disrupt crude shipments"), position(symbol)
        )

        assert relevance.relevance is RELEVANT
        assert relevance.kind is not None and relevance.kind.value == "DIRECT"
        assert relevance.domains == (FundamentalDomain.CRUDE_OIL, FundamentalDomain.GEOPOLITICAL_RISK)


def test_decorated_index_and_gold_spellings_reach_their_profiles() -> None:
    cpi = classify_relevance(event("US CPI inflation accelerates"), position("US100."))
    fomc = classify_relevance(event("FOMC Rate Decision"), position("XAUUSD.r"))

    assert cpi.relevance is POTENTIALLY
    assert cpi.kind is not None and cpi.kind.value == "MACRO"
    assert cpi.domains == (FundamentalDomain.INFLATION,)
    assert "fundamental profile" in cpi.reason
    assert fomc.relevance is POTENTIALLY
    assert fomc.kind is not None and fomc.kind.value == "MACRO"
    assert fomc.domains == (FundamentalDomain.MONETARY_POLICY,)


def test_a_profile_is_never_a_blanket_for_every_event() -> None:
    # Relevance must come from the profile's configured factor domains: an event
    # about a factor the profile does not document stays NOT_OBVIOUSLY_RELEVANT.
    # (XAUUSD.r is deliberately absent: it HAS a USD currency leg, so a USD
    # event is currency-relevant for it regardless of any profile.)
    for symbol in ("UKOIL.", "BRENT", "US100."):
        relevance = classify_relevance(event("US Grain Stocks Report"), position(symbol))

        assert relevance.relevance is NOT_OBVIOUS
        assert relevance.kind is None
        assert relevance.domains == ()


def test_currency_leg_relevance_is_unchanged_for_profiled_and_unprofiled_pairs() -> None:
    # The currency-scoped contract is authoritative for symbols with a leg:
    # USDJPY keeps its base-currency verdict with no factor attribution, and a
    # profiled symbol whose event currency IS a leg keeps its existing wording.
    usdjpy_fomc = classify_relevance(event("FOMC Rate Decision"), position("USDJPY"))
    assert usdjpy_fomc.relevance is POTENTIALLY
    assert usdjpy_fomc.kind is None
    assert usdjpy_fomc.domains == ()
    assert "base currency" in usdjpy_fomc.reason

    gold_usd = classify_relevance(event("FOMC Rate Decision"), position("XAUUSD"))
    assert gold_usd.relevance is POTENTIALLY
    assert gold_usd.kind is not None and gold_usd.kind.value == "MACRO"
    assert "pricing currency" in gold_usd.reason


def test_multiple_evidence_sources_combine_deterministically() -> None:
    # The same event may reach a position through BOTH the currency rule and the
    # documented profile: XAUUSD.r has a USD leg (currency wording first) and the
    # factor sentence is appended, in a fixed order, every time.
    first = classify_relevance(event("FOMC Rate Decision"), position("XAUUSD.r"))
    second = classify_relevance(event("FOMC Rate Decision"), position("XAUUSD.r"))

    assert first == second
    assert first.reason.startswith("USD is the quote currency of XAUUSD.r")
    assert "central-bank monetary policy" in first.reason
    assert first.kind is not None and first.kind.value == "MACRO"


def test_an_unprofiled_instrument_without_a_currency_leg_stays_not_obvious() -> None:
    # US30 keeps its fail-closed verdict, and so does a broker-decorated symbol
    # no profile covers (COCOA. — a trailing separator changes nothing).
    for symbol in ("US30", "COCOA."):
        relevance = classify_relevance(event("US CPI inflation accelerates"), position(symbol))

        assert relevance.relevance is NOT_OBVIOUS
        assert "No currency leg could be identified" in relevance.reason


# --- fact only: no advice, no prediction -------------------------------------------------------


def test_no_reason_is_directional_or_predictive() -> None:
    titles = (
        "Gold demand rises as central banks increase purchases",
        "Federal Reserve officials discuss interest rates",
        "Persian Gulf tensions disrupt crude oil shipments",
        "Major semiconductor export restrictions announced",
        "Apple launches a new consumer device",
        "Saudi oil production changes",
    )
    calendar_reasons = [
        classify_relevance(event(title, currency="USD"), position("XAUUSD")).reason
        for title in titles
    ]
    calendar_reasons += [
        classify_relevance(event(title, currency="USD"), position("NAS100")).reason
        for title in titles
    ]

    reasons = [
        classify_instrument_relevance(news(title), symbol).reason
        for title in titles
        for symbol in ("XAUUSD", "USOIL", "NAS100")
    ] + calendar_reasons

    for reason in reasons:
        lowered = reason.lower()
        assert lowered
        for forbidden in _FORBIDDEN_WORDS:
            assert forbidden not in lowered, (forbidden, reason)


def test_the_reported_levels_are_only_the_discrete_contract_values() -> None:
    levels = {
        classify_instrument_relevance(news(title), symbol).level
        for title in ("Gold prices rise", "US CPI data due", "Quarterly statement")
        for symbol in ("XAUUSD", "USOIL", "NAS100", "EURUSD", "US30")
    }

    assert levels <= {RELEVANT, POTENTIALLY, NOT_OBVIOUS}
