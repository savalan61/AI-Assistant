"""Prepares the provider-neutral prompt the agent sends to an LLM provider.

This is the agent layer's job: turn the user request plus the read-only
financial context into plain text. The provider therefore never needs to know
about FinancialContext, and this module never knows about any provider.

Two deliberate restrictions on what is sent:

* only facts already present in the context are rendered — nothing is inferred
  here, and no forecast, recommendation or trading suggestion is composed;
* account identity is omitted (login, holder name, server, and the account
  number). The model needs the numbers, not the identifiers, so unnecessary
  personal data is not shipped to an external service.
"""
from app.providers.llm import LLMPrompt
from app.providers.position import Position
from app.providers.trade_history import TradeHistoryEntry
from app.services.financial_context import FinancialContext
from app.services.portfolio_intelligence import SymbolExposure

# System-side framing. Read-only and conservative by construction: the model is
# told not to predict, not to advise, and that "I don't know" is a valid answer.
ASSISTANT_INSTRUCTIONS = (
    "You are a read-only financial assistant for a brokerage customer. "
    "Answer the user's request using only the context below. Be factual, "
    "concise and conservative. Do not predict prices, do not give investment "
    "advice, and never suggest opening, closing or modifying a trade. If the "
    "context does not contain the answer, say so."
)

_NONE = "- none"


def _positions_block(positions: tuple[Position, ...]) -> list[str]:
    if not positions:
        return [_NONE]
    return [
        f"- {position.symbol} {position.type.value} {position.volume:.2f} "
        f"@ {position.open_price:.2f} (current {position.current_price:.2f}, "
        f"profit {position.profit:.2f})"
        for position in positions
    ]


def _exposure_block(exposure: tuple[SymbolExposure, ...]) -> list[str]:
    if not exposure:
        return [_NONE]
    return [
        f"- {item.symbol}: buy {item.buy_volume:.2f} / sell {item.sell_volume:.2f} / "
        f"net {item.net_volume:.2f} ({item.position_count} position(s))"
        for item in exposure
    ]


def _trades_block(trades: tuple[TradeHistoryEntry, ...]) -> list[str]:
    if not trades:
        return [_NONE]
    lines: list[str] = []
    for trade in trades:
        reason = trade.close_reason.value if trade.close_reason is not None else "unknown"
        lines.append(
            f"- {trade.time.isoformat()} {trade.symbol} {trade.type.value} "
            f"{trade.volume:.2f} @ {trade.price:.2f} profit {trade.profit:.2f} "
            f"close_reason {reason}"
        )
    return lines


def build_prompt(request: str, context: FinancialContext) -> LLMPrompt:
    """Render ``request`` and ``context`` into a provider-neutral prompt.

    Pure and deterministic: the same request and context always produce the
    same prompt, so a provider sees a stable, reproducible input.
    """
    portfolio = context.portfolio_intelligence
    symbols = ", ".join(portfolio.symbols) if portfolio.symbols else "none"

    content = "\n".join(
        [
            "User request:",
            request,
            "",
            f"Read-only financial context (as of {context.as_of.isoformat()}):",
            f"Account currency: {context.account.currency}",
            f"Balance: {context.account.balance:.2f}",
            f"Equity: {context.account.equity:.2f}",
            f"Used margin: {context.account.margin:.2f}",
            f"Free margin: {context.account.free_margin:.2f}",
            f"Margin level: {context.account.margin_level:.2f}",
            "",
            f"Open positions: {portfolio.open_positions} "
            f"(buy {portfolio.buy_positions}, sell {portfolio.sell_positions})",
            f"Symbols held: {symbols}",
            f"Total volume: {portfolio.total_volume:.2f} "
            f"(buy {portfolio.buy_volume:.2f}, sell {portfolio.sell_volume:.2f})",
            f"Directional balance (buy volume minus sell volume): "
            f"{portfolio.directional_balance:.2f}",
            "",
            "Exposure by symbol:",
            *_exposure_block(portfolio.exposure),
            "",
            f"Risk classification: {portfolio.risk.level.value} - {portfolio.risk.basis}",
            "",
            f"Open positions ({portfolio.open_positions}):",
            *_positions_block(context.positions),
            "",
            f"Recent executed trades ({len(context.trade_history)}):",
            *_trades_block(context.trade_history),
        ]
    )

    return LLMPrompt(instructions=ASSISTANT_INSTRUCTIONS, content=content)
