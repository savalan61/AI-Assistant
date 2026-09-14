from app.providers.account_info import AccountInfo, AccountInfoProvider


# Depends on the provider abstraction, not on MT5 directly (mirrors
# MarketDataService). No database access, no business calculations: account
# data is a passthrough read.
class AccountInfoService:
    def __init__(self, provider: AccountInfoProvider):
        self._provider = provider

    def get_account_info(self) -> AccountInfo:
        return self._provider.get_account_info()
