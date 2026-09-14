# CURRENT_CHECKPOINT.md

## Current Status

Step 17 — MT5 Account Information API

Status:

VERIFIED + COMMITTED

Latest checkpoint commit:

4bae954 ("feat(account): add MT5 account information") — covers Steps 16 and 17

Working tree at this checkpoint:

CLEAN

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
- full test suite: 115 passed

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
MarketDataService
    ↓
MarketDataProvider
    ↓
MT5MarketDataProvider
    ↓
MT5
    ↓
Candle

Blocking MT5 work is executed through the thread-pool boundary.

## Current Account Information Flow

Authenticated request
    ↓
get_current_user()
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

Known technical debt: this path currently runs the synchronous MT5 call on the
FastAPI event loop (see Known Issues item 9).

## Current Verified Facts

- Application authentication exists.
- Login endpoint exists.
- JWT access tokens exist.
- get_current_user() exists.
- Market-data endpoint requires authentication.
- User broker_id comes from the database.
- MT5 password is separate from application password.
- No broker_id is trusted from JWT claims.
- No trading/order functionality exists.
- No BUY/SELL/OPEN/CLOSE/MODIFY functionality exists.
- MT5 provider remains read-only.
- MT5 market-data blocking calls are kept outside the async event loop through the existing threadpool boundary.
- Broker Admin / Customer roles exist; POST /users lets a Broker Admin create Customer Users in their own tenant.
- Account information (AccountInfo contract, MT5AccountInfoProvider, AccountInfoService, GET /account-info) exists and is read-only.
- Test suite currently has 115 passing tests.
- Only the three pre-existing third-party deprecation warnings remain.
- Working tree was clean after Step 17 (commit 4bae954).
- Nothing has been pushed/synced.

Static/type verification:

Direct Pylance/pyright execution was not available in the environment for any of
Steps 8–17. Manual static/type reviews were performed instead. This limitation
must be reported rather than hidden.

## Known Issues (current)

1. MT5 singleton is process-wide and not tenant-scoped.
2. MT5 IPC timeout is not implemented.
3. /health does not currently represent MT5 readiness.
4. Multi-worker deployment semantics need future documentation/design.
5. Candle timestamps need future UTC review.
6. MT5 last_error handling has a minor robustness concern.
7. MetaTrader5 is currently a Windows-specific dependency and needs future CI/Docker consideration.
8. Minor cleanup/deprecation/hygiene items remain (Pydantic class-based Config, .env.example format, .gitignore entries).
9. MT5 blocking-call technical debt:
   - GET /account-info currently invokes the synchronous MT5 provider directly from the FastAPI request path.
   - Therefore the blocking MT5 operation currently runs on the FastAPI event loop.
   - This is intentional for the current Step 17 scope and is NOT considered a Step 17 defect.
   - It must be corrected later when MT5 blocking-call handling is consolidated across account information, positions, trade history, and other MT5 operations.
   - Do NOT fix this issue now.

Resolved since the Stage 6 audit: the market-data endpoint now requires
authentication (Step 11).

These issues are known and must NOT be fixed automatically.

They should be addressed one controlled stage at a time.

## Next Step

Steps 12–17 are complete. The next logical areas, in no committed order, are:

- consolidation of MT5 blocking-call handling across all MT5 operations
  (resolves Known Issues item 9)
- read-only MT5 positions and trade history through the established
  provider → service → API pattern
- GET /users listing for Broker Admins (tenant-scoped)

Do NOT implement any next step until explicitly instructed.

When instructed, begin by inspecting the existing provider abstractions
(app/providers/account_info.py, app/providers/market_data.py), the MT5 providers,
and the composition root (app/core/dependencies.py).

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
