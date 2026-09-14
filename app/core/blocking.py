"""Consolidated blocking boundary for MT5 operations.

MT5 is a blocking C extension: its calls must never run on the FastAPI event
loop. All MT5-touching endpoints route their synchronous service call through
`run_mt5_call`, which executes it on the worker threadpool instead. Providers
and services stay deliberately synchronous; the async boundary lives here, at
the application layer above them.
"""
from collections.abc import Callable
from typing import TypeVar

from starlette.concurrency import run_in_threadpool

T = TypeVar("T")


async def run_mt5_call(fn: Callable[..., T], *args: object) -> T:
    """Run a blocking MT5-backed service call off the event-loop thread."""
    return await run_in_threadpool(fn, *args)
