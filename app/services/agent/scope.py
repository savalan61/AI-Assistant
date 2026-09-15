"""Deterministic scope guard for the financial assistant (read-only).

The Agent is a financial assistant for the user's own MT5 account, not a
general-purpose chatbot. This module decides, cheaply and deterministically,
whether a request is in scope — before any context read or LLM call.

Design notes:

* No LLM and no external dependency: a small keyword classifier over lowercase
  words. It is deliberately conservative and easy to extend — new topics are
  one tuple entry.
* Whitelist-first, then blocklist: a request is allowed when it names a
  financial topic; otherwise a small set of clearly off-topic patterns
  (image generation, story/movie writing, generic coding, jokes) rejects it.
  Anything else falls through to allow: the assistant's own system prompt
  already confines answers to the provided financial context, so a short
  ambiguous message ("hello") is answered as finance-of-my-account small talk
  rather than hard-rejected.
* It classifies scope only: no trading signal, no prediction, no advice.
"""
import re
from enum import StrEnum
from typing import Final


class ScopeDecision(StrEnum):
    ALLOW = "ALLOW"
    REJECT = "REJECT"


# Requests the assistant exists to answer: the user's own account, positions,
# trades and the market/economic context around them. Single-word topics match
# whole words only; multi-word topics ("margin call") match as phrases.
_FINANCIAL_TOPICS: Final[tuple[str, ...]] = (
    # account and money
    "account", "balance", "equity", "margin", "margin_level", "margin call",
    "deposit", "withdrawal", "credit", "leverage", "currency", "usd", "eur",
    "gbp", "jpy", "broker", "swap", "spread", "pip", "pips",
    # positions, orders and instruments
    "position", "positions", "trade", "trades", "trading", "lot", "volume",
    "ticket", "xauusd", "eurusd", "gbpusd", "usdjpy", "gold", "forex", "fx",
    "symbol", "symbols",
    # performance and risk
    "profit", "loss", "pnl", "p&l", "drawdown", "exposure", "risk",
    "portfolio", "history",
    # market and analysis
    "market", "markets", "price", "prices", "candle", "candles", "chart",
    "trend", "support", "resistance", "technical", "fundamental", "analysis",
    "indicator", "rsi", "macd", "moving average",
    # news and economics
    "news", "economic", "economy", "inflation", "cpi", "interest rate",
    "interest rates", "gdp", "nfp", "fomc", "fed", "ecb", "central bank",
    "calendar", "event", "events",
)

# Clearly out-of-scope requests for a financial assistant. Each pattern is a
# regex applied to the lowercased request; one tuple entry per off-topic
# capability keeps extension trivial.
_OFF_TOPIC_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bimage\b", r"\bimages?\b.*\b(generate|generation|create|draw|picture|photo)\b",
        r"\b(generate|create|draw|make)\b.*\b(image|picture|photo|logo|painting)\b",
        r"\bdraw\b",  # standalone image ask; "drawdown" is safe (word boundary + whitelist-first)
        r"\bjoke\b", r"\bfunny\b",
        r"\bstory\b", r"\bnovel\b", r"\bpoem\b", r"\bwrite (me )?a (story|movie|song|script)\b",
        r"\bmovie\b", r"\bfilm\b", r"\bsong\b", r"\blyrics\b",
        r"\bcode\b", r"\bcoding\b", r"\bprogram(ming)?\b", r"\bpython\b", r"\bjavascript\b",
        r"\bjava\b", r"\bc\+\+\b", r"\bdebug\b", r"\bregex\b", r"\bapi design\b",
        r"\bhomework\b", r"\bessay\b", r"\btranslate\b", r"\brecipe\b", r"\bworkout\b",
    )
)


def is_financial_request(message: str) -> bool:
    """True when the message names a financial topic (word/phrase match)."""
    text = message.lower()
    words = set(re.findall(r"[a-z&]+", text))
    return any(
        (topic in words if " " not in topic else topic in text)
        for topic in _FINANCIAL_TOPICS
    )


def is_out_of_scope(message: str) -> bool:
    """True when the message clearly asks for a non-financial capability."""
    text = message.lower()
    return any(pattern.search(text) is not None for pattern in _OFF_TOPIC_PATTERNS)


def check_scope(message: str) -> ScopeDecision:
    """Classify one request: ALLOW for in-scope finance questions, else REJECT.

    Deterministic and side-effect free. REJECT means the assistant cannot
    usefully answer the request from the user's financial context; it carries
    no judgment about the user and no financial content.
    """
    if is_financial_request(message):
        return ScopeDecision.ALLOW
    if is_out_of_scope(message):
        return ScopeDecision.REJECT
    # Ambiguous short talk ("hi", "thanks"): in scope by default — the answer
    # is still derived only from the user's financial context.
    return ScopeDecision.ALLOW
