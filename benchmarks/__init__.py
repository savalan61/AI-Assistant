"""Offline Fundamental LLM Quality Benchmark — evaluation harness, not a feature.

What it measures
----------------
Whether the pipeline presents the RIGHT evidence to the model: the captured
prompt for each benchmark scenario is graded against observable properties —
required grounded facts present, irrelevant facts absent, provenance retained,
windows stated, exposure statuses distinct, safety framing intact.

What it does NOT measure
------------------------
The quality of a model's PROSE. This baseline grades the prompt (what the
pipeline supplies); a response grader for generated answers lives alongside it
and is applied whenever a captured response exists (offline: the fake
provider's; live: whatever a future runner records). It cannot measure fluency,
persuasiveness, or factual correctness of a live model beyond what the supplied
context contains — those need a live run and a human or model judge, which this
harness deliberately does not include (no LLM judge).

How to run it
-------------
    python -m pytest benchmarks/ -q          # the whole benchmark
    python -m pytest benchmarks/test_benchmark_prompt_quality.py -q

How to compare a future model/provider
--------------------------------------
Point the harness at any LLMProvider implementation (the seam already accepts
one) and re-run: the scenario fixtures and checks are identical, so a new
provider is compared against the recorded baseline by (1) the same prompt
checks passing unchanged — grounding and safety are pipeline properties — and
(2) the response checks applied to whatever the new provider actually returned.
A live model's answer can be dropped into the response grader verbatim: it
fails if the answer asserts numbers the context never supplied, promotes
withheld items, or uses prohibited safety vocabulary.
"""
from benchmarks.fundamental import (
    ResponseGrade,
    Scenario,
    ScenarioCheck,
    ScenarioResult,
    grade_response,
    run_scenario,
)
from benchmarks.providers import (
    BENCHMARK_AS_OF,
    BENCHMARK_WINDOW_FROM,
    BENCHMARK_WINDOW_TO,
    RecordingLLMProvider,
    build_agent,
    build_economic_service,
    build_fundamental_service,
    build_research_service,
    POSITION_NICKEL,
    POSITION_XAUUSD,
    QUESTION_EMPTY,
    QUESTION_NASDAQ,
    QUESTION_NICKEL,
    QUESTION_POSITIONS,
    QUESTION_USOIL,
    QUESTION_XAUUSD,
)

__all__ = [
    "BENCHMARK_AS_OF",
    "BENCHMARK_WINDOW_FROM",
    "BENCHMARK_WINDOW_TO",
    "POSITION_NICKEL",
    "POSITION_XAUUSD",
    "QUESTION_EMPTY",
    "QUESTION_NASDAQ",
    "QUESTION_NICKEL",
    "QUESTION_POSITIONS",
    "QUESTION_USOIL",
    "QUESTION_XAUUSD",
    "RecordingLLMProvider",
    "ResponseGrade",
    "Scenario",
    "ScenarioCheck",
    "ScenarioResult",
    "build_agent",
    "build_economic_service",
    "build_fundamental_service",
    "build_research_service",
    "grade_response",
    "run_scenario",
]
