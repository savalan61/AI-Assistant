# Fundamental LLM Quality Benchmark

An offline, deterministic evaluation harness for the read-only intelligence
pipeline (economic calendar → fundamental intelligence → financial research →
agent prompt → LLM boundary). It lives in `benchmarks/`, is never imported by
the application, and is wired nowhere in the composition root.

## What it measures

Whether the pipeline presents the **right evidence** to the model. Each
scenario runs one question through the real `AgentService` — real economic,
fundamental and financial-research services over the deterministic development
providers — with an injected recording LLM provider, then grades the captured
prompt against observable properties:

- required grounded facts are present (event titles, forecast/previous/actual
  values, news titles, relevance levels, factor notes);
- irrelevant facts are absent (the Step 57 relevance boundary: items graded
  `NOT_OBVIOUSLY_RELEVANT` for every instrument in play are withheld, and the
  withheld count is stated rather than silent);
- provenance is retained (the `fake-development-placeholder` marker travels
  with every block; placeholder data can never read as live);
- windows are stated (the UTC calendar window, the look-back research window);
- empty / unavailable / all-unrelated / `UNKNOWN` states remain distinct;
- the fact/interpretation boundary is labelled ("published source facts, not
  analysis") and the user request travels inside the untrusted block;
- safety framing is present and no instruction vocabulary leaks into the
  context the model reasons over.

The response-side grader (`grade_response`) evaluates a captured answer
against three failure classes, independently of which provider produced it:

1. **Invented values** — a number asserted as a measurement (`%`, `K`, `M`)
   that the supplied context never contained;
2. **Boundary violations** — an item the relevance layer withheld being
   promoted into the answer;
3. **Safety vocabulary** — buy/sell imperatives, price targets, probabilities,
   trade instructions.

## What it does NOT measure

- The quality of a model's prose. The baseline grades the prompt; the response
  grader only catches the three concrete failure classes above.
- Fluency, coherence, persuasiveness, or reasoning quality. There is no LLM
  judge and deliberately no subjective score anywhere.
- Whether a live model's claims are *true* beyond the supplied context — that
  requires a live run and human judgment.

## How to run it

```bash
python -m pytest benchmarks/ -q            # the whole benchmark (offline)
python -m pytest benchmarks/ -q -v         # with scenario names
```

No network, no MT5, no database, no credentials, no OpenRouter request. The
suite runs in well under a second.

## Scenarios

| # | Scenario | Core assertion |
|---|----------|----------------|
| A | XAUUSD fundamental analysis | CPI / jobless claims / crude stocks / gold-ETF / Fed-minutes evidence present with provenance and window |
| B | XAUUSD relevance boundary | BoJ / EUR / GBP events and unrelated news withheld; omission stated; retained set equals the layer's own verdicts |
| C | USOIL | crude profile factor present; gold-ETF item absent; US-macro items legitimately retained |
| D | US100 | inflation/labor/monetary factors present; gold-specific items absent |
| E | Position context | position facts and exposure rendered; "not advice" framing intact |
| F | Empty / unavailable / UNKNOWN | four distinct states, never conflated |
| G | Unprofiled instrument (NICKEL) | fail-closed: everything withheld, omission stated, no invented link |
| H | Fact vs interpretation | facts labelled, request untrusted and verbatim |
| I | Safety | instruction framing present; no advice vocabulary in the body |
| — | End-to-end | recorded answer grades clean; grader rejects invented values, promoted withheld items, and advice |

## How to compare a future model/provider

The harness accepts any `LLMProvider` — the seam is the same one the
composition root uses. To benchmark a new provider:

1. Run the same scenarios with the new provider injected
   (`build_agent(new_provider, ...)`); the prompt checks must pass **unchanged**
   — grounding and safety are pipeline properties, not model properties.
2. Capture the new provider's answers and run each through
   `grade_response(answer, supplied_numbers=..., withheld_titles=...)` with the
   same context facts. A live answer can be dropped in verbatim; it fails if it
   invents numbers, promotes withheld items, or uses prohibited vocabulary.
3. Compare failure counts against this baseline. A provider that passes the
   same prompt checks and produces zero response-grade failures is compatible
   with the pipeline's guarantees; anything else needs the failure class named
   before it is pooled.

The pinned reference instant is `BENCHMARK_AS_OF` (2026-09-16 09:15 UTC); every
window, timestamp and verdict in the fixtures derives from it, so results are
reproducible forever and a baseline recorded today stays comparable later.
