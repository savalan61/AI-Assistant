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
from collections.abc import Iterable
from typing import NamedTuple

from app.providers.instrument import Instrument, InstrumentProvider

# Input bounds. Symbol names and search terms are short vendor identifiers; the
# cap keeps a malformed caller from turning a lookup into an unbounded read.
MAX_SYMBOL_LENGTH = 64
MAX_SEARCH_LENGTH = 64

# Broker decoration forms for the resolution rule below. A broker marks its own
# variant of a base instrument with a short decoration — the common `.r`, `.p`,
# `.m`, `.cash`, `.pro`, `.ecn`, `_i`, `-r`, `#1` spellings, a trailing
# separator with no token at all (`XAUUSD.`, `UKOIL.`, `US100.`), or a
# delimiter-free lowercase tag glued to the base (`XAUUSDm`, `XAUUSDpro`).
# Three bounds keep the rule a *decoration* rule rather than guessing:
#
# * the candidate must START with the requested name (case-insensitive), so a
#   partial name (`US`, `GOL`, `XA`) never resolves to a longer instrument; and
# * after a separator the tail is empty or a short alphanumeric token, so a
#   descriptive or compound tail (`XAUUSD.verylongsuffix`, `XAUUSD.r.x`) is not
#   a decoration either; and
# * a delimiter-free tail must be a SHORT ALPHABETIC LOWERCASE tag. Lower case
#   letters are the broker convention for such tags (the micro/cent/mini and
#   `pro` spellings); an uppercase tail is how a genuinely different base
#   symbol is spelled (`XAUUSDX`, `XAUUSDXAUUSD`), and a tail with digits or
#   punctuation (`XAUUSD1`, `US500.cash` read from `US`) marks nothing — all
#   stay unresolved rather than being read as variants.
#
# Broker symbols are ASCII in practice; a non-ASCII name simply never matches
# this rule (it stays unresolved rather than being guessed at).
_SUFFIX_SEPARATORS = (".", "_", "-", "#")
_MAX_SUFFIX_LENGTH = 8


def _is_broker_suffix_variant(symbol: str, base: str) -> bool:
    """True when ``symbol`` is ``base`` plus a broker decoration.

    All bounds above are applied: the requested name must be an exact
    (case-insensitive) prefix of the catalog symbol, and the remainder must be
    one of the three decoration forms — a separator with an empty or short
    alphanumeric tail, or a short lowercase tag glued to the base. Nothing
    about the symbol's *meaning* is inferred: the caller still decides what a
    unique candidate means, and anything else stays unresolved.
    """
    if not (symbol.isascii() and base.isascii()) or len(symbol) <= len(base):
        return False
    if symbol[: len(base)].casefold() != base.casefold():
        return False
    remainder = symbol[len(base) :]
    if remainder[0] in _SUFFIX_SEPARATORS:
        tail = remainder[1:]
        return len(tail) <= _MAX_SUFFIX_LENGTH and (not tail or tail.isalnum())
    # Delimiter-free form: a short alphabetic lowercase tag (`XAUUSDm`,
    # `XAUUSDpro`). ``islower`` requires at least one cased character and all
    # cased characters lower, and ``isalpha`` additionally excludes digits and
    # punctuation — so an uppercase or mixed-case tail (a different base
    # symbol), a digit-only tail (`XAUUSD1`) and a compound remainder such as
    # ``US500.cash`` read from ``US`` are never accepted.
    return remainder.isalpha() and remainder.islower() and len(remainder) <= _MAX_SUFFIX_LENGTH


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


class InstrumentResolution(NamedTuple):
    """One requested name's outcome, resolved against one catalog snapshot.

    ``requested`` is the caller's name after normalisation, so a batch caller can
    pair outcomes with its own input. ``instrument`` is the broker's own record
    when the name resolved (exactly, case-insensitively, or through a unique
    broker decoration), and ``reason`` is then ``None``. When the name did not
    resolve, ``instrument`` is ``None`` and ``reason`` is the very message
    ``resolve()`` raises for that name — so the batch path reports WHY with the
    same wording, and never invents a reason of its own.
    """

    requested: str
    instrument: Instrument | None
    reason: str | None


def _match_from_catalog(
    requested: str, catalog: tuple[Instrument, ...]
) -> tuple[Instrument | None, str | None]:
    """Apply the catalog-fallback rules to one requested name.

    The single implementation of Steps 52-54 semantics — first a unique
    case-insensitive match, then a unique broker-DECORATED spelling — shared by
    the single-name and multi-name entry points, so the two can never disagree.
    Returns ``(instrument, None)`` for a unique match and ``(None, reason)``
    otherwise; the reason is exactly the ``ValueError`` message a caller raises.
    """
    matches = tuple(
        instrument for instrument in catalog if instrument.symbol.casefold() == requested.casefold()
    )
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        # Distinct broker symbols that differ only by case: guessing one
        # would silently resolve the wrong instrument.
        return None, f"Instrument name is ambiguous: {requested}"

    suffix_matches = tuple(
        instrument for instrument in catalog if _is_broker_suffix_variant(instrument.symbol, requested)
    )
    if len(suffix_matches) == 1:
        return suffix_matches[0], None
    if len(suffix_matches) > 1:
        # Several suffixed variants of the same base (XAUUSD.r and XAUUSD.m):
        # they are different instruments, so picking one would be a guess. The
        # caller must name the spelling it wants.
        return None, f"Instrument name is ambiguous: {requested}"
    return None, f"MT5 does not offer instrument {requested}"


class InstrumentService:
    """Read-only instrument discovery for the authenticated customer's broker.

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
        3. a unique BROKER-DECORATED spelling of the requested base symbol
           (``XAUUSD`` -> ``XAUUSD.r``, ``XAUUSD.`` or ``XAUUSDm``), which is
           what makes a broker that decorates its whole catalog usable at all.

        The returned ``Instrument.symbol`` is always the broker's own spelling.
        Every step requires *one* match: an ambiguous catalog is an error rather
        than a coin flip, and a name the catalog does not offer — with or
        without a suffix — stays unresolved rather than being guessed at.

        Unknown or ambiguous input raises ValueError (a client error); an MT5
        availability failure raised by the provider propagates as RuntimeError.

        This is exactly ``resolve_many((symbol,))`` promoted to an exception: one
        name, at most one catalog read, and the same rules the multi-name path
        applies, so the two can never drift apart.
        """
        outcome = self.resolve_many((symbol,))[0]
        if outcome.instrument is None:
            # An unresolved outcome always carries the reason, and that reason is
            # the message this method has always raised for that name.
            raise ValueError(outcome.reason)
        return outcome.instrument

    def resolve_many(self, symbols: Iterable[str]) -> tuple[InstrumentResolution, ...]:
        """Resolve several requested symbols against ONE catalog snapshot.

        The same deterministic rules ``resolve()`` applies to one name (the exact
        spelling, then a unique case-insensitive match, then a unique
        broker-decorated spelling), applied to each name in order — but the
        broker's catalog is discovered AT MOST ONCE for the whole operation.

        Why it exists: the MT5 Python API serializes every read on the one
        process-wide session, so scanning the same catalog once per requested
        name made a request that named several suffixed instruments pay a
        catalog read per name for data that cannot change between them. Here the
        exact lookups run first (one vendor call each, exactly as before), and
        the first name that needs the fallback triggers the single catalog read
        that every remaining name then reuses. A catalog read still happens only
        when a name actually needs it: resolving names the terminal spells
        exactly costs no scan at all.

        Request-local by construction: the snapshot is a local variable of this
        call. Nothing is cached on the service, nothing is shared between calls,
        requests or customers, and the caller's ordering is preserved (one outcome
        per requested name, in input order; a repeated name is looked up again
        rather than deduplicated here).

        ``ValueError`` from input normalisation still propagates (a malformed
        name is a caller error, not an unresolved instrument), and an MT5
        availability failure still propagates as ``RuntimeError`` — an
        infrastructure failure is never reported as "the broker does not offer
        it".
        """
        requested = tuple(normalize_symbol(symbol) for symbol in symbols)
        catalog: tuple[Instrument, ...] | None = None
        outcomes: list[InstrumentResolution] = []
        for name in requested:
            try:
                instrument = self._provider.get_instrument(name)
            except ValueError:
                # Only reached for a spelling the terminal did not recognise
                # directly. ONE catalog read serves every remaining name in this
                # operation, and none is made when no name needs it.
                if catalog is None:
                    catalog = self._catalog()
                matched, reason = _match_from_catalog(name, catalog)
                if matched is None:
                    outcomes.append(InstrumentResolution(requested=name, instrument=None, reason=reason))
                    continue
                instrument = matched
            outcomes.append(InstrumentResolution(requested=name, instrument=instrument, reason=None))
        return tuple(outcomes)

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
