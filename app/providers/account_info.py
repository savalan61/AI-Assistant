import abc
from decimal import Decimal
from typing import NamedTuple


# Typed read-only account snapshot shared by all account-info providers.
# Mirrors MT5's account_info() fields at the application boundary so callers
# never depend on the MetaTrader5 package directly.
#
# Money fields are Decimal (Step 37): balances, equity and margin are exact
# financial quantities, converted at the provider boundary with
# Decimal(str(raw_value)) so no binary-float artifact enters the domain.
# margin_level is a ratio (equity / used margin * 100), not money, and stays
# float.
class AccountInfo(NamedTuple):
    login: int
    name: str
    balance: Decimal
    equity: Decimal
    margin: Decimal
    free_margin: Decimal
    margin_level: float
    currency: str
    server: str


# Abstraction boundary: services depend on this, never on MT5 directly
# (mirrors MarketDataProvider in market_data.py).
class AccountInfoProvider(abc.ABC):
    @abc.abstractmethod
    def get_account_info(self) -> AccountInfo:
        ...
