# AI Broker Assistant — Project Knowledge

> **Status note (authority).** This document is a historical project-knowledge
> snapshot that stops at Steps 18–20. Several sections below (role names, MT5
> session design, the future-stage list) describe earlier states of the system.
> CURRENT_CHECKPOINT.md is authoritative for the current state; treat anything
> here that conflicts with it as historical. Two facts in particular have moved:
> roles are now super_admin / admin / customer (Step 21A, one super_admin per
> Broker enforced by a database partial unique index), and MT5 authentication is
> tenant-scoped (Step 36) with a per-user, administrator-provisioned credential
> (Step 38): explicit mt5_login / mt5_server columns plus an encrypted MT5
> INVESTOR (read-only) password, falling back to a numeric username +
> Broker.mt5_server when those columns are NULL. The MT5 trading (master)
> password is never requested, stored or used, and no API returns a credential.

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
POST /auth/login issues the token
get_current_user() in app/core/dependencies.py resolves a Bearer JWT to the
database-backed active User
get_current_broker_admin() adds database-backed role authorization
All MT5-touching endpoints (market-data, account-info, positions) and
POST /users require authentication

Authentication failures return HTTP 401 with WWW-Authenticate: Bearer; the
authoritative broker_id and role always come from the database User record,
never from JWT claims.

Future stages may extend authentication (refresh tokens, channel identity,
etc.), but the core login/JWT/dependency path must be treated as existing.

6. Multi-Tenant Architecture

The product is intended to be multi-tenant.

The primary tenant is:

Broker

A customer's data must never leak across brokers.

The database architecture is intended to support broker-level isolation and potentially DB-per-broker in the future.

However:

Tenant isolation is implemented at the database level (broker-scoped unique
constraints, broker_id on User) and in user management (a Broker Admin can
only create users in their own tenant).

Tenant isolation is NOT yet implemented in the MT5 data flows: the MT5
terminal connection is process-wide and non-tenant-scoped, so market-data,
account-info, and positions are not scoped to the authenticated user's
broker.

Do NOT implement MT5 tenant isolation during unrelated tasks.

When tenant isolation is introduced, it must be designed explicitly and consistently across:

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
    → dependency/composition wiring + provider caches (market-data,
      account-info, positions) + authentication dependencies

app/core/blocking.py
    → consolidated blocking boundary: run_mt5_call(...) executes synchronous
      MT5-backed service calls on the worker threadpool, never on the event loop

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

Do not modify MT5 lifecycle or blocking behavior during unrelated tasks.

11. Current API

Current read-only, JWT-protected endpoints:

POST /auth/login
GET /market-data/{symbol}
GET /account-info
GET /positions
GET /trade-history?from=<UTC ISO>&to=<UTC ISO>
POST /users (Broker Admin only)
GET /health (unauthenticated infrastructure probe; does not reflect MT5 readiness)

Current HTTP layer is responsible for:

receiving the request
offloading the blocking MT5-backed service call via run_mt5_call
calling the service
converting service/provider failures into HTTP responses
returning the HTTP/Pydantic response schema

Error mapping for the MT5 endpoints:

401 unauthenticated/invalid token
404 market data unavailable for the requested symbol (market-data only)
409 duplicate user (POST /users)
422 missing/malformed query parameters (trade-history from/to)
400 non-UTC-aware window boundary or from >= to (trade-history)
503 MT5 infrastructure/availability failure

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

The suite currently has 200 passing tests (verified 2026-09-14 with
pytest tests/ -q; the 3 remaining warnings are pre-existing third-party
deprecation warnings).

A root-level:

conftest.py

exists to make project imports work during pytest execution.

Current Fake Providers:

app/providers/fake_market_data.py
app/providers/fake_position.py
app/providers/fake_trade_history.py

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

Completed:

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
10. User roles (broker_admin/customer) and POST /users customer creation
11. Read-only MT5 account information (contract, provider, service, API)
12. Read-only MT5 open positions (contract, provider, service, API)
13. Consolidated MT5 blocking boundary (app/core/blocking.py, run_mt5_call)
    used by market-data, account-info, and positions
14. Read-only MT5 trade history (contract, provider, service, API)

The repository remains strictly read-only with respect to trading.
14. Current Development Stage

The project has completed the MT5 read-only data foundation (lifecycle,
market data, account information, open positions, trade history) and the
consolidated blocking boundary.

The next planned stage is:

Stage 21 — GET /users listing or tenant-scoped MT5 design, or another
explicitly chosen area

However:

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

process-level MT5 lifecycle
lazy process-wide provider initialization (one cache per provider type,
all attaching to the same terminal session)
thread-safe initialization; failed initialization is not cached
provider shutdown via FastAPI lifespan (startup warm-up, graceful shutdown)
controlled execution of blocking MT5 calls through run_mt5_call
(app/core/blocking.py)

Future work should still address:

MT5 IPC timeouts where appropriate
multiple broker/customer MT5 sessions (tenant-scoped connections)
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

When an AI coding agent receives a task:

1. Read knowledge.md
        ↓
2. Inspect the actual repository
        ↓
3. Compare repository state with knowledge.md
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

Pytest generated:

.pytest_cache/

This is currently not ignored by .gitignore.

This should be addressed during a future cleanup task.

Do not mix this cleanup into unrelated feature work unless explicitly requested.

27. Current Immediate Objective

Steps 18–20 (MT5 Open Positions, Consolidate MT5 Blocking Boundary, MT5
Trade History) are implemented, verified, committed (544cd51) and pushed to
origin/master.

Wait for explicit instruction before starting any further stage
(e.g. GET /users listing or tenant-scoped MT5 design).