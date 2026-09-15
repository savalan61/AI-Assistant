"""Tests for FinancialContextService (composition, window, determinism, errors).

Require none of: real MT5, PostgreSQL, network, or real credentials. The
service is exercised with deterministic in-memory fakes for the three provider
contracts (the FakePositionProvider/FakeTradeHistoryProvider pattern); one test
additionally drives the real composition-root wiring with the provider seams
patched, so no MT5 terminal is needed. No pytest asyncio plugin.
"""
from datetime import UTC, datetime, timedelta, timezone

import pytest

import app.core.dependencies as deps
from app.core.mt5_session import MT5AccountCredentials
from app.providers.account_info import AccountInfo, AccountInfoProvider
from app.providers.fake_position import FakePositionProvider
from app.providers.fake_trade_history import FakeTradeHistoryProvider
from app.providers.position import Position, PositionProvider, PositionType
from app.providers.trade_history import TradeHistoryEntry, TradeHistoryProvider, TradeType
from app.services.account import AccountInfoService
from app.services.financial_context import (
    DEFAULT_TRADE_HISTORY_DAYS,
    FinancialContext,
    FinancialContextService,
)
from app.services.portfolio_intelligence import build_portfolio_intelligence
from app.services.positions import PositionService
from app.services.trade_history import TradeHistoryService

AS_OF = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

ACCOUNT = AccountInfo(
    login=10001,
    name="Test Trader",
    balance=10000.0,
    equity=10050.0,
    margin=250.0,
    free_margin=9800.0,
    margin_level=4020.0,
    currency="USD",
    server="Test-Server",
)

POSITIONS: tuple[Position, ...] = (
    Position(
        ticket=123456789,
        symbol="XAUUSD",
        type=PositionType.BUY,
        volume=0.10,
        open_price=3642.50,
        current_price=3648.20,
        profit=57.00,
    ),
    Position(
        ticket=987654321,
        symbol="EURUSD",
        type=PositionType.SELL,
        volume=1.00,
        open_price=1.0850,
        current_price=1.0820,
        profit=-30.00,
    ),
)

TRADES: tuple[TradeHistoryEntry, ...] = (
    TradeHistoryEntry(
        ticket=246802468,
        order_ticket=987654321,
        symbol="XAUUSD",
        type=TradeType.BUY,
        volume=0.10,
        price=3648.20,
        profit=57.00,
        time=datetime(2026, 9, 14, 12, 30, 0, tzinfo=UTC),
        close_reason=None,
        stop_loss=3635.00,
        take_profit=3650.00,
    ),
)


class _FakeAccountInfoProvider(AccountInfoProvider):
    """Deterministic in-memory account provider (no MT5)."""

    def __init__(self, account: AccountInfo = ACCOUNT) -> None:
        self.account = account
        self.call_count = 0

    def get_account_info(self) -> AccountInfo:
        self.call_count += 1
        return self.account


class _FailingAccountInfoProvider(AccountInfoProvider):
    def get_account_info(self) -> AccountInfo:
        raise RuntimeError("account information unavailable")


class _FailingPositionProvider(PositionProvider):
    def get_positions(self) -> tuple[Position, ...]:
        raise RuntimeError("open positions unavailable")


class _FailingTradeHistoryProvider(TradeHistoryProvider):
    def get_trade_history(self, from_time: datetime, to_time: datetime) -> tuple[TradeHistoryEntry, ...]:
        raise RuntimeError("trade history unavailable")


def make_service(
    account: AccountInfo = ACCOUNT,
    positions: tuple[Position, ...] = POSITIONS,
    trades: tuple[TradeHistoryEntry, ...] = TRADES,
) -> tuple[FinancialContextService, _FakeAccountInfoProvider, FakePositionProvider, FakeTradeHistoryProvider]:
    account_provider = _FakeAccountInfoProvider(account)
    position_provider = FakePositionProvider(positions=positions)
    trade_provider = FakeTradeHistoryProvider(trades=trades)
    service = FinancialContextService(
        account_service=AccountInfoService(account_provider),
        position_service=PositionService(position_provider),
        trade_history_service=TradeHistoryService(trade_provider),
    )
    return service, account_provider, position_provider, trade_provider


# --- trade-history window ---------------------------------------------------------------


def test_default_window_is_thirty_days() -> None:
    service, _, _, trade_provider = make_service()

    context = service.build(broker_id=1, now=AS_OF)

    assert DEFAULT_TRADE_HISTORY_DAYS == 30
    assert context.as_of == AS_OF
    assert trade_provider.last_from_time == AS_OF - timedelta(days=30)
    assert trade_provider.last_to_time == AS_OF


def test_custom_history_window_is_respected() -> None:
    service, _, _, trade_provider = make_service()

    context = service.build(broker_id=1, trade_history_days=7, now=AS_OF)

    assert trade_provider.last_from_time == AS_OF - timedelta(days=7)
    assert trade_provider.last_to_time == AS_OF
    assert context.as_of == AS_OF


def test_history_window_is_anchored_to_the_snapshot_reference_time() -> None:
    service, _, _, trade_provider = make_service()

    context = service.build(broker_id=1, trade_history_days=90, now=AS_OF)

    assert trade_provider.last_to_time == context.as_of
    assert trade_provider.last_from_time == context.as_of - timedelta(days=90)


def test_zero_day_window_is_rejected() -> None:
    service, _, _, trade_provider = make_service()

    with pytest.raises(ValueError):
        service.build(broker_id=1, trade_history_days=0, now=AS_OF)

    # Rejected before any provider is touched.
    assert trade_provider.call_count == 0


def test_negative_window_is_rejected() -> None:
    service, _, _, _ = make_service()

    with pytest.raises(ValueError):
        service.build(broker_id=1, trade_history_days=-5, now=AS_OF)


# --- composition ------------------------------------------------------------------------


def test_context_composes_account_positions_history_and_portfolio() -> None:
    service, _, _, _ = make_service()

    context = service.build(broker_id=42, now=AS_OF)

    assert isinstance(context, FinancialContext)
    assert context.broker_id == 42
    assert context.as_of == AS_OF
    assert context.account == ACCOUNT
    assert context.positions == POSITIONS
    assert context.trade_history == TRADES
    assert context.portfolio_intelligence == build_portfolio_intelligence(ACCOUNT, POSITIONS, AS_OF)
    # The portfolio component reflects the composed snapshot.
    assert context.portfolio_intelligence.open_positions == 2
    assert context.portfolio_intelligence.buy_positions == 1
    assert context.portfolio_intelligence.sell_positions == 1
    assert context.portfolio_intelligence.symbols == ("EURUSD", "XAUUSD")


def test_portfolio_intelligence_is_derived_from_the_same_snapshot() -> None:
    # A deliberately different provider value proves the portfolio component is
    # built from the reported account, not from a second, independent read.
    account = ACCOUNT._replace(balance=555.0, margin_level=250.0)
    service, _, _, _ = make_service(account=account)

    context = service.build(broker_id=1, now=AS_OF)

    assert context.account.balance == 555.0
    assert context.portfolio_intelligence.balance == 555.0
    assert context.portfolio_intelligence.margin_level == 250.0


def test_account_and_positions_are_read_once_per_context() -> None:
    service, account_provider, position_provider, trade_provider = make_service()

    service.build(broker_id=1, now=AS_OF)

    # One consistent snapshot: no duplicate MT5 reads behind the portfolio view.
    assert account_provider.call_count == 1
    assert position_provider.call_count == 1
    assert trade_provider.call_count == 1


def test_empty_history_is_a_valid_context() -> None:
    service, _, _, trade_provider = make_service(trades=())

    context = service.build(broker_id=1, now=AS_OF)

    assert context.trade_history == ()
    assert trade_provider.call_count == 1


def test_broker_id_is_carried_from_the_caller() -> None:
    service, _, _, _ = make_service()

    assert service.build(broker_id=7, now=AS_OF).broker_id == 7
    assert service.build(broker_id=8, now=AS_OF).broker_id == 8


def test_positions_keep_the_provider_order() -> None:
    service, _, _, _ = make_service()

    context = service.build(broker_id=1, now=AS_OF)

    assert context.positions == POSITIONS


# --- determinism / reference time ---------------------------------------------------------


def test_repeated_builds_are_deterministic() -> None:
    service, _, _, _ = make_service()

    first = service.build(broker_id=1, now=AS_OF)
    second = service.build(broker_id=1, now=AS_OF)

    assert first == second


def test_default_reference_time_is_current_utc() -> None:
    service, _, _, trade_provider = make_service()
    before = datetime.now(UTC)

    context = service.build(broker_id=1)

    after = datetime.now(UTC)
    assert before <= context.as_of <= after
    assert context.as_of.tzinfo is UTC
    assert trade_provider.last_to_time == context.as_of


def test_naive_reference_time_is_rejected() -> None:
    service, _, _, _ = make_service()

    with pytest.raises(ValueError):
        service.build(broker_id=1, now=datetime(2026, 9, 15, 12, 0))


def test_offset_reference_time_is_normalized_to_utc() -> None:
    service, _, _, _ = make_service()

    context = service.build(broker_id=1, now=datetime(2026, 9, 15, 14, 0, tzinfo=timezone(timedelta(hours=2))))

    assert context.as_of == AS_OF
    assert context.as_of.tzinfo is UTC


# --- failure propagation ------------------------------------------------------------------


def test_account_provider_failure_propagates() -> None:
    service = FinancialContextService(
        account_service=AccountInfoService(_FailingAccountInfoProvider()),
        position_service=PositionService(FakePositionProvider(positions=POSITIONS)),
        trade_history_service=TradeHistoryService(FakeTradeHistoryProvider(trades=TRADES)),
    )

    with pytest.raises(RuntimeError, match="account information unavailable"):
        service.build(broker_id=1, now=AS_OF)


def test_position_provider_failure_propagates() -> None:
    service = FinancialContextService(
        account_service=AccountInfoService(_FakeAccountInfoProvider()),
        position_service=PositionService(_FailingPositionProvider()),
        trade_history_service=TradeHistoryService(FakeTradeHistoryProvider(trades=TRADES)),
    )

    with pytest.raises(RuntimeError, match="open positions unavailable"):
        service.build(broker_id=1, now=AS_OF)


def test_trade_history_provider_failure_propagates() -> None:
    service = FinancialContextService(
        account_service=AccountInfoService(_FakeAccountInfoProvider()),
        position_service=PositionService(FakePositionProvider(positions=POSITIONS)),
        trade_history_service=TradeHistoryService(_FailingTradeHistoryProvider()),
    )

    with pytest.raises(RuntimeError, match="trade history unavailable"):
        service.build(broker_id=1, now=AS_OF)


# --- real composition-root wiring ------------------------------------------------------------


@pytest.fixture()
def composition_root_fakes(monkeypatch: pytest.MonkeyPatch):
    """Patch the three provider seams with in-memory fakes (no MT5 terminal).

    The composition root builds each provider with the authenticated tenant's
    credentials and the process-wide session manager, so the fakes accept both
    (and ignore them: the session boundary has its own tests).
    """

    class FakeMT5AccountInfoProvider:
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            pass

        def get_account_info(self) -> AccountInfo:
            return ACCOUNT

    class FakeMT5PositionProvider:
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            pass

        def get_positions(self) -> tuple[Position, ...]:
            return POSITIONS

    class FakeMT5TradeHistoryProvider:
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            pass

        def get_trade_history(self, from_time: datetime, to_time: datetime) -> tuple[TradeHistoryEntry, ...]:
            return TRADES

    monkeypatch.setattr(deps, "MT5AccountInfoProvider", FakeMT5AccountInfoProvider)
    monkeypatch.setattr(deps, "MT5PositionProvider", FakeMT5PositionProvider)
    monkeypatch.setattr(deps, "MT5TradeHistoryProvider", FakeMT5TradeHistoryProvider)


def test_service_composes_the_real_composition_root_services(composition_root_fakes) -> None:
    # The service is assembled from the same getters an API endpoint would use,
    # bound to one tenant's resolved MT5 credentials.
    credentials = MT5AccountCredentials(login=10001, server="Test-Broker", password_encrypted="cipher")
    service = FinancialContextService(
        account_service=deps.get_account_info_service(credentials),
        position_service=deps.get_position_service(credentials),
        trade_history_service=deps.get_trade_history_service(credentials),
    )

    context = service.build(broker_id=5, now=AS_OF, trade_history_days=14)

    assert context.broker_id == 5
    assert context.account == ACCOUNT
    assert context.positions == POSITIONS
    assert context.trade_history == TRADES
    assert context.portfolio_intelligence.open_positions == 2
