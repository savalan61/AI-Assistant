"""Deterministic focus-instrument detection for a request (READ-ONLY).

The fundamental context is about an instrument (plus whatever the caller holds),
so a question like "what is happening with XAUUSD today?" needs XAUUSD to become
part of the instruments in play — otherwise the answer only ever covers the
positions that happen to be open.

This module answers that need with a small, deterministic, testable rule and
nothing else. It does NOT guess an instrument from a topic:

* a token qualifies only when it is built ENTIRELY from known currency/metal
  tokens (the shared ``symbol_currencies`` table), e.g. ``XAUUSD`` -> XAU+USD.
  The reconstruction must match exactly, so ``XAUUSD1`` or ``XAUUSDS`` does not
  qualify and a suffix variant is never invented;
* a single-token token such as ``USD`` does not qualify: a currency is not an
  instrument, and matching one would add a currency to the instruments in play;
* a word like ``gold`` does not qualify: mapping a commodity name to a specific
  broker symbol would be a guess, and the relevance layer already covers the
  metal through the instrument's documented fundamental profile.

Step 48 adds one narrow extension for instruments the currency rule cannot
express at all, such as an index CFD: a token that IS an explicit instrument name
(``USOIL``, ``WTI``, ``NAS100``, ``NASDAQ``, declared by an instrument profile) is
accepted and reported under the profile's canonical symbol. Commodity words and
bare currencies are still refused, so a topic word can never silently become the
user's instrument.

Detection is a LABEL, never a scope decision: the caller's tenant identity and
the positions it may see still come only from the authenticated user, so a
detected symbol can never widen what is read.
"""
import re
from typing import Final

from app.services.economic_intelligence import symbol_currencies
from app.services.instrument_intelligence import focus_symbol_for_token

# Runs of letters/digits. Punctuation, spaces and symbols separate candidates,
# so a MT5-style name written as "XAUUSD.r" still yields "XAUUSD".
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9]+")

# A currency pair/CFD needs at least a base and a quote token; one token would
# only ever be a bare currency.
_MIN_TOKENS: Final[int] = 2


def _qualifies(token: str) -> bool:
    """True when ``token`` is exactly a concatenation of known currency tokens."""
    upper = token.upper()
    tokens = symbol_currencies(upper)
    return len(tokens) >= _MIN_TOKENS and "".join(tokens) == upper


def detect_focus_symbols(text: str, limit: int = 1) -> tuple[str, ...]:
    """Instrument tokens named in ``text``, in first-appearance order.

    Two deterministic rules, in one pass: a token built entirely from known
    currency/metal tokens is taken as written (``XAUUSD``), and a token that is an
    explicit instrument name from an instrument profile is reported under that
    profile's canonical symbol (``WTI`` -> ``USOIL``). The result is
    deduplicated, and ``limit`` bounds it (the agent only ever needs the
    instrument the question is about).
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    found: list[str] = []
    for candidate in _TOKEN_RE.findall(text):
        if _qualifies(candidate):
            symbol = candidate.upper()
        else:
            symbol = focus_symbol_for_token(candidate)
            if symbol is None:
                continue
        if symbol not in found:
            found.append(symbol)
        if len(found) >= limit:
            break
    return tuple(found)


def detect_focus_symbol(text: str) -> str | None:
    """The single instrument ``text`` is about, or None when it names none."""
    symbols = detect_focus_symbols(text, limit=1)
    return symbols[0] if symbols else None
