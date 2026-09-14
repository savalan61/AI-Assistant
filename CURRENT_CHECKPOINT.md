# CURRENT_CHECKPOINT.md

## Current Status

Step 19 — Consolidate MT5 Blocking Boundary (with Step 18 — MT5 Open Positions)

Status:

VERIFIED — NOT YET COMMITTED

The Step 18/19 implementation (MT5 Open Positions + consolidated blocking
boundary) is fully present in the working tree but has no commit yet. The
last commit is the documentation checkpoint caa83d5 ("docs: update checkpoint
through account information").

Nothing has been pushed/synced; the local branch is ahead of origin/master.

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
Completed and committed (cc782e0, "feat(mt5): add lifecycle and blocking boundary").

Includes:

- lazy process-wide MT5 provider singleton
- thread-safe initialization; failed initialization is not cached
- provider shutdown via FastAPI lifespan (startup warm-up, graceful shutdown)
- blocking MT5 call executed through the thread-pool boundary

### Step 18 — MT5 Open Positions (READ-ONLY)
Status: VERIFIED — NOT YET COMMITTED (part of the current working tree)

Includes:

- Position NamedTuple contract with PositionType StrEnum ("BUY"/"SELL") and
  PositionProvider abstraction, exported through app/providers/__init__.py
- MT5PositionProvider in app/providers/mt5_positions.py: read-only
  positions_get() translation into the Position contract, RuntimeError
  translation, explicit failure on unmapped position types, never-raising
  shutdown()
- FakePositionProvider for tests
- PositionService in app/services/positions/ (synchronous passthrough)
- GET /positions protected by get_current_user(); wrapped contract
  {"positions": [...]} — empty result is 200 with {"positions": []}, never 404
- process-wide positions provider cache in the composition root, mirroring
  market-data and account-info (failed init not cached)

### Step 19 — Consolidate MT5 Blocking Boundary
Status: VERIFIED — NOT YET COMMITTED (part of the current working tree)

Includes:

- app/core/blocking.py with run_mt5_call(...): the single consolidated
  blocking boundary; synchronous MT5-backed service calls are executed on the
  worker threadpool (starlette run_in_threadpool), never on the event loop
- GET /account-info now routes its service call through run_mt5_call,
  resolving the Known Issues item 9 debt from Step 17
- GET /positions built on the same boundary from the start
- GET /market-data/{symbol} migrated to the same boundary
- providers and services remain synchronous; the async boundary lives only
  in the API layer through run_mt5_call

### Step 8 — Authentication Security Foundation
Completed and committed (531e5cb, "feat(auth): add security foundation").

Includes:

- bcrypt password hashing/verification (hash_password / verify_password)
- PyJWT token creation/decoding with expiration validation (create_access_token / decode_token)
- SecurityError: one application-level error for invalid/expired/malformed tokens
- environment-driven JWT configuration (SECRET_KEY from env only, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES)
- fail-closed behavior when SECRET_KEY is not configured

### Step 9 — Current User Authentication Dependency
Status: VERIFIED + COMMITTED

Commit: 0febd333eb4c13fc953424100dc74cf6a526d67c (0febd33)

Message: feat(auth): add current-user dependency

Includes:

- get_current_user() in app/core/dependencies.py
- FastAPI HTTPBearer extraction (Authorization: Bearer <JWT>)
- JWT validation delegated to decode_token()
- JWT sub safely validated as User.id
- User loaded from the database via get_db(); inactive/nonexistent users rejected
- authentication failures return HTTP 401 with WWW-Authenticate: Bearer
- broker_id comes from the database User record, never from JWT claims

### Step 10 — Login / Authentication Endpoint
Status: VERIFIED + COMMITTED

Commit: 004367c69029a02209ac78f9093a4444a58c4169 (004367c)

Message: feat(auth): add login endpoint

Includes:

- POST /auth/login with username/password (application password; MT5 password never used)
- password verified with verify_password(); active User and active Broker required
- generic 401 for every failure path; no information leaked about which check failed
- JWT created with create_access_token(subject=str(user.id)); no broker_id and no MT5 credentials in the JWT

### Step 11 — Protect Market Data Endpoint
Status: VERIFIED + COMMITTED

Commit: 5e0302e929ccc18f936c99a59080bfe2cca40dfa (5e0302e)

Message: feat(auth): protect market data endpoint

Includes:

- GET /market-data/{symbol} requires get_current_user()
- unauthenticated/invalid/expired requests return HTTP 401
- existing 404 (ValueError) and 503 (RuntimeError) behavior preserved
- MT5/provider architecture and threadpool boundary unchanged

### Step 12 — Development User Creation (scope re-purposed by explicit instruction)
Status: VERIFIED + COMMITTED (dev seed script folded into the Step 13 commit)

Includes:

- scripts/create_dev_user.py: development-only, idempotent seed for the first Broker/User
- refuses to run unless APP_ENV=development; password never printed or logged
- no public registration endpoint was created

### Step 13 — User Role Foundation
Status: VERIFIED + COMMITTED

Commit: 5ac9aeb ("feat(auth): add user roles")

Includes:

- UserRole StrEnum (broker_admin, customer) on the User model
- non-native enum storage: VARCHAR + ck_users_role CHECK, portable across PostgreSQL/SQLite
- Alembic migration 3f025a5d3b39 with least-privilege backfill to customer
- dev user promoted to broker_admin via the seed script; no role claim in the JWT

### Step 14 — Broker Admin Creates Customer User
Status: VERIFIED + COMMITTED

Commits: ab460d0 ("feat(users): allow broker admins to create customers"),
cef4808 ("feat(users): add input validation", Step 14.1)

Includes:

- get_current_broker_admin() authorization dependency (database-backed role; 403 for non-admins)
- POST /users protected by broker-admin authorization; broker_id/role derived server-side
- CreateUserRequest with extra="forbid" and full input validation (4-12 digit username, password
  length 8-72 bytes, structural email check, optional '+' + 7-15 digit phone)
- generic 409 for tenant-scoped duplicate conflicts; no sensitive fields in responses

### Step 15 — MT5 Account Information Contract
Status: VERIFIED + COMMITTED

Commit: 9d5fa0e ("feat(mt5): add account info contract")

Includes:

- AccountInfo NamedTuple with the nine application-level fields
- exported through app/providers/__init__.py

### Step 16 — MT5 Account Information Provider
Status: VERIFIED (real MT5 terminal probe passed)

Includes:

- MT5AccountInfoProvider in app/providers/mt5_account_info.py using the established
  mt5_api: Any boundary seam, RuntimeError translation, and never-raising shutdown()
- explicit conversion of the raw MT5 object into AccountInfo
- real MT5 field margin_free correctly mapped to the contract field free_margin
  (defect caught by the real-terminal probe and fixed)
- 9 fake-boundary tests including the read-only MT5 function-surface guarantee

### Step 17 — MT5 Account Information API
Status: VERIFIED + COMMITTED (real MT5 API probe passed)

Commit: 4bae954 ("feat(account): add MT5 account information")

Includes:

- AccountInfoProvider abstraction (ABC) and AccountInfoService (services/account/)
- GET /account-info protected by get_current_user(); JWT protected
- AccountInfoResponse Pydantic model exposing exactly the nine AccountInfo fields
- composition-root provider cache mirroring market-data (failed init not cached)
- RuntimeError from the provider mapped to HTTP 503 with a generic detail
- full test suite at that checkpoint: 115 passed

## Current Authentication Flow

POST /auth/login
    ↓
JWT access token
    ↓
Authorization: Bearer <token>
    ↓
get_current_user()
    ↓
database-backed User
    ↓
protected API

## Current Market Data Flow

Authenticated request
    ↓
get_current_user()
    ↓
run_mt5_call (blocking boundary, app/core/blocking.py)
    ↓
MarketDataService
    ↓
MarketDataProvider
    ↓
MT5MarketDataProvider
    ↓
MT5
    ↓
Candle

## Current Account Information Flow

Authenticated request
    ↓
get_current_user()
    ↓
run_mt5_call (blocking boundary, app/core/blocking.py)
    ↓
AccountInfoService
    ↓
AccountInfoProvider (abstraction)
    ↓
MT5AccountInfoProvider
    ↓
MT5
    ↓
AccountInfo

## Current Positions Flow

Authenticated request
    ↓
get_current_user()
    ↓
run_mt5_call (blocking boundary, app/core/blocking.py)
    ↓
PositionService
    ↓
PositionProvider (abstraction)
    ↓
MT5PositionProvider
    ↓
MT5
    ↓
tuple[Position, ...]

All three MT5-backed endpoints route their blocking calls through the same
consolidated boundary (run_mt5_call); providers and services remain synchronous.

## Positions API Contract

GET /positions returns the wrapped response:

    {
      "positions": [
        {
          "ticket": 123456789,
          "symbol": "XAUUSD",
          "type": "BUY",
          "volume": 0.10,
          "open_price": 3642.50,
          "current_price": 3648.20,
          "profit": 57.00
        }
      ]
    }

No open positions is a normal 200 response:

    {"positions": []}

It is never a 404. Position.type is the string "BUY" or "SELL" (PositionType
StrEnum). MT5 direction codes (0 = BUY, 1 = SELL) are translated in the
provider; unmapped values fail loudly with RuntimeError (→ HTTP 503).

## Current Verified Facts

- Application authentication exists.
- Login endpoint exists.
- JWT access tokens exist.
- get_current_user() exists.
- Market-data, account-info, and positions endpoints all require authentication.
- User broker_id comes from the database.
- MT5 password is separate from application password.
- No broker_id is trusted from JWT claims.
- No trading/order functionality exists.
- No BUY/SELL/OPEN/CLOSE/MODIFY functionality exists.
- MT5 providers remain read-only (verified by code inspection: no trading
  function exists anywhere in app/).
- All MT5 blocking calls (market-data, account-info, positions) are kept
  outside the event loop through the consolidated run_mt5_call boundary.
- Broker Admin / Customer roles exist; POST /users lets a Broker Admin create Customer Users in their own tenant.
- Account information (AccountInfo contract, MT5AccountInfoProvider, AccountInfoService, GET /account-info) exists and is read-only.
- Open positions (Position contract, MT5PositionProvider, FakePositionProvider, PositionService, GET /positions) exist and are read-only.
- Test suite verified 2026-09-14: pytest tests/ -q → 145 passed, 3 warnings.
- The 3 warnings are pre-existing third-party deprecation warnings (anyio
  PortalFactoryType and Pydantic class-based Config in app/core/config.py).
- compileall over app, tests, and scripts is clean.
- git diff --check is clean.
- The Step 18/19 working tree is not committed yet (last commit: caa83d5).
- Nothing has been pushed/synced.

Static/type verification:

Direct Pylance/pyright execution was not available in the environment for any of
Steps 8–17. Manual static/type reviews were performed instead. This limitation
must be reported rather than hidden.

## Known Issues (current)

1. MT5 singleton is process-wide and not tenant-scoped. Every provider cache
   attaches to the same MT5 terminal session; connection semantics are
   process-wide, non-tenant-scoped, process-attached. This is intentional for
   the current stage and remains a known limitation.
2. MT5 IPC timeout is not implemented.
3. /health does not currently represent MT5 readiness.
4. Multi-worker deployment semantics need future documentation/design.
5. Candle timestamps need future UTC review.
6. MT5 last_error handling has a minor robustness concern.
7. MetaTrader5 is currently a Windows-specific dependency and needs future CI/Docker consideration.
8. Minor cleanup/deprecation/hygiene items remain (Pydantic class-based Config,
   .gitignore entries such as .pytest_cache/).

Resolved:

- (Former item 9) MT5 blocking-call technical debt — RESOLVED by Step 19.
  All MT5-backed endpoints (account-info, positions, market-data) now execute
  their synchronous service calls off the event loop through the consolidated
  run_mt5_call boundary in app/core/blocking.py.
- The market-data endpoint now requires authentication (Step 11).

These issues are known and must NOT be fixed automatically.

They should be addressed one controlled stage at a time.

## Next Step

Steps 12–19 are complete. Before any further implementation, the Step 18/19
working tree (positions + consolidated blocking boundary) should be committed
as the next Git checkpoint.

After that, the next logical areas, in no committed order, are:

- read-only MT5 trade history through the established
  provider → service → API pattern
- GET /users listing for Broker Admins (tenant-scoped)
- tenant-scoped MT5 design (known issue 1)

Do NOT implement any next step until explicitly instructed.

When instructed, begin by inspecting the existing provider abstractions
(app/providers/position.py, app/providers/account_info.py,
app/providers/market_data.py), the MT5 providers, the consolidated blocking
boundary (app/core/blocking.py), and the composition root
(app/core/dependencies.py).

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
