from datetime import UTC, datetime
from typing import Any

from app.core.mt5_session import MT5AccountCredentials, MT5SessionManager
from app.providers.trade_history import TradeCloseReason, TradeHistoryEntry, TradeHistoryProvider, TradeType

# MT5 deal reason constants (verified against the MetaTrader5 package API:
# DEAL_REASON_CLIENT, DEAL_REASON_MOBILE, DEAL_REASON_WEB, DEAL_REASON_EXPERT,
# DEAL_REASON_AGENT, DEAL_REASON_DEALER, DEAL_REASON_SOFTWARE,
# DEAL_REASON_TP, DEAL_REASON_SO). These are numeric module constants on the
# real package; the provider reads them through the authenticated MT5 api so
# tests can fake them.
_DEAL_REASON_TP = 5  # DEAL_REASON_TP
_DEAL_REASON_SO = 6  # DEAL_REASON_SO (stop-out)


def _map_close_reason(raw_reason: object) -> TradeCloseReason:
    """Translate the MT5 deal reason into the application contract.

    The deal reason describes who/how the deal was triggered:
    - DEAL_REASON_TP  → the position was closed by its Take Profit → "TP"
    - DEAL_REASON_SO  → the position was closed by its Stop Loss/Stop-Out → "SL"
    - CLIENT/MOBILE/WEB (and their legacy equivalents) → a human closed the
      position in a terminal → "MANUAL"
    - EXPERT/AGENT/DEALER/SOFTWARE → programmatic close → "OTHER"
    - anything unmapped → "OTHER" (the deal is still reported; the reason is
      simply classified as not TP/SL/MANUAL rather than dropping the trade)
    """
    reason = int(raw_reason)
    if reason == _DEAL_REASON_TP:
        return TradeCloseReason.TP
    if reason == _DEAL_REASON_SO:
        return TradeCloseReason.SL
    if reason in (0, 1, 2):  # DEAL_REASON_CLIENT / _MOBILE / _WEB
        return TradeCloseReason.MANUAL
    if reason in (3, 4, 6, 7, 8):  # _EXPERT / _AGENT / _SO / _DEALER / _SOFTWARE
        return TradeCloseReason.OTHER
    # Unknown third-party value: classify, never guess TP/SL/MANUAL.
    return TradeCloseReason.OTHER


class MT5TradeHistoryProvider(TradeHistoryProvider):
    """Read-only MT5 executed-trade-history provider, scoped to one tenant.

    Tenant scope: the provider is a cheap per-request object carrying the
    authenticated tenant's credentials; every raw MT5 call (the deals read and
    the related-order lookup) is made inside ``MT5SessionManager.acquire`` so the
    terminal is authenticated as *that* tenant, under the process-wide session
    lock, for the whole read. Read-only by design: history is only queried, never
    modified, and no trading operation exists here.
    """

    def __init__(self, session_manager: MT5SessionManager, credentials: MT5AccountCredentials) -> None:
        self._session = session_manager
        self._credentials = credentials

    def get_trade_history(self, from_time: datetime, to_time: datetime) -> tuple[TradeHistoryEntry, ...]:
        # Authenticate (or reuse) the requesting tenant's session and read inside
        # that authenticated span. MT5's own history is account-scoped, so this
        # is exactly the boundary that must not fall back to a global session.
        with self._session.acquire(self._credentials) as mt5_api:
            # history_deals_get accepts (date_from, date_to) or
            # (date_from, date_to, group). Both boundaries are passed as
            # timezone-aware UTC datetimes as established for MT5 calls.
            try:
                raw_deals = mt5_api.history_deals_get(from_time, to_time)
            except Exception as exc:  # expected third-party MT5 exception at this boundary only
                raise RuntimeError("MT5 trade history request failed") from exc

            # None means the request itself failed (terminal/IPC problem) — an
            # availability issue. An empty tuple legitimately means "no executed
            # trades in this window" and must return 200 with trades: [].
            if raw_deals is None:
                error = mt5_api.last_error()
                raise RuntimeError(f"MT5 trade history unavailable: {error}")

            entries: list[TradeHistoryEntry] = []
            for raw in raw_deals:
                if int(raw.entry) != mt5_api.DEAL_ENTRY_OUT:
                    # Only closing deals represent an executed trade exit with a
                    # realized profit; entry-in deals are position openings.
                    continue
                entries.append(self._map_deal(mt5_api, raw))
            return tuple(entries)

    def _map_deal(self, mt5_api: Any, raw: Any) -> TradeHistoryEntry:
        # MT5 encodes direction numerically (0 = DEAL_TYPE_BUY, 1 = DEAL_TYPE_SELL)
        # — unmapped values are a contract violation and fail loudly rather
        # than being guessed.
        if int(raw.type) == mt5_api.DEAL_TYPE_BUY:
            trade_type = TradeType.BUY
        elif int(raw.type) == mt5_api.DEAL_TYPE_SELL:
            trade_type = TradeType.SELL
        else:
            raise RuntimeError(f"MT5 returned an unknown deal type: {raw.type}")

        # close_reason may be None when the MT5 version supplies no reason
        # field for the deal; the application contract keeps it None rather
        # than inventing a value.
        raw_reason = getattr(raw, "reason", None)
        close_reason = None if raw_reason is None else _map_close_reason(raw_reason)

        sl, tp = self._protective_levels(mt5_api, raw)

        # time is an MT5 Unix timestamp (seconds); UTC-awareness is attached
        # explicitly — a naive datetime must never leak into the contract.
        deal_time = datetime.fromtimestamp(int(raw.time), tz=UTC)

        return TradeHistoryEntry(
            ticket=int(raw.ticket),
            order_ticket=int(raw.order),
            symbol=str(raw.symbol),
            type=trade_type,
            volume=float(raw.volume),
            price=float(raw.price),
            profit=float(raw.profit),
            time=deal_time,
            close_reason=close_reason,
            stop_loss=sl,
            take_profit=tp,
        )

    def _protective_levels(self, mt5_api: Any, raw: Any) -> tuple[float | None, float | None]:
        # The historical deal object does not reliably carry the SL/TP levels
        # of the closing order, so they are taken from the related order when
        # it can actually be retrieved; otherwise they are None — never
        # invented values. Called inside the authenticated session span.
        order_ticket = int(raw.order)
        if order_ticket == 0:
            return None, None
        try:
            related_orders = mt5_api.history_orders_get(ticket=order_ticket)
        except Exception as exc:  # expected third-party MT5 exception at this boundary only
            raise RuntimeError("MT5 trade history request failed") from exc

        if not related_orders:
            # No related closing order found (e.g. balance operations or
            # terminal-specific behavior): levels are simply unknown here.
            return None, None

        order = related_orders[0]
        sl = float(order.price_sl) if float(order.price_sl) > 0.0 else None
        tp = float(order.price_tp) if float(order.price_tp) > 0.0 else None
        return sl, tp
