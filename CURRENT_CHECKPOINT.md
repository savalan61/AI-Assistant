# CURRENT_CHECKPOINT.md

## Current Status

Step 39 — Live MT5 Investor-Password Verification
+ Step 39A/39B — configuration startup hardening & Pylance `_env_file` fix
+ Step 38 — MT5 Investor / Read-Only Credential Provisioning
+ Step 37 — Decimal Money & Financial Numeric Representation
+ maintenance — role-migration ordering fix & development user seed

Status:

VERIFIED + COMMITTED + SYNCED

Checkpoint commit:

The latest commit is "fix(config): harden environment settings loading"
(Steps 39A/39B and this document update), which carries the tolerant,
secret-safe settings loading and the statically visible env-file selection.
The prior synced commit was "feat(mt5): add investor credential provisioning"
(Step 38), which carried the per-user MT5 account fields, the provisioning
endpoints and the new migration; before that "fix(db): correct user role
migration ordering" (3a63af9), which carriedthe reordered role migration and the development user seed script; before that
the
Step 37 checkpoint ("feat(financial): harden numeric representation"), the Step
36 MT5 tenant-session commit, the Step 35 security hardening commit and b95eaa1
("feat(ai): add broker llm routing and agent controls", Steps 29–33).

Step 39 closed the one open verification from Step 38: with a real MT5 investor
credential available locally, a one-time live READ-ONLY session was performed —
explicit login with the investor password succeeded, AccountInfo.trade_allowed
is False, and account-info, positions, deal-history and rate reads all
succeeded. Only MT5 read APIs were touched; no trading operation exists or was
called. Steps 39A/39B harden configuration loading: an unrecognised key in the
env file no longer aborts startup (it is ignored and reported by NAME only) and
configuration errors never render a submitted value, while the dotenv file is
selected through model_config so the Pylance "No parameter named '_env_file'"
diagnostic is structurally impossible — with no type suppression anywhere.

Step 38 makes the MT5 credential an explicit, administrator-provisioned,
per-user fact — the MT5 account number, the MT5 server, and the encrypted
INVESTOR (read-only) password — instead of deriving the first two from the
application username and the broker row. A broker administrator, or the
broker's super_admin, writes them for a customer through a protected endpoint;
a customer can neither write nor read them, and no API ever returns the
password. The legacy derivation (numeric username + Broker.mt5_server) is
preserved as the fallback, so every pre-existing row keeps working unchanged.
There is still no master/trading-password support anywhere.

The maintenance fix corrects a real sequencing defect in the Step 21A role
migration: it ran the broker_admin → super_admin data rotation while the old
two-role CHECK constraint was still in place, so upgrading any database that
still held broker_admin rows aborted. The constraint is now dropped first. The
local development database was also brought to head and seeded with one
development account per role.

Step 37 replaced float money with exact Decimal arithmetic at the domain
boundary (the deferred "money representation" item). Balances, equity, margin,
prices, profit and volume are Decimal in every provider/domain contract and in
portfolio-intelligence aggregation, converted from MT5 floats with
Decimal(str(raw_value)) so no binary-float artifact enters the domain. Ratios
and non-money numbers deliberately stay float (account margin_level; candle
tick volume). The API wire format is unchanged: one shared serializer renders
Decimal as JSON numbers, so no existing client sees a different response shape.
No database migration was needed — these values are not persisted.

Test result at this checkpoint:

pytest tests/ -q → 735 passed, 3 warnings (pre-existing third-party
deprecation warnings); verified 2026-09-15 on this exact tree. The count is
unchanged from Step 36: Step 37 re-expressed existing expectations in Decimal
and added no new cases.

Additional Step 37 verification: a live serialization probe confirmed Decimal
fields render as JSON numbers (not strings), and a float/Decimal mixing scan
over app/ found no mixed arithmetic — the only remaining float() call is the
candle tick-volume conversion, by design.

Static verification: python -m compileall app tests alembic → clean.
git diff --check → clean.
Direct Pylance/pyright execution remains unavailable in this environment
(as recorded for Steps 8–36); a focused manual static/type review was
performed for Step 37 instead — every changed app file was read, an AST
unused-import scan ran over the changed modules, and one real typing defect
was found and fixed (MT5TradeHistoryProvider._protective_levels declared
tuple[float | None, float | None] while returning Decimals). No type
suppressions and no # type: ignore were added.

No trading functionality was added or changed in Step 37; all reads remain
strictly read-only and no order/position mutation of any kind exists. The AI
remains strictly READ-ONLY. No new issues were introduced by this step.

Working tree after this checkpoint:

CLEAN

Local HEAD and origin/master both point at this checkpoint commit.

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

### Step 23 — Economic Intelligence (READ-ONLY)
Status: VERIFIED + COMMITTED (21957c5)

Includes:

- EconomicEvent NamedTuple contract (event_id, timestamp, currency, title,
  impact, forecast, previous, actual) with the EventImpact StrEnum
  (LOW/MEDIUM/HIGH) and impact_meets_minimum(), plus the
  EconomicCalendarProvider abstraction — exported through
  app/providers/__init__.py
- the provider abstraction carries an explicit data-source provenance marker
  so a development/test source can never be mistaken for live financial data
- EconomicCalendarService: requested-window retrieval, today's (UTC day)
  events, minimum-impact filtering, chronological ordering, and
  timezone-aware validation (naive datetimes are rejected, not guessed)
- EconomicIntelligenceService: composes today's events with the
  authenticated user's open positions (read through the existing
  PositionService) and a deterministic, conservative relevance classifier —
  RELEVANT / POTENTIALLY_RELEVANT / NOT_OBVIOUSLY_RELEVANT — with a factual
  reason per (event, position) pair
- GET /economic-intelligence/today (authenticated; optional min_impact
  filter): broker_id is echoed from the database-backed user and never
  accepted as input; the blocking position read is routed through
  run_mt5_call; provider failure → generic 503
- no LLM is called: the response is structured AI-ready context, not
  generated analysis
- CALENDAR DATA SOURCE IS NOT FINAL: the wired provider is
  FakeEconomicCalendarProvider, a deterministic development/test
  placeholder. No production economic-calendar provider exists yet
  (see Known Issues item 9)

### Step 24 — Portfolio Intelligence (READ-ONLY)
Status: VERIFIED + COMMITTED (21957c5)

Includes:

- PortfolioIntelligence NamedTuple (as_of, account fields, position counts
  by direction, symbols held, total/buy/sell volume, directional balance,
  per-symbol exposure, risk assessment) plus SymbolExposure and
  RiskAssessment, in app/services/portfolio_intelligence/portfolio.py
- deterministic pure analysis: aggregate_exposure() groups open positions
  by symbol (buy/sell/net volume, position count) in symbol order;
  build_portfolio_intelligence() derives the whole result from one account
  snapshot plus one positions snapshot
- risk classification based only on data that actually exists (the account
  margin level): FLAT with no open positions, LOW/ELEVATED/HIGH bands
  otherwise, and an explicit UNKNOWN when positions exist but the margin
  level is not usable — no invented monetary exposure, leverage, market
  value, unrealized P&L or percentage exposure
- PortfolioIntelligenceService composes the existing AccountInfoService and
  PositionService; no MT5 access and no new provider
- GET /portfolio-intelligence (authenticated): broker_id from the
  database-backed user; blocking reads routed through run_mt5_call;
  provider failure → generic 503
- strictly read-only: no price prediction, no recommendation, no
  BUY/SELL/OPEN/CLOSE/MODIFY action

### Step 25 — Financial Context (READ-ONLY)
Status: VERIFIED + COMMITTED (67e8f76)

Includes:

- FinancialContext NamedTuple composing AccountInfo + Positions +
  TradeHistory + PortfolioIntelligence, plus broker_id and as_of
- FinancialContextService in app/services/financial_context/ composes the
  existing AccountInfoService, PositionService and TradeHistoryService; it
  adds no provider, no database access and no MT5 call of its own
- the trade-history window is configurable: build(broker_id,
  trade_history_days=30, now=None) — the default is 30 days
  (DEFAULT_TRADE_HISTORY_DAYS), any positive day count is accepted, and a
  value below 1 is rejected before any provider is touched
- the reference time is a single UTC-aware as_of (injected, else now); a
  naive datetime is rejected rather than read as local time
- the portfolio component is derived from the same account/positions
  snapshot the context reports (through the existing
  build_portfolio_intelligence analysis), so the context can never contain a
  portfolio view that disagrees with its own account or positions, and the
  account and positions are read exactly once per context
- broker_id is supplied by the caller from the authenticated database user;
  nothing in the service derives tenant identity from a request
- internal service/domain capability: no HTTP endpoint was added, and no LLM
  or agent logic exists
- 19 focused tests; full suite 359 passed

### Step 26 — AI Agent Boundary (READ-ONLY)
Status: VERIFIED + COMMITTED (ecfc800)

Includes:

- AgentService in app/services/agent/: a minimal internal agent boundary that
  orchestrates FinancialContextService and an injected LLMProvider — it
  touches no MT5, no database and no provider directly, and duplicates no
  financial-context logic
- handle(request, broker_id, trade_history_days=30, now=None): broker_id is a
  caller-supplied parameter from the authenticated database user (never
  derived from the request text); the trade-history window stays configurable
  with the existing 30-day default; the context read must be offloaded by an
  API caller through the existing run_mt5_call boundary
- AgentResponse NamedTuple: request (echoed verbatim), broker_id, context
  (the read-only FinancialContext the answer was resolved against) and
  answer (the provider's text, unchanged)
- prompt preparation lives in the agent layer (app/services/agent/prompt.py):
  build_prompt(request, context) renders a deterministic, provider-neutral
  LLMPrompt — only facts already present in the context, account identity
  (login/holder/server) deliberately omitted — so the provider never depends
  on FinancialContext and the agent never depends on a vendor format
- failures from the context service or the LLM provider propagate unchanged;
  the model is asked only after the context exists, so a failed read never
  reaches the provider
- read-only by construction: the class's only public capability is handle;
  there is no trading tool and no mutation
- internal service/domain capability: no HTTP endpoint was added
- 25 focused tests

### Step 27 — LLM Provider Abstraction (READ-ONLY)
Status: VERIFIED + COMMITTED (ecfc800)

Includes:

- LLMPrompt (instructions + content) and the LLMProvider ABC in
  app/providers/llm.py: one synchronous complete(prompt) -> str method —
  deliberately vendor-neutral and dependent on nothing from the app's
  financial, persistence, transport or MT5 layers (enforced by a test)
- FakeLLMProvider in app/providers/fake_llm.py: the current and only
  implementation — deterministic, offline, explicitly labelled a placeholder,
  records every prompt; no vendor SDK, HTTP client, API key or agent
  framework (LangChain/LangGraph or similar) was introduced
- provider failure is signalled by raising RuntimeError; providers never
  return partial or fabricated text on failure
- no real LLM is connected and no HTTP agent endpoint exists yet
- 10 focused tests; combined Steps 26+27 suite 394 passed

### Step 28 — Agent HTTP Endpoint (READ-ONLY)
Status: VERIFIED + COMMITTED (5506fa9)

Includes:

- POST /agent in app/api/agent_router.py: the single authenticated HTTP
  surface for the agent capability
- AgentRequest (extra="forbid"): message (required, min length 1) and optional
  trade_history_days (>= 1, default 30); there is no broker_id/user_id field,
  so a supplied one is a 422 — tenant scope cannot be redirected
- response: request (echoed verbatim), broker_id (from the authenticated
  database User), answer, and the read-only context (account, positions,
  trade_history, portfolio_intelligence) projected through explicit Pydantic
  schemas that mirror the existing endpoints
- account identity (login/holder/server) is omitted from the response, the
  same fields the LLM prompt deliberately excludes
- blocking MT5 work is offloaded through the consolidated run_mt5_call
  boundary; a RuntimeError from the MT5/LLM infrastructure becomes a generic
  503 and unexpected exceptions propagate
- no real LLM was connected at this step; the fake provider was injected
- 22 focused tests

### Step 29 — Real LLM Adapter
Status: VERIFIED + COMMITTED

Includes:

- OpenAICompatibleLLMProvider in app/providers/openai_compatible_llm.py: the
  production counterpart to FakeLLMProvider, implementing the existing
  LLMProvider contract against any server exposing the OpenAI-compatible
  POST {base_url}/chat/completions shape (vendor-agnostic: no vendor SDK)
- configuration from settings — LLM_API_KEY, LLM_BASE_URL, LLM_MODEL,
  LLM_TIMEOUT_SECONDS; nothing hard-coded, and the key is never logged or
  embedded in an exception message
- uses httpx, already present in the dependency tree (starlette's TestClient
  depends on it); the HTTP transport is injectable (httpx.MockTransport in
  tests), so the suite stays fully offline
- fail-closed construction: a blank/placeholder key or an empty model raises
  RuntimeError before any network call
- failure translation at the provider boundary into RuntimeError, preserving
  the existing API 503 behavior; a successful status with an unusable body is
  an error, never fabricated text; no payload or credential is echoed
- 28 focused tests

### Step 30 — LLM DI Wiring
Status: VERIFIED + COMMITTED

Includes:

- the production LLM composition seam (deps.get_llm_provider) selects the
  real adapter from Settings; the fake is never the production provider
- missing/invalid LLM configuration fails closed with 503 instead of silently
  degrading to a placeholder answer
- tests override the seam (deps.get_llm_provider) with the deterministic
  FakeLLMProvider, so no API test constructs the HTTP adapter or opens a
  socket
- AgentService remains provider-agnostic: it depends on LLMProvider only
- focused composition-root wiring tests

### Step 31 — Agent Scope Guard + Per-User Daily Limit
Status: VERIFIED + COMMITTED

Includes:

- app/services/agent/scope.py: check_scope(message) → ScopeDecision, a
  deterministic whitelist-first financial-scope classification (account,
  balance, equity, margin, positions, trades, P&L, risk/exposure, market,
  technical, fundamental, news, economic topics). No LLM is used to classify
  and no agent framework was introduced
- a small blocklist of clearly off-topic asks (image generation, jokes,
  story/movie/poem writing, generic coding, essays, translation, recipes,
  workouts); ambiguous short talk is allowed by design, and a financial
  question wins over an incidental off-topic word
- app/services/agent/usage.py: AgentUsageLimiter — a per
  (broker_id, user_id, UTC day) counter, in-process and lock-guarded; the
  daily maximum comes from AGENT_DAILY_REQUEST_LIMIT (settings, never
  hard-coded)
- enforced order: authenticated user → scope guard → usage limit →
  FinancialContextService → LLM; rejections happen before any context read or
  LLM call, and a rejected request consumes no quota
- out-of-scope request → 422 ("Request is outside the assistant's financial
  scope"); limit exceeded → 429 ("Daily agent request limit reached"), both
  generic and exposing no counters or internals
- in-process only: no Redis, no database table, no billing, no token
  accounting, no distributed limiting (see known issue 10)
- 21 scope tests + 13 usage tests, plus API integration tests

### Step 32 — Broker LLM Configuration (Super Admin)
Status: VERIFIED + COMMITTED

Includes:

- BrokerLLMConfig (app/db/models/broker_llm_config.py): one configuration per
  broker enforced by a database UNIQUE constraint on broker_id, plus a CHECK
  constraint allowing only implemented provider kinds; columns: provider kind,
  model, base_url, encrypted API key, is_active, created_at/updated_at
- Alembic migration b1f7c9d24e08_add_broker_llm_config (current single head),
  verified to render the correct PostgreSQL DDL and to apply and roll back
  cleanly on SQLite
- app/core/encryption.py: Fernet (cryptography) authenticated symmetric
  encryption — encrypt_secret / decrypt_secret / generate_encryption_key /
  EncryptionError. The key comes from SECRET_ENCRYPTION_KEY (environment) and
  is never hard-coded; the utility fails closed with no usable key; nothing
  logs, returns or echoes the key, plaintext or ciphertext. No crypto is
  hand-rolled
- GET /broker/llm-config (super_admin only) → provider, model, base_url,
  is_active, api_key_set, updated_at; the API key is never returned; 404 when
  no configuration exists for the authenticated broker
- PUT /broker/llm-config (super_admin only) → create-or-update upsert; a
  broker_id/role in the body is a 422; 503 when encryption is unavailable;
  409 on a concurrent-create constraint race
- POST /broker/llm-config/test (super_admin only) → status OK|FAILED with
  provider/model and a safe detail; 404 unset, 409 disabled, 503 undecryptable
  credential; the connection check runs through the existing LLMProvider
  boundary
- authorization: super_admin may manage its own broker's configuration;
  admin/customer → 403; unauthenticated → 401; broker_id is the authenticated
  super_admin's only, so cross-broker read/modify is impossible
- 42 focused tests (including encryption unit tests); no trading operation is
  involved

### Step 33 — LLM Router + Free LLM Pool
Status: VERIFIED + COMMITTED

Includes:

- LLMRouter (app/providers/llm_router.py): a broker-aware LLMProvider.
  Policy: a broker with an ACTIVE configuration always uses its own provider,
  and a failure there raises a safe RuntimeError and never silently consumes
  the shared free pool; a broker with no active configuration uses the free
  pool. The router performs selection only — it holds no credential, never
  logs or returns a key, and imports no FastAPI/SQLAlchemy/MT5 or financial
  logic
- LLMProviderPool (app/providers/llm_pool.py): an ordered provider pool that
  falls through to the next provider only on LLMFallbackError, stops
  immediately on any other RuntimeError, and raises one generic
  "no LLM provider is currently available" when exhausted (not itself
  fallback-eligible). An empty pool is valid and fails safely at call time
- LLMFallbackError(RuntimeError) in app/providers/llm.py: the single,
  explicitly designated fallback-eligible failure (transient only)
- adapter error classification: transport failures, timeouts, 429 and 5xx →
  LLMFallbackError; authentication (401/403), malformed body and empty text →
  plain RuntimeError
- resolve_broker_llm_provider
  (app/services/broker_llm_config/broker_llm_provider_resolver.py): resolves a
  broker's active provider by broker_id (from the authenticated user, never a
  request body); decrypts the stored key only here, only when needed; returns
  None when no active configuration exists; raises
  BrokerLLMConfigurationError when an active configuration cannot be used, so
  the caller fails safely instead of silently falling back
- production seam: get_llm_provider(broker_id, session) returns
  LLMRouter(broker_provider=resolved-or-None, free_pool=get_free_llm_pool());
  get_free_llm_pool() holds at most the deployment-level OpenAI-compatible
  endpoint from Settings and is empty until one is configured
- no real free-tier provider was integrated; FakeFreeLLMProvider (a
  deterministic offline test double) exercises the pool's ordering and
  fallback completely offline
- 33 focused tests (pool 9, router/resolver 14, composition-root wiring 10)

### Step 34 — Architecture & Production-Readiness Audit (READ-ONLY)
Status: AUDIT ONLY — no code, no commit

A read-only audit of the whole project (no file was created, modified,
deleted, renamed, staged or committed). It recorded, among other findings:

- the MT5 data plane has no tenant scoping (all brokers share one terminal
  account) — known issue 1, and the product blocker it implies
- an SSRF vector through the broker-supplied LLM base_url
- uncontrolled egress of customer financial data to third-party LLMs
- no login brute-force protection
- no token revocation; exp was verified-if-present rather than required
- broker suspension was enforced only at login
- the role-evolution migration can fail on duplicate per-broker admins
- money modelled as float throughout the domain contracts

Step 35 implemented the fixes the audit identified as required now; the
remaining findings stay in Known Issues and Next Step below.

### Step 35 — Security & Agent Hardening
Status: VERIFIED + COMMITTED

Includes:

- app/core/url_security.py: the single outbound-endpoint (SSRF) policy,
  validate_llm_base_url, provider-agnostic and applied both on write and
  immediately before an outbound request. Rejects non-http(s) schemes,
  embedded credentials, invalid ports, plaintext http outside development,
  localhost/loopback, link-local (including the 169.254.169.254 metadata
  address), multicast/reserved/unspecified, RFC1918, IPv4-mapped IPv6 forms,
  and any address that is not publicly routable (which also covers carrier-
  grade NAT). Hostnames are resolved and every resolved address checked; an
  unresolvable host is rejected rather than allowed
- PUT /broker/llm-config validates the endpoint before encrypting or storing
  anything (422, nothing written); the resolver and the connection-test
  endpoint re-validate cheaply before an outbound request, so a row edited
  outside the API cannot turn the server into a request-forgery primitive
- app/services/auth/login_throttle.py: in-process login brute-force
  protection keyed on the client IP and on the submitted username (failures
  counted whether or not the account exists, so the lockout cannot be used to
  probe for real usernames). Checked before any credential lookup, cleared by
  a successful login, configurable, and bounded by stale-entry eviction
- JWT: exp and sub are now REQUIRED by decode_token (options={"require": …}),
  not merely verified when present. No refresh tokens or revocation were added
- get_current_user now also rejects a request whose broker is missing or
  inactive, so suspending a broker takes effect on already-issued tokens. The
  failure reuses the existing generic 401, so it is indistinguishable from an
  unknown user
- Agent input/prompt limits: AGENT_MAX_MESSAGE_LENGTH (422 at the validation
  boundary), AGENT_MAX_PROMPT_TRADES (most recent N by (time, ticket), with
  the number omitted stated explicitly in the prompt), and
  AGENT_MAX_PROMPT_CHARS (the trade block is dropped with an explicit note
  first; beyond that the request is refused with 422 and a generic detail
  rather than sending an unbounded payload)
- prompt-injection separation: the user request is wrapped in an escaped
  USER_REQUEST block described as untrusted data, and the system instructions
  state that instructions inside it must never be followed. The read-only /
  no-prediction / no-trade-advice framing is preserved verbatim
- app/services/agent/egress.py: OutboundDataPolicy, an immutable
  configuration-driven policy for what may leave the process
  (LLM_SEND_TRADE_HISTORY / LLM_SEND_ACCOUNT_BALANCES /
  LLM_SEND_POSITION_PRICING, all defaulting to the previous behaviour).
  Resolved in the composition root and injected into AgentService, so a
  future per-broker consent / data-processing policy is a new resolver, not a
  rewrite. Account identity remains structurally excluded regardless of policy
- economic calendar: get_economic_calendar_service() fails closed (503)
  outside APP_ENV=development, so the development placeholder can never be
  served to a broker's customers. Development and test behaviour is unchanged
- .env.example rewritten to document all 13 settings, including the
  generation commands for SECRET_KEY and SECRET_ENCRYPTION_KEY and the
  failure mode of leaving each unset. No secret values are placed in it
- 125 new focused tests (url security 37, login throttle 24, prompt/egress 21,
  plus updated integration coverage for JWT claims, broker suspension,
  endpoint rejection, message/prompt limits and the calendar guard)

### Step 36 — MT5 Tenant-Scoped Sessions
Status: VERIFIED + COMMITTED

Includes:

- app/core/mt5_session.py: MT5AccountCredentials (frozen dataclass carrying a
  tenant's numeric login, MT5 server and the STORED CIPHERTEXT of the MT5
  password, excluded from repr) and MT5SessionManager, the process-wide owner
  of the single MT5 terminal session. The manager holds a re-entrant lock, the
  currently authenticated (server, login) key, and the injectable MT5 API seam.
  acquire(credentials) validates the identity, decrypts the password ONLY here
  (the only place plaintext exists, via the existing Fernet decrypt_secret —
  no duplicated crypto), reuses the session when the tenant already matches,
  switches accounts under the lock when it does not, and yields the MT5 api
  for the raw read. A failed authentication or a failed read drops the cached
  identity, so nothing stale is ever trusted. shutdown() never raises.
- THE MT5 PYTHON API LIMITATION, BY DESIGN: MetaTrader5 authenticates one
  account per process; two tenants' sessions cannot coexist. The manager makes
  the switch safe (lock held for the whole acquire → read span) instead of
  pretending independent sessions exist. Consequence, documented in the module:
  every MT5 read in the process is serialized on the one terminal.
- MT5SessionError extends RuntimeError, so every existing API maps an unusable
  tenant session to the established generic 503 with no new error mapping.
- Credentials resolution (app/core/dependencies.py, resolve_mt5_account_credentials
  and the get_mt5_credentials dependency): User.username (numeric) is the MT5
  login, user.mt5_password_encrypted the ciphertext, Broker.mt5_server the
  server — all from the authenticated database user, never from a request body,
  query parameter or token claim. Resolution never decrypts; an incomplete
  record (non-numeric username, missing server, missing/undecryptable password,
  missing broker) fails closed inside the session boundary with a message that
  never names which value was missing.
- The four process-wide provider caches (market-data, account-info, positions,
  trade-history) are REMOVED. Each provider is now a cheap per-request object
  constructed with the session manager and that request's tenant credentials;
  the only process-wide MT5 object is the session manager (lazy, lock-guarded,
  failed authentication never cached).
- app/providers/mt5_account_info.py, mt5_positions.py, mt5_trade_history.py,
  mt5_market_data.py: rewritten onto the session boundary — each read runs
  inside session_manager.acquire for the requesting tenant. All conversion,
  error-translation and read-only behaviour is unchanged; every response
  contract is unchanged.
- app/main.py lifespan: no startup warm-up (no tenant is authenticated at
  boot, so there is nothing to warm); the single terminal session is released
  once at shutdown via shutdown_mt5_session().
- run_mt5_call and the consolidated blocking boundary are unchanged; services,
  provider abstractions, fake providers and every API contract are unchanged.
- 36 net new tests (test_mt5_session.py 33: authentication on first connect vs
  account switch, no re-authentication for the same tenant, per-tenant session
  observation, concurrent-thread serialization with max-inside==1 asserted,
  incomplete/undecryptable/wrong-key/empty credentials failing closed without
  touching MT5, failed-login identity forgetting, shutdown safety, read-only
  surface, credential/ciphertext absence from every error and repr, DB-row
  resolution incl. non-numeric usernames and no-decryption-at-resolution;
  plus rewritten provider/lifecycle/API seam tests, including a tenant
  binding test per endpoint and a 503 path for an unusable tenant session)

### Step 37 — Decimal Money & Financial Numeric Representation
Status: VERIFIED + COMMITTED

Includes:

- Provider/domain contracts now carry exact financial values as Decimal:
  AccountInfo (balance, equity, margin, free_margin), Position (volume,
  open_price, current_price, profit), TradeHistoryEntry (volume, price, profit,
  stop_loss, take_profit — nullable semantics unchanged), Candle (open, high,
  low, close), and PortfolioIntelligence / SymbolExposure (total/buy/sell
  volume, net_volume, directional_balance).
- Intentionally retained float fields: AccountInfo.margin_level (a ratio that
  feeds the risk bands — not money) and Candle.volume (MT5 tick volume, a
  count). Candle.timestamp, ids, counts and strings are unchanged.
- MT5 → Decimal conversion happens only at the provider boundary, via
  Decimal(str(raw_value)) — never Decimal(raw_float), which would preserve the
  binary-float artifact the conversion exists to remove. The trade-history
  SL/TP zero-sentinel check is now Decimal-vs-Decimal; unset levels still
  become None rather than a fabricated 0.
- Portfolio intelligence arithmetic is entirely Decimal over Decimal — sums
  seed from Decimal(0), so no float enters an aggregate, a comparison or the
  directional balance. Aggregation and ordering behaviour is otherwise
  unchanged, including the UNKNOWN risk band when margin level cannot support
  a classification.
- API serialization is unchanged on the wire: app/api/numeric.py defines the
  single DecimalAsNumber = Annotated[Decimal, PlainSerializer(...float...)]
  used by every affected response model (account-info, positions,
  trade-history, market-data, portfolio-intelligence and the agent's embedded
  financial context), so Decimal renders as a JSON number rather than
  Pydantic's default string. The JSON transport remains approximate by nature
  (no JSON number represents 0.1 exactly); what Step 37 removes is inexactness
  INSIDE the application, leaving only the final display edge.
- No database migration (these values are not persisted), no new dependency,
  and no change to the MT5 session/tenant architecture, LLM/agent behaviour,
  authentication, roles, or any endpoint contract.
- Tests were updated to construct contracts with Decimal("...") literals. Raw
  MT5 payload fakes in the MT5 provider tests deliberately stay float — that is
  what MT5 returns — and are converted by the provider under test. Test count
  is unchanged at 735.

### Maintenance — Role-Migration Ordering Fix + Development User Seed
Status: VERIFIED + COMMITTED

Includes:

- alembic/versions/7c41e2d9a5b0_evolve_user_roles.py: upgrade() reordered so the
  old two-role CHECK constraint is dropped BEFORE the data rotation
  (broker_admin → super_admin); the new three-role CHECK and the partial unique
  super_admin index are created after it. Previously the UPDATE ran while
  ck_users_role still permitted only ('broker_admin', 'customer'), so upgrading
  any database that still held broker_admin rows aborted with a
  CheckViolationError. downgrade() and the revision identifiers are unchanged,
  no new migration was added, and the resulting schema and data are identical
  to the intended end state. The ordering requirement is now documented in the
  function docstring so it cannot be "tidied" back into the broken shape.
- Why this surfaced only now: no test exercises the Alembic chain (the test
  modules build their schema with Base.metadata.create_all / create_all), so the
  defect was reachable only on a real database — it appeared while provisioning
  the local development database, which still sat at 3f025a5d3b39 with a
  broker_admin row.
- scripts/create_dev_users.py (new, development-only, outside the app package):
  clears users (only with an explicit --clear) and then creates exactly three
  accounts on the EXISTING development broker — one per role
  (super_admin / admin / customer) — using the application's own password
  hashing, and verifies each account through the same credential path the login
  endpoint uses. It refuses to run unless APP_ENV=development and refuses to
  delete anything without --clear. The passwords are fixed, clearly marked
  development-only values (documented in the file); no real secret exists in it.
- Local development database brought to head (b1f7c9d24e08): the three-role
  CHECK domain and the one-super-admin partial unique index now exist, and the
  broker_llm_configs table was created by the existing migration.
- Verification for 3a63af9: the ordering semantics were reproduced on the real
  PostgreSQL engine using temporary tables that were rolled back — the old order
  raised CheckViolationError, the new order applied cleanly, and the partial
  unique index still rejected a second super_admin. End to end through the app,
  all three development accounts log in (200) and authorize correctly
  (super_admin and admin: GET /users 200 with role-scoped visibility, the
  super_admin not listing itself; customer: 403); wrong password and unknown
  username return an identical generic 401. The dev database holds exactly the
  three accounts and the broker row is untouched. pytest tests/ -q → 735 passed,
  3 warnings; compileall and git diff --check clean.

### Step 38 — MT5 Investor / Read-Only Credential Provisioning
Status: VERIFIED + COMMITTED

Includes:

- app/db/models/user.py: two additive nullable columns, mt5_login (String(32))
  and mt5_server (String(100)). The existing mt5_password_encrypted is now
  documented as the ciphertext of the MT5 INVESTOR (read-only) password. There
  is no master/trading-password field anywhere, and no API accepts one.
- alembic/versions/c4a91f2e6d77_add_user_mt5_credentials.py: additive migration
  (nullable columns, no backfill, nothing rewritten). Both directions were
  executed on the local PostgreSQL database: downgrade removes the two new
  columns and KEEPS the encrypted-password column, upgrade restores them, and
  the three development users were intact afterwards.
- app/core/dependencies.py: effective_mt5_login() and effective_mt5_server(),
  and a resolver built on them — the user's provisioned mt5_login/mt5_server
  win, with the legacy fallback of a numeric username and Broker.mt5_server
  preserved exactly, so no existing behaviour changed.
- app/api/users_router.py: PUT and GET /users/{user_id}/mt5-credentials. PUT
  upserts the three facts (the password is encrypted BEFORE anything is
  assigned, so an encryption failure writes nothing); GET reports safe metadata
  only — the effective mt5_login, the effective mt5_server and mt5_configured.
  Neither response model has a password field, so the plaintext cannot be
  echoed back by construction. UserResponse is unchanged, so the existing user
  list/create contracts and their exact-field tests are untouched.
- Authorization: both endpoints sit behind get_current_broker_manager
  (customers get 403 on both verbs). The target is resolved inside the caller's
  tenant: a user id belonging to another broker is reported exactly like a
  non-existent one (404) and is never modified. An admin may manage CUSTOMER
  credentials only (403 when the target is an admin or the super_admin); the
  super_admin holds broker-level privileges and may also provision an admin or
  itself.
- Secrets: the password is write-only, Fernet-encrypted at rest via the existing
  encrypt_secret (no new crypto, no new dependency), decrypted only inside
  MT5SessionManager.acquire, and excluded from MT5AccountCredentials.__repr__.
  Failures are generic (503 when encryption is unavailable) and no API, log or
  error message contains the credential. The agent prompt path is unchanged and
  omits account identity entirely (login, holder name, server, account number),
  so no credential can reach a model.
- Read-only safety: no trading function was added anywhere; the AI remains
  strictly READ-ONLY, and a test asserts the provisioning module references no
  MT5 trading function.
- 26 new tests (test_mt5_credentials_api.py): admin authorization, customer
  rejection on both verbs, an admin being unable to manage admin credentials,
  the super_admin managing an admin, cross-broker 404 with the target row proven
  unmodified, unknown id, encrypted at rest (not the plaintext, not containing
  it, and round-tripping through the real Fernet path), update replacing the
  previous credential, only the credential columns written, no password in any
  response text, fail-closed 503 with nothing written when the encryption key is
  absent, resolution precedence and legacy fallback, and repr masking of the
  ciphertext at the session boundary.

Not verified in this step (reported rather than hidden):

- A LIVE login with a real investor password was NOT performed: no MT5
  credential is available in this environment, and the application deliberately
  has nowhere to keep a real one in source. The compatibility conclusion comes
  from the installed MetaTrader5==5.0.6180 API surface: login() is the same call
  for either password type, the read functions never depend on trading
  permission, and the only read-only signal that exists is
  AccountInfo.trade_allowed (an investor password reports False; TerminalInfo.
  trade_allowed is merely the terminal's algo-trading switch and is NOT the
  credential's permission).
- Write-time proof that a submitted credential is read-only is NOT implemented
  (optional hardening, deliberately left out of this step): it would require an
  account_info() call inside the MT5 session boundary, which by design touches
  only initialize/login/last_error/shutdown today, and it would make
  provisioning depend on a live terminal.
- Provisioning fails closed with 503 on the local development database because
  SECRET_ENCRYPTION_KEY is not set there. That is the intended behaviour
  (nothing is written) and it was verified end to end through the real app.

### Step 39 — Live MT5 Investor-Password Verification (READ-ONLY)
Status: VERIFIED (no application code changed)

One-time live verification against the locally installed, running MT5 terminal
(MetaTrader5==5.0.6180, server ComplateCapitalTrade-Server), using a real
investor credential read from the local environment without ever printing,
logging or committing it. No application file was modified in this step.

- Explicit login with the investor password: mt5.login(login, password=<investor>,
  server=<server>) → True, last_error (1, 'Success').
- AccountInfo.trade_allowed == False with the investor credential — the
  read-only permission signal Step 38 predicted, now empirically confirmed.
- All four read families succeeded AFTER the explicit login (so the reads were
  not merely riding a pre-existing terminal session): account_info(),
  positions_get() (0 open), history_deals_get() over the trailing 7 days
  (7 deals), copy_rates_from_pos('EURUSD', H1) (rows returned).
- trade_mode == 2 (a REAL account). terminal_info().trade_allowed was False as
  well (the terminal's algo-trading switch, also off — belt and braces).
- Only initialize/login/terminal_info/account_info/symbol_info/symbols_get/
  positions_get/history_deals_get/copy_rates_from_pos/shutdown were called.
  No trading operation exists or was executed; the trading-safety scan over
  app/ stayed clean (the only ORDER_TYPE_* references are read-only direction
  mapping in mt5_positions.py).
- Caveat, reported rather than hidden: MetaTrader5's Python package uses the
  SAME login() call for either password type, so login success alone does not
  distinguish investor from master — the read-only proof is the
  trade_allowed == False flag on the authenticated account.
- Side finding that motivated Step 39A: the local .env held the credential
  under an unrecognised key, and pydantic-settings' extra_forbidden error —
  raised at import time — rendered the value verbatim. The app could not even
  start. (A step-39 probe reproduced this and unavoidably displayed the local
  value once; the credential should be treated as exposed and rotated.)

### Step 39A — Configuration Startup Hardening & Secret-Safe Errors
Status: VERIFIED + COMMITTED

- app/core/config.py: Settings moved off the deprecated class Config onto
  model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore"). An
  unrecognised key in the env FILE no longer aborts startup. This was the only
  safe choice on both axes: extra_forbidden necessarily renders the submitted
  VALUE (the disclosure mechanism), and the failure fires during module import
  (a total startup blocker). Typo detection is preserved — unrecognised keys
  are logged by NAME only via _warn_unknown_env_file_keys(); the value is never
  read.
- Process environment variables were never treated as settings inputs by
  pydantic-settings, so normal deployment environments (PATH, CI variables)
  are unaffected; that path is pinned by test.
- load_settings() builds Settings and, on ValidationError, raises RuntimeError
  rendered from error loc + type only (msg, input and input_value dropped),
  with 'from None' so the value-bearing original is never chained into the
  traceback. A bad value for a KNOWN setting (int or bool) fails closed with a
  message naming the setting and never the value — verified against str(exc)
  AND the full traceback.format_exception() output.
- tests/test_config_settings.py (new): module settings load for the real
  project configuration; unknown key tolerated and reported by name, never by
  value; comments/blank lines not misreported; known-field errors name the
  setting and omit the value in str and traceback; strict validation of known
  fields retained; valid values still read; missing env file fine; unrelated
  process env vars ignored.
- .env.example: documents that unrecognised keys are ignored and named-only in
  warnings, and warns against parking local credentials under undefined names.
- Side effect: leaving the deprecated class-based Config removed the Pydantic
  class-config deprecation — warnings 3 → 2.
- The real local .env was never modified, and is not tracked by git.

### Step 39B — Pylance `_env_file` Diagnostic (no type suppression)
Status: VERIFIED + COMMITTED

- Symptom: Pylance reported "No parameter named '_env_file'" on the previous
  Settings(_env_file=...) call. Root cause, established by runtime inspection
  of the installed package (pydantic 2.13.5 / pydantic-settings 2.15.0):
  Settings.__init__ IS BaseSettings.__init__ and its runtime signature DOES
  declare _env_file — but pydantic's @dataclass_transform makes type checkers
  synthesise __init__ from the model fields alone, hiding every injected
  pydantic-settings parameter. Any _env_file= keyword is therefore flagged,
  including one hidden behind a TypedDict splat; only an opaque
  dict[str, Any] would dodge the check, which would be a soft suppression.
- Fix: the dotenv file is selected through model_config instead —
  _settings_for_env_file() derives a Settings variant with
  SettingsConfigDict(env_file=..., extra="ignore") via type(), and
  load_settings(env_file=...) instantiates the plain Settings class on the
  default path (production settings identity unchanged: type(settings) is
  Settings). The file contains no _env_file= token at all, so the diagnostic
  is structurally impossible; this is pinned by a source-inspection test.
- No # type: ignore, no suppression, no checker configuration change.
- Focused tests grew 10 → 14 (override reads the requested file; default path
  keeps the base class; env_file=None drops only the dotenv source while still
  reading the process environment; the keyword form is absent from source).

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

## Current MT5 Credential Provisioning Flow

Authenticated request (PUT /users/{user_id}/mt5-credentials)
    ↓
get_current_user() → get_current_broker_manager()   (customer ⇒ 403)
    ↓
target resolved inside the caller's OWN broker (otherwise 404; never written)
    ↓
role rule: admin → customers only; super_admin → any user in their broker
    ↓
encrypt_secret(mt5_investor_password)   (failure ⇒ 503, nothing written)
    ↓
users.mt5_login / users.mt5_server / users.mt5_password_encrypted (ciphertext)

Then, on every MT5-backed read:

authenticated user
    ↓
get_mt5_credentials → effective login/server: the provisioned mt5_login and
    ↓                 mt5_server, else numeric username + Broker.mt5_server
MT5AccountCredentials (ciphertext only; masked in repr)
    ↓
MT5SessionManager.acquire → decrypt (only here) → initialize / login
    ↓
provider read (account info / positions / trade history / market data)

## Current Market Data Flow

Authenticated request
    ↓
get_current_user()
    ↓
get_mt5_credentials (User.username + mt5_password_encrypted + Broker.mt5_server, from the database)
    ↓
run_mt5_call (blocking boundary, app/core/blocking.py)
    ↓
MarketDataService
    ↓
MarketDataProvider
    ↓
MT5MarketDataProvider — inside MT5SessionManager.acquire(tenant credentials)
    ↓
MT5 (authenticated as the requesting tenant's account)
    ↓
Candle

## Current Account Information Flow

Authenticated request
    ↓
get_current_user()
    ↓
get_mt5_credentials (from the authenticated database user; never the request)
    ↓
run_mt5_call (blocking boundary, app/core/blocking.py)
    ↓
AccountInfoService
    ↓
AccountInfoProvider (abstraction)
    ↓
MT5AccountInfoProvider — inside MT5SessionManager.acquire(tenant credentials)
    ↓
MT5 (authenticated as the requesting tenant's account)
    ↓
AccountInfo

## Current Positions Flow

Authenticated request
    ↓
get_current_user()
    ↓
get_mt5_credentials (from the authenticated database user; never the request)
    ↓
run_mt5_call (blocking boundary, app/core/blocking.py)
    ↓
PositionService
    ↓
PositionProvider (abstraction)
    ↓
MT5PositionProvider — inside MT5SessionManager.acquire(tenant credentials)
    ↓
MT5 (authenticated as the requesting tenant's account)
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

All six MT5-backed endpoints route their blocking calls through the same
consolidated boundary (run_mt5_call); providers and services remain synchronous.

## Current Economic Intelligence Flow

Authenticated request
    ↓
get_current_user()
    ↓
run_mt5_call (blocking boundary, app/core/blocking.py)
    ↓
EconomicIntelligenceService
    ↓
EconomicCalendarService → EconomicCalendarProvider → FakeEconomicCalendarProvider
        (deterministic development/test placeholder — no production source yet)
    ↓
PositionService → PositionProvider → MT5PositionProvider → MT5
    ↓
deterministic relevance classification (relevance.py)
    ↓
AI-ready economic context

## Current Portfolio Intelligence Flow

Authenticated request
    ↓
get_current_user()
    ↓
run_mt5_call (blocking boundary, app/core/blocking.py)
    ↓
PortfolioIntelligenceService
    ↓
AccountInfoService + PositionService
    ↓
pure exposure analysis (portfolio.py)
    ↓
portfolio / exposure result + margin-based risk classification

## Current Financial Context Flow

caller (no HTTP endpoint yet)
    ↓
FinancialContextService
    ↓
AccountInfoService + PositionService + TradeHistoryService
    ↓
build_portfolio_intelligence (same snapshot)
    ↓
FinancialContext (account + positions + trade_history +
                   portfolio_intelligence + broker_id + as_of)

## Current Agent Flow

Authenticated user (broker_id comes from the database User, never the request)
    ↓
POST /agent (Agent API)
    ↓
Scope Guard — deterministic financial-scope classification (reject → 422)
    ↓
Usage Limiter — per-user daily quota, in-process (exceeded → 429)
    ↓
AgentService (app/services/agent/)
    ↓
FinancialContextService → FinancialContext (existing flows above)
    ↓
build_prompt(request, context) → LLMPrompt (agent layer; identity omitted)
    ↓
LLMRouter (broker-aware selection)
    ├── broker's own configured LLM (BrokerLLMConfig) when active
    │       └── failure → safe 503; never falls back to the free pool
    └── shared Free LLM Pool when the broker has no active configuration
            └── falls through only on LLMFallbackError; empty pool fails safely
    ↓
LLMProvider.complete(prompt) → answer text
    ↓
AgentResponse (request + broker_id + answer + context) → 200

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

## Economic Intelligence API Contract

GET /economic-intelligence/today?min_impact=LOW|MEDIUM|HIGH (optional) returns
today's (UTC day) events with per-position relevance:

    {
      "broker_id": 1,
      "as_of": "2026-09-15T07:08:54Z",
      "window_from": "2026-09-15T00:00:00Z",
      "window_to": "2026-09-16T00:00:00Z",
      "data_source": "fake-development-placeholder",
      "position_symbols": ["XAUUSD"],
      "events": [
        {
          "event": {
            "event_id": "...", "timestamp": "...", "currency": "USD",
            "title": "...", "impact": "HIGH",
            "forecast": null, "previous": null, "actual": null
          },
          "overall_relevance": "POTENTIALLY_RELEVANT",
          "positions": [
            {"ticket": 123456789, "symbol": "XAUUSD", "type": "BUY",
             "relevance": "POTENTIALLY_RELEVANT", "reason": "..."}
          ]
        }
      ]
    }

- data_source names the calendar provenance. It is currently
  "fake-development-placeholder": those events are deterministic
  development/test data, NOT live financial data.
- relevance is RELEVANT / POTENTIALLY_RELEVANT / NOT_OBVIOUSLY_RELEVANT. The
  classifier is conservative and does not assert RELEVANT from a symbol
  string alone; reasons are factual, never forecasts.
- invalid min_impact → 422; unauthenticated → 401; provider failure → 503.
- no BUY/SELL action, recommendation or price prediction is returned.

## Portfolio Intelligence API Contract

GET /portfolio-intelligence returns the combined account + exposure summary:

    {
      "broker_id": 1,
      "as_of": "2026-09-15T12:00:00Z",
      "account_currency": "USD",
      "balance": 10000.0, "equity": 10050.0,
      "margin": 250.0, "free_margin": 9800.0, "margin_level": 4020.0,
      "open_positions": 2, "buy_positions": 1, "sell_positions": 1,
      "symbols": ["EURUSD", "XAUUSD"],
      "total_volume": 1.1, "buy_volume": 0.1, "sell_volume": 1.0,
      "directional_balance": -0.9,
      "exposure": [
        {"symbol": "EURUSD", "buy_volume": 0.0, "sell_volume": 1.0,
         "net_volume": -1.0, "position_count": 1}
      ],
      "risk": {"level": "LOW", "basis": "..."}
    }

- directional_balance is BUY volume minus SELL volume across all open
  positions; exposure has one entry per symbol in symbol order.
- risk.level is FLAT (no positions) / LOW / ELEVATED / HIGH (margin-level
  bands) / UNKNOWN (positions exist but the margin level is not usable).
  risk.basis is a factual, deterministic reason — never a forecast.
- unauthenticated → 401; provider failure → 503.

## Financial Context (internal capability — no HTTP endpoint)

FinancialContextService composes the account snapshot, open positions, trade
history and portfolio intelligence into one typed FinancialContext for AI
consumption. It is intentionally an internal service/domain capability:
no endpoint exists. The trade-history window is configurable and defaults to
30 days. Tenant identity (broker_id) is supplied by the caller from the
authenticated database user. It remains the single source of financial data
for the agent layer.

## Agent / LLM Boundary (HTTP surface: POST /agent)

POST /agent is the single authenticated HTTP surface for the agent. The router
enforces the guard chain (scope → quota) before any context read or LLM call.
AgentService orchestrates FinancialContextService and an injected LLMProvider;
it computes no finance of its own. The LLMProvider contract is vendor-neutral
(one synchronous complete(prompt) -> str over a provider-neutral LLMPrompt).
The production provider is the broker-aware LLMRouter: a broker with an active
BrokerLLMConfig uses its own OpenAI-compatible provider, and a broker without
one uses the shared Free LLM Pool. Production selection is built from
configuration through the composition root; FakeLLMProvider (deterministic and
offline) remains the explicit test/development provider and is never the
production choice, and FakeFreeLLMProvider is the offline test double for the
pool. No agent framework (LangChain/LangGraph or similar) is used, and no
trading tool exists anywhere in the agent layer. Failures from the context
service or the provider propagate to the caller's boundary and become a
generic 503 — no credential, endpoint or upstream detail is leaked.

## Agent API Contract

POST /agent (any authenticated user) — request body:

    {"message": "What is my exposure?", "trade_history_days": 30}

→ 200:

    {
      "request": "What is my exposure?",
      "broker_id": 1,
      "answer": "...",
      "context": {
        "as_of": "2026-09-15T12:00:00Z",
        "account": {"currency": "USD", ...},
        "positions": [ ... ],
        "trade_history": [ ... ],
        "portfolio_intelligence": { ... }
      }
    }

- message is required (min length 1); trade_history_days is optional (>= 1,
  default 30); extra fields — including broker_id/user_id — are a 422, so
  tenant scope cannot be redirected.
- enforced order: scope guard → usage limit → context read → LLM.
- out-of-scope request → 422; daily limit exceeded → 429; unauthenticated →
  401; MT5/LLM infrastructure failure (RuntimeError) → 503 with a generic
  detail.
- broker_id is the authenticated database User's, never from the request.
- account identity (login, holder name, server) is omitted from the response.

## Broker LLM Configuration API Contract

Super Admin only, under /broker/llm-config; the API key is never returned:

- GET /broker/llm-config → 200

      {"provider": "openai_compatible", "model": "...", "base_url": "...",
       "is_active": true, "api_key_set": true, "updated_at": "..."}

  404 when no configuration exists for the authenticated broker.
- PUT /broker/llm-config → 200 (create or update, upsert); body
  {provider, model, base_url, api_key} — broker_id/role fields are a 422. The
  key is stored as Fernet ciphertext; 503 if encryption is unavailable; 409 on
  a concurrent-create constraint race.
- POST /broker/llm-config/test → 200
  {"status": "OK"|"FAILED", "provider": "...", "model": "...", "detail": "..."}; 404 unset,
  409 disabled, 503 undecryptable credential.
- authorization: super_admin → allowed; admin/customer → 403; unauthenticated
  → 401; a broker can only ever read or modify its own configuration.

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
- Economic intelligence (EconomicEvent/EventImpact contract,
  EconomicCalendarProvider, FakeEconomicCalendarProvider,
  EconomicCalendarService, EconomicIntelligenceService, the relevance
  classifier, GET /economic-intelligence/today) exists and is read-only; its
  calendar source is a deterministic placeholder, not live data.
- Portfolio intelligence (PortfolioIntelligence / SymbolExposure /
  RiskAssessment contracts, PortfolioIntelligenceService,
  GET /portfolio-intelligence) exists and is read-only.
- Financial context (FinancialContext contract, FinancialContextService with a
  configurable trade-history window defaulting to 30 days) exists as an
  internal read-only capability with no HTTP endpoint.
- Agent boundary (AgentService + AgentResponse in app/services/agent/) exists
  and is read-only: it orchestrates FinancialContextService and an injected
  LLMProvider.
- POST /agent exposes the agent over HTTP (authenticated; broker_id from the
  database User; no broker_id/user_id accepted in the body) and is read-only.
- Agent guard chain exists and is enforced in this order: scope guard
  (deterministic, no LLM) → per-user daily usage limit → context read → LLM.
- The agent scope guard rejects clearly non-financial requests (422) without
  reading the context or calling the LLM; the in-process, configuration-driven
  daily limit rejects over-quota requests (429) in the same way, and a rejected
  request consumes no quota.
- LLM provider boundary (LLMPrompt/LLMProvider in app/providers/llm.py) exists
  and is vendor-neutral; implementations are FakeLLMProvider (test/development),
  FakeFreeLLMProvider (offline test double for the pool) and
  OpenAICompatibleLLMProvider (production, settings-driven).
- A real LLM adapter exists and is configured from Settings (LLM_API_KEY,
  LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT_SECONDS); it fails closed when the key
  or model is missing/invalid and never logs or returns the key.
- Broker LLM configuration exists: one configuration per broker (database
  UNIQUE) with the API key encrypted at rest via Fernet; only the broker's
  super_admin can read or change it, and the key is never returned by any API.
- LLMRouter exists: a broker with an active configuration uses its own
  provider, and a broker failure never silently falls back to the free pool;
  a broker with no active configuration uses the shared Free LLM Pool, whose
  providers fall through only on LLMFallbackError.
- The Free LLM Pool is currently empty until a deployment-level
  OpenAI-compatible endpoint is configured; no real free-tier provider has been
  integrated yet (see known issue 11).
- No agent framework (LangChain/LangGraph or similar) is used, and no trading
  tool exists in the agent or LLM layer.
- No price prediction, BUY/SELL recommendation or trading action is produced
  by any intelligence endpoint or the agent.
- Broker LLM base_url is validated against SSRF on write and before every
  outbound request; loopback, private, link-local, metadata and non-publicly-
  routable destinations are refused, and https is required outside development.
- JWT access tokens must carry exp and sub; get_current_user rejects inactive
  users AND inactive/suspended brokers with the same generic 401.
- Login is brute-force throttled in-process, per client IP and per submitted
  username, and the lockout is identical for existing and unknown usernames.
- Agent input is bounded (message length at the validation boundary; trade
  block capped with the omission count stated; total prompt size guarded), and
  the user's text is separated from system framing as untrusted data.
- Outbound LLM data is governed by an explicit, configurable
  OutboundDataPolicy resolved at the composition root; account identity is
  never sent regardless of policy.
- The development economic calendar fails closed outside APP_ENV=development.
- Money, price and volume fields are Decimal in every provider/domain contract
  and in portfolio aggregation (exact arithmetic), converted at the MT5 boundary
  with Decimal(str(...)); account margin_level and candle tick volume
  intentionally remain float. API JSON still exposes numbers, not strings, via
  the shared DecimalAsNumber serializer.
- Steps 18–39B plus the role-migration ordering fix and the development user
  seed are committed and pushed to origin/master (latest commit: "fix(config):
  harden environment settings loading"; the prior synced commit was "feat(mt5):
  add investor credential provisioning" (Step 38), before that "fix(db):
  correct user role migration ordering" (3a63af9), the Step 37 checkpoint
  "feat(financial): harden numeric representation", the Step 36 MT5
  tenant-session commit, the Step 35 security hardening commit and b95eaa1).
- A user carries its own MT5 identity: mt5_login and mt5_server (nullable,
  explicit) plus mt5_password_encrypted holding the ciphertext of the MT5
  INVESTOR (read-only) password. An admin may provision these for a customer and
  a super_admin for any user in its broker; a customer can neither provision nor
  read them, and no endpoint ever returns the password. The legacy derivation
  (numeric username + Broker.mt5_server) still applies when the explicit fields
  are NULL, so pre-Step-38 rows behave exactly as before. No master/trading
  password is accepted or stored anywhere.
- The development database (local PostgreSQL, APP_ENV=development) is at
  migration head and holds exactly three development accounts on the existing
  developer Broker: one per role (super_admin / admin / customer), created by
  scripts/create_dev_users.py with documented development-only credentials.
  These usernames are non-numeric and the broker has no mt5_server, so the
  MT5-backed endpoints answer 503 for them by design (the tenant session fails
  closed); they exercise authentication, roles and the agent surface. Their MT5
  credentials are unprovisioned, and provisioning them there currently returns
  503 as well because SECRET_ENCRYPTION_KEY is not set in the local environment
  — the fail-closed path, verified end to end.
- The MT5 INVESTOR (read-only) password was verified LIVE (Step 39) against a
  real account on the locally installed terminal: explicit login succeeded,
  AccountInfo.trade_allowed is False, and account-info, positions, deal-history
  and rate reads all succeeded afterwards. No trading operation was called.
- Configuration loading is hardened (Steps 39A/39B): an unrecognised env-file
  key is ignored and reported by name only (startup no longer aborts), a
  validation failure raises a sanitized RuntimeError that names the offending
  setting and never renders its value (in str and full traceback), the dotenv
  file is selected through model_config (the Pylance _env_file diagnostic is
  structurally impossible), and the deprecated class-based Config is gone.
- Test suite verified 2026-09-15 on this exact tree: pytest tests/ -q → 775 passed, 2 warnings.
- The 2 warnings are pre-existing third-party deprecation warnings (anyio
  PortalFactoryType and starlette testclient). The former Pydantic class-based
  Config warning was eliminated by Step 39A.
- compileall over app, tests, and scripts is clean.
- git diff --check is clean.
- Working tree is clean; this checkpoint commit has been pushed/synced to
  origin/master (local HEAD == origin/master).

Static/type verification:

Direct Pylance/pyright execution was not available in the environment for any of
Steps 8–39B (no mypy/pyright/basedpyright/pytype is installed either). Manual
static/type reviews were performed instead; Step 39B's fix additionally removed
the flagged construct from the source entirely and pinned that with a test.
This limitation must be reported rather than hidden.

## Known Issues (current)

1. MT5 reads are serialized process-wide on the ONE terminal session. This is
   inherent to the MetaTrader5 Python API (one authenticated account per
   process) and is now the explicit, documented tenant-safety design (Step 36):
   the session manager holds a lock for every acquire → read span, so a tenant
   switch can never land inside another tenant's read. Cross-tenant data
   leakage is impossible, but MT5 throughput is a process-wide bottleneck; the
   future production answer (one MT5 worker process per broker, or equivalent)
   remains future work.
2. MT5 IPC timeout is not implemented.
3. /health does not currently represent MT5 readiness.
4. Multi-worker deployment semantics need future documentation/design.
5. Candle timestamps need future UTC review.
6. MT5 last_error handling has a minor robustness concern.
7. MetaTrader5 is currently a Windows-specific dependency and needs future CI/Docker consideration.
8. Minor cleanup/deprecation/hygiene items remain (.gitignore entries such as
   .pytest_cache/; the Pydantic class-based Config deprecation was resolved by
   Step 39A).
9. Economic calendar data source is unresolved. Economic intelligence is wired
   to FakeEconomicCalendarProvider, a deterministic development/test
   placeholder; there is NO production economic-calendar provider, and its
   responses must not be presented as live financial data. MT5's Python
   integration (MetaTrader5==5.0.6180) does not expose the MQL5 Economic
   Calendar API at all (verified by introspection), and the evaluated
   third-party free tiers either gate the calendar behind a paid plan or
   restrict the free/personal tier to non-commercial use — unsuitable for a
   commercial multi-tenant product. The provider abstraction is in place and
   ready for a suitable source; selecting one is an open architectural
   decision. (This is a new numbered item; the former item 9, the MT5
   blocking-call debt, remains resolved below.)
10. Agent usage limiting is in-process only: a lock-guarded counter in this
    process's memory, keyed by (broker_id, user_id, UTC day). Counters reset on
    restart and are not shared across workers. This is intentional for the
    current single-process development architecture; distributed production
    limiting is future work and must not be added (no Redis, no database
    limiter) without an explicit decision.
11. The shared Free LLM Pool is empty until a deployment-level OpenAI-compatible
    endpoint (LLM_API_KEY/LLM_MODEL) is configured, and no real free-tier
    provider has been integrated. With neither a broker configuration nor a
    deployment endpoint, POST /agent fails safely with 503. Provider pool
    ordering, health and quota monitoring are future work.
12. Broker LLM configuration has no audit trail (who changed what, and when)
    and no API-key rotation flow (re-encrypting existing rows under a new key).
13. Residual limitations of the Step 35 hardening, each a deliberate trade-off:
    - DNS rebinding between configuration write and request time is only partly
      mitigated. The write-time DNS check plus the call-time literal/address
      re-check are in place, but the resolved address is not pinned for the
      request lifetime (pinning would require replacing the HTTP client's
      connection handling).
    - Login throttling is in-process and per worker, and its lockout is keyed on
      the submitted username, so an attacker flooding one username can lock
      that user out for the configured window. The window is short and
      configurable.
    - The real Free LLM Pool and real economic-calendar source are still absent
      (items 9 and 11), so a non-development deployment refuses those
      capabilities (503) instead of degrading.

Resolved:

- (Former item 9) MT5 blocking-call technical debt — RESOLVED by Step 19.
  All MT5-backed endpoints (account-info, positions, market-data) now execute
  their synchronous service calls off the event loop through the consolidated
  run_mt5_call boundary in app/core/blocking.py.
- The market-data endpoint now requires authentication (Step 11).
- (Audit finding) SSRF through the broker-supplied LLM base_url — RESOLVED by
  Step 35 (validated on write and before every outbound request).
- (Audit finding) No login brute-force protection — RESOLVED by Step 35.
- (Audit finding) exp was verified-if-present rather than required; no token
  could be assumed to carry an expiry — RESOLVED by Step 35.
- (Audit finding) Broker suspension was enforced only at login — RESOLVED by
  Step 35.
- (Audit finding) Agent request and prompt size were unbounded — RESOLVED by
  Step 35.
- (Audit finding) The development economic calendar could be selected outside
  development — RESOLVED by Step 35 (fails closed with 503).
- (Audit finding) What may leave the process toward an external LLM was
  implicit in the prompt template — RESOLVED by Step 35 (explicit
  OutboundDataPolicy).
- (Deferred item) Money representation: balances, equity, profit and volume
  were float throughout the provider contracts — RESOLVED by Step 37 (Decimal
  end to end, JSON numbers preserved on the wire).
- (Deferred item) MT5 credential provisioning UX — RESOLVED by Step 38: the
  MT5 account number, server and encrypted investor password are now
  administrator-provisioned per user through a protected, tenant-scoped API
  instead of being seeded directly into the database.
- (Maintenance defect) The role-evolution migration rotated broker_admin rows
  to super_admin before dropping the old two-role CHECK constraint, so it
  aborted on any database still holding broker_admin rows — RESOLVED in 3a63af9
  (constraint dropped first; the same end state, with no new migration and no
  change to downgrade()).

These issues are known and must NOT be fixed automatically.

They should be addressed one controlled stage at a time.

## Next Step

Steps 12–39B plus the role-migration ordering fix and the development user seed
are complete, committed, and synced to origin/master (latest commit:
"fix(config): harden environment settings loading").

The following are DEFERRED FUTURE WORK only. None of them is implemented, and
none may be started without an explicit instruction:

- investor-only credential verification: prove — at write time or at session
  acquisition — that a provisioned credential really is the read-only one, using
  AccountInfo.trade_allowed. Deliberately NOT implemented in Step 38 (it would
  add an account_info() call to a session boundary that today touches only
  initialize/login/last_error/shutdown, and would make provisioning depend on a
  live terminal).
- live MT5 investor-password verification: DONE in Step 39 (explicit login
  with a real investor credential succeeded, trade_allowed == False, all read
  families succeeded). The remaining open half is write-time investor-only
  verification, below.
- market-data multi-tenant semantics: market data is now tenant-scoped like
  every other read; whether a shared read-only market-data feed (no customer
  account needed) should be carved out is an open product decision
- real free LLM provider integrations: wire one or more genuine free-tier
  OpenAI-compatible providers into the shared pool (the abstraction is ready;
  the pool is empty today)
- free-provider pool configuration/order: make the pool's membership and order
  operator-configurable rather than a single deployment endpoint
- provider health/quota monitoring, and automatic quota/credential status:
  report sanely — without leaking secrets — whether a provider is usable
- distributed production usage limiting: replace the in-process agent limit
  with a shared mechanism once multi-worker/production deployment is designed
  (known issue 10)
- LLM configuration audit trail: record who changed a broker's LLM settings and
  when (known issue 12)
- API-key rotation: a controlled re-encryption flow for stored broker
  credentials (known issue 12)
- the economic calendar production data source (known issue 9) — still required
  before economic intelligence can carry real data
- prompt safety screening before generation (deterministic refusal of
  trading-instruction requests) — a deliberate decision, not yet started
- observability foundation: request IDs, structured logging, and a real
  readiness endpoint that reports database / MT5 / LLM availability separately
  from liveness (today /health is an unconditional ok)
- role-migration pre-flight: the broker_admin→super_admin migration aborts on
  the unique partial index if any broker holds more than one broker_admin (the
  CHECK-ordering half of this defect was fixed in 3a63af9; the duplicate-row
  hazard remains, and a clear pre-flight error instead of a raw uniqueness
  failure is still wanted)
- login tenant discriminator: usernames are unique per broker but login matches
  on username alone, so a cross-tenant collision currently makes a user
  unloggable
- CORS with an explicit origin allowlist, plus a coarse global rate limit
  (only login and the per-user agent quota are throttled today)
- trade-history N+1 MT5 IPC calls: each closing deal triggers a separate
  history_orders_get round-trip
- remaining known issues (IPC timeout, /health MT5 readiness, multi-worker
  semantics, candle UTC review, last_error robustness, Windows dependency,
  hygiene, and the residual Step 35 trade-offs in known issue 13)

Do NOT implement any next step until explicitly instructed.

When instructed, begin by inspecting the existing provider abstractions
(app/providers/position.py, app/providers/trade_history.py,
app/providers/account_info.py, app/providers/economic_calendar.py,
app/providers/llm.py, app/providers/llm_pool.py, app/providers/llm_router.py,
app/providers/openai_compatible_llm.py, app/providers/market_data.py), the MT5
providers and the tenant session boundary (app/core/mt5_session.py),
the consolidated blocking boundary (app/core/blocking.py), the
intelligence services (app/services/economic_intelligence/,
app/services/portfolio_intelligence/, app/services/financial_context/), the
agent boundary and its guard chain (app/services/agent/, including egress.py),
the outbound-endpoint policy (app/core/url_security.py), the login throttle
(app/services/auth/), the broker LLM configuration service and resolver
(app/services/broker_llm_config/), and the composition root
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
