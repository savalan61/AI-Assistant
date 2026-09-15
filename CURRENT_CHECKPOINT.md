# CURRENT_CHECKPOINT.md

## Current Status

Step 22 — Super Admin Creates Admin User (POST /users/admins)

Status:

VERIFIED + COMMITTED + SYNCED

Implementation commit:

cde617e ("feat(auth): add super admin admin creation")
(full hash: cde617e8325fe6ec87eeeb544d83d37edf2663d8)

Test result at this checkpoint:

pytest tests/ -q → 242 passed, 3 warnings (pre-existing third-party
deprecation warnings); verified 2026-09-15 on the exact committed tree

Static verification: python -m compileall app scripts tests → clean.
git diff --check → clean.
Direct Pylance/pyright execution remains unavailable in this environment
(as recorded for Steps 8–21A); a focused manual static/type review was
performed instead. No type suppressions were used.

No trading functionality was changed in Step 22; all MT5 behavior is
untouched.

Working tree at this checkpoint:

CLEAN

The commit has been pushed/synced to origin/master.

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
Status: VERIFIED + COMMITTED (5b367a4)

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
Status: VERIFIED + COMMITTED (5b367a4)

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

### Step 20 — MT5 Trade History (READ-ONLY)
Status: VERIFIED + COMMITTED (544cd51)

Includes:

- TradeHistoryEntry NamedTuple contract (11 fields) with TradeType StrEnum
  ("BUY"/"SELL") and TradeCloseReason StrEnum ("TP"/"SL"/"MANUAL"/"OTHER"),
  plus TradeHistoryProvider abstraction, exported through app/providers/__init__.py
- MT5TradeHistoryProvider in app/providers/mt5_trade_history.py: read-only
  history_deals_get translation into the contract — DEAL_ENTRY_OUT-only
  filtering, DEAL_REASON_* mapped to TP/SL/MANUAL/OTHER (unmapped → OTHER,
  missing → None), SL/TP taken from the related closing order via
  history_orders_get(ticket=...) or None, UTC-aware deal timestamps,
  RuntimeError translation, never-raising shutdown()
- FakeTradeHistoryProvider for tests
- TradeHistoryService in app/services/trade_history/ (synchronous passthrough)
- GET /trade-history?from=&to= protected by get_current_user(); from/to are
  required (422), must be UTC-aware (400) and from < to (400); non-UTC offsets
  are converted to UTC; wrapped contract {"trades": [...]} — empty result is
  200 with {"trades": []}, never 404
- process-wide trade-history provider cache in the composition root,
  mirroring market-data/account-info/positions (failed init not cached);
  blocking call routed through run_mt5_call

### Step 21A — User Role Model Evolution (super_admin / admin / customer)
Status: VERIFIED + COMMITTED (935f2a2)

Includes:

- UserRole evolved from (broker_admin, customer) to
  (super_admin, admin, customer) on the existing User table; no separate
  Admin table was created
- role semantics: super_admin manages admins and customers (broker-level
  owner/manager); admin manages customers only; customer has no
  user-management permissions
- exactly one super_admin per Broker enforced at the database layer by the
  partial unique index uq_users_broker_super_admin (broker_id WHERE
  role = 'super_admin'), declared for PostgreSQL and SQLite so the
  application tests exercise the real constraint (verified including raw
  SQL inserts)
- ck_users_role CHECK updated to the three-role domain (same pinned name)
- Alembic migration 7c41e2d9a5b0: data-first migration of existing
  broker_admin users to super_admin (nothing lost or duplicated), then the
  constraint swap and partial index; conservative downgrade maps
  super_admin/admin back to broker_admin
- get_current_broker_manager() (super_admin or admin) replaces
  get_current_broker_admin(); get_current_super_admin() added for future
  admin-management endpoints; the role remains authoritative from the
  database User record — no role claim in the JWT
- GET /users (Step 21, completed here): Broker-manager-only listing with
  role-based visibility — super_admin sees admins and customers of their
  broker, admin sees customers only; same-broker scope is structural
  (broker_id from the database-backed user; no broker_id parameter), the
  requesting manager is excluded, deterministic id-ascending order
- POST /users: super_admin/admin may create customer users only; the
  created role is forced to customer server-side; role/broker_id escalation
  attempts are rejected with 422
- development seed: the dev operator account is created as super_admin
- test coverage: role matrix, cross-tenant isolation, super-admin
  uniqueness (ORM-level and raw-SQL), creation paths, credential-exposure
  guards; full suite 224 passed

### Step 22 — Super Admin Creates Admin User
Status: VERIFIED + COMMITTED (cde617e)

Includes:

- POST /users/admins: super_admin-only admin creation, authorized by
  get_current_super_admin() (the dependency introduced in Step 21A)
- authorization matrix: unauthenticated → 401 (WWW-Authenticate: Bearer);
  customer → 403; admin → 403
- the created role is a server-side constant (UserRole.ADMIN), never a
  request field — no caller can create another super_admin or escalate
  through this endpoint
- broker_id is always derived from the authenticated database-backed
  super_admin record; a supplied broker_id field is rejected with 422;
  tenant isolation stays structural
- reuses CreateUserRequest validation (username/password/email/phone),
  bcrypt hashing, the credential-free UserResponse projection, and the
  generic tenant-scoped IntegrityError → 409 duplicate behavior
  (username/email/phone per broker; the one-super-admin partial index
  cannot fire here — only role='admin' rows are written)
- existing POST /users customer creation is unchanged (manager guard,
  customer-forced role, 422 on any role field)
- no delete/update/reset-password endpoints and no broker-management
  functionality were added
- 18 focused tests; full suite 242 passed

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

- UserRole StrEnum (originally broker_admin, customer; evolved to
  super_admin/admin/customer in Step 21A) on the User model
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

## Current Trade History Flow

Authenticated request
    ↓
get_current_user()
    ↓
run_mt5_call (blocking boundary, app/core/blocking.py)
    ↓
TradeHistoryService
    ↓
TradeHistoryProvider (abstraction)
    ↓
MT5TradeHistoryProvider
    ↓
MT5 (history_deals_get + related history orders)
    ↓
tuple[TradeHistoryEntry, ...]

All four MT5-backed endpoints route their blocking calls through the same
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

## Trade History API Contract

GET /trade-history?from=<UTC ISO datetime>&to=<UTC ISO datetime> returns the
wrapped response:

    {
      "trades": [
        {
          "ticket": 246802468,
          "order_ticket": 987654321,
          "symbol": "XAUUSD",
          "type": "BUY",
          "volume": 0.10,
          "price": 3648.20,
          "profit": 57.00,
          "time": "2026-09-14T12:30:00Z",
          "close_reason": "TP",
          "stop_loss": 3635.00,
          "take_profit": 3650.00
        }
      ]
    }

- from/to are required: missing or malformed → 422; naive (non-UTC-aware)
  → 400; from >= to → 400; non-UTC offsets are converted to UTC before the
  provider call.
- No executed trades in the window is a normal 200: {"trades": []} — never 404.
- close_reason is TP/SL/MANUAL/OTHER, or null when MT5 supplies no reason.
- stop_loss/take_profit come from the related closing order when it can be
  retrieved, otherwise null — never invented.

## Users API Contract

POST /users (super_admin or admin; creation of customer users only):

    {
      "username": "20002",
      "password": "application password",
      "email": "optional@example.com",
      "phone": "+12345678901"
    }

→ 201 with the non-sensitive projection:

    {
      "id": 3,
      "broker_id": 1,
      "username": "20002",
      "email": "optional@example.com",
      "phone": "+12345678901",
      "role": "customer",
      "is_active": true
    }

- role is always forced to customer server-side; extra="forbid" rejects any
  supplied role/broker_id with 422; tenant-scoped duplicates → 409.

POST /users/admins (super_admin only; creation of Admin users):

- same request/response shape as POST /users; the created role is the
  server-side constant "admin"
- customer/admin callers → 403; unauthenticated → 401; supplied
  broker_id/role fields → 422; tenant-scoped duplicates → 409
- broker_id comes only from the authenticated super_admin's database
  record, preserving tenant isolation

GET /users (super_admin or admin; role-based visibility):

    [ { ...UserResponse... }, ... ]

- super_admin: admins and customers of their own broker
- admin: customers of their own broker only
- the requesting manager is excluded; empty result is a normal 200 with []
- unauthenticated → 401; customer → 403
- password_hash and mt5_password_encrypted are structurally absent from
  every response; UserResponse exposes exactly: id, broker_id, username,
  email, phone, role, is_active

## Current Verified Facts

- Application authentication exists.
- Login endpoint exists.
- JWT access tokens exist.
- get_current_user() exists.
- Market-data, account-info, positions, and trade-history endpoints all require authentication.
- User broker_id comes from the database.
- MT5 password is separate from application password.
- No broker_id is trusted from JWT claims.
- No trading/order functionality exists.
- No BUY/SELL/OPEN/CLOSE/MODIFY functionality exists.
- MT5 providers remain read-only (verified by code inspection: no trading
  function exists anywhere in app/).
- All MT5 blocking calls (market-data, account-info, positions, trade-history) are kept
  outside the event loop through the consolidated run_mt5_call boundary.
- super_admin / admin / customer roles exist; exactly one super_admin per
  Broker is enforced by the database partial unique index
  (uq_users_broker_super_admin).
- POST /users lets a super_admin or admin create Customer Users in their own
  tenant (created role forced to customer); POST /users/admins lets a
  super_admin create Admin Users (server-side role, same tenant); GET /users
  provides role-based, tenant-scoped listing (super_admin: admins+customers;
  admin: customers).
- Account information (AccountInfo contract, MT5AccountInfoProvider, AccountInfoService, GET /account-info) exists and is read-only.
- Open positions (Position contract, MT5PositionProvider, FakePositionProvider, PositionService, GET /positions) exist and are read-only.
- Trade history (TradeHistoryEntry contract, MT5TradeHistoryProvider,
  FakeTradeHistoryProvider, TradeHistoryService, GET /trade-history) exists and is read-only.
- Steps 18/19/20/21A/22 are committed and pushed to origin/master (latest: cde617e).
- Test suite verified 2026-09-15 on the exact committed tree: pytest tests/ -q → 242 passed, 3 warnings.
- The 3 warnings are pre-existing third-party deprecation warnings (anyio
  PortalFactoryType and Pydantic class-based Config in app/core/config.py).
- compileall over app, tests, and scripts is clean.
- git diff --check is clean.
- Working tree is clean; the latest implementation commit (cde617e) has been pushed/synced to origin/master.

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

Steps 12–22 are complete, committed (cde617e), and synced to origin/master.

The next logical areas, in no committed order, are:

- tenant-scoped MT5 design (known issue 1)
- remaining known issues (IPC timeout, /health MT5 readiness, multi-worker
  semantics, candle UTC review, last_error robustness, Windows dependency,
  hygiene)

Do NOT implement any next step until explicitly instructed.

When instructed, begin by inspecting the existing provider abstractions
(app/providers/position.py, app/providers/trade_history.py,
app/providers/account_info.py, app/providers/market_data.py), the MT5
providers, the consolidated blocking boundary (app/core/blocking.py), and
the composition root (app/core/dependencies.py).

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
