from decimal import Decimal

from app.core.mt5_session import MT5AccountCredentials, MT5SessionManager
from app.providers.account_info import AccountInfo, AccountInfoProvider


class MT5AccountInfoProvider(AccountInfoProvider):
    """Read-only MT5 account-information provider, scoped to one tenant's session.

    Tenant scope: the provider is a cheap per-request object carrying the
    authenticated tenant's credentials; the raw MT5 call is made inside
    ``MT5SessionManager.acquire`` so the terminal is authenticated as *that*
    tenant, under the process-wide session lock, for the whole read. Expected
    third-party failures are translated into the application's RuntimeError.
    Read-only by design: no trading operation exists here.
    """

    def __init__(self, session_manager: MT5SessionManager, credentials: MT5AccountCredentials) -> None:
        self._session = session_manager
        self._credentials = credentials

    def get_account_info(self) -> AccountInfo:
        # Authenticate (or reuse) the requesting tenant's session and read inside
        # that authenticated span: the lock is held until the read completes, so
        # another tenant can never re-authenticate the terminal mid-read.
        with self._session.acquire(self._credentials) as mt5_api:
            # Boundary rule: the C extension raises plain built-in Exception for
            # terminal/IPC failures. This try block covers only the external MT5
            # call; raw MT5 exceptions must never escape the provider.
            try:
                info = mt5_api.account_info()
            except Exception as exc:  # expected third-party MT5 exception at this boundary only
                raise RuntimeError("MT5 account information request failed") from exc

            if info is None:
                error = mt5_api.last_error()
                raise RuntimeError(f"MT5 account information unavailable: {error}")

            # Explicit construction into the application contract: the raw MT5
            # object never leaves the provider. Money goes through Decimal(str())
            # — never Decimal(raw_float), which would bake in the binary-float
            # artifact the Decimal conversion exists to remove. margin_level is a
            # ratio, not money, and stays float.
            return AccountInfo(
                login=int(info.login),
                name=str(info.name),
                balance=Decimal(str(info.balance)),
                equity=Decimal(str(info.equity)),
                margin=Decimal(str(info.margin)),
                # MT5 names this field margin_free; the application contract calls
                # it free_margin (verified against a real terminal).
                free_margin=Decimal(str(info.margin_free)),
                margin_level=float(info.margin_level),
                currency=str(info.currency),
                server=str(info.server),
            )
