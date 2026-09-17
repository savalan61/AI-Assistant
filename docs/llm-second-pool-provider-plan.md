# Plan: Second LLM Pool Provider

Planning document only — **nothing here is implemented**. It records the
design for adding a second provider behind the existing vendor-neutral
`LLMProvider` seam so it can coexist with the pinned development free tier
(`nvidia/nemotron-3-super-120b-a12b:free`) and, later, anchor a paid
production tier.

## Current seam (what already exists)

- `app/providers/llm.py` — `LLMProvider.complete(LLMPrompt) -> str`, with
  `LLMFallbackError` as the designated transient failure.
- `app/providers/openai_compatible_llm.py` — the one concrete wire adapter.
  Speaks any OpenAI-compatible `/chat/completions` endpoint; injectable HTTP
  transport; classifies HTTP status codes and HTTP-200 error envelopes into
  transient (`LLMFallbackError`) vs permanent (plain `RuntimeError`).
- `app/providers/llm_pool.py` — ordered pool; falls through only on
  `LLMFallbackError`; one safe terminal error when exhausted.
- `app/core/dependencies.py::get_free_llm_pool` — the composition seam:
  deployment endpoint first (settings `LLM_*`), then the pinned OpenRouter
  free model, development-only.

The critical property: **a "provider" is already just a constructor call**.
Adding a second one requires configuration and composition, not new
abstractions.

## Provider choice: OpenAI-compatible endpoint (the `LLM_*` slot, already built)

The strongest candidate is deliberately unglamorous: any **paid, stable,
OpenAI-compatible endpoint** (OpenAI itself, Azure OpenAI, or a self-hosted
gateway exposing the same wire format) registered in the **existing
`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` settings**.

Why this fits:

- **Zero adapter code.** `OpenAICompatibleLLMProvider` already speaks the wire
  format, already classifies transient vs permanent failures, already handles
  HTTP-200 error envelopes, and already keeps the key out of logs and errors.
- **It is the designed production slot.** The pool already puts the `LLM_*`
  endpoint *first*; the composition-root comment names it "the seam a paid
  production provider plugs into". The work is configuration plus tests, not
  engineering.
- **Vendor-replaceable.** Because the adapter is wire-format-level, switching
  paid vendors later is a settings change, not a code change.

A second *free* development tier (e.g. another `:free` OpenRouter model) is the
cheapest immediate fallback — see "Development tier" below — but free tiers
share the same overload/rate-limit profile, so a second free provider reduces
single-vendor flakiness only marginally. It is a convenience, not the
production answer.

## Development tier (immediate, optional)

Add a second pinned OpenRouter free model as a third pool entry:

- New settings: `OPENROUTER_FALLBACK_MODEL` (pinned model id, blank default —
  blank keeps the pool exactly as today).
- Composition: inside the existing `APP_ENV == "development"` guard, append a
  second `OpenAICompatibleLLMProvider` (same base URL and key, fallback model).
- Order: deployment endpoint → primary free model → fallback free model. The
  pool's existing fall-through-on-`LLMFallbackError` semantics do the rest: the
  Step 56 overload case (HTTP-200 `provider_overloaded` envelope) now falls
  through to the second free model instead of ending the request.
- Testing offline: `httpx.MockTransport` handlers returning the overload
  envelope, asserting the second provider serves the request (the same pattern
  `tests/test_openai_compatible_llm.py` already uses pool-level).

## Production tier (the actual goal)

1. **Configuration.** Operator sets `LLM_BASE_URL`, `LLM_API_KEY`,
   `LLM_MODEL` (one pinned production model — never a dynamic pool).
2. **Environment gating.** The OpenRouter branch stays `development`-only.
   Outside development the pool is exactly the deployment endpoint: if it is
   unconfigured, the pool is empty and requests fail safely (503) — the
   existing fail-closed behavior, unchanged.
3. **Credential handling.** Keys stay in environment/secret configuration;
   the adapter already never logs or echoes them, and the existing secret
   scans cover the new settings names automatically (they scan values, not
   names). No new credential mechanism.
4. **Failure classification.** Already implemented and tested: 429/5xx →
   `LLMFallbackError`; 401/403/400-shaped envelopes → plain `RuntimeError`
   (pool stops — a misconfigured paid provider must fail loudly, not silently
   shift traffic to the free tier). No change needed or wanted.
5. **Timeouts.** Production models answer in single-digit seconds; the default
   30 s client timeout is appropriate for the paid slot. The development free
   tier's 240 s runtime override stays a development concern.
6. **Cost/quota.** A paid provider makes per-request cost real. The existing
   in-process per-user daily agent limit (known issue 10) is the only bound;
   distributed limiting and token accounting remain documented future work —
   this plan must not add them.
7. **Rate limits/availability.** Paid endpoints still return 429s; the pool's
   fall-through covers a *transient* 429 only when a second provider exists.
   In a single-provider production pool a 429 surfaces as the safe terminal
   error — acceptable, and honest.

## What changes / what must not change

Changes (all configuration-level):
- settings: `OPENROUTER_FALLBACK_MODEL` (dev) and operator-set `LLM_*` (prod)
- `get_free_llm_pool`: append the dev fallback provider in the existing guard
- `.env.example` documentation; offline tests for the new composition branch

Must not change:
- `LLMProvider`, `LLMFallbackError`, the pool's fall-through semantics
- the adapter's failure classification (it is the contract)
- `AgentService`, prompt construction, all intelligence services
- broker-configured providers (`LLMRouter`): a broker's own provider never
  falls back to the shared pool — that policy is tenant-safety, not an
  implementation detail
- the benchmark: it already treats providers as interchangeable; the new
  provider must pass the same prompt checks and `grade_response`

## Verification plan for the eventual implementation

Focused offline tests (MockTransport): dev fallback appended only in
development; blank fallback model leaves the pool unchanged; overload envelope
on provider 1 falls through to provider 2; auth-shaped envelope stops the pool
even with a fallback available. Full suite, compileall, Pyright, `git diff
--check`, trading-safety and secret scans per repo convention.
