# AI Broker Assistant — Project Knowledge

> **Status note (authority).** This document began as a project-knowledge
> snapshot, and its frozen sections (role names, the section 6 tenant-isolation
> paragraph, the section 8 composition root, the section 16 MT5 lifecycle
> description, the completed-work list, the future-stage list and the current
> immediate objective) stop at Steps 18–20 and are kept only as historical
> records. CURRENT_CHECKPOINT.md is authoritative for the current state, and the
> sections maintained to match it — authentication, MT5 tenant isolation, the
> repository layout, the API list and this note — describe the system as it is
> now; where any of them ever disagree with CURRENT_CHECKPOINT.md,
> CURRENT_CHECKPOINT.md wins.
>
> Facts that have moved since the snapshot was written:
> - the product is ONE BROKER ONLY (2026-09-17 decision; the one-broker
>   refactor): the deployment serves exactly one broker — the single `brokers`
>   row — multi-broker support is no longer a product requirement, and the
>   boundary that must hold is customer-to-customer. The current MT5
>   integration remains the current implementation; replacing MT5 with official
>   Broker APIs (account/customer data + market data) is a recorded future
>   possibility, not a task
> - roles are super_admin / admin / customer (Step 21A), with exactly ONE
>   super_admin in the deployment enforced by a database partial unique index
> - the user identity is the single `login` column: it is both the application
>   login and the MT5 account/login number (Step 42), and it is unique
>   deployment-wide since the one-broker refactor. The former `username` and
>   `mt5_login` columns no longer exist
> - MT5 authentication is customer-scoped (Step 36, evolved) with an
>   administrator-provisioned encrypted MT5 INVESTOR (read-only) password
>   (Step 38). The account number is the user's own `login`, and the MT5 server
>   is the BROKER's own configuration — the former per-user `mt5_server` column
>   was dropped by the one-broker refactor
> - login does NOT select a tenant (the one-broker refactor superseded Step
>   43): the request carries only `login` + `password`; the broker is resolved
>   server-side and no request field can select or influence a customer
>
> The MT5 trading (master) password is never requested, stored or used, and no
> API returns a credential.

## 1. Project Overview

AI Broker Assistant is a production-oriented, read-only AI assistant for brokers.

A broker can offer the assistant to customers through:

- Web
- Telegram
- WhatsApp
- Mobile App

The assistant connects to trading and market-data sources and provides account information, market analysis, risk analysis, reports, and AI-powered assistance.

### The assistant CAN

- Account Balance
- Equity
- Margin / Free Margin
- Open Positions
- Trading History
- P&L
- Risk / Exposure Analysis
- Market Data
- Technical Analysis
- Fundamental Analysis
- News Analysis
- Economic Calendar
- Daily Reports
- Chart Analysis
- XAUUSD 4H Analysis

### The assistant MUST NOT

- BUY
- SELL
- MODIFY orders
- CLOSE positions
- Execute trades
- Perform autonomous trading actions

This is intentionally a **read-only financial assistant**.

---

# 2. Architecture

The project uses a:

**FastAPI Modular Monolith**

Current architecture:

```text
app/
├── core/
├── db/
├── api/
├── services/
├── providers/
└── ...
    
tests/
alembic/
scripts/

Current known modules:

app/core/
app/db/
app/api/
app/services/
app/providers/
tests/
scripts/

The following directories are NOT currently present and must not be assumed to exist:

app/schemas/
app/utils/

They may be introduced later if the architecture genuinely requires them.

Layer responsibilities
core

Application-wide infrastructure:

configuration
security
exceptions
logging
dependency/composition wiring
consolidated MT5 blocking boundary (app/core/blocking.py)
db

Database infrastructure:

SQLAlchemy
database connection
models
database base configuration
api

HTTP/API layer:

routes
HTTP dependencies
HTTP-specific behavior
HTTP error mapping
HTTP response schemas when appropriate
offloading of blocking MT5-backed service calls via run_mt5_call
services

Application/business logic.

Services should not directly depend on external systems such as MT5.

providers

Adapters for external data sources.

Examples:

MT5 provider
Fake/test provider
future market-data providers
tests

Automated tests.

3. Architecture Principle

Preferred dependency direction:

API
 ↓
Service
 ↓
Provider Interface
 ↓
Provider Implementation
 ↓
External System

For market data:

API
 ↓
MarketDataService
 ↓
MarketDataProvider
 ↓
MT5MarketDataProvider
 ↓
MetaTrader 5

The same pattern is established for account information and open positions:

API
 ↓
AccountInfoService / PositionService
 ↓
AccountInfoProvider / PositionProvider
 ↓
MT5AccountInfoProvider / MT5PositionProvider
 ↓
MetaTrader 5

The API must not contain MT5/business logic.

The service must not directly depend on MT5.

The provider layer isolates external systems from external infrastructure.

4. Database Status

The current repository contains:

PostgreSQL configuration
Async SQLAlchemy infrastructure
Alembic
Broker model
User model
Current models verified in the repository
Broker
User

Do NOT assume the following models currently exist:

TradingAccount
Position
Trade

They are future product concepts unless they are actually present in the repository.

Do not modify database models unless the current task explicitly requires it.

5. Authentication Status

JWT authentication IS implemented and verified:

bcrypt password hashing/verification (app/core/security.py)
PyJWT access-token creation/decoding with expiration validation
POST /auth/login issues the token (one-broker since the one-broker refactor:
login + application password)
get_current_user() in app/core/dependencies.py resolves a Bearer JWT to the
database-backed active User
get_current_broker_manager() (super_admin or admin) and
get_current_super_admin() add database-backed role authorization
Every endpoint except POST /auth/login and GET /health requires authentication:
the MT5 read surface (market-data, account-info, positions, trade-history), the
AI surface (POST /agent, economic-intelligence/today, portfolio-intelligence),
the broker LLM configuration endpoints, and all user-management endpoints

Login is one-broker (the one-broker refactor superseded the Step 43 tenant
selector). Because `login` is the MT5 account/login number and the deployment
serves exactly ONE broker, the request carries ONLY the customer's own
credentials — there is no `broker` field to send, and nothing a request
carries can select or influence a customer:

POST /auth/login  { "login": <login>, "password": <app password> }

The deployment's one broker is resolved server-side
(app/core/dependencies.load_deployment_broker: exactly one brokers row — none
or several fails closed with the same generic 401) and is used only to scope
the credential lookup (User.broker_id + User.login; unique deployment-wide, so
at most one match); it is never an authorization fact, and the identity a
request is authorized for still comes from the authenticated database User row.
A stale `broker` field sent by an old client is ignored, not honored.

Every rejection — an unusable deployment broker (none or several rows),
unknown login, wrong password, inactive user, inactive broker — returns the
identical generic 401, and the paths that have no stored hash to check spend an
equivalent dummy bcrypt verification, so neither the response nor its timing
reveals which logins exist. The login brute-force throttle
counts failures per client IP and per submitted login (one broker, so the
login alone is the customer key), so one customer's login number can no longer
be used to lock another customer's identical number out.

Authentication failures return HTTP 401 with WWW-Authenticate: Bearer; the
authoritative broker_id and role always come from the database User record,
never from JWT claims.

Future stages may extend authentication (refresh tokens, channel identity,
etc.), but the core login/JWT/dependency path must be treated as existing.

6. Multi-Tenant Architecture

**Superseded by the ONE-BROKER product decision (2026-09-17).** This deployment
is one broker's assistant; multi-broker support is no longer a product
requirement, and this section's multi-tenant framing is historical. What
remains true — and is the boundary that must hold — is CUSTOMER-to-customer
isolation:

The ONE Broker is:

the single `brokers` row this deployment resolves server-side
(load_deployment_broker; none or several rows ⇒ fail closed).

A customer's data must never leak to another customer.

Customer isolation is implemented at the database level (deployment-wide
unique constraints on login/email/phone, broker_id on User as an integrity
reference, exactly one super_admin) and in user management (a manager creates
users in the deployment's one broker; role rules unchanged).

Authentication is one-broker too: POST /auth/login carries only `login` +
`password`; the broker is resolved server-side and the credential lookup is
scoped to it, so no request field can select a customer.

MT5 customer isolation IS implemented (Step 36, evolved). Reads are
authenticated per customer through the session manager (app/core/mt5_session.py),
which resolves the requesting user's credentials and holds a lock across the
whole acquire → read span, so market-data, account-info, positions and
trade-history can never read another customer's account — pinned end to end by
tests/test_customer_mt5_isolation.py. The MetaTrader5 Python
API authenticates one account per process, so MT5 throughput remains a
process-wide bottleneck; worker processes with account affinity are still
future work (see CURRENT_CHECKPOINT.md Known Issues item 1).

The session lock is an in-process threading.RLock, so it protects ONE process
only: the current topology assumes exactly one application process owns the MT5
terminal. That deployment invariant IS now enforced: the application takes an
exclusive OS-level lock per terminal at application startup
(app/core/mt5_ownership.py), keyed on MT5_TERMINAL_PATH and released by the OS
when the process exits (so a crash leaves no stale lock behind), and it refuses
to start — fail closed, before any MT5 work — when another process already owns
that terminal. A stray second instance or a multi-worker deployment therefore
cannot silently share one terminal (Known Issues item 4, resolved). The session
identity is verified before
every read: the terminal must itself report the requested (server, login), a
mismatch re-authenticates under the lock, and an identity that cannot be
confirmed fails closed (503) instead of serving another account's data. The
login is compared exactly and the server case-insensitively; unreadable or
malformed account data never counts as a match. A failure that is about the
REQUEST rather than the session is not a session failure: the provider reads the
terminal's own answer (a symbol this terminal does not offer, or no bars for the
requested window, is MT5ClientError, still a ValueError, so the existing 404 is
unchanged; an IPC/transport failure stays a RuntimeError), and the session
boundary keeps the verified identity only for the client-level result — every
other exception still drops it (Known Issues items 32 and 33, resolved; each
provider keeps its own client/availability threshold rather than sharing one).
The vendor codes were verified against a LIVE terminal, read-only: an unattached
read reports (-10004, 'No IPC connection'), a candle-read miss reports (-1,
'Terminal: Call failed'), and a symbol_info miss reports (-4, 'Terminal: Not
found'); the connection stayed usable after each miss, which is the whole
point — a miss is an answer, not damage. Authentication is bounded by an
explicit IPC timeout, and the terminal executable can be pinned explicitly
(MT5_TERMINAL_PATH / MT5_TIMEOUT_SECONDS). Account count alone does not set the
scaling requirement; peak concurrent MT5 reads and read duration do.

Tenant isolation must stay explicitly designed and consistent across:

authentication
users
trading accounts
market data
trading data
services
providers
7. Current Market Data Provider Architecture

Current flow:

GET /market-data/{symbol}
        ↓
MarketDataService
        ↓
MarketDataProvider
        ↓
MT5MarketDataProvider
        ↓
MetaTrader 5

The important design decision is:

MarketDataService depends on the provider abstraction, not directly on MT5.

This allows the application to work with:

MT5
Fake Provider
future providers

without changing application/business logic.

8. Current Composition Root

The current composition/wiring is located in:

app/core/dependencies.py

The API router should not directly construct the concrete MT5 provider.

Current responsibility separation:

app/core/dependencies.py
    → dependency/composition wiring (per-request providers built around the
      authenticated tenant's credentials) + authentication dependencies

app/core/blocking.py
    → consolidated blocking boundary: run_mt5_call(...) executes synchronous
      MT5-backed service calls on the worker threadpool, never on the event loop
    → second, separate boundary: run_llm_call(...) executes the blocking outbound
      model call on the event loop's default executor (a different pool), so a
      request waiting on a model does not hold a worker thread an MT5 read needs

app/services/market/market_data_service.py
    → application/service logic (market data)

app/services/account/account_info_service.py
    → application/service logic (account information)

app/services/positions/position_service.py
    → application/service logic (open positions)

app/services/trade_history/trade_history_service.py
    → application/service logic (executed trade history)

app/providers/market_data.py
    → provider abstraction + Candle contract

app/providers/mt5_market_data.py
    → MT5 implementation (market data)

app/providers/account_info.py
    → provider abstraction + AccountInfo contract

app/providers/mt5_account_info.py
    → MT5 implementation (account information)

app/providers/position.py
    → provider abstraction + Position contract

app/providers/mt5_positions.py
    → MT5 implementation (open positions)

app/providers/trade_history.py
    → provider abstraction + TradeHistoryEntry contract

app/providers/mt5_trade_history.py
    → MT5 implementation (executed trade history)

app/providers/fake_market_data.py
    → deterministic fake (market data, tests)

app/providers/fake_position.py
    → deterministic fake (positions, tests)

app/providers/fake_trade_history.py
    → deterministic fake (trade history, tests)

app/api/market_data_router.py
    → HTTP route + HTTP error mapping + CandleResponse

app/api/account_info_router.py
    → HTTP route + HTTP error mapping + AccountInfoResponse

app/api/positions_router.py
    → HTTP route + HTTP error mapping + PositionsResponse

app/api/trade_history_router.py
    → HTTP route + from/to window validation + TradeHistoryResponse

Keep this separation unless an explicit architecture task changes it.

9. MarketDataProvider Contract

The provider contract is currently synchronous.

Conceptually:

MarketDataProvider
    └── get_market_data(symbol) -> Candle

The domain-level Candle contract should remain independent from:

FastAPI
HTTP
PostgreSQL
MT5

The HTTP response schema may use Pydantic separately.

Do not change the provider contract to async unless explicitly requested.

10. MT5 Provider

Current real providers:

app/providers/mt5_market_data.py
app/providers/mt5_account_info.py
app/providers/mt5_positions.py
app/providers/mt5_trade_history.py

They use the Python MetaTrader 5 package.

Installed version:

MetaTrader5==5.0.6180 (pinned in requirements.txt)

Important:

MT5 Python calls are blocking.

The blocking boundary is now consolidated in app/core/blocking.py
(run_mt5_call): every MT5-touching endpoint routes its synchronous service
call through it so the blocking operation never runs on the FastAPI event
loop. Providers and services stay deliberately synchronous.

Blocking work that is NOT MT5 uses a second boundary in the same module:
run_llm_call runs the outbound model call on the event loop's default executor,
which is a separate pool from the worker threadpool run_mt5_call borrows from.
The agent boundary is two phases for exactly this reason: AgentService.prepare()
(every blocking read: MT5, calendar, news) goes through run_mt5_call, and
AgentService.respond() (prompt rendering plus the one model call, which touches
no MT5) goes through run_llm_call. POST /agent uses the two phases separately, so
the MT5 worker is released before the model round trip; AgentService.handle()
remains as their synchronous composition for callers that own their threading.

MT5 reads are also reused WITHIN one request and never across requests. The
MT5 API serializes every read on the single process-wide session (and each
acquire verifies the identity), so a service that has already collected data
this request may hand it down instead of reading again: POST /agent passes the
financial context's position snapshot to the economic layer (one positions read
per request, not two), and the research path reuses the focus-symbol resolution
it already performed instead of resolving the catalog again. A multi-name
resolution goes through InstrumentService.resolve_many, which applies the SAME
exact/case-insensitive/suffix rules as resolve() against ONE catalog discovery
per operation (three decorated instruments cost one symbols_get, not three);
resolve() is that operation for a single name, so the two cannot disagree. The
snapshot is a parameter/local for that request only — there is no cache, no
cross-request or cross-tenant reuse, and a read whose data is genuinely
different (the market-data symbol resolution before the candle read, and the
per-name exact symbol lookup) is kept.

Do not modify MT5 lifecycle or blocking behavior during unrelated tasks.

11. Current API

CURRENT_CHECKPOINT.md is authoritative for exact contracts and is the
maintained record; the list below is kept current for orientation. Every
endpoint except the two unauthenticated ones requires a Bearer JWT, and a
role-gated endpoint reads the role from the database User row, never from the
token.

Unauthenticated:

POST /auth/login  { "broker": <broker code>, "login": <login>, "password": <app password> }
GET /health (infrastructure liveness probe; does not reflect MT5 readiness)

Any authenticated user:

GET /market-data/{symbol}
GET /account-info
GET /positions
GET /trade-history?from=<UTC ISO>&to=<UTC ISO>
POST /agent
GET /economic-intelligence/today
GET /portfolio-intelligence

Broker manager (admin or super_admin):

POST /users (optional explicit role; "super_admin" is refused for every caller)
GET /users (role-scoped visibility: super_admin sees admins and customers, admin sees customers)
PUT /users/{user_id}/mt5-credentials
GET /users/{user_id}/mt5-credentials (safe metadata only)

super_admin only:

POST /users/admins
GET /users/{user_id}
PATCH /users/{user_id}
DELETE /users/{user_id}
GET /broker/llm-config
PUT /broker/llm-config
POST /broker/llm-config/test

Current HTTP layer is responsible for:

receiving the request
offloading the blocking MT5-backed service call via run_mt5_call
(and the blocking outbound model call via run_llm_call, its own boundary)
calling the service
converting service/provider failures into HTTP responses
returning the HTTP/Pydantic response schema

Error mapping:

400 non-UTC-aware window boundary or from >= to (trade-history)
401 unauthenticated/invalid/expired token; every login rejection
403 authenticated but insufficient role (manager- and super_admin-only routes)
404 market data unavailable for the requested symbol; unknown or ambiguous
    user id; no LLM configuration set for the broker
409 duplicate user (login/email/phone, unique deployment-wide); the deployment's only
    super_admin protected from demotion or deletion; LLM configuration modified
    concurrently or disabled
422 schema/validation failure (missing or malformed input, extra fields
    refused, out-of-range windows); agent request outside financial scope; LLM
    endpoint not permitted; super_admin role requested
429 agent daily quota exhausted; login throttle tripped
503 MT5, provider, encryption-key or LLM-credential unavailability

GET /positions returns {"positions": [...]} and GET /trade-history returns
{"trades": [...]} for a required UTC-aware window; an empty result is a
normal 200 with an empty array, never a 404.

Do not change the API contract unless the current task explicitly requires it.

12. Testing Status

Unit testing infrastructure now exists.

Current testing framework:

pytest==9.1.1

Current test directory:

tests/

The suite currently has 827 passing tests (verified 2026-09-16 with pytest -q;
the 2 remaining warnings are pre-existing third-party deprecation warnings: the
anyio BlockingPortal alias and the starlette testclient httpx notice).

A root-level:

conftest.py

exists to make project imports work during pytest execution.

Current Fake Providers:

app/providers/fake_market_data.py
app/providers/fake_position.py
app/providers/fake_trade_history.py
app/providers/fake_economic_calendar.py
app/providers/fake_llm.py

Note: the Fake Providers are used to test service/API behavior without MT5;
authentication endpoints are tested against a SQLite/AIOSQLite-backed
session. Tests never require a real MetaTrader terminal.

Tests do not require:

MT5
MetaTrader terminal
PostgreSQL
network access
credentials
environment-specific configuration
13. Current Project Status

Completed — this ledger is frozen at item 14; CURRENT_CHECKPOINT.md carries the
authoritative and complete stage history:

1. Provider contract
2. MT5 provider
3. MarketDataService
4. Market-data API
5. Provider dependency/composition extraction
6. Fake Market Data Provider
7. MarketDataService unit tests
8. MT5 lifecycle (lazy process-wide singleton, thread-safe init, failed init
   not cached, FastAPI lifespan startup/shutdown, graceful shutdown)
9. JWT authentication (hashing, tokens, login endpoint, get_current_user,
   broker-admin authorization)
10. User roles (broker_admin/customer at the time; the role model became
    super_admin / admin / customer in Step 21A) and POST /users customer creation
11. Read-only MT5 account information (contract, provider, service, API)
12. Read-only MT5 open positions (contract, provider, service, API)
13. Consolidated MT5 blocking boundary (app/core/blocking.py, run_mt5_call)
    used by market-data, account-info, and positions
14. Read-only MT5 trade history (contract, provider, service, API)

Completed since the ledger was frozen (summary only; see CURRENT_CHECKPOINT.md
"Completed Stages" for the full record):

15. Tenant-scoped user management: role-scoped GET /users listing, super_admin
    user CRUD, optional-role POST /users and admin creation
16. Economic intelligence, portfolio intelligence and financial context
    (Steps 23–25, READ-ONLY)
17. Read-only AI agent: agent boundary, LLM provider abstraction, real adapter,
    DI wiring, scope guard and per-user daily limit (Steps 26–31)
18. Broker LLM configuration with encrypted credentials, the LLM router and the
    shared free-pool boundary (Steps 32–33)
19. Security and agent hardening: SSRF policy, required exp/sub, broker
    suspension enforcement, login throttle, prompt/egress limits (Step 35)
20. MT5 tenant-scoped sessions (Step 36); Decimal money end to end (Step 37);
    per-user provisioned MT5 investor credentials (Steps 38–39)
21. One user identity: the single `login` column (Step 42)
22. One-broker login: the deployment's single broker resolved server-side,
    deployment-wide unique login, throttle keyed on (client IP, submitted login),
    uniform failures and timing equalisation (the one-broker refactor superseded
    Step 43's tenant selector)
23. QuantGist free tier as the development/test economic-calendar source,
    selected only when QUANTGIST_API_KEY is configured (Step 44); there is still
    no production calendar source (CURRENT_CHECKPOINT.md known issue 9)
24. Economic intelligence composed into every agent prompt: one reference
    instant per request, a dedicated calendar block carrying impact, relevance
    and provenance, mandatory on every request, with the existing generic 503 as
    its failure path (Step 45)
25. Explicit economic-calendar source selection: ECONOMIC_CALENDAR_SOURCE
    (auto | development_fake | quantgist | production, default auto) resolved at
    the composition root, development/test sources served inside development
    only, a production slot that refuses until a real vendor is registered, and
    the same generic 503 for every unusable selection instead of degrading to
    another source (Step 46)
28. Instrument-aware fundamental relevance (Step 48): one shared domain
    vocabulary (app/services/instrument_intelligence/domains.py) used by BOTH the
    calendar and news relevance layers, and static instrument fundamental
    profiles (profiles.py) stating which factors reach an instrument and how —
    DIRECT (the instrument's own underlying asset or market), MACRO (a documented
    broad factor) or INDIRECT (a documented transmission) — for XAUUSD, USOIL/WTI
    and NASDAQ-100; a DIRECT match is the only route to the strongest level
    RELEVANT, macro/indirect matches are POTENTIALLY_RELEVANT, and an instrument
    with no profile keeps the previous symbol-string behaviour exactly. Focus
    detection gained explicit instrument names (USOIL/WTI/NAS100/NASDAQ/US100/
    USTEC) while commodity words stay refused, position exposures now name the
    factors their drivers matched, and the agent prompt states the relationship
    and factor for a profile-classified item. Still fully deterministic (no score,
    no sentiment model, no learned matching, no LLM, no new provider, no schema
    change), and the calendar layer's currency-scoped LEVEL contract is unchanged
    for symbols that have a currency leg (CURRENT_CHECKPOINT.md known issues 17-19)
33. Market-data symbol resolution (Step 53): the SAME InstrumentService.resolve()
    semantics (exact, then unique case-insensitive, then unique broker suffix)
    now decide which instrument GET /market-data/{symbol} reads, so ``xauusd``
    reads the broker's ``XAUUSD``, ``XAuUsD`` does the same, and a broker whose
    catalog only lists ``XAUUSD.r`` is read for ``XAUUSD.r`` instead of taking a
    404. MarketDataService takes an OPTIONAL InstrumentService (None = the exact
    pre-Step-53 behaviour), resolves FIRST and hands the broker's own spelling to
    the unchanged candle provider; no resolution logic is duplicated, an
    unknown/ambiguous symbol stays the existing client error, a catalog/MT5
    failure stays the existing availability error, and both are decided before
    any candle is read. Ambiguity fails closed exactly as everywhere else (no
    guess, no deterministic pick), and the composition root composes the same
    instrument service GET /instruments uses (one credential path, explicit
    provider selection). Response schema, candle contract, research/agent paths,
    providers, config, schema and trading behaviour are untouched; no cache, no
    second catalog read, no live API request. Cost: one extra terminal read per
    market-data request, which matters because MT5 serializes reads on the single
    process-wide session (CURRENT_CHECKPOINT.md known issue 24)
33. Profile-documented Brent spellings (Step 54 relevance fix): the crude-oil
    profile (profiles.py) now documents UKOIL and BRENT (and their broker-
    decorated forms such as UKOIL., which the prefix rule already handled) as
    spellings of the same underlying commodity, so its documented factor tiers
    apply to a Brent position exactly as to a WTI one. A coverage audit of
    real broker catalogs later extended the same table (data, not engine) with
    the verified aliases USCRUDE, XBRUSD, BRENTUSD, UKBRAND (oil) and
    NASDAQ100, USTECH, NDXUSD (NASDAQ-100); futures/ETF tickers (CL, NQ, QQQ)
    are deliberately NOT roots, and gold quoted in EUR/GBP (XAUEUR/XAUGBP)
    needs a currency-layer decision before any root is added. Concretely: UKOIL. + a
    USD FOMC event is POTENTIALLY_RELEVANT through MONETARY_POLICY instead of
    NOT_OBVIOUSLY_RELEVANT, and Persian-Gulf/crude events are RELEVANT through
    the DIRECT crude-oil tier — previously unreachable because Brent has no
    currency leg and the profile did not cover the spelling. This is a DATA edit
    (two symbol roots), not a new profile system, and no existing behaviour
    changes: currency-leg rules stay authoritative for symbols with a leg
    (USDJPY, EURUSD, XAUUSD all keep their verdicts and wording), unprofiled
    instruments without a leg (US30, COCOA.) stay fail-closed, and a profile
    never makes every USD event relevant (US Grain Stocks Report stays
    NOT_OBVIOUSLY_RELEVANT for every profiled instrument)
32. Safe broker-suffix resolution (Step 52, extended in Step 54): InstrumentService.resolve's third and final step — after exact spelling and
    a unique case-insensitive match, both unchanged — accepts a UNIQUE
    broker-DECORATED spelling of the requested base symbol. Three decoration
    forms, with NO list of known suffixes: separator + empty tail (XAUUSD.,
    UKOIL., US100.), separator + short (1-8) alphanumeric token (XAUUSD.r,
    XAUUSD.p, XAUUSD.cash, XAUUSD_m, XAUUSD-m, XAUUSD#1), or a short ALPHABETIC
    LOWERCASE tag glued to the base (XAUUSDm, XAUUSDpro). The form rule is not
    fuzzy matching: the candidate must begin with the requested name
    (case-insensitive), so partial names (US, GOL, GO) never match, and an
    uppercase undelimited tail (XAUUSDX), a digit-only tail (XAUUSD1), longer
    symbols and descriptive/compound tails (XAUUSD.verylongsuffix, XAUUSD.r.x)
    never match either. Exactly one candidate resolves in the broker's own
    spelling; zero or several stay unknown/ambiguous and are never guessed.
    This is what makes a broker whose whole catalog is decorated usable:
    research, the agent's research block and market data all resolve the base
    symbol through the same rule (focus: XAUUSD.r in the prompt) instead of
    silently dropping the block. Same single InstrumentService boundary, same
    FocusResolution contract, no provider/agent/prompt/schema change, no extra
    catalog read, and GET /instruments/{symbol} gains the same resolution
    (CURRENT_CHECKPOINT.md known issue 22)
31. Instrument resolution inside financial research (Step 51): a requested
    instrument is resolved through the Step 50 broker catalog BEFORE anything is
    researched, and only the broker's own canonical spelling is graded. The
    research service takes an OPTIONAL InstrumentService (None = exact Step 49
    behaviour, so nothing existing changes), exposes resolve_focus_symbols()
    returning FocusResolution(requested, resolved, unresolved), and reports
    unresolved_symbols on the context: a name the catalog cannot identify
    (unknown, ambiguous or unusable) is never graded as if it were a real
    instrument, while an MT5/catalog outage still propagates as RuntimeError and
    the existing generic 503. GET /financial-research/today resolves first (off
    the event loop) and fails closed with 404 "Instrument unavailable for the
    requested symbol" — the instruments/market-data contract — when any named
    instrument is not offered, echoing the broker's spelling in focus_symbols;
    the agent resolves the detected focus through the same single mechanism and
    builds no research at all (no look-back fetch) when nothing resolves, so an
    unconfirmed label never reaches the prompt and the mandatory calendar/
    fundamental context still answers. No catalog logic is duplicated, no
    provider changed, no schema/cache/scheduler, and the Step 48 profiles stay
    optional enhancements applied after resolution — any broker symbol (AAPL,
    LVMH, BTCUSD, NICKEL, COFFEE, XAUUSD.r, USOIL, NAS100) is researchable with
    no profile at all (CURRENT_CHECKPOINT.md known issues 21-23)
30. MT5 instrument discovery and resolution (Step 50): a vendor-neutral
    Instrument/InstrumentProvider contract under app/providers (identity and
    metadata only — the broker's own spelling, description, broker-group asset
    class, base/quote currency, digits and a normalised TradeMode availability —
    with every optional field nullable because brokers genuinely omit them),
    implemented read-only over the EXISTING tenant-scoped MT5SessionManager with
    symbol_info/symbols_get only (symbol_select is never called: it mutates
    terminal state), plus InstrumentService in app/services/instruments holding
    the deterministic presentation rules (trim-only normalisation that never
    re-cases a broker symbol, exact-then-unique-case-insensitive resolution that
    always returns the broker's spelling, literal substring search over symbol
    and description, alphabetical ordering, a 200-instrument bound reported as
    total/truncated), the explicit get_instrument_service composition seam, and
    the JWT-protected GET /instruments (bounded catalog) and
    GET /instruments/{symbol} (one resolved instrument; 404 unknown, 503 MT5
    unavailable). Any broker symbol resolves generically — AAPL, LVMH, BTCUSD,
    NICKEL, COFFEE, XAUUSD.r, USOIL, NAS100 — and no symbol is special-cased in
    production logic; the XAUUSD/USOIL/NASDAQ fundamental profiles stay optional
    intelligence enhancements rather than a prerequisite. No database table,
    cache, scheduler, ingestion, provider redesign, LLM/tool change or schema
    migration; the three suites are fully offline (CURRENT_CHECKPOINT.md known
    issue 20 records the no-persistent-catalog gap)
29. Financial research in the Agent pipeline (Step 49 follow-up): the graded
    research context composed into AgentService as an OPTIONAL constructor
    parameter, built ONLY when the request names a focus instrument (bounded to
    3) and AGENT_RESEARCH_LOOKBACK_DAYS > 0 (default 2 UTC days; 0 disables),
    covering the half-open look-back span immediately BEFORE the calendar
    window so research and fundamental news never overlap and no item is
    fetched twice. The prompt's research block renders the SAME graded items
    through ONE shared news-line renderer (one relevance mechanism, Step 48
    profiles), labels them published source facts rather than analysis, states
    the look-back window explicitly, keeps provenance, carries no
    account/position/tenant data, and is dropped by a new reduction-ladder rung
    BEFORE the mandatory calendar. Research failure propagates through the
    existing error boundary (LLM never asked); without a research service (or
    without a focus instrument) the prompt is byte-identical to before; no
    provider, API endpoint, schema or trading path changed (no live API
    request; the research service itself holds no calendar by design)
28. Graded financial research context (Step 49): a reusable slice of the
    fundamental-intelligence layer — FinancialResearchService composing graded
    published-source news for an EXPLICIT half-open UTC window and explicit
    focus instruments, with no account, position or tenant data (no MT5
    provider, no positions read, no tenant identity accepted; the JWT-protected
    GET /financial-research/today endpoint echoes only the caller's own
    broker_id). Classification is the SAME news_intelligence grading the
    fundamental context uses (one relevance mechanism, Step 48 profiles), an
    absent news source is reported as explicitly unavailable (never as "no
    news"), a failing source fails closed with the generic 503, output stays
    bounded and deterministically ordered, provenance travels on every item,
    and the NEWS_SOURCE matrix, Alpha Vantage, the fake, the agent pipeline and
    the schema are untouched (no live API request was made; no production news
    vendor — CURRENT_CHECKPOINT.md known issues 15 and 16)
27. Alpha Vantage as the real development news source (Step 47A): a NewsProvider
    implementation behind the Step 47 contract (one bounded NEWS_SENTIMENT query
    per call, the half-open UTC window sent to the vendor and re-applied locally,
    aware UTC publication times, the vendor's own excerpt bounded below the
    service's limit, topic labels as categories, sentiment scores and ticker tags
    dropped), a new explicit NEWS_SOURCE value (alphavantage) served inside
    development only, a configured-key-only selection in auto, a missing key or a
    non-development environment refusing with the existing generic 503 instead of
    falling back to the fake, a redaction filter that keeps the key out of the
    HTTP client's own log lines, and ONE verified live smoke request; there is
    still no production news vendor, and the unfiltered development feed is not
    instrument-focused (CURRENT_CHECKPOINT.md known issues 15 and 16)
26. News and fundamental intelligence as one vertical slice (Step 47): a
    vendor-neutral NewsProvider contract with a deterministic development/test
    feed (no network, explicit placeholder provenance), an explicit NEWS_SOURCE
    selection (auto | development_fake | production, default auto) whose
    production slot refuses until a real vendor is registered, deterministic
    news relevance built on the existing calendar classifier rather than a
    parallel one, FundamentalIntelligenceService composing the MANDATORY
    calendar context with relevant news and each open position's factual
    exposure (explicit UNKNOWN when it cannot be established, never read as "no
    risk"), the JWT-protected GET /fundamental-intelligence/today endpoint, and
    the fundamental block in the agent prompt (bounded, labelled as published
    source facts, provenance carried); there is still no production news vendor
    (CURRENT_CHECKPOINT.md known issue 15)

The repository remains strictly read-only with respect to trading.
14. Current Development Stage

Frozen snapshot: at the time of writing, the project had completed the MT5
read-only data foundation (lifecycle, market data, account information, open
positions, trade history) and the consolidated blocking boundary, and the next
planned area was GET /users listing or tenant-scoped MT5 design.

Development has continued far past that snapshot — roles and user management,
customer-scoped MT5 sessions, provisioned MT5 credentials, economic
and portfolio intelligence, the read-only AI agent, broker LLM configuration
and a tenant-safe login contract are all implemented. CURRENT_CHECKPOINT.md
holds the current stage and the next-step list; do not treat this section as
current.

Do not start any next stage automatically.

Wait for explicit instruction.

15. Planned Development Roadmap

The current high-level roadmap is:

Provider contract
        ↓
MT5 provider
        ↓
MarketDataService
        ↓
Market-data API
        ↓
Fake Provider + Unit Tests
        ↓
MT5 lifecycle management
        ↓
Blocking MT5 / async boundary
        ↓
Authentication
        ↓
User roles / user management
        ↓
Account information (read-only)
        ↓
Open positions (read-only)
        ↓
Trade history (read-only)
        ↓
Tenant-aware market data
        ↓
Trading/account data
        ↓
Richer market data
        ↓
Technical analysis
        ↓
Risk analysis
        ↓
News / fundamentals
        ↓
AI Agent / tools
        ↓
Reports
        ↓
Customer-facing channels

The exact order may change as architecture decisions are made.

Do not implement future stages without explicit instruction.

16. MT5 Lifecycle and Blocking Boundary

The current implementation already provides:

process-level MT5 lifecycle owned by one MT5SessionManager
(app/core/mt5_session.py): the single terminal session is authenticated per
tenant and reused while the tenant matches, under a lock that covers the whole
acquire → read span
no provider caches: each request builds its own provider objects around the
authenticated tenant's credentials, so no cached provider can serve one
tenant's data to another (the former process-wide caches were removed in
Step 36)
no startup warm-up: no tenant is authenticated at boot, so the session is
established lazily by the first authenticated MT5 read; shutdown releases the
session from the FastAPI lifespan
controlled execution of blocking MT5 calls through run_mt5_call
(app/core/blocking.py)

Future work should still address:

MT5 IPC timeouts where appropriate
one MT5 worker process (or equivalent) per broker, so MT5 throughput stops
being a single process-wide bottleneck
broker-specific MT5 configuration
multi-worker deployment semantics

These are future architecture tasks.

Do not implement them during unrelated work.

17. Async Boundary

MT5 is blocking.

The async boundary is now explicitly implemented:

Async FastAPI
      ↓
run_mt5_call (worker threadpool) — app/core/blocking.py
      ↓
Synchronous Service
      ↓
Synchronous Provider (Blocking MT5 Adapter)

It uses starlette's run_in_threadpool. Do not change this mechanism during
unrelated work.

18. Time Handling

Market-data timestamps should eventually use timezone-aware UTC datetimes.

Avoid silently creating naive local datetimes from MT5 Unix timestamps.

This is a known future improvement unless explicitly requested earlier.

19. Error Handling

External-provider errors should be translated at the provider boundary.

The application should not unnecessarily expose raw MT5 implementation details.

Expected failures should be handled explicitly.

Avoid broad exception handling without a clear reason.

Typed provider exceptions may be introduced later if they materially improve the architecture.

Do not introduce unnecessary exception hierarchies.

20. Coding Rules
Inspect first

Before modifying anything:

Inspect the existing repository.
Inspect the relevant files.
Understand the current implementation.
Compare the actual code with this document.
Identify the smallest required change.
Reuse existing patterns.

The actual repository is the final authority for implementation details.

If the repository differs from this document:

Report the discrepancy before making architectural changes.

One small step at a time

Implement only the requested stage.

Do not combine:

refactoring
architecture redesign
future features
unrelated cleanup

with the current task.

Minimal changes

Modify only files necessary for the requested task.

Do not:

rename unrelated files
reorganize folders without reason
rewrite working code
introduce unnecessary dependencies
add speculative abstractions
change unrelated APIs
No architectural drift

Do not change established architecture merely because another architecture looks theoretically better.

Architecture changes require an explicit project decision.

Comments

Use short, meaningful comments only where they improve understanding.

Do not comment obvious code.

Error Handling

Handle expected failures explicitly.

Do not hide errors.

Do not use broad exception handling without a clear reason.

21. AI Coding Agent Workflow

AGENTS.md defines the mandatory reading order (AGENTS.md, PROJECT_CONTEXT.md,
CURRENT_CHECKPOINT.md) and takes precedence over this document; knowledge.md is
background, not a required first step.

When an AI coding agent receives a task:

1. Read the files AGENTS.md requires
        ↓
2. Inspect the actual repository
        ↓
3. Compare the repository state with the documentation (CURRENT_CHECKPOINT.md
   is authoritative for current state; this document's frozen sections are
   history)
        ↓
4. Identify the smallest required change
        ↓
5. Implement only that change
        ↓
6. Run relevant tests
        ↓
7. Inspect git diff
        ↓
8. Report results

After implementation, report:

files changed
what changed
tests executed
test results
remaining issues

Do not claim tests passed unless they were actually executed.

22. Existing Uncommitted Work

AI coding agents must preserve legitimate existing uncommitted changes.

Before editing:

git status

must be inspected.

Do NOT:

reset
revert
stash
overwrite
discard

existing work unless explicitly instructed.

If an existing uncommitted change conflicts with the current task, stop and report the conflict.

23. Stop Condition

If implementing a requested task requires a significant architectural change outside the stated scope:

STOP.

Explain:

what conflict was found
why the requested task cannot safely be completed as specified
what architectural decision would be required

Do not silently expand the scope.

24. Product Safety Boundary

This project is intentionally read-only.

The system may:

inspect
analyze
calculate
summarize
explain
report

The system must not:

place trades
modify trades
close trades
autonomously execute financial transactions

This boundary must be preserved throughout development.

25. Development Philosophy

The goal is to build a real production-oriented product incrementally.

Priorities:

Correct architecture
Clear separation of responsibilities
Testability
Security
Tenant isolation
Reliability
Observability
Maintainability
Controlled scalability

Prefer:

simple
explicit
modular
testable
maintainable

Avoid:

overengineering
premature optimization
unnecessary abstractions
speculative features
giant refactors
changing architecture without a reason

The goal is not to create the most complicated architecture.

The goal is to create a system that can grow safely from a working MVP into a real broker-facing product.

26. Known Cleanup Item

Pytest generates .pytest_cache/ in the project root.

The root .gitignore still has no entry for it, but pytest also writes
.pytest_cache/.gitignore containing "*", so the cache can never be committed by
accident: this is a cosmetic cleanup, not a real hygiene risk.

Do not mix this cleanup into unrelated feature work unless explicitly requested.

27. Current Immediate Objective

Frozen snapshot: at the time of writing, Steps 18–20 (MT5 Open Positions,
Consolidate MT5 Blocking Boundary, MT5 Trade History) were implemented,
verified, committed (544cd51) and pushed to origin/master.

The current objective, and the authoritative commit/push state, live in
CURRENT_CHECKPOINT.md; do not treat the paragraph above as current.

Wait for explicit instruction before starting any further stage.