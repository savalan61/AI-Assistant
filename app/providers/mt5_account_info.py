from typing import Any

import MetaTrader5 as mt5

from app.providers.account_info import AccountInfo

# MT5 is a C extension without Python type stubs; Any bypasses Pylance type checking.
mt5_api: Any = mt5


class MT5AccountInfoProvider:
    """Read-only MT5 account-information provider.

    Mirrors the established MT5MarketDataProvider boundary: the constructor
    initializes the terminal, expected third-party failures are translated
    into the application's RuntimeError, and shutdown() never raises during
    teardown. Read-only by design: no trading operation exists here.
    """

    def __init__(self):
        # Boundary rule (same as MT5MarketDataProvider): the MT5 C extension
        # raises plain built-in Exception for terminal/IPC failures instead of
        # returning False. This try block covers only the external MT5 call;
        # raw MT5 exceptions must never escape the provider.
        try:
            initialized = mt5_api.initialize()
        except Exception as exc:  # expected third-party MT5 exception at this boundary only
            raise RuntimeError("MT5 terminal initialization failed") from exc
        if not initialized:
            error = mt5_api.last_error()
            raise RuntimeError(f"MT5 initialization failed: {error}")

    def get_account_info(self) -> AccountInfo:
        # Same boundary rule as __init__: translate an expected plain Exception
        # raised by the MT5 C extension into RuntimeError, with chaining kept
        # for logs and no third-party detail in the message.
        try:
            info = mt5_api.account_info()
        except Exception as exc:  # expected third-party MT5 exception at this boundary only
            raise RuntimeError("MT5 account information request failed") from exc

        if info is None:
            error = mt5_api.last_error()
            raise RuntimeError(f"MT5 account information unavailable: {error}")

        # Explicit construction into the application contract: the raw MT5
        # object never leaves the provider, and each field is coerced to the
        # contract type without any business calculation.
        return AccountInfo(
            login=int(info.login),
            name=str(info.name),
            balance=float(info.balance),
            equity=float(info.equity),
            margin=float(info.margin),
            # MT5 names this field margin_free; the application contract calls
            # it free_margin (verified against a real terminal).
            free_margin=float(info.margin_free),
            margin_level=float(info.margin_level),
            currency=str(info.currency),
            server=str(info.server),
        )

    def shutdown(self) -> None:
        # Release the terminal connection. Runs during application shutdown, so
        # any failure (e.g. terminal already gone) is deliberately swallowed:
        # shutdown must never raise at process exit.
        try:
            mt5_api.shutdown()
        except Exception:  # expected third-party MT5 failure during teardown only
            pass
