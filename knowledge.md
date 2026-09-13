# AI Broker Assistant — Project Knowledge

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

Current known modules:

app/core/
app/db/
app/api/
app/services/
app/providers/
tests/

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

JWT authentication is a planned part of the product architecture, but it is not currently implemented in the repository.

Do not assume that authentication already exists.

Do not add authentication as part of an unrelated task.

Authentication will be implemented deliberately in a future stage.

6. Multi-Tenant Architecture

The product is intended to be multi-tenant.

The primary tenant is:

Broker

A customer's data must never leak across brokers.

The database architecture is intended to support broker-level isolation and potentially DB-per-broker in the future.

However:

Tenant isolation is not currently implemented in the market-data flow.

Do NOT implement tenant isolation during unrelated tasks.

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

The current market-data composition/wiring is located in:

app/core/dependencies.py

The API router should not directly construct the concrete MT5 provider.

Current responsibility separation:

app/core/dependencies.py
    → dependency/composition wiring

app/services/market/market_data_service.py
    → application/service logic

app/providers/market_data.py
    → provider abstraction + Candle contract

app/providers/mt5_market_data.py
    → MT5 implementation

app/api/market_data_router.py
    → HTTP route + HTTP error mapping + CandleResponse

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

Current real provider:

app/providers/mt5_market_data.py

It uses the Python MetaTrader 5 package.

Installed version:

metatrader5 5.0.6180

Important:

MT5 Python calls are blocking.

Future architecture must handle this explicitly.

Do not modify MT5 lifecycle or blocking behavior during unrelated tasks.

11. Current API

Current market-data endpoint:

GET /market-data/{symbol}

Current HTTP layer is responsible for:

receiving the request
calling the service
converting service/provider failures into HTTP responses
returning the HTTP/Pydantic response schema

Do not change the API contract unless the current task explicitly requires it.

12. Testing Status

Unit testing infrastructure now exists.

Current testing framework:

pytest==9.1.1

Current test directory:

tests/

A root-level:

conftest.py

exists to make project imports work during pytest execution.

Current Fake Provider:

app/providers/fake_market_data.py

Current service tests:

tests/test_market_data_service.py

The Fake Provider is intended for testing and returns deterministic Candle data.

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

Stage 5 verification:

pytest tests/ -v
4 passed

The tests verify:

service works with FakeMarketDataProvider
provider abstraction is respected
get_market_data(symbol) delegation works
symbol is passed correctly
deterministic Candle values are returned
14. Current Development Stage

The project has completed the initial Provider Architecture validation.

The next planned stage is:

Stage 6 — MT5 Lifecycle / Blocking Boundary

However:

Do not start Stage 6 automatically.

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

16. Future MT5 Lifecycle

The current implementation requires future lifecycle improvements.

Future work should address:

process-level MT5 lifecycle
initialization
shutdown
avoiding unnecessary repeated initialization
controlled execution of blocking MT5 calls
timeouts where appropriate
multiple broker/customer MT5 sessions
broker-specific MT5 configuration

These are future architecture tasks.

Do not implement them during unrelated work.

17. Future Async Boundary

MT5 is blocking.

Future architecture should explicitly separate:

Async FastAPI
      ↓
Async Service Boundary
      ↓
Blocking MT5 Adapter

A possible implementation may use:

asyncio.to_thread(...)

or another controlled execution mechanism.

The exact implementation must be decided when Stage 6 begins.

Do not introduce async changes prematurely.

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

Stage 5 is complete and verified.

The next planned objective is:

Stage 6 — MT5 Lifecycle / Blocking Boundary

Do not implement Stage 6 automatically.

Wait for explicit instruction from the project owner.