"""Instrument discovery and resolution over the provider boundary.

Why this service exists
-----------------------

The provider answers "what does the broker's terminal know"; this service owns
the *deterministic presentation rules* that must not live in a vendor adapter:
input normalisation, resolution (with a case-insensitive fallback), searching,
ordering and the response bound. Every rule below is reproducible from the
provider's data alone, so the same catalog always produces the same response.

The result is deliberately generic: any symbol the broker offers — an FX pair, a
metal, an energy contract, an index, a share, a crypto pair, a soft commodity,
with or without a broker suffix — resolves through the same path. Nothing in
this module knows about XAUUSD, USOIL, NASDAQ or any other instrument: the
suffix rule below is a form rule over the requested name and the broker's own
catalog, and the fundamental relevance profiles in
``app.services.instrument_intelligence`` stay an optional intelligence
enhancement layered on top, never a prerequisite.
"""
from typing import NamedTuple

from app.providers.instrument import Instrument, InstrumentProvider

# Input bounds. Symbol names and search terms are short vendor identifiers; the
# cap keeps a malformed caller from turning a lookup into an unbounded read.
MAX_SYMBOL_LENGTH = 64
MAX_SEARCH_LENGTH = 64

# Broker suffix forms for the resolution rule below. A broker marks its own
# variant of a base instrument with a separator and a short token — the common
# `.r`, `.m`, `.cash`, `.pro`, `.ecn`, `_i`, `-r`, `#1` spellings. Two bounds
# keep the rule a *suffix* rule rather than guessing:
#
# * the tail must START with a separator, so an undelimited tail (`XAUUSDm`) or
#   simply a longer symbol (`XAUUSDX`) is never read as a suffix — the catalog
#   offers no marker that such a tail is a variant rather than a different
#   instrument; and
# * the tail after the separator is a short alphanumeric token, so a
#   descriptive or compound tail (`XAUUSD.verylongsuffix`, `XAUUSD.r.x`) is not
#   a suffix either.
#
# Broker symbols are ASCII in practice; a non-ASCII name simply never matches
# this rule (it stays unresolved rather than being guessed at).
_SUFFIX_SEPARATORS = (".", "_", "-", "#")
_MAX_SUFFIX_LENGTH = 8


def _is_broker_suffix_variant(symbol: str, base: str) -> bool:
    """True when ``symbol`` is ``base`` plus a broker suffix.

    Both bounds above are applied: the requested name must be an exact
    (case-insensitive) prefix of the catalog symbol, the next character must be
    a broker separator, and the remainder must be a short alphanumeric token.
    Nothing about the symbol's *meaning* is inferred: the caller still decides
    what a unique candidate means, and anything else stays unresolved.
    """
    if not (symbol.isascii() and base.isascii()) or len(symbol) <= len(base):
        return False
    if symbol[: len(base)].casefold() != base.casefold():
        return False
    remainder = symbol[len(base) :]
    if remainder[0] not in _SUFFIX_SEPARATORS:
        return False
    tail = remainder[1:]
    return 1 <= len(tail) <= _MAX_SUFFIX_LENGTH and tail.isalnum()


def normalize_symbol(symbol: str) -> str:
    """Deterministically normalise caller-supplied symbol text.

    Only surrounding whitespace is removed and empty/control-character input is
    rejected. Case is deliberately preserved: broker symbol names are
    case-sensitive and carry broker suffixes (``XAUUSD.r``, ``US500.cash``), so
    re-casing here would ask the broker for a symbol it does not offer.
    Case-insensitive matching belongs to resolution (``InstrumentService.resolve``),
    which always answers with the broker's own spelling.
    """
    text = symbol.strip()
    if not text or len(text) > MAX_SYMBOL_LENGTH or any(character < " " for character in text):
        raise ValueError("symbol must be a non-empty broker instrument name")
    return text


class InstrumentCatalog(NamedTuple):
    """A bounded instrument listing plus the true size of the match.

    ``total`` and ``truncated`` exist so the bound is stated rather than hidden:
    a caller can always tell "the broker offers exactly this" from "this is the
    first page of a larger catalog".
    """

    instruments: tuple[Instrument, ...]
    total: int
    truncated: bool


class InstrumentService:
    """Read-only instrument discovery for the authenticated tenant's broker.

    Boundary: API -> InstrumentService -> InstrumentProvider -> MT5. The service
    never holds credentials, never talks to MT5 directly and never mutates
    anything: it orders, filters and bounds what the provider read.
    """

    # Hard bound on a discovery response. Broker catalogs run to thousands of
    # symbols and this is a browsing aid, not a data feed; the cap keeps the
    # payload bounded while ``total``/``truncated`` state the omission
    # explicitly (never silently).
    MAX_INSTRUMENTS = 200

    def __init__(self, provider: InstrumentProvider) -> None:
        self._provider = provider

    def resolve(self, symbol: str) -> Instrument:
        """Resolve one requested symbol to the broker's own instrument record.

        Three steps, in order, each of them exact and deterministic:

        1. the broker's own spelling, as requested;
        2. a unique case-insensitive match over the broker's catalog, so a user
           typing ``xauusd.r`` still resolves while a broker's case-sensitive
           name is never rewritten;
        3. a unique BROKER-SUFFIXED spelling of the requested base symbol
           (``XAUUSD`` -> ``XAUUSD.r``), which is what makes a broker that
           suffixes its whole catalog usable at all.

        The returned ``Instrument.symbol`` is always the broker's own spelling.
        Every step requires *one* match: an ambiguous catalog is an error rather
        than a coin flip, and a name the catalog does not offer — with or
        without a suffix — stays unresolved rather than being guessed at.

        Unknown or ambiguous input raises ValueError (a client error); an MT5
        availability failure raised by the provider propagates as RuntimeError.
        """
        requested = normalize_symbol(symbol)
        try:
            return self._provider.get_instrument(requested)
        except ValueError:
            pass

        # Only reached for a spelling the terminal did not recognise directly.
        # One catalog read serves both remaining steps, so resolution never
        # costs more than one extra vendor call.
        catalog = self._catalog()

        matches = tuple(
            instrument
            for instrument in catalog
            if instrument.symbol.casefold() == requested.casefold()
        )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Distinct broker symbols that differ only by case: guessing one
            # would silently resolve the wrong instrument.
            raise ValueError(f"Instrument name is ambiguous: {requested}")

        suffix_matches = tuple(
            instrument
            for instrument in catalog
            if _is_broker_suffix_variant(instrument.symbol, requested)
        )
        if len(suffix_matches) == 1:
            return suffix_matches[0]
        if len(suffix_matches) > 1:
            # Several suffixed variants of the same base (XAUUSD.r and
            # XAUUSD.m): they are different instruments, so picking one would be
            # a guess. The caller must name the spelling it wants.
            raise ValueError(f"Instrument name is ambiguous: {requested}")
        raise ValueError(f"MT5 does not offer instrument {requested}")

    def list_instruments(self, search: str | None = None) -> InstrumentCatalog:
        """List (optionally search) the broker's instruments, bounded and ordered.

        ``search`` is a literal, case-insensitive substring matched against the
        symbol and the broker's description — deliberately not MT5's group-mask
        syntax, so the same query means the same thing for every broker. A blank
        search is treated as "no search", never as "match nothing".
        """
        needle = self._normalize_search(search)
        catalog = self._catalog()
        if needle is not None:
            catalog = tuple(
                instrument
                for instrument in catalog
                if needle in instrument.symbol.casefold()
                or needle in (instrument.name or "").casefold()
            )
        return InstrumentCatalog(
            instruments=catalog[: self.MAX_INSTRUMENTS],
            total=len(catalog),
            truncated=len(catalog) > self.MAX_INSTRUMENTS,
        )

    def _catalog(self) -> tuple[Instrument, ...]:
        """The broker's full catalog, deterministically ordered by symbol.

        MT5 returns the catalog in terminal order, which is not a contract:
        ordering (and therefore every downstream result, including truncation) is
        fixed here so a response is reproducible across terminals and brokers.
        """
        return tuple(
            sorted(self._provider.list_instruments(), key=lambda instrument: instrument.symbol)
        )

    @staticmethod
    def _normalize_search(search: str | None) -> str | None:
        if search is None:
            return None
        text = search.strip()
        if not text:
            return None
        if len(text) > MAX_SEARCH_LENGTH:
            raise ValueError("search must be at most 64 characters")
        return text.casefold()
