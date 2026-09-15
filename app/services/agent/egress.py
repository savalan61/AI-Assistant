"""Explicit outbound-data policy for the LLM boundary.

Everything the assistant sends to an external model is composed by the prompt
builder. This module makes *what may leave the process* an explicit, named
decision instead of an implicit property of the prompt template:

* the policy is a small immutable value, so it can be inspected, asserted in
  tests and logged (as a description, never as data);
* it is configuration-driven today (``LLM_SEND_*`` settings), so an operator
  can narrow it without a code change;
* it is the seam a future per-broker consent / data-processing agreement plugs
  into: a broker-aware resolver only has to return a different instance, and
  nothing in the prompt builder or the agent changes.

Account identity (login, holder name, account number, server) is *never* sent
regardless of policy — that is a structural property of the prompt builder, not
a switch. And nothing in this module logs financial context or secrets.
"""
from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class OutboundDataPolicy:
    """Which classes of financial data may be rendered into an external prompt.

    The three switches remove a specific data class; everything else the
    assistant needs to answer a question (account currency, margin level,
    position counts and volumes by symbol, exposure volumes, the risk band)
    always travels, because without them the answer would be meaningless.

    ``allow_account_balances``  absolute balance / equity / margin / free margin.
    ``allow_position_pricing``  each open position's open price, current price
                                and unrealized profit.
    ``allow_trade_history``     the executed-trade look-back window.
    """

    allow_account_balances: bool = True
    allow_position_pricing: bool = True
    allow_trade_history: bool = True

    @classmethod
    def from_settings(cls) -> "OutboundDataPolicy":
        """Build the process-wide policy from application configuration."""
        return cls(
            allow_account_balances=settings.LLM_SEND_ACCOUNT_BALANCES,
            allow_position_pricing=settings.LLM_SEND_POSITION_PRICING,
            allow_trade_history=settings.LLM_SEND_TRADE_HISTORY,
        )

    def describe(self) -> str:
        """Secret-free, context-free summary of what may leave (for logs/docs)."""
        included = [
            "account currency",
            "margin level",
            "position counts and volumes",
            "exposure volumes",
            "risk classification",
        ]
        omitted = []
        if self.allow_account_balances:
            included.append("account balances")
        else:
            omitted.append("account balances")
        if self.allow_position_pricing:
            included.append("position pricing and unrealized profit")
        else:
            omitted.append("position pricing and unrealized profit")
        if self.allow_trade_history:
            included.append("executed trade history")
        else:
            omitted.append("executed trade history")
        summary = "outbound LLM data policy: sends " + ", ".join(included)
        if omitted:
            summary += "; withholds " + ", ".join(omitted)
        return summary
