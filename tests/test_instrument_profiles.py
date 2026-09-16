"""Tests for the shared instrument/domain vocabulary (READ-ONLY, no LLM).

Pins the properties the instrument-aware relevance layer depends on: the domain
vocabulary is one deterministic table shared by the calendar and news layers, an
instrument's profile states a *transmission* relationship per domain with no
tier overlap, a symbol resolves to its profile through its documented roots, and
nothing in the vocabulary expresses a direction, a magnitude or a probability.

Offline and deterministic: no MT5, no database, no network, no LLM.
"""
import pytest

from app.services.instrument_intelligence import (
    DOMAIN_LABELS,
    DOMAIN_ORDER,
    FOCUS_INSTRUMENT_NAMES,
    PROFILES,
    FundamentalDomain,
    RelevanceKind,
    factor_reason,
    focus_symbol_for_token,
    match_domains,
    match_profile_domains,
    matched_domain_terms,
    normalize_text,
    profile_for,
)

# Wording that would turn a factual exposure statement into advice or a
# prediction. No label, keyword or reason this layer produces may contain any of
# it (and no score-like vocabulary either).
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
    "score",
    "signal",
    "will",
    "outlook",
)


# --- the vocabulary table ---------------------------------------------------------------


def test_every_domain_has_a_label() -> None:
    assert set(DOMAIN_LABELS) == set(FundamentalDomain)


def test_domain_order_covers_every_domain_once() -> None:
    assert set(DOMAIN_ORDER) == set(FundamentalDomain)
    assert len(DOMAIN_ORDER) == len(set(DOMAIN_ORDER))


def test_labels_and_keywords_contain_no_directional_or_predictive_wording() -> None:
    texts = [*DOMAIN_LABELS.values(), *[domain.value for domain in FundamentalDomain]]
    for kind, domain in (
        (RelevanceKind.DIRECT, FundamentalDomain.PRECIOUS_METALS),
        (RelevanceKind.MACRO, FundamentalDomain.INFLATION),
        (RelevanceKind.INDIRECT, FundamentalDomain.ENERGY_SUPPLY),
    ):
        texts.append(factor_reason(kind, (domain,), "XAUUSD", subject="The item"))

    for text in texts:
        lowered = text.lower()
        for forbidden in _FORBIDDEN_WORDS:
            assert forbidden not in lowered, (forbidden, text)


def test_every_profile_name_is_directional_free() -> None:
    for profile in PROFILES:
        lowered = profile.name.lower()
        for forbidden in _FORBIDDEN_WORDS:
            assert forbidden not in lowered, (forbidden, profile.name)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Gold demand rises", ("precious_metals",)),
        ("US CPI inflation accelerates", ("inflation",)),
        ("US Treasury yields rise", ("rates_and_yields",)),
        ("Federal Reserve discusses interest rates", ("monetary_policy",)),
        ("Persian Gulf tensions disrupt crude oil shipments", ("crude_oil", "geopolitical_risk")),
        # A hyphenated spelling is the same factor as the spaced one.
        ("US 10-year yield rises", ("rates_and_yields",)),
        # Whole words only: a partial word is never a match.
        ("Inflationary pressures mentioned", ()),
        ("Boiling water", ()),
        # Nothing documented: an explicit "nothing matched", never a guess.
        ("Market wrap: energy and shipping costs in focus", ()),
        ("", ()),
    ],
)
def test_domain_matching_is_whole_word_and_deterministic(
    text: str, expected: tuple[str, ...]
) -> None:
    assert match_domains(text) == expected


def test_matched_terms_name_the_keyword_that_matched() -> None:
    terms = dict(matched_domain_terms("OPEC+ announces production cuts"))

    assert FundamentalDomain.ENERGY_SUPPLY in terms
    assert "opec" in terms[FundamentalDomain.ENERGY_SUPPLY]
    assert "production cuts" in terms[FundamentalDomain.ENERGY_SUPPLY]


def test_matching_is_table_ordered_not_set_ordered() -> None:
    # Both domains match; the result follows the vocabulary's own order.
    first = match_domains("Gold prices and Nasdaq technology shares")
    second = match_domains("Gold prices and Nasdaq technology shares")

    assert first == second
    assert first == ("precious_metals", "technology_sector")
    assert list(first) == [domain for domain in DOMAIN_ORDER if domain in set(first)]


def test_normalization_is_lowercase_and_hyphen_insensitive() -> None:
    assert normalize_text("  US   10-Year   Yield ") == "us 10 year yield"
    assert normalize_text("Crude-Oil") == normalize_text("crude oil")


# --- profiles -----------------------------------------------------------------------------


def test_every_profile_has_non_empty_tiers_that_never_overlap() -> None:
    for profile in PROFILES:
        assert profile.direct, profile.name
        assert profile.macro, profile.name
        assert profile.indirect, profile.name
        tiers = (set(profile.direct), set(profile.macro), set(profile.indirect))
        assert tiers[0] & tiers[1] == set(), profile.name
        assert tiers[0] & tiers[2] == set(), profile.name
        assert tiers[1] & tiers[2] == set(), profile.name


def test_every_profile_resolves_its_own_canonical_symbol() -> None:
    for profile in PROFILES:
        assert profile.canonical in profile.symbols
        assert profile.canonical in profile.focus_names
        assert profile_for(profile.canonical) is profile


def test_profiles_are_a_static_table_with_a_stable_order() -> None:
    assert [profile.canonical for profile in PROFILES] == ["XAUUSD", "USOIL", "NAS100"]


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("XAUUSD", "XAUUSD"),
        ("xauusd", "XAUUSD"),
        ("XAUUSD.r", "XAUUSD"),
        ("GOLD", "XAUUSD"),
        ("USOIL", "USOIL"),
        ("USOIL.cash", "USOIL"),
        ("WTI", "USOIL"),
        # The documented Brent/WTI alias spellings (audit-verified broker forms).
        ("UKOIL", "USOIL"),
        ("UKOIL.", "USOIL"),
        ("BRENT", "USOIL"),
        ("BRENTUSD", "USOIL"),
        ("XBRUSD", "USOIL"),
        ("USCRUDE", "USOIL"),
        ("UKBRAND", "USOIL"),
        ("NAS100", "NAS100"),
        ("nasdaq", "NAS100"),
        ("US100.i", "NAS100"),
        ("NASDAQ100", "NAS100"),
        ("USTECH", "NAS100"),
        ("NDXUSD", "NAS100"),
        # Instruments without a profile keep their existing behaviour.
        ("EURUSD", None),
        ("US30", None),
        ("GBPJPY", None),
        ("", None),
    ],
)
def test_symbol_resolves_to_its_profile(symbol: str, expected: str | None) -> None:
    profile = profile_for(symbol)

    assert (profile.canonical if profile else None) == expected


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("USOIL", "USOIL"),
        ("wti", "USOIL"),
        ("NASDAQ", "NAS100"),
        ("us100", "NAS100"),
        ("XAUUSD", "XAUUSD"),
        # A commodity word is not an instrument name: never a focus instrument.
        ("GOLD", None),
        ("OIL", None),
        ("gold", None),
        ("USD", None),
        ("NAS", None),
        ("", None),
    ],
)
def test_focus_names_are_error_resistant(token: str, expected: str | None) -> None:
    assert focus_symbol_for_token(token) == expected


def test_commodity_words_are_not_focus_instrument_names() -> None:
    assert "GOLD" not in FOCUS_INSTRUMENT_NAMES
    assert "OIL" not in FOCUS_INSTRUMENT_NAMES
    assert FOCUS_INSTRUMENT_NAMES == {
        "XAUUSD",
        "USOIL",
        "WTI",
        "XTIUSD",
        "OILUSD",
        "NASDAQ",
        "NAS100",
        "US100",
        "USTEC",
    }


# --- profile matching ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("canonical", "text", "kind", "domains"),
    [
        (
            "XAUUSD",
            "Gold demand rises as central banks increase purchases",
            RelevanceKind.DIRECT,
            ("precious_metals",),
        ),
        (
            "XAUUSD",
            "Federal Reserve officials discuss interest rates",
            RelevanceKind.MACRO,
            ("monetary_policy",),
        ),
        (
            "XAUUSD",
            "Persian Gulf tensions escalate",
            RelevanceKind.INDIRECT,
            ("geopolitical_risk",),
        ),
        # Several factors in the same tier are all reported.
        (
            "USOIL",
            "Persian Gulf tensions disrupt crude oil shipments",
            RelevanceKind.DIRECT,
            ("crude_oil", "geopolitical_risk"),
        ),
        ("USOIL", "China industrial activity slows", RelevanceKind.MACRO, ("labor_and_growth",)),
        (
            "NAS100",
            "Major semiconductor export restrictions announced",
            RelevanceKind.DIRECT,
            ("technology_sector", "trade_and_tariffs"),
        ),
        ("NAS100", "US CPI inflation surprises", RelevanceKind.MACRO, ("inflation",)),
        # The weaker tier is reported when the stronger one does not match.
        ("NAS100", "Saudi oil production changes", RelevanceKind.INDIRECT, ("crude_oil",)),
        ("XAUUSD", "Apple launches a new consumer device", None, ()),
        ("USOIL", "Apple announces a new iPhone", None, ()),
        ("XAUUSD", "Market wrap: energy and shipping costs in focus", None, ()),
    ],
)
def test_profile_matching_reports_the_strongest_tier(
    canonical: str, text: str, kind: RelevanceKind | None, domains: tuple[str, ...]
) -> None:
    profile = profile_for(canonical)
    assert profile is not None

    match = match_profile_domains(profile, text)

    assert (match.kind if match else None) is kind
    assert (match.domains if match else ()) == domains


def test_profile_matching_is_deterministic() -> None:
    profile = profile_for("XAUUSD")
    assert profile is not None

    text = "Gold, Fed policy and Treasury yields all in focus"

    assert match_profile_domains(profile, text) == match_profile_domains(profile, text)
