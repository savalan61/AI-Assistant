"""Tests for the deterministic Agent scope guard.

Pure classification tests: no LLM, no network, no I/O, no fixture needed.
"""
import pytest

from app.services.agent.scope import ScopeDecision, check_scope, is_financial_request, is_out_of_scope


# --- clearly financial requests are allowed -------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "What is my account balance?",
        "How much equity do I have right now?",
        "Show me my open positions",
        "Am I exposed to USD today?",
        "What is my margin level?",
        "Summarize my trade history",
        "How did my XAUUSD trades perform? What is my P&L?",
        "What is my portfolio risk and drawdown?",
        "Show me the market price for gold",
        "Any important economic events today? CPI, interest rates?",
        "Is there any news affecting my EURUSD position?",
        "What does the RSI indicator say about the trend?",
        "Explain the technical and fundamental analysis for my symbols",
        "What is my total volume across all symbols?",
    ],
)
def test_clearly_financial_requests_are_allowed(message: str) -> None:
    assert check_scope(message) is ScopeDecision.ALLOW


def test_financial_topic_match_is_whole_word() -> None:
    # "trade" matches; an unrelated word merely containing it must not.
    assert is_financial_request("explain my trade history")
    assert not is_financial_request("explain the MasTrade museum opening hours")


# --- clearly off-topic requests are rejected -------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "Generate an image of a sunset",
        "Create a picture of a cat",
        "Draw me a logo",
        "Tell me a joke",
        "Write me a story about dragons",
        "Write a movie script",
        "Write a poem about autumn",
        "Help me debug my Python code",
        "What is wrong with this JavaScript program?",
        "Write an essay about the French Revolution",
        "Translate this paragraph to French",
        "Give me a pasta recipe",
        "Plan a workout for me",
    ],
)
def test_clearly_off_topic_requests_are_rejected(message: str) -> None:
    assert check_scope(message) is ScopeDecision.REJECT


def test_off_topic_detection_does_not_require_a_financial_topic() -> None:
    # No financial keyword at all, but an explicit off-topic ask.
    assert is_out_of_scope("tell me something funny")
    assert check_scope("tell me something funny") is ScopeDecision.REJECT


def test_financial_request_containing_an_off_topic_word_is_still_allowed() -> None:
    # Whitelist-first: a genuine finance question wins even if it mentions an
    # off-topic word (e.g. discussing a news story about markets).
    assert check_scope("What market news stories affected my positions today?") is ScopeDecision.ALLOW


# --- ambiguous input ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "hello",
        "hi",
        "thanks",
        "What should I know?",
        "Anything interesting today?",
    ],
)
def test_ambiguous_short_talk_defaults_to_allow(message: str) -> None:
    # The guard is conservative: short ambiguous talk stays in scope because
    # the assistant answers only from the user's financial context anyway.
    assert check_scope(message) is ScopeDecision.ALLOW


# --- contract properties -----------------------------------------------------------------


def test_decision_is_deterministic_and_repeatable() -> None:
    assert check_scope("my balance?") == check_scope("my balance?")
    assert check_scope("draw a cat") == check_scope("draw a cat")


def test_scope_never_decides_trading_actions() -> None:
    # The decision vocabulary is scope-only: ALLOW/REJECT, nothing that could
    # read as a trading instruction or prediction.
    assert {member.value for member in ScopeDecision} == {"ALLOW", "REJECT"}
