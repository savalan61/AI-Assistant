# CURRENT_CHECKPOINT.md

## Current Status

Step 9 — Current User Authentication Dependency

Status:

VERIFIED + READY FOR COMMIT

Previous checkpoint (Step 8 — Authentication Security Foundation):

531e5cbc479ab1c35af8ff0e16ee4c3fb311b347 (531e5cb, "feat(auth): add security foundation")

Working tree at this checkpoint:

The Step 9 implementation files (app/core/dependencies.py, requirements.txt, tests/test_get_current_user.py) are present and verified but NOT yet committed; they are ready for the next Step 9 commit.

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

### Step 8 — Authentication Security Foundation
Completed and committed (531e5cb, "feat(auth): add security foundation").

### Step 9 — Current User Authentication Dependency
Verified. Implementation files remain uncommitted in the working tree (see Current Status).

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

## Step 8 Implementation

### Security Primitives

bcrypt + PyJWT security primitives implemented in app/core/security.py:

- hash_password() / verify_password() — bcrypt password hashing and verification
- create_access_token() / decode_token() — JWT creation and decoding with expiration validation
- SecurityError — one application-level exception for invalid/expired/malformed tokens; PyJWT details never leak

### Configuration

Environment-driven JWT configuration added to app/core/config.py:

- SECRET_KEY (no committed value; must come from the environment)
- ALGORITHM (default HS256)
- ACCESS_TOKEN_EXPIRE_MINUTES (default 30)

Fail-closed behavior: token signing refuses to run when SECRET_KEY is not configured.

Dependencies added: bcrypt==5.0.0, PyJWT==2.14.0 (no passlib).

### Step 8 Verification

- pytest tests/ -v → 25 passed (4 service + 8 lifecycle + 13 security)
- git diff --check → clean
- py_compile → clean
- Pylance/pyright/mypy unavailable; manual type review completed

### Explicit Non-Goals (still true after Step 8)

- No authentication endpoint or login route
- No get_current_user dependency
- No API protection on the market-data route
- No authorization or broker/tenant checks
- No MT5/provider changes
- No secrets committed

## Step 9 Implementation

### Current-User Dependency

get_current_user() implemented in app/core/dependencies.py:

- FastAPI HTTPBearer extraction (Authorization: Bearer <JWT>); no manual header parsing
- JWT validation delegated to decode_token(); no duplicated JWT logic
- JWT sub safely validated as User.id (string check + int coercion)
- User loaded from the existing get_db() AsyncSession by primary key (session.get)
- nonexistent users rejected; inactive users rejected (is_active checked)
- returns the real User ORM object; broker_id comes from the database record, never from token claims

### Authentication Behavior

- All expected authentication failures return HTTP 401 with WWW-Authenticate: Bearer and generic messages
- JWT and database internals never reach the client
- Database/infrastructure failures are deliberately not swallowed (propagate as server errors)

### Explicit Non-Goals (still true after Step 9)

- No login endpoint or /auth router
- No API route protected yet (get_current_user is not attached to any route)
- No authorization system or broker checks
- No MT5/provider code changed
- No secrets committed

### Step 9 Verification

- pytest tests/ -v → 35 passed (4 service + 8 lifecycle + 13 security + 10 dependency)
- git diff --check → clean
- py_compile → clean
- Pylance/pyright/mypy unavailable; manual static/type review completed
- aiosqlite==0.22.1 added to requirements.txt for the SQLite-backed test database

## Next Logical Area

The next logical authentication slice is the login/authentication endpoint that verifies application credentials and issues an access token.

Do NOT implement it until explicitly instructed.

When instructed, begin by inspecting app/core/security.py (hash_password/verify_password/create_access_token), the User model, and the existing get_current_user dependency.

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
