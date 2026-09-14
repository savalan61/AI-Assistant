# CURRENT_CHECKPOINT.md

## Current Status

Step 11 — Protect Market Data Endpoint

Status:

VERIFIED + COMMITTED

Latest checkpoint commit:

5e0302e929ccc18f936c99a59080bfe2cca40dfa (5e0302e, "feat(auth): protect market data endpoint")

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
- MT5 blocking calls are kept outside the async event loop through the existing threadpool boundary.
- Test suite currently has 54 passing tests.
- Only the three pre-existing third-party deprecation warnings remain.
- Working tree was clean after Step 11.
- Nothing has been pushed/synced.

Static/type verification:

Direct Pylance/pyright execution was not available in the environment for any of
Steps 8–11. Manual static/type reviews were performed instead. This limitation
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

Resolved since the Stage 6 audit: the market-data endpoint now requires
authentication (Step 11).

These issues are known and must NOT be fixed automatically.

They should be addressed one controlled stage at a time.

## Next Step

STEP 12 — MT5 Account Information

Planned scope:

- read-only account information from MT5
- Balance
- Equity
- Margin
- Free Margin
- account-level data only
- provider abstraction preserved
- no trading operations

Do NOT implement Step 12 until explicitly instructed.

When instructed, begin by inspecting the existing provider abstraction
(app/providers/market_data.py), the MT5 provider (app/providers/mt5_market_data.py),
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
