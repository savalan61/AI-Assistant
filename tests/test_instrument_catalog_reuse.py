"""Multi-symbol resolution discovers the broker's catalog ONCE.

Resolving a suffixed instrument needs the broker's catalog (the requested base
``XAUUSD`` has to be matched to the broker's own ``XAUUSD.r``), and the MT5
Python API serializes every read on the one process-wide session — so scanning
that same catalog once per requested name made one request pay N catalog reads
for a snapshot that cannot change between them.

``InstrumentService.resolve_many`` resolves a whole set against ONE catalog
discovery: the exact lookups run first (one vendor call each, exactly as
before), and the first name that needs the fallback triggers the single catalog
read every remaining name reuses. ``resolve`` is that same operation for one
name, so the rules, the broker spelling and the ambiguity/unknown outcomes are
identical between the two paths.

Two layers are pinned here:

* the service, through a RECORDING fake provider (``get_calls`` per-name lookups
  and ``list_calls`` catalog scans) — one scan for 2, 3 or more suffixed names,
  none at all when every name is spelled exactly, and no reuse between calls;
* the terminal, through a recording fake MT5 behind the REAL session manager,
  provider, service and research service (and the real research router), counting
  ``symbols_get`` (= catalog discovery) and ``symbol_info`` (= per-name lookup) at
  the seam the application actually calls.

Everything here is offline: no MT5 terminal, no network, no database, no real
credentials. No pytest asyncio plugin — the async handler is driven with
asyncio.run.
"""
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.fundamental_intelligence_router import get_todays_financial_research
from app.core.encryption import encrypt_secret, generate_encryption_key
from app.core.mt5_session import MT5AccountCredentials, MT5SessionManager
from app.db.models import User, UserRole
from app.providers.fake_instrument import FakeInstrumentProvider
from app.providers.instrument import Instrument, InstrumentProvider, TradeMode
from app.providers.mt5_instruments import MT5InstrumentProvider
from app.services.fundamental_intelligence import FinancialResearchService
from app.services.instruments import InstrumentResolution, InstrumentService

WINDOW_FROM = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
WINDOW_TO = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)
PASSWORD = "mt5-investor-password-under-test"
SERVER = "BrokerA-Live"


@pytest.fixture(autouse=True)
def test_only_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test-only encryption key, so the tenant credentials round-trip for real."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", generate_encryption_key(), raising=True)


def instrument(symbol: str) -> Instrument:
    return Instrument(
        symbol=symbol,
        name=f"{symbol} instrument",
        asset_class="Metals",
        base_currency=None,
        quote_currency=None,
        digits=2,
        trade_mode=TradeMode.FULL,
    )


def service_over(*symbols: str) -> tuple[InstrumentService, FakeInstrumentProvider]:
    """The REAL service over a recording fake provider (one catalog: ``symbols``)."""
    provider = FakeInstrumentProvider(instruments=tuple(instrument(symbol) for symbol in symbols))
    return InstrumentService(provider), provider


class _UnavailableCatalogProvider(InstrumentProvider):
    """A provider whose catalog read is an availability failure (RuntimeError)."""

    def get_instrument(self, symbol: str) -> Instrument:
        raise ValueError(f"MT5 does not offer instrument {symbol}")

    def list_instruments(self) -> tuple[Instrument, ...]:
        raise RuntimeError("MT5 instrument catalog request failed")


# --- the service: one discovery per operation ---------------------------------


def test_exact_symbols_never_read_the_catalog() -> None:
    """A spelling the terminal knows exactly costs one lookup and no scan."""
    service, provider = service_over("XAUUSD", "EURUSD", "AAPL")

    outcomes = service.resolve_many(("XAUUSD", "EURUSD", "AAPL"))

    assert [outcome.instrument.symbol for outcome in outcomes if outcome.instrument] == [
        "XAUUSD",
        "EURUSD",
        "AAPL",
    ]
    assert provider.get_calls == ["XAUUSD", "EURUSD", "AAPL"]
    assert provider.list_calls == 0


def test_three_suffixed_symbols_scan_the_catalog_once() -> None:
    """The regression: N suffixed names used to cost N catalog scans."""
    service, provider = service_over("XAUUSD.r", "US30.r", "UKOIL.r")

    outcomes = service.resolve_many(("XAUUSD", "US30", "UKOIL"))

    assert [outcome.instrument.symbol for outcome in outcomes if outcome.instrument] == [
        "XAUUSD.r",
        "US30.r",
        "UKOIL.r",
    ]
    assert provider.get_calls == ["XAUUSD", "US30", "UKOIL"]
    assert provider.list_calls == 1  # ONE discovery for all three


def test_two_suffixed_symbols_scan_the_catalog_once() -> None:
    service, provider = service_over("XAUUSD.r", "UKOIL.r")

    outcomes = service.resolve_many(("XAUUSD", "UKOIL"))

    assert all(outcome.instrument is not None for outcome in outcomes)
    assert provider.list_calls == 1


def test_mixed_resolved_and_unresolved_symbols_share_one_scan() -> None:
    """An unknown name does not buy its own scan, and does not poison the rest."""
    service, provider = service_over("XAUUSD.r", "EURUSD")

    outcomes = service.resolve_many(("XAUUSD", "EURUSD", "NOTREAL"))

    assert [outcome.requested for outcome in outcomes] == ["XAUUSD", "EURUSD", "NOTREAL"]
    resolved = {outcome.requested: outcome.instrument.symbol for outcome in outcomes if outcome.instrument}
    assert resolved == {"XAUUSD": "XAUUSD.r", "EURUSD": "EURUSD"}
    unresolved = {outcome.requested: outcome.reason for outcome in outcomes if outcome.instrument is None}
    assert unresolved == {"NOTREAL": "MT5 does not offer instrument NOTREAL"}
    assert provider.list_calls == 1


def test_an_ambiguous_name_is_reported_for_that_name_only() -> None:
    """Ambiguity still fails that one name closed, without guessing or re-scanning."""
    service, provider = service_over("XAUUSD.r", "XAUUSD.m", "EURUSD")

    outcomes = service.resolve_many(("XAUUSD", "EURUSD"))

    assert outcomes[0].instrument is None
    assert outcomes[0].reason == "Instrument name is ambiguous: XAUUSD"
    assert outcomes[1].instrument is not None and outcomes[1].instrument.symbol == "EURUSD"
    assert provider.list_calls == 1


def test_input_order_and_the_brokers_own_spelling_are_preserved() -> None:
    service, _ = service_over("XAUUSD.r", "UKOIL.m")

    outcomes = service.resolve_many(("UKOIL", "xauusd"))

    assert [outcome.requested for outcome in outcomes] == ["UKOIL", "xauusd"]
    assert [outcome.instrument.symbol for outcome in outcomes if outcome.instrument] == [
        "UKOIL.m",
        "XAUUSD.r",
    ]


def test_no_input_reads_nothing() -> None:
    service, provider = service_over("XAUUSD.r")

    assert service.resolve_many(()) == ()
    assert provider.get_calls == [] and provider.list_calls == 0


def test_a_malformed_name_is_a_caller_error_before_any_read() -> None:
    """Normalisation happens first: an invalid name never turns into a lookup."""
    service, provider = service_over("XAUUSD.r")

    with pytest.raises(ValueError):
        service.resolve_many(("XAUUSD", "   "))

    assert provider.get_calls == [] and provider.list_calls == 0


def test_a_catalog_availability_failure_is_not_an_unresolved_instrument() -> None:
    """RuntimeError propagates: an MT5 failure must never read as "not offered"."""
    service = InstrumentService(_UnavailableCatalogProvider())

    with pytest.raises(RuntimeError):
        service.resolve_many(("XAUUSD", "UKOIL"))


def test_separate_calls_do_not_share_a_catalog_snapshot() -> None:
    """Reuse is request-local: the next operation discovers the catalog again."""
    service, provider = service_over("XAUUSD.r")

    service.resolve_many(("XAUUSD",))
    service.resolve_many(("XAUUSD",))

    assert provider.list_calls == 2


def test_single_name_resolution_still_uses_the_same_rules() -> None:
    """resolve() is resolve_many() for one name — same records, same errors."""
    service, provider = service_over("XAUUSD.r", "XAUUSD.m")

    with pytest.raises(ValueError, match="ambiguous"):
        service.resolve("XAUUSD")

    exact, _ = service_over("XAUUSD", "XAUUSD.r")
    outcome = exact.resolve_many(("XAUUSD",))[0]
    assert outcome.instrument is not None
    # The exact spelling wins over the decorated variant, in both paths.
    assert exact.resolve("XAUUSD").symbol == "XAUUSD"
    assert outcome.instrument.symbol == "XAUUSD"

    unknown, _ = service_over("EURUSD")
    with pytest.raises(ValueError, match="does not offer"):
        unknown.resolve("XAUUSD")


def test_resolve_and_resolve_many_agree_on_the_same_catalog() -> None:
    service, _ = service_over("XAUUSD.r", "EURUSD", "NOTREAL")

    for requested in ("XAUUSD", "EURUSD", "NOTREAL"):
        outcome = service.resolve_many((requested,))[0]
        if outcome.instrument is None:
            with pytest.raises(ValueError) as excinfo:
                service.resolve(requested)
            assert str(excinfo.value) == outcome.reason
        else:
            assert service.resolve(requested).symbol == outcome.instrument.symbol


# --- the terminal: one catalog discovery per request --------------------------


class RecordingMT5:
    """A recording stand-in for the MT5 surface the instrument path uses.

    Serves each tenant's OWN catalog from the account the fake is authenticated
    as — a real terminal behaves exactly that way — so a test can prove one
    tenant never resolves against another tenant's symbols.
    """

    def __init__(self, symbols_by_login: dict[int, tuple[SimpleNamespace, ...]]) -> None:
        self.calls: list[str] = []
        self.symbols_by_login = symbols_by_login
        self.authenticated: tuple[str, int] | None = None

    def _record(self, name: str) -> None:
        self.calls.append(name)

    def initialize(self, **kwargs: object) -> object:
        self._record("initialize")
        self.authenticated = (str(kwargs["server"]), int(kwargs["login"]))  # type: ignore[arg-type]
        return True

    def login(self, **kwargs: object) -> object:
        self._record("login")
        self.authenticated = (str(kwargs["server"]), int(kwargs["login"]))  # type: ignore[arg-type]
        return True

    def last_error(self) -> tuple[int, str]:
        self._record("last_error")
        return (-1, "simulated MT5 failure")

    def account_info(self) -> object:
        self._record("account_info")
        if self.authenticated is None:  # pragma: no cover - every read authenticates first
            return None
        server, login = self.authenticated
        return SimpleNamespace(login=login, server=server)

    def _catalog(self) -> tuple[SimpleNamespace, ...]:
        if self.authenticated is None:  # pragma: no cover - every read authenticates first
            return ()
        return self.symbols_by_login.get(self.authenticated[1], ())

    def symbol_info(self, symbol: str) -> object:
        self._record("symbol_info")
        return next((candidate for candidate in self._catalog() if candidate.name == symbol), None)

    def symbols_get(self) -> tuple[SimpleNamespace, ...]:
        self._record("symbols_get")
        return self._catalog()


def mt5_symbol(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        digits=2,
        trade_mode=4,
        description=f"{name} instrument",
        path="Metals",
        currency_base="XAU",
        currency_profit="USD",
    )


def make_user(broker_id: int = 1, user_id: int = 7) -> User:
    return User(
        id=user_id,
        broker_id=broker_id,
        login="10001",
        password_hash="x-not-a-real-hash",
        is_active=True,
        role=UserRole.CUSTOMER,
    )


def research_over(
    mt5: RecordingMT5,
    session: MT5SessionManager,
    *,
    login: int = 10001,
    server: str = SERVER,
) -> FinancialResearchService:
    """The REAL research service over the REAL provider/service, fake terminal only."""
    credentials = MT5AccountCredentials(
        login=login, server=server, password_encrypted=encrypt_secret(PASSWORD)
    )
    return FinancialResearchService(
        news_service=None,
        instrument_service=InstrumentService(
            MT5InstrumentProvider(session_manager=session, credentials=credentials)
        ),
    )


def test_a_multi_symbol_resolution_discovers_the_catalog_once() -> None:
    """The terminal-level regression: one symbols_get for three decorated names."""
    mt5 = RecordingMT5(
        {10001: (mt5_symbol("XAUUSD.r"), mt5_symbol("US30.r"), mt5_symbol("UKOIL.r"))}
    )
    research = research_over(mt5, MT5SessionManager(mt5_api=mt5))

    resolution = research.resolve_focus_symbols(("XAUUSD", "US30", "UKOIL"))

    assert resolution.resolved == ("UKOIL.r", "US30.r", "XAUUSD.r")
    assert resolution.unresolved == ()
    assert mt5.calls.count("symbols_get") == 1  # ONE catalog discovery
    assert mt5.calls.count("symbol_info") == 3  # one exact lookup per name


def test_a_request_that_names_only_exact_spellings_scans_nothing() -> None:
    mt5 = RecordingMT5({10001: (mt5_symbol("XAUUSD"), mt5_symbol("US30"))})
    research = research_over(mt5, MT5SessionManager(mt5_api=mt5))

    resolution = research.resolve_focus_symbols(("XAUUSD", "US30"))

    assert resolution.resolved == ("US30", "XAUUSD")
    assert mt5.calls.count("symbols_get") == 0
    assert mt5.calls.count("symbol_info") == 2


def test_the_research_router_discovers_the_catalog_once_per_request() -> None:
    """End to end through the real router coroutine (the /financial-research path)."""
    mt5 = RecordingMT5(
        {10001: (mt5_symbol("XAUUSD.r"), mt5_symbol("US30.r"), mt5_symbol("UKOIL.r"))}
    )
    research = research_over(mt5, MT5SessionManager(mt5_api=mt5))

    response = asyncio.run(
        get_todays_financial_research(
            WINDOW_FROM,
            WINDOW_TO,
            "XAUUSD,US30,UKOIL",
            make_user(),
            research,
        )
    )

    assert list(response.focus_symbols) == ["UKOIL.r", "US30.r", "XAUUSD.r"]
    assert mt5.calls.count("symbols_get") == 1
    assert mt5.calls.count("symbol_info") == 3


def test_an_unknown_symbol_still_fails_closed_after_one_discovery() -> None:
    """The 404 contract is unchanged — and it costs one catalog read, not one per name."""
    mt5 = RecordingMT5({10001: (mt5_symbol("XAUUSD.r"), mt5_symbol("US30.r"))})
    research = research_over(mt5, MT5SessionManager(mt5_api=mt5))

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            get_todays_financial_research(
                WINDOW_FROM,
                WINDOW_TO,
                "XAUUSD,NOTREAL",
                make_user(),
                research,
            )
        )

    assert excinfo.value.status_code == 404
    assert mt5.calls.count("symbols_get") == 1


def test_separate_requests_do_not_share_a_catalog() -> None:
    """No cache: the second request performs its own discovery."""
    mt5 = RecordingMT5({10001: (mt5_symbol("XAUUSD.r"),)})
    session = MT5SessionManager(mt5_api=mt5)

    for _ in range(2):
        research_over(mt5, session).resolve_focus_symbols(("XAUUSD",))

    assert mt5.calls.count("symbols_get") == 2


def test_two_tenants_resolve_against_their_own_catalogs() -> None:
    """Tenant isolation: each broker's own spelling, never the other's snapshot."""
    mt5 = RecordingMT5(
        {
            10001: (mt5_symbol("XAUUSD.r"),),
            10002: (mt5_symbol("XAUUSD.p"),),
        }
    )
    session = MT5SessionManager(mt5_api=mt5)
    tenant_a = research_over(mt5, session, login=10001)
    tenant_b = research_over(mt5, session, login=10002)

    resolution_a = tenant_a.resolve_focus_symbols(("XAUUSD",))
    resolution_b = tenant_b.resolve_focus_symbols(("XAUUSD",))

    assert resolution_a.resolved == ("XAUUSD.r",)
    assert resolution_b.resolved == ("XAUUSD.p",)
    # Each request discovered its own tenant's catalog once.
    assert mt5.calls.count("symbols_get") == 2


def test_the_batch_outcomes_expose_the_shared_resolution_contract() -> None:
    """resolve_many() reports per-name outcomes, not a second API shape."""
    service, _ = service_over("XAUUSD.r")

    outcomes = service.resolve_many(("XAUUSD",))

    assert isinstance(outcomes, tuple) and len(outcomes) == 1
    assert isinstance(outcomes[0], InstrumentResolution)
    assert outcomes[0].instrument is not None and outcomes[0].reason is None
