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

## Non-negotiable invariants

Every phase below must preserve all of these. A phase that would break one of
them must not be implemented without an explicit architectural decision that
changes this document first.

1. **Read-only, always.** The assistant never executes, modifies, closes or
   otherwise controls trades, and has no order/position-mutating capability.
2. **Economic Intelligence / calendar is MANDATORY for every Agent request.**
   Every agent request passes through the existing economic-intelligence context;
   no request may bypass, skip or degrade it, and no phase may make it optional.
3. **QuantGist is a development/test source only.** It is never described,
   configured or shipped as a production provider, and its provenance marker
   (`quantgist-free-development`) must travel with its data into every response
   and prompt.
4. **No production calendar vendor is invented.** The production slot
   (`ECONOMIC_CALENDAR_SOURCE=production`) refuses until a real vendor is chosen
   and registered at the existing single seam.
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
| G6 | **Market data is a single M1 candle** — no timeframe, count or series | P1 (gates P2) |
| G7 | **No production economic-calendar source** (vendor decision open); QuantGist is dev/test only | P3 |
| G8 | **The shared free LLM pool is empty** until a deployment endpoint is configured; no real free-tier provider integrated | P5, P8 |
| G9 | **Two position reads per agent request** (financial context and calendar relevance each read positions) — known issue 14; safe, but wasted serialized MT5 work | optional cleanup, P5 |
| G10 | **No audit trail for broker LLM configuration changes and no API-key rotation flow**; no application-password reset/rotation | P7 |
| G11 | **No technical analysis, no market series, no charts** | P1, P2 |
| G12 | **No reports/historical analytics, no watchlist/favorites, no P&L history** | P6, P9 |
| G13 | **MT5 authenticates one account per process** → all MT5 reads serialize process-wide (known issue 1) | P8, P12 |
| G14 | **Offline-only tests** (fakes/`MockTransport`): live vendor drift is invisible to the suite | standing practice: live smoke per provider change |
| G15 | **No channel-facing abstraction beyond REST** (no conversation/session concept, no idempotency keys) | P8 |
| G16 | **Windows-only MT5** and no MT5 IPC timeout; `/health` does not reflect MT5 readiness (known issues 2, 3, 7) | P8 |

## 3. MVP phases (P0–P8)

Ordering note: **P1 gates P2** (the flagship 4H analysis needs a candle series),
and **P3 is MVP-critical** because the calendar is mandatory and today's only
sources are development/test ones.

### P0 — Fail-closed deployment configuration and environment matrix

**Goal.** Make every capability's availability explicit per environment so no
deployment can serve development data or an unconfigured provider to a customer.

**Status.** **DONE for the calendar** (Step 46, `ebbb86b`: explicit
`ECONOMIC_CALENDAR_SOURCE`, development-only development sources, a production
seam that refuses, generic 503s, value-free operator logs, and the full
source × environment matrix under test). Remaining sub-items: PLANNED.

**Delivers.** (done) explicit calendar source selection with a production seam;
(remaining) a documented per-environment capability matrix for secrets,
encryption key, LLM endpoint and calendar source; startup validation that names
what is missing without echoing any value; a test pinning that whole matrix.

**Existing modules.** `app/core/config.py`, `app/core/dependencies.py`,
`tests/test_config_settings.py`, `tests/test_quantgist_economic_calendar.py`,
`.env.example`.

**New components.** None required.

**Depends on.** Nothing.

**Acceptance criteria.** Every non-development capability either has a configured
provider or answers a documented generic failure; no development/test artifact is
ever served outside development; invalid configuration fails at startup naming
the setting only; no value is echoed.

**Verification.** Config and composition-root matrix tests; `compileall`;
Pyright; `git diff --check`.

**Criticality.** MVP-critical (partly done).

### P1 — Market data depth: multi-timeframe candles and symbol catalog

**Goal.** Turn market data from one M1 candle into a real, bounded series API, so
chart-based analysis and channel clients can exist.

**Status.** **NEXT** (the first phase after P0). Requires explicit instruction.

**Delivers.** An explicit set of allowed timeframes (M1–MN1 subset); a bounded
`count` with a server-side hard cap; a candle-series contract with UTC alignment
and provenance; a symbol catalog endpoint describing what the authenticated
account can actually read; 422 for invalid timeframe/count, existing 404
semantics for unavailable symbols; the current single-candle response preserved
(or a deliberately versioned path).

**Existing modules.** `app/providers/market_data.py`,
`app/providers/mt5_market_data.py`, `app/providers/fake_market_data.py`,
`app/services/market/`, `app/api/market_data_router.py`, `app/api/numeric.py`,
`app/core/dependencies.py`.

**New components.** A symbol/contract-catalog provider contract only if the
catalog is genuinely a new external read; otherwise extend the existing
market-data contract.

**Depends on.** Nothing (P0 delivered).

**Acceptance criteria.** A request returns N candles for an allowed timeframe in
UTC; invalid timeframe/count is refused before any provider call; caps are
enforced server-side; tenant-scoped session usage and `run_mt5_call` offloading
are unchanged; no new trading capability.

**Verification.** Provider unit tests with a fake MT5, service tests, API tests
(auth, validation, 404, caps), UTC/window tests, `compileall`, Pyright, and one
minimal live read-only smoke.

**Criticality.** MVP-critical (gates P2).

### P2 — XAUUSD 4H analysis (technical analysis vertical slice)

**Goal.** The flagship read the product is bought for: an explainable,
deterministic 4H XAUUSD view for a customer, strictly read-only.

**Status.** **PLANNED** (depends on P1).

**Delivers.** A technical-analysis service computing deterministic indicators
over the P1 series (for example SMA/EMA, ATR, recent range and structure, simple
momentum) with an `as_of` timestamp and provenance; an HTTP endpoint exposing
that analysis; the same analysis rendered as a **bounded** block in the agent
prompt so the assistant can discuss the instrument; explicit
insufficient-data behaviour (fewer candles than required → clear generic failure,
never invented numbers).

**Existing modules.** P1 market data, `app/services/`, `app/providers/`,
`app/services/agent/prompt.py`, `app/core/dependencies.py`.

**New components.** One module under `app/services/` for pure, in-process
indicator functions. **No analytics vendor and no new external data dependency**;
indicators are computed from provider candles.

**Depends on.** P1.

**Acceptance criteria.** Identical inputs produce identical output; output
contains no forecast, no BUY/SELL language and no recommendation; the agent
prompt gains one bounded block while the existing size discipline and egress
policy still hold; account identity never enters the analysis or the prompt;
empty/short series fail closed.

**Verification.** Golden-value indicator tests, insufficient-data tests, API
contract tests, agent-prompt tests (block rendered, reductions still ordered),
and a trading-language scan over rendered output.

**Criticality.** MVP-critical (flagship capability).

### P3 — Production economic-calendar source (vendor decision, existing seam)

**Goal.** Serve real calendar data to customers through the unchanged calendar
architecture, without ever making the calendar optional.

**Status.** **DECISION** — blocked on the vendor choice (licensing,
redistribution rights, coverage, cost, quota), not on code. QuantGist remains a
development/test source and is never the production answer.

**Delivers.** A decision record for the chosen vendor; one provider implementing
the unchanged `EconomicCalendarProvider` registered in the existing PRODUCTION
branch; per-vendor settings (key, base URL, timeout); the deployment's provenance
marker; and the removal of the "production refuses" state for that deployment
only.

**Existing modules.** `app/providers/economic_calendar.py`,
`app/core/dependencies.py` (the single registration point),
`app/services/economic_intelligence/`, `app/core/config.py`, tests.

**New components.** Exactly one provider module plus its settings, shaped like
the QuantGist adapter (no retries, no caching unless a later phase explicitly
adds them).

**Depends on.** P0 (done). Blocked by the vendor decision.

**Acceptance criteria.** Production calendar data flows through the same
relevance classifier into both the API response and the agent prompt carrying its
provenance; no date-filter assumptions (the live-verified lesson); pagination or
the vendor's equivalent handled; the key never appears in a log, message or
response; development/test sources remain development-only.

**Verification.** Offline provider tests against recorded vendor payloads, local
window filtering, failure translation to the existing 503, one minimal live smoke
with a small window, and secret-hygiene checks over logs and responses.

**Criticality.** MVP-critical (the calendar is mandatory for the agent).

### P4 — Risk and portfolio intelligence depth

**Goal.** Make the read-only risk picture something a customer can act on, and
ground the agent in the same numbers.

**Status.** **PLANNED**.

**Delivers.** Exposure concentration per symbol; margin-level interpretation with
explicit documented thresholds; direction imbalance; drawdown over the
trade-history window; a documented deterministic risk band (extending the current
FLAT/LOW/ELEVATED/HIGH/UNKNOWN classification); and the same figures rendered
into the agent prompt.

**Existing modules.** `app/services/portfolio_intelligence/`,
`app/services/financial_context/`, `app/api/portfolio_intelligence_router.py`,
`app/services/agent/prompt.py`.

**New components.** None (extend the existing deterministic functions).

**Depends on.** P1/P2 only for symbol-level context; currency-level risk can land
independently.

**Acceptance criteria.** Every figure derives only from account/position/trade
data; unknown inputs yield an explicit UNKNOWN rather than a guess; no
recommendation language; the response contract is extended additively.

**Verification.** Deterministic unit tests on fixed snapshots, empty and
single-position edge cases, API contract tests, agent-prompt tests.

**Criticality.** MVP-critical.

### P5 — Agent answer quality and safety (explicitly no tool calling)

**Goal.** Make the assistant's answers trustworthy and bounded now that it
carries financial, calendar and market context.

**Status.** **PLANNED**.

**Delivers.** A documented, deterministic layout and budget per context block; a
deterministic **prompt-safety screening** step that refuses trading-instruction
requests before any provider call (rule-based, not LLM-based); per-broker request
governance alongside the existing per-user quota, with a documented quota
contract; failure copy that never invents data; and a pinned, recorded decision
that the MVP has **no tool/function calling, no agent framework, no multi-turn
loop**.

**Existing modules.** `app/services/agent/` (`agent_service.py`, `prompt.py`,
`scope.py`, `usage.py`, `egress.py`), `app/api/agent_router.py`.

**New components.** Screening logic inside `app/services/agent/` — no new
framework, no new dependency.

**Depends on.** P2/P3/P4 for the contexts it renders.

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
from trade history, positions summary, calendar context for the period, risk
band) with a deterministic report contract; and an optional LLM narrative that
reuses the existing router and egress policy unchanged.

**Existing modules.** Financial context, portfolio intelligence, trade history,
economic intelligence, `app/services/agent/` (LLM boundary), `app/api/`.

**New components.** `app/services/reports/` composing existing services. No
storage, no scheduler, no queue.

**Depends on.** P2 (analysis), P3 (real calendar), P4 (risk).

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
readiness endpoint reporting database / MT5 / LLM / calendar availability
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
data (P1), portfolio intelligence. New component: a small watchlist model +
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
action; the economic-calendar context must remain mandatory on every request; and
the existing guard chain (scope → quota → contexts → provider) must still run.
**DEFERRED.**

## 5. MVP scope

**In the MVP (P0–P8):** tenant-safe authentication and user management; read-only
account, positions, trade history and market data with multi-timeframe series;
portfolio/risk intelligence; economic intelligence with an explicit,
fail-closed source configuration and a real production calendar source; the
read-only agent whose every request carries the financial, calendar (and, after
P2, market) context; on-demand reports; broker operations with auditability;
production readiness (observability, limits, CI, deployment/retention
documentation) and an API a channel client can consume.

**Explicitly outside the MVP:** tool/function calling and any agent framework;
multi-turn conversation or conversation memory; persisted chat history;
streaming/websockets; any Web/Telegram/WhatsApp/Mobile client; watchlists;
billing/subscription management; localization/i18n; multi-broker MT5 worker
pools; distributed limiters; schedulers/background jobs; multiple accounts per
user or portfolio aggregation across users; news/sentiment feeds; self-service
registration; and any form of trade execution, modification or closing.

## 6. Never (permanent constraints)

- Never execute, modify, close or otherwise control a trade; never add a trading
  tool, not even behind an indirect AI feature.
- Never request, store or use the MT5 master/trading password — only the
  read-only investor password is ever accepted, and never in a prompt or a log.
- Never expose credentials, secrets, tokens or API keys in prompts, logs, chat
  output or API responses.
- Never allow cross-tenant access to account, position, trade or credential data.
- Never present development/test data (Deterministic fake, QuantGist) as live
  market data; provenance must always travel with the data.
- Never make the Economic Intelligence / calendar context optional for the agent.
- Never select QuantGist (or any development/test source) as a production
  provider.
- Never invent a production calendar vendor: the production slot stays closed
  until a real one is chosen and registered.
- Never replace the modular monolith with microservices, and never commit `.env`
  or any secret.

## 7. Risks and dependencies

| # | Risk | Impact | Mitigation / owning phase |
|---|---|---|---|
| R1 | MT5 Python API authenticates one account per process, so all MT5 reads serialize | Throughput ceiling per broker | Current lock discipline keeps it safe (not a correctness risk); scale-out is P8 (topology) and P12 (worker per broker) |
| R2 | `MetaTrader5` is Windows-only; no CI/Docker today | Deployment and CI friction; drift between dev and prod | P8 explicit packaging/CI decision (Windows runner for MT5 paths) |
| R3 | No production calendar vendor: licensing/redistribution rights are unresolved | Blocks real calendar data for customers | P3 decision; today the production slot fails closed, so no development data can leak |
| R4 | QuantGist free tier is delayed and quota-limited | Unsuitable for customers; quota exhaustion | Development/test only (enforced); production slot closed until P3 |
| R5 | LLM availability/cost; the shared free pool is empty by default | The agent answers 503 rather than degrading | Fail-closed today; P5 quota governance; P8 readiness reporting |
| R6 | In-process quota/throttle only | Multi-worker deployments under-enforce limits | P8 (documented/refused multi-worker), P13 (distributed state) |
| R7 | Offline test suite cannot see live vendor drift (the QuantGist shape mismatch was found only live) | Silent provider breakage | Standing practice: one minimal live smoke per provider change, plus recorded payload fixtures |
| R8 | Prompt injection through user text or third-party calendar titles | Model misbehaviour | Untrusted-block framing and escaping already in place; deterministic screening in P5 |
| R9 | Technical analysis could be read as investment advice | Product/regulatory risk | P2 requires deterministic, explainable, explicitly non-advisory output; no forecasts anywhere |
| R10 | No audit trail / retention policy today | Broker (and regulatory) requirements unmet | P7 (audit), P8 (retention statement) |
| R11 | Two position reads per agent request (known issue 14) | Wasted serialized MT5 work | Optional cleanup once the read paths are revisited (P5) |
| R12 | Read-only assurance must survive every future phase | Core product promise | Architecture tests (no trading capability anywhere), review per phase, invariants above |

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
