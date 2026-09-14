import abc
from typing import NamedTuple


# Typed read-only account snapshot shared by all account-info providers.
# Mirrors MT5's account_info() fields at the application boundary so callers
# never depend on the MetaTrader5 package directly.
class AccountInfo(NamedTuple):
    login: int
    name: str
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float
    currency: str
    server: str


# Abstraction boundary: services depend on this, never on MT5 directly
# (mirrors MarketDataProvider in market_data.py).
class AccountInfoProvider(abc.ABC):
    @abc.abstractmethod
    def get_account_info(self) -> AccountInfo:
        ...
