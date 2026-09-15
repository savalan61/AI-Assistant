"""Tests for the deterministic portfolio/exposure analysis and its service.

Require none of: real MT5, PostgreSQL, network, or real credentials. The pure
analysis functions are exercised directly over the existing AccountInfo and
Position contracts; the service is exercised with a deterministic fake account
provider plus the project's FakePositionProvider. No pytest asyncio plugin.
"""
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.providers.account_info import AccountInfo, AccountInfoProvider
from app.providers.fake_position import FakePositionProvider
from app.providers.position import Position, PositionType
from app.services.account import AccountInfoService
from app.services.portfolio_intelligence import (
    PortfolioIntelligenceService,
    PortfolioRiskLevel,
    aggregate_exposure,
    build_portfolio_intelligence,
    classify_portfolio_risk,
)
from app.services.positions import PositionService

AS_OF = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def make_account(
    *,
    balance: Decimal = Decimal("10000.00"),
    equity: Decimal = Decimal("10000.00"),
    margin: Decimal = Decimal("0.00"),
    free_margin: Decimal = Decimal("10000.00"),
    margin_level: float = 0.0,
    currency: str = "USD",
) -> AccountInfo:
    return AccountInfo(
        login=10001,
        name="Test Trader",
        balance=balance,
        equity=equity,
        margin=margin,
        free_margin=free_margin,
        margin_level=margin_level,
        currency=currency,
        server="Test-Server",
    )


def make_position(ticket: int, symbol: str, position_type: PositionType, volume: Decimal) -> Position:
    return Position(
        ticket=ticket,
        symbol=symbol,
        type=position_type,
        volume=volume,
        open_price=Decimal("1.00"),
        current_price=Decimal("1.00"),
        profit=Decimal("0.00"),
    )


def test_no_positions_yields_a_flat_empty_portfolio() -> None:
    portfolio = build_portfolio_intelligence(make_account(), (), AS_OF)

    assert portfolio.open_positions == 0
    assert portfolio.buy_positions == 0
    assert portfolio.sell_positions == 0
    assert portfolio.total_volume == 0
    assert portfolio.buy_volume == 0
    assert portfolio.sell_volume == 0
    assert portfolio.directional_balance == 0
    assert portfolio.symbols == ()
    assert portfolio.exposure == ()
    assert portfolio.risk.level is PortfolioRiskLevel.FLAT


def test_only_buy_positions_are_counted_and_summed() -> None:
    positions = (
        make_position(1, "XAUUSD", PositionType.BUY, Decimal("0.10")),
        make_position(2, "EURUSD", PositionType.BUY, Decimal("1.00")),
    )

    portfolio = build_portfolio_intelligence(make_account(), positions, AS_OF)

    assert portfolio.open_positions == 2
    assert portfolio.buy_positions == 2
    assert portfolio.sell_positions == 0
    assert portfolio.buy_volume == Decimal("1.10")
    assert portfolio.sell_volume == 0
    assert portfolio.total_volume == Decimal("1.10")
    # Directional balance equals BUY volume minus SELL volume.
    assert portfolio.directional_balance == Decimal("1.10")


def test_only_sell_positions_produce_a_negative_directional_balance() -> None:
    positions = (
        make_position(1, "EURUSD", PositionType.SELL, Decimal("0.75")),
        make_position(2, "GBPUSD", PositionType.SELL, Decimal("0.25")),
    )

    portfolio = build_portfolio_intelligence(make_account(), positions, AS_OF)

    assert portfolio.buy_positions == 0
    assert portfolio.sell_positions == 2
    assert portfolio.buy_volume == 0
    assert portfolio.sell_volume == Decimal("1.00")
    assert portfolio.directional_balance == Decimal("-1.00")


def test_mixed_positions_split_by_direction() -> None:
    positions = (
        make_position(1, "XAUUSD", PositionType.BUY, Decimal("0.10")),
        make_position(2, "EURUSD", PositionType.SELL, Decimal("1.00")),
    )

    portfolio = build_portfolio_intelligence(make_account(), positions, AS_OF)

    assert portfolio.open_positions == 2
    assert portfolio.buy_positions == 1
    assert portfolio.sell_positions == 1
    assert portfolio.buy_volume == Decimal("0.10")
    assert portfolio.sell_volume == Decimal("1.00")
    assert portfolio.total_volume == Decimal("1.10")
    assert portfolio.directional_balance == Decimal("-0.90")


def test_multiple_positions_for_the_same_symbol_aggregate_into_one_exposure() -> None:
    positions = (
        make_position(1, "XAUUSD", PositionType.BUY, Decimal("0.10")),
        make_position(2, "XAUUSD", PositionType.BUY, Decimal("0.20")),
        make_position(3, "XAUUSD", PositionType.SELL, Decimal("0.05")),
    )

    exposure = aggregate_exposure(positions)

    assert len(exposure) == 1
    entry = exposure[0]
    assert entry.symbol == "XAUUSD"
    assert entry.buy_volume == Decimal("0.30")
    assert entry.sell_volume == Decimal("0.05")
    assert entry.net_volume == Decimal("0.25")
    assert entry.position_count == 3


def test_multiple_symbols_produce_one_entry_each_in_symbol_order() -> None:
    positions = (
        make_position(1, "XAUUSD", PositionType.BUY, Decimal("0.10")),
        make_position(2, "EURUSD", PositionType.SELL, Decimal("1.00")),
        make_position(3, "GBPUSD", PositionType.BUY, Decimal("0.50")),
    )

    portfolio = build_portfolio_intelligence(make_account(), positions, AS_OF)

    assert [entry.symbol for entry in portfolio.exposure] == ["EURUSD", "GBPUSD", "XAUUSD"]
    assert portfolio.symbols == ("EURUSD", "GBPUSD", "XAUUSD")


def test_symbols_currently_held_are_unique_and_sorted() -> None:
    positions = (
        make_position(1, "XAUUSD", PositionType.BUY, Decimal("0.10")),
        make_position(2, "XAUUSD", PositionType.SELL, Decimal("0.10")),
        make_position(3, "EURUSD", PositionType.BUY, Decimal("0.10")),
    )

    portfolio = build_portfolio_intelligence(make_account(), positions, AS_OF)

    assert portfolio.symbols == ("EURUSD", "XAUUSD")


def test_zero_volume_positions_still_count_as_open_positions() -> None:
    positions = (make_position(1, "XAUUSD", PositionType.BUY, Decimal("0.0")),)

    exposure = aggregate_exposure(positions)

    assert exposure[0].position_count == 1
    assert exposure[0].buy_volume == 0
    assert exposure[0].net_volume == 0

    portfolio = build_portfolio_intelligence(make_account(), positions, AS_OF)
    assert portfolio.open_positions == 1
    assert portfolio.buy_positions == 1
    assert portfolio.total_volume == 0
    assert portfolio.directional_balance == 0


def test_exposure_is_deterministic_regardless_of_input_order() -> None:
    first = make_position(1, "XAUUSD", PositionType.BUY, Decimal("0.10"))
    second = make_position(2, "EURUSD", PositionType.SELL, Decimal("1.00"))
    third = make_position(3, "XAUUSD", PositionType.SELL, Decimal("0.20"))

    assert aggregate_exposure((first, second, third)) == aggregate_exposure((third, first, second))


def test_risk_is_flat_when_there_are_no_positions() -> None:
    assessment = classify_portfolio_risk((), margin_level=0.0)

    assert assessment.level is PortfolioRiskLevel.FLAT
    assert assessment.basis


def test_risk_is_unknown_when_open_positions_have_no_usable_margin_level() -> None:
    positions = (make_position(1, "XAUUSD", PositionType.BUY, Decimal("0.10")),)

    assessment = classify_portfolio_risk(positions, margin_level=0.0)

    assert assessment.level is PortfolioRiskLevel.UNKNOWN
    assert "cannot be classified" in assessment.basis


@pytest.mark.parametrize(
    ("margin_level", "expected"),
    [
        (500.0, PortfolioRiskLevel.LOW),
        (5060.0, PortfolioRiskLevel.LOW),
        (200.0, PortfolioRiskLevel.ELEVATED),
        (350.0, PortfolioRiskLevel.ELEVATED),
        (100.0, PortfolioRiskLevel.HIGH),
        (10.0, PortfolioRiskLevel.HIGH),
    ],
)
def test_risk_bands_follow_the_margin_level(margin_level: float, expected: PortfolioRiskLevel) -> None:
    positions = (make_position(1, "XAUUSD", PositionType.BUY, Decimal("0.10")),)

    assessment = classify_portfolio_risk(positions, margin_level=margin_level)

    assert assessment.level is expected
    assert f"{margin_level:.2f}%" in assessment.basis


def test_risk_basis_is_descriptive_and_never_predictive() -> None:
    positions = (make_position(1, "XAUUSD", PositionType.BUY, Decimal("0.10")),)

    for margin_level in (600.0, 250.0, 50.0, 0.0):
        basis = classify_portfolio_risk(positions, margin_level=margin_level).basis.lower()
        for forbidden in (
            "predict",
            "forecast",
            "will rise",
            "will fall",
            "recommend",
            "target",
            "liquidat",
            "buy ",
            "sell ",
            "open a",
            "close",
        ):
            assert forbidden not in basis


def test_account_fields_pass_through_unmodified() -> None:
    account = make_account(
        balance=Decimal("25000.0"),
        equity=Decimal("25120.5"),
        margin=Decimal("500.0"),
        free_margin=Decimal("24620.5"),
        margin_level=5024.1,
        currency="EUR",
    )

    portfolio = build_portfolio_intelligence(account, (), AS_OF)

    assert portfolio.as_of == AS_OF
    assert portfolio.account_currency == "EUR"
    assert portfolio.balance == 25000.0
    assert portfolio.equity == 25120.5
    assert portfolio.margin == 500.0
    assert portfolio.free_margin == 24620.5
    assert portfolio.margin_level == 5024.1


# --- service composition ---------------------------------------------------------------


class _FakeAccountInfoProvider(AccountInfoProvider):
    """Deterministic in-memory account provider (no MT5)."""

    def __init__(self, account: AccountInfo) -> None:
        self.account = account
        self.call_count = 0

    def get_account_info(self) -> AccountInfo:
        self.call_count += 1
        return self.account


def make_service(
    account: AccountInfo, positions: tuple[Position, ...]
) -> tuple[PortfolioIntelligenceService, _FakeAccountInfoProvider, FakePositionProvider]:
    account_provider = _FakeAccountInfoProvider(account)
    position_provider = FakePositionProvider(positions=positions)
    service = PortfolioIntelligenceService(
        account_service=AccountInfoService(account_provider),
        position_service=PositionService(position_provider),
    )
    return service, account_provider, position_provider


def test_service_combines_account_and_positions() -> None:
    positions = (
        make_position(1, "XAUUSD", PositionType.BUY, Decimal("0.10")),
        make_position(2, "EURUSD", PositionType.SELL, Decimal("0.40")),
    )
    service, account_provider, position_provider = make_service(make_account(), positions)

    portfolio = service.build(now=AS_OF)

    assert account_provider.call_count == 1
    assert position_provider.call_count == 1
    assert portfolio.open_positions == 2
    assert portfolio.buy_volume == Decimal("0.10")
    assert portfolio.sell_volume == Decimal("0.40")
    assert portfolio.directional_balance == Decimal("-0.30")


def test_service_normalizes_the_reference_time_to_utc() -> None:
    service, _, _ = make_service(make_account(), ())
    offset_now = datetime(2026, 9, 15, 14, 0, tzinfo=timezone(timedelta(hours=2)))

    portfolio = service.build(now=offset_now)

    assert portfolio.as_of == datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    assert portfolio.as_of.tzinfo is UTC


def test_service_defaults_as_of_to_current_utc_time() -> None:
    service, _, _ = make_service(make_account(), ())
    before = datetime.now(UTC)

    portfolio = service.build()

    after = datetime.now(UTC)
    assert before <= portfolio.as_of <= after
    assert portfolio.as_of.tzinfo is UTC


def test_service_rejects_a_naive_reference_time() -> None:
    service, _, _ = make_service(make_account(), ())

    with pytest.raises(ValueError):
        service.build(now=datetime(2026, 9, 15, 12, 0))
