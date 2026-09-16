# PROJECT_ROADMAP.md

## Purpose and document roles

This is the **authoritative full project roadmap**: current verified state,
infrastructure gaps, MVP phases (P0–P8), post-MVP phases (P9–P14), the MVP
boundary, what is explicitly out of scope, the constraints that must never be
violated, and the standing risks.

The three project documents have distinct jobs:

| Document | Job |
|---|---|
| `PROJECT_CONTEXT.md` | Current project context (product, entities, architecture, safety) plus a pointer to this roadmap |
| `PROJECT_ROADMAP.md` (this file) | The full roadmap: phases, scope, ordering, acceptance criteria, risks |
| `CURRENT_CHECKPOINT.md` | The exact latest implementation/checkpoint status, verification results and commit state |

**A phase in this roadmap is a plan, not a status.** Nothing here may be assumed
implemented. Before planning or implementing any phase:

1. read `PROJECT_CONTEXT.md`;
2. read the relevant section of this roadmap;
3. inspect the actual codebase;
4. never assume a roadmap item is implemented without verifying it in the code
   (and in `CURRENT_CHECKPOINT.md` for the recorded verification).

Status labels used below:

- **DONE** — implemented and verified in the repository (recorded in
  `CURRENT_CHECKPOINT.md`).
- **NEXT** — the ordered next piece of work; still requires an explicit
  instruction before implementation starts.
- **PLANNED** — agreed direction, not started.
- **DECISION** — blocked on an operator/business decision, not on code.
- **DEFERRED** — deliberately out of the MVP; a decision point, not a commitment.

### Revision note

2026-09-16 — **fundamental intelligence now comes before technical analysis.**
The product becomes useful far earlier if the agent can answer "what is
happening with XAUUSD today", "which fundamental factors matter", "which news
and calendar events are relevant" and "what threatens my positions", so P1 is
News + Fundamental Intelligence (built on the already-mandatory economic
calendar), P2 is instrument catalog + multi-timeframe market data, and P3 is
deterministic technical analysis, which then adds technical context to the same
agent. The economic calendar remains mandatory for every agent request and the
invariants below are unchanged. This reordering is a product decision, not a
status: every phase listed below still requires an explicit instruction before it
starts.

## Non-negotiable invariants

Every phase below must preserve all of these. A phase that would break one of
them must not be implemented without an explicit architectural decision that
changes this document first.

1. **Read-only, always.** The assistant never executes, modifies, closes or
   otherwise controls trades, and has no order/position-mutating capability.
2. **Economic Intelligence / calendar is MANDATORY for every Agent request.**
   Every agent request passes through the existing economic-intelligence context;
   no request may bypass, skip or degrade it, and no phase may make it optional.
3. **Development/test sources are never production sources.** QuantGist (the
   development/test economic-calendar tier) and the deterministic calendar and
   news fakes are never described, configured or shipped as production
   providers, and their provenance markers (`quantgist-free-development`,
   `fake-development-placeholder`) must travel with their data into every
   response and prompt.
4. **No production calendar or news vendor is invented.** The production slots
   (`ECONOMIC_CALENDAR_SOURCE=production`, `NEWS_SOURCE=production`) refuse until
   a real vendor is chosen and registered at the existing single seam, and no
   provider is ever called production-ready without a live verification recorded
   in `CURRENT_CHECKPOINT.md`.
5. **Tenant isolation.** A request can only ever read the authenticated user's own
   broker/account data; tenant identity comes from the database, never from a
   request body or a token claim.
6. **No credentials or secrets in prompts, logs, chat or API responses** — no
   application password, no MT5 password (investor or otherwise), no API key, no
   encryption key. Only the MT5 **investor** (read-only) password is ever accepted
   and stored; the master/trading password is never requested, stored or used.
7. **No cross-tenant data access** in any new capability, including any future
   agent tool.
8. **Provider/service/API boundaries are preserved** (API → service → provider
   contract → implementation → external system). No premature abstractions, no
   microservices, no framework migrations without a concrete reason.
9. **Small vertical slices.** Each phase is implemented, verified and recorded in
   `CURRENT_CHECKPOINT.md` before the next phase starts.

## 1. Current verified state

Verified against the repository on 2026-09-16 (local `HEAD` c95ae6c), 76
application files, 42 test files, **948 tests passing** with 2 pre-existing
third-party deprecation warnings, `compileall` clean, Pyright clean on changed
files (no suppressions anywhere).

**Runtime and stack.** FastAPI 0.141.1 modular monolith; PostgreSQL + async
SQLAlchemy 2.0 + Alembic; `MetaTrader5==5.0.6180` (Windows-only dependency);
`httpx`, `pydantic-settings`, `bcrypt`, `PyJWT`, `cryptography`; pytest 9.1.1.
No Dockerfile, no CI configuration, no `pyproject.toml`/`tox.ini` — dependencies
are pinned in `requirements.txt`.

**HTTP surface** (`app/main.py`): 10 routers plus `/health` (liveness only).
`POST /auth/login` · `GET/POST /users`, `POST /users/admins`,
`GET/PATCH/DELETE /users/{id}`, `GET/PUT /users/{id}/mt5-credentials` ·
`GET /account-info` · `GET /positions` · `GET /trade-history` ·
`GET /market-data/{symbol}` · `GET /portfolio-intelligence` ·
`GET /economic-intelligence/today` · `POST /agent` ·
`GET/PUT/POST /broker/llm-config(/test)`.

**Delivered capabilities.**

- **Tenant-safe authentication** (Step 43): explicit tenant selection
  (`broker` code + `login` + application password), uniform generic 401s, dummy
  bcrypt on paths with no stored hash, in-process throttle per IP and per
  `(broker, login)`, JWT carrying only `sub` (with required `exp`), and
  broker/user active state re-checked on every request.
- **Users and roles** (Steps 21A–42): three roles, exactly one `super_admin` per
  broker (database partial unique index), tenant-scoped CRUD, optional-role
  creation, and administrator-provisioned MT5 investor credentials (Fernet
  encrypted, write-only, never returned).
- **Read-only MT5 data** (Steps 15–20, 36, 39): account information, open
  positions, trade history and market data behind typed provider contracts; one
  process-wide tenant-scoped session (`MT5SessionManager`) with the lock held
  across the whole acquire → read span; every blocking call through the single
  `run_mt5_call` boundary; verified live against a real investor credential
  (`trade_allowed == False`).
- **Market data**: exactly one M1 candle per call
  (`copy_rates_from_pos(symbol, TIMEFRAME_M1, 1, 1)`) — no timeframe, no series
  (see gap G6).
- **Portfolio intelligence** (Step 24): symbol exposure, directional balance and
  a deterministic margin-based risk band; Decimal money end to end (Step 37).
- **Financial context** (Step 25): account + positions + trade history +
  portfolio intelligence in one read-only snapshot (internal, no endpoint).
- **Economic intelligence** (Steps 23, 44): `EconomicCalendarProvider` contract,
  deterministic development fake, and the QuantGist free tier as a
  development/test source written against the **live-verified** API shape
  (paginated `data` envelope, ignored date filters, `release_time`-ascending feed
  walked with an early stop, local half-open `[from, to)` window filtering,
  fail-closed envelope validation); deterministic currency-leg relevance; a
  provenance marker carried into every response.
- **Explicit calendar-source configuration** (Step 46, commit `ebbb86b`):
  `ECONOMIC_CALENDAR_SOURCE` (`auto` | `development_fake` | `quantgist` |
  `production`, default `auto`) resolved at the composition root; the
  development/test sources are served inside development only; the production
  slot is the seam a real vendor is registered behind and refuses until one
  exists; every unusable selection answers the same generic 503 with the precise,
  value-free reason logged server-side.
- **Agent** (Steps 26–33, 45): `POST /agent` with a deterministic scope guard, an
  in-process per-user daily quota, an explicit egress policy (three `LLM_SEND_*`
  switches plus structural exclusion of account identity), prompt size discipline
  with ordered, stated reductions, a broker-aware LLM router over a shared free
  pool, and an OpenAI-compatible adapter. The injected
  `EconomicIntelligenceService` supplies **today's calendar to every request**
  (Step 45) using one reference instant shared with the financial context.
- **Broker LLM configuration** (Step 32): one row per broker, Fernet-encrypted
  key, SSRF-validated base URL, super_admin-only access, connection test.
- **Verification culture**: focused suites, full suite, `compileall`, Pyright, and
  live smoke tests for external providers (the QuantGist payload mismatch was
  found only by a live test).

**Commit state.** `origin/master` is `638f972` (Step 44). Local `HEAD` is four
unpushed commits: `b9785cb` + `9ba0d95` (Step 45 and its checkpoint-status
commit) and `ebbb86b` + `c95ae6c` (Step 46 and its checkpoint-status commit).

## 2. Current infrastructure gaps

Verified absences/limitations in the current tree (each becomes work in a phase
below):

| # | Gap | Where it bites |
|---|---|---|
| G1 | **No HTTP middleware at all**: no request IDs, no structured/access logging, no global rate limit, no CORS allowlist | P8 |
| G2 | **`/health` is liveness only** (unconditional ok); no readiness for database / MT5 / LLM / calendar | P8 |
| G3 | **All limiting state is in-process** (agent daily quota, login throttle): per worker, reset on restart | P8, P13 |
| G4 | **No CI/CD, no container/deployment manifests**; `MetaTrader5` pins the runtime to Windows | P8 |
| G5 | **No background jobs/scheduler and no retention policy**; nothing conversational is persisted today | P13 (only if needed) |
| G6 | **Market data is a single M1 candle** — no timeframe, count or series | P2 (gates P3) |
| G7 | **No production economic-calendar source** (vendor decision open); QuantGist is dev/test only, so the calendar is real only in development today | P0 residual (vendor decision) |
| G8 | **The shared free LLM pool is empty** until a deployment endpoint is configured; no real free-tier provider integrated | P5, P8 |
| G9 | **Two position reads per agent request** (financial context and calendar relevance each read positions) — known issue 14; safe, but wasted serialized MT5 work | P1 (ride-along cleanup), P5 |
| G10 | **No audit trail for broker LLM configuration changes and no API-key rotation flow**; no application-password reset/rotation | P7 |
| G11 | **No technical analysis, no market series, no charts** | P2, P3 |
| G12 | **No reports/historical analytics, no watchlist/favorites, no P&L history** | P6, P9 |
| G13 | **MT5 authenticates one account per process** → all MT5 reads serialize process-wide (known issue 1) | P8, P12 |
| G14 | **Offline-only tests** (fakes/`MockTransport`): live vendor drift is invisible to the suite | standing practice: live smoke per provider change |
| G15 | **No channel-facing abstraction beyond REST** (no conversation/session concept, no idempotency keys) | P8 |
| G16 | **Windows-only MT5** and no MT5 IPC timeout; `/health` does not reflect MT5 readiness (known issues 2, 3, 7) | P8 |
| G17 | **No fundamental/news capability at all**: no news provider contract, no fundamental-intelligence service, and instrument→currency relevance exists only as the economic calendar's currency legs, so XAUUSD-specific fundamental relevance (and per-position fundamental exposure) has no home | P1 |

## 3. MVP phases (P0–P8)

Ordering note: **P1 is the point of the product direction** (a usable fundamental
answer about the instruments a customer actually holds), **P1 does not wait for
P2** (it works from the calendar plus a documented instrument/currency relevance
map), **P2 gates P3** (technical analysis needs a candle series), and the
calendar stays mandatory throughout.

### P0 — Economic Calendar production posture

**Goal.** Make the calendar's availability explicit and fail closed per
environment, so no deployment can serve development/test calendar data to a
customer and the eventual real vendor has exactly one place to be registered.

**Status.** **DONE** (Step 46, `ebbb86b`: explicit `ECONOMIC_CALENDAR_SOURCE`,
development-only development sources, a production seam that refuses, generic
503s, value-free operator logs, and the full source × environment matrix under
test), with one recorded residual: **the production vendor selection itself is
DECISION** (see the residual item below).

**Delivers.** (done) explicit calendar source selection with a production seam;
(remaining, DECISION) choosing and registering a real production calendar vendor
behind that seam, with its licensing/redistribution rights settled and one
minimal live smoke recorded in `CURRENT_CHECKPOINT.md`. A customer-facing launch
is gated on this; until it lands, non-development deployments keep refusing
rather than serving development data.

**Existing modules.** `app/core/config.py`, `app/core/dependencies.py`,
`tests/test_config_settings.py`, `tests/test_quantgist_economic_calendar.py`,
`.env.example`.

**New components.** None required for the posture; exactly one provider module
plus its settings when the vendor is chosen.

**Depends on.** Nothing.

**Acceptance criteria.** Every non-development capability either has a configured
provider or answers a documented generic failure; no development/test artifact is
ever served outside development; invalid configuration fails at startup naming
the setting only; no value is echoed; a chosen production calendar vendor is
verified live before being called production-ready.

**Verification.** Config and composition-root matrix tests; `compileall`;
Pyright; `git diff --check`. For the residual item: offline provider tests
against recorded vendor payloads plus one minimal live smoke.

**Criticality.** MVP-critical (posture done; vendor selection open).

### P1 — News and Fundamental Intelligence

**Goal.** Make the assistant practically useful immediately: answer "what is
happening with XAUUSD today?", "which fundamental factors matter for XAUUSD?",
"which news and economic-calendar events are relevant to XAUUSD?" and "what is
threatening my current positions today?" from deterministic,
provenance-carrying fundamental context — never from model guesswork. XAUUSD is
the first and reference use case.

**Status.** **NEXT**. Requires explicit instruction before implementation.

**Delivers.**

1. **News provider abstraction** (`app/providers/news.py`): a typed, vendor-neutral
   news contract (a `NewsItem`-style record with id, `published_at` UTC, source,
   title, a bounded summary/excerpt, an optional link, declared
   instruments/currencies/categories, and a per-implementation provenance marker,
   mirroring how `EconomicCalendarProvider` works today). Retrieval is
   window-bounded (from/to) and symbol/currency-scoped where the provider supports
   it, with a hard result cap.
2. **Development/test implementation plus the production seam**: a deterministic
   news fake (provenance `fake-development-placeholder`, no network) and a
   `NEWS_SOURCE` setting resolved at the composition root exactly like
   `ECONOMIC_CALENDAR_SOURCE` (the Step 46 pattern: development/test sources
   inside development only; a `production` slot that refuses until a real vendor
   is registered in that single branch; invalid values rejected at startup naming
   the setting only). **No production news vendor is chosen or invented here**,
   and nothing is called production-ready without a live verification recorded in
   `CURRENT_CHECKPOINT.md`.
3. **Instrument/currency relevance mapping**: a deterministic layer mapping a
   supported instrument to the currencies/commodity factors that move it (XAUUSD
   → the USD leg plus gold-specific factors), extending the existing calendar
   relevance classifier rather than adding a second one. Provider-declared tags
   are used when present; a documented deterministic keyword/entity mapping covers
   items that arrive untagged; relevance is one of the existing discrete levels,
   never a score that implies direction.
4. **Fundamental context service** (`app/services/fundamental_intelligence/`):
   combines today's economic calendar (the existing, mandatory
   `EconomicIntelligenceService`) with relevant news, per-instrument relevance and
   the authenticated user's own open positions into one deterministic
   `FundamentalContext` (reference instant, window, provenance per source; per
   item: timestamp, source, relevance, factual text).
5. **Portfolio-position fundamental risk context**: for each open position, the
   deterministic exposure of that position's instrument/currency legs to today's
   calendar events and relevant news — factual exposure, not advice: no direction,
   no probability, no recommendation, and an explicit UNKNOWN when data is missing
   (never an implied "no risk").
6. **HTTP surface**: `GET /fundamental-intelligence/today` (JWT-protected,
   read-only; tenant identity from the authenticated user, so only the caller's own
   positions are ever resolved), returning the deterministic context with
   provenance per source.
7. **Agent prompt integration**: a bounded fundamental block rendered beside the
   existing financial and calendar blocks, keeping the established size discipline
   (ordered, stated reductions) and the egress policy unchanged.
8. **Fact-versus-interpretation separation, structurally enforced**: the
   deterministic layer emits only source facts, timestamps, provenance and
   relevance; interpretation happens only in model output, and the prompt labels
   source material as source material with its provenance. Nothing deterministic
   here produces an outlook, forecast, target or directional claim, and a
   rendered-output scan enforces that.
9. **Fail-closed behaviour where required**: a configured-but-unusable news source
   refuses with the existing generic 503 (never silently answering as if there
   were no news); a deployment with no news source configured marks news as
   explicitly unavailable in both the context and the prompt — absence of data is
   never presented as absence of events. The economic calendar keeps its existing
   mandatory behaviour unchanged.
10. **No trading advice or predictions anywhere**: no BUY/SELL/ENTER/EXIT wording,
    no price forecasts, no probability statements, no "recommended action" — only
    facts, relevance and exposure.

**Existing modules.** `app/providers/economic_calendar.py` (contract shape to
mirror), `app/services/economic_intelligence/` (relevance classifier to extend),
`app/services/financial_context/` and `app/providers/position.py` (positions),
`app/core/config.py` + `app/core/dependencies.py` (the source-selection seam),
`app/services/agent/` (`agent_service.py`, `prompt.py`), `app/api/`.

**New components.** `app/providers/news.py`, a deterministic news fake, the
`NEWS_SOURCE` resolver branch, `app/services/fundamental_intelligence/`
(relevance mapping + context builder), one read-only router, and the fundamental
prompt block. No new external dependency, no scraper/parser, no scheduler, no
cache, no retry.

**Depends on.** P0 (DONE). Deliberately does **not** wait for P2: XAUUSD
relevance works from a documented instrument/currency map, which P2 later
generalises through the instrument catalog.

**Acceptance criteria.** The four example questions above are answerable from
deterministic context plus model synthesis, with XAUUSD working end to end; every
rendered news/calendar item carries its source, provenance and a UTC timestamp;
the deterministic layer contains no interpretation and interpretation appears only
in model output; the prompt block stays inside the configured ceiling with stated
reductions; the calendar is still composed into every agent request; a
configured-but-unusable news source produces the generic 503; an unconfigured news
source is labelled unavailable rather than empty; tenant isolation holds (a caller
resolves only their own positions); no credential or secret reaches a prompt, log
or response.

**Verification.** Fully offline: fake-news provider tests, retrieval/window/cap
tests, relevance-mapping golden tests (XAUUSD and at least one non-USD
instrument), calendar+news combination tests, portfolio-exposure tests (no
positions, one position, missing data → UNKNOWN), API tests (auth, tenant scope,
provenance), prompt tests (bounded block, provenance present, fact labels kept,
size discipline intact), failure-path tests (503 versus explicitly unavailable), a
rendered-output scan for advisory/forecast language, secret-hygiene checks,
`compileall`, Pyright, `git diff --check`. A live smoke is possible only after a
real news vendor is registered; it is never run against the fake, and no fake
result may be described as live.

**Criticality.** MVP-critical (the capability that makes the product useful
early).

### P2 — Instrument catalog and multi-timeframe market data

**Goal.** Turn market data from one M1 candle into a real, bounded series API and
let the product name what it can actually read, so fundamental and (later)
technical context can be symbol-scoped for any supported instrument. P1 starts
with a documented XAUUSD relevance map and does not wait for this.

**Status.** **PLANNED**.

**Delivers.** An explicit set of allowed timeframes (M1–MN1 subset); a bounded
`count` with a server-side hard cap; a candle-series contract with UTC alignment
and provenance; a symbol catalog describing what the authenticated account can
actually read and which instruments the fundamental relevance map covers; 422 for
invalid timeframe/count, existing 404 semantics for unavailable symbols; the
current single-candle response preserved (or a deliberately versioned path).

**Existing modules.** `app/providers/market_data.py`,
`app/providers/mt5_market_data.py`, `app/providers/fake_market_data.py`,
`app/services/market/`, `app/api/market_data_router.py`, `app/api/numeric.py`,
`app/core/dependencies.py`.

**New components.** A symbol/contract-catalog provider contract only if the
catalog is genuinely a new external read; otherwise extend the existing
market-data contract.

**Depends on.** Nothing (P0 delivered). P1 benefits from it but does not require
it, and it gates P3.

**Acceptance criteria.** A request returns N candles for an allowed timeframe in
UTC; invalid timeframe/count is refused before any provider call; caps are
enforced server-side; tenant-scoped session usage and `run_mt5_call` offloading
are unchanged; no new trading capability.

**Verification.** Provider unit tests with a fake MT5, service tests, API tests
(auth, validation, 404, caps), UTC/window tests, `compileall`, Pyright, and one
minimal live read-only smoke.

**Criticality.** MVP-critical (gates P3).

### P3 — Deterministic technical analysis (XAUUSD 4H first)

**Goal.** Add explainable, deterministic technical context for the same
instruments the fundamental capability already covers, so the agent can combine
fundamental facts (P1) with technical facts (this phase) for one instrument —
XAUUSD 4H first.

**Status.** **PLANNED** (deliberately after P1 and P2; the fundamental capability
is what makes the product useful, technical analysis is the depth that follows).

**Delivers.** A technical-analysis service computing deterministic indicators over
the P2 candle series (for example SMA/EMA, ATR, recent range and structure, simple
momentum) with an `as_of` timestamp and provenance; an HTTP endpoint exposing that
analysis; the same analysis rendered as a **bounded** block in the agent prompt
next to the financial, calendar and fundamental blocks; explicit
insufficient-data behaviour (fewer candles than the required window → clear
generic failure, never invented numbers).

**Existing modules.** P2 market data, `app/services/`, `app/providers/`,
`app/services/agent/prompt.py`, `app/core/dependencies.py`.

**New components.** One module under `app/services/` for pure, in-process
indicator functions. **No analytics vendor and no new external data dependency**;
indicators are computed from provider candles.

**Depends on.** P2 (the candle series). Complements P1: fundamental and technical
contexts reach the same agent, each deterministic and provenance-carrying.

**Acceptance criteria.** Identical inputs produce identical output; output
contains no forecast, no BUY/SELL language and no recommendation; the agent
prompt gains one bounded block while the existing size discipline and egress
policy still hold; account identity never enters the analysis or the prompt;
empty/short series fail closed.

**Verification.** Golden-value indicator tests, insufficient-data tests, API
contract tests, agent-prompt tests (block rendered, reductions still ordered), and
a trading-language scan over rendered output.

**Criticality.** MVP-critical (the technical half of the instrument view, on top
of the fundamental half from P1).

### P4 — Risk and portfolio intelligence depth

**Goal.** Make the read-only risk picture something a customer can act on, and
ground the agent in the same numbers.

**Status.** **PLANNED**.

**Delivers.** Exposure concentration per symbol; margin-level interpretation with
explicit documented thresholds; direction imbalance; drawdown over the
trade-history window; a documented deterministic risk band (extending the current
FLAT/LOW/ELEVATED/HIGH/UNKNOWN classification); and the same figures rendered
into the agent prompt, deterministically combined with P1's fundamental exposure
(still no advice, no direction, no probability).

**Existing modules.** `app/services/portfolio_intelligence/`,
`app/services/financial_context/`, `app/api/portfolio_intelligence_router.py`,
`app/services/agent/prompt.py`.

**New components.** None (extend the existing deterministic functions).

**Depends on.** P1 (the fundamental exposure it complements); symbol-level depth
benefits from P2, while currency-level risk can land independently.

**Acceptance criteria.** Every figure derives only from account/position/trade
data; unknown inputs yield an explicit UNKNOWN rather than a guess; no
recommendation language; the response contract is extended additively.

**Verification.** Deterministic unit tests on fixed snapshots, empty and
single-position edge cases, API contract tests, agent-prompt tests.

**Criticality.** MVP-critical.

### P5 — Agent grounding, safety and cost control (explicitly no tool calling)

**Goal.** Make the assistant's answers trustworthy, auditable and affordable now
that it carries financial, calendar, fundamental and market context.

**Status.** **PLANNED**.

**Delivers.** A documented, deterministic layout, grounding rule and budget per
context block, with every rendered block traceable to a source and its
provenance; a deterministic **prompt-safety screening** step that refuses
trading-instruction requests before any provider call (rule-based, not LLM-based);
per-broker request governance and cost control alongside the existing per-user
quota (prompt budget, provider-call accounting, documented quota contract); a
per-request record of which context blocks and sources were sent (never the
contents of a secret); failure copy that never invents data; and a pinned,
recorded decision that the MVP has **no tool/function calling, no agent
framework, no multi-turn loop**.

**Existing modules.** `app/services/agent/` (`agent_service.py`, `prompt.py`,
`scope.py`, `usage.py`, `egress.py`), `app/api/agent_router.py`.

**New components.** Screening, grounding and cost-accounting logic inside
`app/services/agent/` — no new framework, no new dependency.

**Depends on.** P1 (fundamental context), P3 (technical context when it lands)
and P4 (risk depth).

**Acceptance criteria.** No prompt exceeds the configured ceiling; every omitted
block is stated in the body; trading instructions are refused deterministically
before any provider call; no identity, credential or secret can enter a prompt
(structurally and under test); existing callers see no contract change.

**Verification.** Prompt-limit tests, egress tests, scope/screening tests, usage
tests, and an architecture test asserting that no tool/function-calling mechanism
exists in the agent layer.

**Criticality.** MVP-critical.

### P6 — Reports and historical analysis (on-demand)

**Goal.** Give a customer a repeatable written summary without introducing a
scheduler.

**Status.** **PLANNED**.

**Delivers.** An on-demand report endpoint (account snapshot, period performance
from trade history, positions summary, the period's calendar and relevant-news
context, risk band) with a deterministic report contract; and an optional LLM
narrative that reuses the existing router and egress policy unchanged.

**Existing modules.** Financial context, portfolio intelligence, trade history,
economic intelligence, `app/services/agent/` (LLM boundary), `app/api/`.

**New components.** `app/services/reports/` composing existing services. No
storage, no scheduler, no queue.

**Depends on.** P1 (fundamental context), P3 (technical analysis) and P4 (risk).

**Acceptance criteria.** Report output is reproducible for identical inputs;
contains no trading advice; no account identity in LLM-bound text; generating a
report performs no MT5 read beyond the ones the equivalent context already needs.

**Verification.** Golden-report tests, egress tests, API tests, and an explicit
read-only assertion.

**Criticality.** MVP-critical (first clearly billable capability).

### P7 — Broker operations and auditability

**Goal.** Let a broker operate the product safely: credential lifecycle,
configuration accountability and consent.

**Status.** **PLANNED**.

**Delivers.** Application-password reset/rotation with an explicit policy
(admin-triggered, and whether self-service exists); MT5 credential
re-provisioning flow; an **audit trail** for broker LLM configuration changes
(who, when, which field — never the key); an API-key rotation flow that
re-encrypts stored secrets under a new `SECRET_ENCRYPTION_KEY`; and a per-broker
egress consent policy constructed at the composition root (the seam already
exists in `get_outbound_data_policy`).

**Existing modules.** `app/services/broker_llm_config/`,
`app/core/encryption.py`, `app/api/users_router.py`,
`app/services/agent/egress.py`, `app/db/models/`, `alembic/`.

**New components.** One audit model plus its migration — the only schema change
planned in the MVP, justified by auditability being a broker-facing requirement.

**Depends on.** Nothing blocking; independent of P1–P6.

**Acceptance criteria.** Every stored-credential change is attributable to an
actor; keys/tokens never appear in audit rows or logs; the rotation flow is
proven by a re-encryption test; no endpoint ever returns a secret.

**Verification.** Migration tests, audit tests, rotation tests, secret-hygiene
tests, API authorization tests.

**Criticality.** MVP-critical for a B2B broker deployment.

### P8 — Production readiness and channel-ready API

**Goal.** Operate the assistant as a service, and give a future channel client a
surface to build on **without building any channel in the MVP**.

**Status.** **PLANNED**.

**Delivers.** Request IDs and structured logging with secret redaction; a
readiness endpoint reporting database / MT5 / LLM / calendar / news availability
separately from liveness; CORS with an explicit allowlist; a coarse global rate
limit; a CI pipeline (format/typecheck/`compileall`/full offline pytest, with a
Windows runner for anything touching MT5); a documented deployment topology for
the one-process-per-MT5-account constraint (worker per broker, or an explicit
single-tenant limitation); and a written data-retention statement (today nothing
conversational is persisted).

**Existing modules.** `app/main.py`, `app/core/` (config, blocking, security,
`mt5_session.py`), `tests/`, `requirements.txt`.

**New components.** Request-ID/structured-logging middleware and a readiness
router; CI configuration; packaging (an image is Windows-hostile because of
`MetaTrader5`, so the decision must be explicit).

**Depends on.** All MVP phases (this phase verifies them).

**Acceptance criteria.** Every log line carries a request id; no secret appears
in rendered logs (under test); readiness reflects real dependency state;
multi-worker semantics are documented or refused; CI runs the full offline suite
on every change.

**Verification.** Middleware/logging tests, readiness tests, a secret-scan test
over rendered logs, and a green CI run.

**Criticality.** MVP-critical (a customer-facing launch is not supportable
without it).

## 4. Post-MVP phases (P9–P14)

### P9 — Watchlist and favorites
**Post-MVP.** A per-user watchlist (tenant-scoped) plus per-symbol summaries, so
users can follow instruments rather than re-query them. Modules: users, market
data (P2), fundamental intelligence (P1), portfolio intelligence. New component: a small watchlist model +
migration (the roadmap's second schema change). **Optional.**

### P10 — Channel clients on the same backend
**Post-MVP.** Web chat first, then Telegram and WhatsApp, then a mobile client —
all against the existing channel-independent REST surface, with the channel
holding no business logic and no credentials. Depends on P8 (channel-ready API).
**Optional.**

### P11 — Streaming and real-time updates
**Post-MVP.** Server-sent events / websockets for quote ticks and long-running
agent answers. Deliberately deferred: it changes the request/response model and
the blocking boundary, so it needs its own architectural decision. **Optional.**

### P12 — Multi-broker scale-out of the MT5 boundary
**Post-MVP.** The documented production answer to one-account-per-process: a
worker process (or equivalent) per broker with the same provider/service API, so
MT5 reads are no longer serialized across tenants. Depends on P8's topology
decision. **Optional until broker count demands it.**

### P13 — Distributed state, background jobs and retention
**Post-MVP.** Shared usage/throttle counters, scheduled report/retention jobs,
and automated data retention — only if the product actually needs them; today
everything is in-process and nothing conversational is stored. Depends on P8/P12.
**Optional.**

### P14 — Agent capability expansion (explicit decision point)
**Post-MVP — DECISION.** Only here may tool/function calling, multi-turn
conversation, memory or streaming agent answers be considered, and only as an
explicit architectural decision recorded in this document first. If ever adopted,
tools must be **read-only**, tenant-scoped, and must never include a trading
action; the economic-calendar context must remain mandatory on every request;
fundamental/news context returned by a tool must still carry its provenance and
stay clearly separated from interpretation; and the existing guard chain
(scope → quota → contexts → provider) must still run. **DEFERRED.**

## 5. MVP scope

**In the MVP (P0–P8):** tenant-safe authentication and user management; read-only
accounts, positions and trade history; **news + fundamental intelligence**
(provenance-carrying news and economic-calendar facts, instrument/currency
relevance and per-position fundamental exposure, XAUUSD first) with an explicit,
fail-closed source configuration and a real production calendar source;
multi-timeframe market data with an instrument catalog; deterministic technical
analysis; deeper portfolio/risk intelligence; the read-only agent whose every
request carries the financial, calendar and fundamental contexts (plus technical
and market context once P2/P3 land); on-demand reports; agent grounding, safety
and cost control; broker operations with auditability; and production readiness
(observability, limits, CI, deployment/retention documentation) with an API a
channel client can consume.

**Explicitly outside the MVP:** tool/function calling and any agent framework;
multi-turn conversation or conversation memory; persisted chat history;
streaming/websockets; any Web/Telegram/WhatsApp/Mobile client; watchlists;
billing/subscription management; localization/i18n; multi-broker MT5 worker
pools; distributed limiters; schedulers/background jobs; multiple accounts per
user or portfolio aggregation across users; social/sentiment feeds and external
analyst commentary; self-service registration; and any form of trade execution,
modification or closing.

## 6. Never (permanent constraints)

- Never execute, modify, close or otherwise control a trade; never add a trading
  tool, not even behind an indirect AI feature.
- Never request, store or use the MT5 master/trading password — only the
  read-only investor password is ever accepted, and never in a prompt or a log.
- Never expose credentials, secrets, tokens or API keys in prompts, logs, chat
  output or API responses.
- Never allow cross-tenant access to account, position, trade or credential data.
- Never present development/test data (the deterministic calendar and news
  fakes, QuantGist) as live market data; provenance must always travel with the
  data, and an absence of data (no news source configured, an empty response)
  must never be presented as an absence of events.
- Never claim a provider is production-ready without a live verification recorded
  in `CURRENT_CHECKPOINT.md`, and never choose or use a news/calendar vendor
  before its licensing and redistribution rights are settled.
- Never let the deterministic news/fundamental/technical layer produce advice, a
  forecast, a target or a recommendation; interpretation belongs to the model, on
  top of labelled source facts.
- Never make the Economic Intelligence / calendar context optional for the agent.
- Never select QuantGist (or any development/test source) as a production
  provider.
- Never invent a production calendar or news vendor: the production slots stay
  closed until a real one is chosen and registered.
- Never replace the modular monolith with microservices, and never commit `.env`
  or any secret.

## 7. Risks and dependencies

| # | Risk | Impact | Mitigation / owning phase |
|---|---|---|---|
| R1 | MT5 Python API authenticates one account per process, so all MT5 reads serialize | Throughput ceiling per broker | Current lock discipline keeps it safe (not a correctness risk); scale-out is P8 (topology) and P12 (worker per broker) |
| R2 | `MetaTrader5` is Windows-only; no CI/Docker today | Deployment and CI friction; drift between dev and prod | P8 explicit packaging/CI decision (Windows runner for MT5 paths) |
| R3 | No production calendar vendor: licensing/redistribution rights are unresolved | Blocks real calendar data for customers | P0 residual vendor decision; today the production slot fails closed, so no development/test calendar data can leak |
| R4 | QuantGist free tier is delayed and quota-limited | Unsuitable for customers; quota exhaustion | Development/test only (enforced); production slot closed until the P0 vendor decision lands |
| R5 | LLM availability/cost; the shared free pool is empty by default | The agent answers 503 rather than degrading | Fail-closed today; P5 quota governance; P8 readiness reporting |
| R6 | In-process quota/throttle only | Multi-worker deployments under-enforce limits | P8 (documented/refused multi-worker), P13 (distributed state) |
| R7 | Offline test suite cannot see live vendor drift (the QuantGist shape mismatch was found only live) | Silent provider breakage | Standing practice: one minimal live smoke per provider change (calendar and news alike), plus recorded payload fixtures |
| R8 | Prompt injection through user text or third-party calendar titles | Model misbehaviour | Untrusted-block framing and escaping already in place; deterministic screening in P5 |
| R9 | Fundamental or technical context could be read as investment advice (headlines plus relevance can imply direction) | Product/regulatory risk | P1 and P3 require deterministic, explainable, explicitly non-advisory output with structural fact-versus-interpretation separation; no forecasts anywhere |
| R10 | No audit trail / retention policy today | Broker (and regulatory) requirements unmet | P7 (audit), P8 (retention statement) |
| R11 | Two position reads per agent request (known issue 14), and P1 adds a fundamental-exposure read | Wasted serialized MT5 work | P1 reuses one position read where it can; otherwise optional cleanup once the read paths are revisited (P5) |
| R12 | Read-only assurance must survive every future phase | Core product promise | Architecture tests (no trading capability anywhere), review per phase, invariants above |
| R13 | News content licensing/redistribution and provider terms of service (headlines, excerpts, storage and re-display rights) are unresolved; many free tiers forbid commercial use | Blocks a production news feed; potential legal exposure | P1 stays source-agnostic behind the provider contract and fails closed in non-development; selecting and using a vendor requires settled rights, and provenance travels with every item |
| R14 | A relevance false negative, or a missing/unconfigured news source, could hide a real fundamental driver | Misleading answers | An unconfigured source is labelled unavailable (never empty), missing exposure is an explicit UNKNOWN, the mandatory calendar remains the baseline context, and the relevance mapping is deterministic and test-pinned |
| R15 | News retrieval is high-volume and latency/cost sensitive, and no caching is planned | Provider cost and rate limits; slow requests | P1 uses bounded windows and hard result caps per request (no per-request fan-out beyond one bounded query); caching is out of scope and would require its own phase and decision |

## 8. Change control

- This document is changed only on explicit instruction; a phase is added,
  reordered or removed deliberately, never implicitly.
- A phase is **DONE** only when its acceptance criteria are met, its verification
  has actually been executed, and the result is recorded in
  `CURRENT_CHECKPOINT.md` (with the commit state).
- Nothing in this roadmap may be implemented ahead of the current checkpoint, and
  no phase may weaken an invariant in section 1 or a constraint in section 6.
- If a phase turns out to require a significant architectural change outside its
  stated scope, **stop and report** the conflict instead of expanding scope
  silently.
