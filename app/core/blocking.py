"""Consolidated blocking boundaries for the application's synchronous work.

Two boundaries, deliberately on two different pools:

* `run_mt5_call` — every MT5-touching service call. MT5 is a blocking C
  extension whose calls must never run on the FastAPI event loop, and the MT5
  session boundary serializes them on one terminal, so they run on the shared
  worker threadpool.
* `run_llm_call` — the outbound model call. It touches no MT5 state, but it
  blocks for a whole network round trip, so it must NOT occupy an MT5 worker:
  otherwise a user waiting on the model holds one of the very worker threads an
  MT5 read needs. It therefore runs on the event loop's own default executor,
  which is a different pool from the worker threadpool above (starlette's
  `run_in_threadpool` goes through anyio's worker threads and its per-loop
  capacity limiter).

Providers and services stay deliberately synchronous; the async boundaries live
here, at the application layer above them.
"""
import asyncio
from collections.abc import Callable
from functools import partial
from typing import TypeVar

from starlette.concurrency import run_in_threadpool

T = TypeVar("T")


async def run_mt5_call(fn: Callable[..., T], *args: object) -> T:
    """Run a blocking MT5-backed service call off the event-loop thread."""
    return await run_in_threadpool(fn, *args)


async def run_llm_call(fn: Callable[..., T], *args: object) -> T:
    """Run a blocking outbound LLM call OUTSIDE the MT5 blocking threadpool.

    Why a second boundary exists: a model round trip takes seconds, and while it
    runs the request needs no MT5 access at all. Running it on the MT5 worker
    threadpool would keep one worker busy for that whole time, which is what
    starved MT5 reads when several requests were waiting on the model.

    The provider abstraction is unchanged: providers stay synchronous, and this
    function is the only place the model call crosses a thread. The pool is the
    event loop's default executor, which asyncio bounds to min(32, cpu + 4)
    threads; the per-user agent quota bounds concurrent requests well below that.
    If outbound model calls ever need their own concurrency cap, that cap belongs
    here rather than in the provider.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(fn, *args))
