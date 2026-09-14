# CURRENT_CHECKPOINT.md

## Current Status

Stage 6 — MT5 Lifecycle / Blocking Boundary

Status:

VERIFIED + COMMITTED

Git commit:

cc782e074a73e36bd6a1874cb0c4e712d35f7169

Short hash:

cc782e0

Commit message:

feat(mt5): add lifecycle and blocking boundary

Working tree at checkpoint:

CLEAN

## Completed Stages

### Stage 1 — Project Initialization
Completed.

### Stage 2 — Configuration Foundation
Completed.

### Stage 3 — PostgreSQL + Async SQLAlchemy Connection
Completed.

### Stage 4 — Database Base Foundation
Completed.

### Stage 5 — Broker/User Database Foundation
Completed.

Includes:

- Broker tenant model
- User model
- broker-scoped uniqueness
- Alembic migration
- PostgreSQL integration

### Stage 6 — MT5 Lifecycle / Blocking Boundary
Completed and committed.

## Stage 6 Implementation

### Provider Lifecycle

MT5MarketDataProvider is created lazily.

A process-wide singleton is maintained.

Initialization is protected against concurrent construction.

Failed initialization is not cached.

Provider shutdown clears the cached provider.

### FastAPI Lifespan

Application lifespan:

- attempts startup provider warm-up
- logs a warning if MT5 is unavailable
- allows application startup to continue
- shuts down the provider during application teardown

### Blocking Boundary

MT5 provider/service methods remain synchronous.

The FastAPI route uses a thread-pool boundary for the blocking market-data call.

This prevents the blocking MT5 call from blocking the FastAPI event loop.

### Error Handling

MT5 integration errors are translated at the provider boundary.

The API maps expected application errors to HTTP responses.

Current known mapping:

- ValueError → 404
- RuntimeError → 503

## Current Market Data Flow

Current intended flow:

HTTP request
→ FastAPI router
→ dependency
→ MarketDataService
→ MarketDataProvider
→ MT5MarketDataProvider
→ MetaTrader5
→ Candle
→ API response

Blocking MT5 work is executed through the thread-pool boundary.

## Verification

Known Stage 6 verification:

- pytest tests/ -v → 12 passed
- git diff --check → clean
- Python compilation → clean
- live MT5 endpoint previously verified
- health endpoint verified
- no trading/order functionality exists
- .env is not tracked

Static/type verification:

Direct Pylance/pyright execution was not available in the environment.

Manual static/type review was performed.

This limitation must be reported rather than hidden.

## Stage 6 Audit

A read-only whole-project audit was performed after Stage 6.

Result:

READY WITH MINOR ISSUES

Known issues:

1. Market-data endpoint currently lacks authentication.
2. MT5 singleton is process-wide and not tenant-scoped.
3. MT5 IPC timeout is not implemented.
4. /health does not currently represent MT5 readiness.
5. Multi-worker deployment semantics need future documentation/design.
6. Candle timestamps need future UTC review.
7. MT5 last_error handling has a minor robustness concern.
8. MetaTrader5 is currently a Windows-specific dependency and needs future CI/Docker consideration.
9. Minor cleanup/deprecation/hygiene items remain.

These issues are known and must NOT be fixed automatically.

They should be addressed one controlled stage at a time.

## Next Logical Area

Authentication and Authorization for the market-data API.

IMPORTANT:

Before implementing authentication, inspect the actual repository and determine what JWT/authentication foundation currently exists.

Do not assume authentication exists merely because it appears in historical context.

The next task should begin with inspection/audit of the existing authentication foundation if necessary.

## Architectural Guardrails

Do not:

- add trading execution
- add BUY/SELL functionality
- remove broker tenant isolation
- replace the modular monolith
- introduce microservices
- add unnecessary infrastructure
- redesign the MT5 lifecycle without explicit approval
- implement future stages prematurely

## Checkpoint Rule

After every verified implementation stage:

- update CURRENT_CHECKPOINT.md
- record what changed
- record verification
- record known issues
- record the Git commit if one was created
- identify the exact next logical step

Do not update the checkpoint to claim completion before verification.
