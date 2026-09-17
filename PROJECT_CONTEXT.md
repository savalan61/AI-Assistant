# PROJECT_CONTEXT.md

## Documents and how to use them

`PROJECT_ROADMAP.md` is the **authoritative full project roadmap**: current
verified state, infrastructure gaps, MVP phases (P0–P8), post-MVP phases
(P9–P14), the MVP boundary, explicit out-of-MVP scope, the permanent
constraints and the standing risks. This document is the current project context
and points to it. `CURRENT_CHECKPOINT.md` records the exact latest
implementation/checkpoint status, verification results and commit state.

The full roadmap is not duplicated here.

Before planning or implementing a new phase:

1. read this file;
2. read the relevant section of `PROJECT_ROADMAP.md`;
3. inspect the actual codebase;
4. never assume a roadmap item is implemented — verify it in the code.

A roadmap phase is a plan, not a status: nothing in `PROJECT_ROADMAP.md` may be
treated as implemented until the code (and, for the recorded verification,
`CURRENT_CHECKPOINT.md`) confirms it.

## Project

AI Financial Assistant / AI Broker Assistant

## Product Goal

Build a production-oriented AI financial assistant for ONE broker.

**Product decision (2026-09-17): the Agent is for ONE BROKER ONLY.**
Multi-broker support is no longer a product requirement. The deployment is one
broker's assistant for its customers; the broker is the single `brokers` row,
resolved server-side, and no request can select a tenant.

The customer's own data must remain isolated from every other customer's: the
boundary that must hold is customer-to-customer, and it is pinned by
tests/test_customer_mt5_isolation.py.

The current MT5-based integration remains the current implementation. A future
architecture MAY replace MT5 with official Broker APIs if the broker provides
(a) an Account/Customer data API and (b) a Market Data/Price API — the existing
provider contracts are the seam. That is a recorded possibility, not an
implementation task.

Future client channels may include:

- Web Chat
- Telegram
- WhatsApp
- Mobile App

The backend and AI agent should remain channel-independent.

## Business Model

The ONE Broker is the operator of this deployment.

The Broker creates and manages application users (its customers).

Users do not self-register.

Application credentials and MT5 credentials are separate.

Current model:

- login = the user's single identity: the application/Agent login AND the MT5
  account/login number (one column since Step 42; there is no separate username);
  unique deployment-wide since the one-broker refactor)
- mt5_server = the BROKER's configuration (the one MT5 server; there is no
  per-user server column — the one-broker refactor dropped it)
- password_hash = Agent/application password
- mt5_password_encrypted = encrypted MT5 INVESTOR (read-only) password

The Broker initially supplies the credentials.

The Broker may reset an application password but should not see the user's current password.

## Core Entities

Current implemented core entities (database models):

- Broker
- User (super_admin / admin / customer)
- BrokerLLMConfig (the deployment broker's LLM provider, model, endpoint and
  encrypted API key; exactly one row, enforced by a unique constraint on
  broker_id against the single brokers row)

Current implemented provider contracts (not database models):

- AccountInfo
- Position
- Candle
- TradeHistoryEntry
- EconomicCalendarProvider (app/providers/economic_calendar.py; the wired
  implementations are development/test sources only — FakeEconomicCalendarProvider
  and the QuantGist free tier — chosen explicitly through
  ECONOMIC_CALENDAR_SOURCE (Step 46), whose production slot refuses until a real
  vendor is registered; neither development source is a production source, and
  see CURRENT_CHECKPOINT.md Known Issues item 9)
- the vendor-neutral LLM provider contract (app/providers/llm.py)
- NewsProvider (app/providers/news.py; the wired implementations are
  development/test sources only — AlphaVantageNewsProvider (Step 47A, the free
  News & Sentiment feed, selected only when its key is configured in
  development) and the deterministic no-network placeholder FakeNewsProvider —
  selected explicitly through NEWS_SOURCE (Step 47/47A), whose production slot
  refuses until a real vendor is registered; see CURRENT_CHECKPOINT.md Known
  Issues items 15 and 16)

Future/domain entities planned:

- Trade

The repository must be treated as the source of truth for the exact current model implementation.

## Broker

Broker is the deployment's single operator row (the one-broker refactor).

Current known fields:

- id
- name
- code
- mt5_server (the ONE MT5 server every customer's account lives on)
- is_active

## User

Current known fields:

- id
- broker_id (integrity reference to the single brokers row)
- login
- email
- phone
- password_hash
- mt5_password_encrypted
- is_active
- role (super_admin / admin / customer)

Important:

- login is BOTH the application/Agent login and the MT5 account/login number;
  it is a string so a leading zero survives, and it is unique deployment-wide
  (one broker ⇒ one login namespace). Credential resolution uses it directly
  when it is numeric, and fails closed at the session boundary when it is not.
- password_hash is the application/Agent password
- There is NO per-user mt5_server column (dropped by the one-broker refactor):
  the MT5 server is Broker.mt5_server, the one server, and no request can
  supply one. The MT5 account number is never provisioned separately either —
  it is the user's own login. Provisioning (PUT /users/{user_id}/mt5-credentials)
  writes ONLY the encrypted investor password.
- mt5_password_encrypted is the encrypted MT5 INVESTOR (read-only) password. The
  trading/master password is never requested, stored or used, a customer can
  neither provision nor read the credential, and no API returns it.
- role follows the three-role model (Step 21A): exactly ONE super_admin in the
  deployment (enforced by a database partial unique index over the role value
  itself), any number of admins, and customers. It was broker_admin/customer
  before that migration.

Customer isolation is customer-to-customer: every customer of the one broker
has its own account, and no request field can redirect a read to another
customer's.

## Trading Safety

The AI assistant is strictly READ-ONLY.

It must never:

- BUY
- SELL
- OPEN
- CLOSE
- MODIFY
- EXECUTE trades

The AI may analyze trading information but may not perform trading actions.

## Technology Stack

- Python
- FastAPI
- PostgreSQL
- Async SQLAlchemy
- Alembic
- JWT Authentication
- MetaTrader5
- AI/LLM
- Git/GitHub
- VS Code
- Freebuff

## Architecture

FastAPI Modular Monolith.

Primary structure:

core/
db/
api/
services/
providers/
tests/
scripts/
alembic/

(schemas/ and utils/ do not currently exist and must not be assumed.)

No microservices unless there is a future explicit architectural decision.

## Provider Architecture

The provider pattern is established for four MT5 data flows:

- MarketDataProvider: get_market_data(symbol) -> Candle
- AccountInfoProvider: get_account_info() -> AccountInfo (nine fields)
- PositionProvider: get_positions() -> tuple[Position, ...] (READ-ONLY)
- TradeHistoryProvider: get_trade_history(from, to) -> tuple[TradeHistoryEntry, ...] (READ-ONLY)
- InstrumentProvider: get_instrument(symbol) -> Instrument and
  list_instruments() -> tuple[Instrument, ...] (READ-ONLY, Step 50) — the
  broker's own symbol catalog, for discovery/resolution rather than trading

Each contract is a typed NamedTuple (Candle, AccountInfo, Position,
TradeHistoryEntry, Instrument, NewsItem) so raw MT5/vendor objects never cross
the provider boundary.

MT5MarketDataProvider, MT5AccountInfoProvider, MT5PositionProvider,
MT5TradeHistoryProvider and MT5InstrumentProvider implement the providers;
FakeMarketDataProvider, FakePositionProvider, FakeTradeHistoryProvider and
FakeInstrumentProvider back the tests.

The Instrument contract carries identity and metadata only (the broker's own
symbol spelling, description, broker-group asset class, base/quote currency,
digits, trade-mode availability): no price, position, relevance or fundamental
field. Symbol spelling is never re-cased — broker names are case-sensitive and
broker suffixes such as `XAUUSD.r` are part of the name — and an MT5
symbol lookup never mutates terminal state (no symbol_select). Resolution and
ordering are deterministic and belong to `InstrumentService`
(app/services/instruments), which resolves a symbol exactly first and then by a
unique case-insensitive match, lists/searches the catalog as a literal
case-insensitive substring over symbol and description, and caps a listing at  200 instruments while reporting `total` and `truncated`. A multi-name
resolution (`resolve_many`) applies those same rules against ONE catalog
discovery per operation, so a request naming several suffixed instruments reads
the broker's catalog once instead of once per instrument. A requested symbol
  that the broker does not list verbatim resolves through a unique
  case-insensitive match, and — since Step 52, extended in Step 54 — through a
  unique broker-DECORATED spelling of it (`XAUUSD` → `XAUUSD.r`, `XAUUSD.p`,
  `XAUUSD.` or the lowercase-tag `XAUUSDm`/`XAUUSDpro` forms), so a broker that
  decorates its whole catalog is usable. The rule is bounded and knows no
  suffix list: a candidate must be the requested name plus one of the three
  decoration forms (separator + empty/short tail, or a short alphabetic
  lowercase tag); a longer symbol, an uppercase undelimited tail, a digit-only
  tail, a partial name or several competing variants stay unresolved rather
  than being guessed.

The same inversion is used for the AI layer: LLMProvider (app/providers/llm.py)
is a vendor-neutral contract whose implementations are FakeLLMProvider,
FakeFreeLLMProvider and OpenAICompatibleLLMProvider, behind the LLMRouter /
provider-pool boundary.

Services delegate to the provider abstractions.

The market-data, financial-research and agent paths ask the InstrumentService
boundary to resolve a requested symbol into the broker's own spelling before
they read anything (Step 51/52/53): there is one resolution architecture, one
credential path and no duplicated catalog logic.

The API exposes them through the market-data, account-info, positions and
trade-history routes; all four are JWT-protected.

## MT5

MetaTrader5 is the trading-data provider.

Current package:

MetaTrader5==5.0.6180

MT5 operations can block.

Providers and services are deliberately synchronous.

All MT5-backed endpoints move the blocking operation outside the FastAPI
event loop through the consolidated blocking boundary run_mt5_call in
app/core/blocking.py (worker threadpool). Blocking work that is NOT MT5 uses a
separate boundary: run_llm_call, in the same module, runs the outbound model call
on the event loop's default executor — a different pool — so a request waiting on
a model never holds a worker thread an MT5 read needs.

The provider contains MT5-specific integration logic.

The application must not expose MT5 implementation details unnecessarily.

## Current MT5 Lifecycle Design

The current architecture includes:

- customer-scoped MT5 authentication: the request's own (server, login) identity is
  resolved from the authenticated database user and the ONE broker row (the
  server is the broker's own configuration), and the
  password is decrypted only inside the session boundary
- one process-wide MT5SessionManager (app/core/mt5_session.py) that owns the
  single global terminal connection: it holds the lock for a whole
  acquire → raw-read span, reuses the session when the customer already matches,
  switches accounts under that lock, and forgets the identity after any failed
  authentication or read
- per-request provider objects carrying one customer's credentials; the former
  process-wide provider caches are gone
- no startup warm-up (no customer exists at boot); the session is established
  lazily by the first authenticated MT5 request
- graceful shutdown releasing the single session from the FastAPI lifespan
- blocking MT5 calls through the consolidated run_mt5_call boundary
  (app/core/blocking.py), and the non-MT5 model call through the separate
  run_llm_call boundary in the same module (a different thread pool)

The MT5 Python API authenticates ONE account per process, so every MT5 read in
the process is serialized on that single terminal. That is the documented cost
of customer safety; the production answer (worker processes per account group)
remains future work.

### Confirmed process-global state

- the MetaTrader5 package is imported in exactly one module,
  app/core/mt5_session.py; no other module or provider touches it
- _mt5_session_manager (app/core/dependencies.py) is a process-local lazy
  singleton, created once under its own lock and shared by every request, and
  _active_key records the (server, login) most recently authenticated
- the session manager holds a threading.RLock for a whole acquire → raw-read
  span, so concurrent customer reads are serialized and an account switch can
  never land inside another customer's read (asserted by tests/test_mt5_session.py
  and tests/test_customer_mt5_isolation.py)
- account switching reuses the ONE terminal connection: first contact calls
  initialize(login, password, server), a customer with a different (server, login)
  calls login(...) on that same session. The Python API exposes no connection or
  session handle on any read function, so there are no independent per-account
  handles to multiplex inside one process
- one exclusive OS lock per terminal is held for the process lifetime
  (app/core/mt5_ownership.py), taken from the FastAPI lifespan before any MT5 work
  and released only after the session is closed, and a process that cannot take it
  refuses to start

### Deployment invariant: one process owns the MT5 terminal (ENFORCED)

The current architecture requires exactly ONE application process to own the MT5
session/terminal. The session lock is in-process only, so it protects nothing
across OS processes: two application processes pointed at the same terminal would
authenticate it independently and one could read the account the other switched
to. That invariant is now enforced by the application, not merely documented:

- app/core/mt5_ownership.py takes an exclusive OS-level file lock for the process
  lifetime. The kernel decides ownership, so there is no check-then-act window for
  two processes to race through, and the OS releases the lock when the process
  exits (including on a crash), so no stale lock is ever left for an operator to
  clear.
- The lock is per TERMINAL, keyed on MT5_TERMINAL_PATH: two deployments that pin
  different terminals do not block each other, while two processes that would
  drive the same terminal — including both leaving the path unset and letting the
  package find it — cannot both start. It lives in the OS temporary directory,
  which is per user, matching the terminal's own scope.
- Ownership is acquired from the FastAPI lifespan before any MT5 work and released
  last, after the session is closed, so no other process can start driving the
  terminal while this one still holds a connection to it.
- Failure is closed and explicit: MT5OwnershipError propagates out of the
  lifespan, so the process refuses to start rather than serve MT5 reads it cannot
  isolate. The message names the lock file and the setting to change, and carries
  no credential.
- This is a guard, not an architecture: no worker processes, no queue, no
  gateway, no distributed lock. Deliberately running several processes against
  several terminals stays roadmap work — the P8 topology decision, then P12 — and
  must not be implemented speculatively. A centralized MT5 gateway is not
  justified today.

### Session identity verification

The authenticated identity is verified, not trusted: before a read is served the
terminal itself must confirm the requested (server, login) through
`account_info()`. A mismatch (a broker-side re-login, a manual login at the
terminal, another process sharing the terminal) re-authenticates under the lock,
and an identity that still cannot be confirmed fails closed (503) and is
forgotten, so the next request authenticates from a clean `initialize()`.
Unreadable, absent or malformed account data counts as "not confirmed", never as
a match. The login number is compared exactly and the server case-insensitively,
with an absent server name falling back to the login match. Authentication is
bounded by an explicit IPC timeout, and an optional terminal executable pins
WHICH terminal is driven; both come from configuration (MT5_TERMINAL_PATH,
MT5_TIMEOUT_SECONDS) and are omitted when unset. The cost is one extra local
`account_info()` call per acquire. Customer-to-customer isolation through this
boundary is pinned end to end by tests/test_customer_mt5_isolation.py.

### Agent threading (two boundaries)

The agent boundary is two phases so the API can thread them separately.
`AgentService.prepare()` performs every blocking read (the MT5 financial context,
the calendar and the research window) and returns a prepared request;
`AgentService.respond()` renders the prompt from those resolved contexts (pure
CPU) and makes the single outbound model call, touching no MT5, no session and no
credential. `POST /agent` therefore runs `prepare` through run_mt5_call (the MT5
worker is released as soon as it returns) and `respond` through run_llm_call, so
a request waiting on the model holds no worker an MT5 read needs. `handle()`
remains as the synchronous composition of both, for callers that own their own
threading. The Broker LLM configuration connection tester's model probe runs
through the same run_llm_call boundary, so no model round trip anywhere in the
HTTP layer occupies an MT5 worker.

### Production risks (identified, NOT fixed)

- an accidental multi-process deployment breaks customer isolation (above)
- read calls other than authentication still have no timeout (the package
  exposes none per call), so a hung read still blocks the process-wide session
- one request still performs several lock acquisitions: every MT5 read acquires
  the session, so it pays its own identity verification, and one exact
  `symbol_info` lookup is needed per requested instrument name (only the terminal
  can say whether a spelling exists). The same-request duplicate READS that were
  safe to remove are gone: the agent reads positions once and scores calendar
  relevance against that same snapshot, research reuses the resolution it already
  performed, and a multi-instrument resolution discovers the broker's catalog
  ONCE per operation instead of once per instrument (`InstrumentService.resolve_many`).
  The market-data symbol-resolution read before the candle read is deliberately
  RETAINED: it is how the caller's spelling becomes the broker's own
  (``xauusd`` -> ``XAUUSD.r``), so removing it would break every suffixed catalog
  (CURRENT_CHECKPOINT.md known issue 24). A client-level answer no longer resets
  the cached session identity: the provider classifies the terminal's own reply
  (a symbol it says it does not offer, or no bars for the requested window, vs an
  IPC/transport failure), and the session boundary keeps the verified identity
  only for the former while a transport failure still drops it — every read,
  either way, confirms that identity with the terminal before it is served (known
  issues 32 and 33, resolved: the instrument and market-data providers each keep
  their own client/availability threshold rather than sharing one). The codes
  themselves were verified against the LIVE terminal, not only the installed
  package: an unattached read reports (-10004, 'No IPC connection'), a
  candle-read miss reports (-1, 'Terminal: Call failed'), and a symbol_info miss
  reports (-4, 'Terminal: Not found') — the last of which corrected the original
  instrument-side rule, and in every case the connection stayed usable
  immediately after the miss
- a customer whose stored server string does not match the server the terminal
  reports now fails closed. That is the point of the verification, but it is the
  one assumption not yet exercised against a real terminal

### Scaling direction (future work — NOT implemented)

The single-process topology is acceptable at the current stage. Account count
alone does not determine the scaling requirement; peak concurrent MT5 reads and
read duration do. Future scaling moves toward stateless FastAPI processes plus
dedicated Windows MT5 worker processes with account affinity; a
centralized MT5 gateway is not justified today. That is roadmap work (the P8
topology decision, then P12) and must not be implemented
speculatively. If the broker later offers official account/data and
market-data APIs, replacing MT5 with them is the other recorded direction —
a possibility, not a task.

Do not redesign it unless explicitly instructed.

## API

This is a partial, steadily growing list; CURRENT_CHECKPOINT.md is the
authoritative record of every endpoint and contract.

Current JWT-protected, read-only endpoints:

- GET /market-data/{symbol} → Candle response; since Step 53 the symbol is
  resolved through the same InstrumentService semantics the other surfaces use
  ("xauusd" reads the broker's "XAUUSD", and a catalog that only lists
  "XAUUSD.r" is read for "XAUUSD.r"); unknown or ambiguous → the existing 404,
  MT5/catalog unavailable → the existing 503
- GET /instruments → bounded instrument catalog from the authenticated customer's
  own MT5 terminal; {instruments, total, truncated}, extra optional fields are
  null when the broker omits them, an empty match is 200 with
  {"instruments": []}, and the query is a literal substring `search`
- GET /instruments/{symbol} → one resolved instrument in the broker's own
  spelling (a "xauusd.r" request resolves to "XAUUSD.r", and since Step 52 a
  "XAUUSD" request resolves to a unique suffixed spelling such as "XAUUSD.r"); unknown or blank
  symbol → 404, MT5 unavailable → 503, oversized input → 422. Generic for any
  broker symbol (equities, crypto, soft commodities, indices, suffixed FX/metal
  spellings); no symbol list is hardcoded.
- GET /account-info → AccountInfo response (nine fields)
- GET /positions → wrapped positions response; empty result is 200 with
  {"positions": []}, never 404
- GET /trade-history?from=<UTC ISO>&to=<UTC ISO> → wrapped trade response;
  empty result is 200 with {"trades": []}, never 404
- POST /users → manager-protected creation with an OPTIONAL explicit role
  (omitted/null → customer; "admin" requires a super_admin caller; "super_admin"
  is refused for every caller)
- POST /users/admins → super_admin-protected admin creation
- GET /users → role-based user listing (super_admin: admins + customers,
  never itself; admin: customers only)
- GET/PATCH/DELETE /users/{user_id} → super_admin-only single-user read, partial
  update and delete
- POST /agent, GET /economic-intelligence/today,
  GET /fundamental-intelligence/today?symbol=<optional instrument>,
  GET /financial-research/today?from=<UTC ISO>&to=<UTC ISO>&symbol=<instruments>
  (resolved against the caller's own broker catalog since Step 51: the broker's
  canonical symbols are echoed, an instrument the broker does not offer is a 404,
  an unavailable catalog or news source is a 503),
  GET /instruments and GET /instruments/{symbol} → the customer's own MT5
  instrument catalog,
  GET /portfolio-intelligence, GET/PUT /broker-llm-config → the read-only AI
  surface and broker LLM settings
- PUT /users/{user_id}/mt5-credentials → provision a user's MT5 investor
  (read-only) credential; admin: customers only, super_admin: any user in its
  broker. Write-only password, encrypted at rest, never returned.
- GET /users/{user_id}/mt5-credentials → safe credential metadata only
  (user_id, login, mt5_server, mt5_configured)

Plus unauthenticated infrastructure:

- POST /auth/login → JWT access token. Requires ONLY the user's login (the MT5
  account/login number) and the application password: the deployment's one
  broker is resolved server-side and `login` is unique deployment-wide, so
  nothing the request carries can select a customer (the one-broker refactor
  removed the former Step 43 `broker` field). The identity a request is
  authorized for still comes from the database User record, never from the
  token.
- GET /health → simple liveness probe (does not reflect MT5 readiness)

Expected error mapping currently includes:

- 401 for unauthenticated/invalid/expired tokens
- 403 for non-admin access to admin-only endpoints
- 404 when requested market data is unavailable (including a symbol no broker
  catalog entry resolves to, or one several suffixed variants make ambiguous)
- 409 for duplicate user creation conflicts
- 503 when an MT5-backed service is temporarily unavailable

## Security

Important security principles:

- customer isolation (customer-to-customer; the deployment has one broker, so
  cross-broker isolation is no longer the boundary — customer identity is:
  the one broker's server + each customer's own login, verified against the
  terminal before every read, pinned by tests/test_customer_mt5_isolation.py)
- one-broker binding: the broker is the single brokers row resolved
  server-side (none or several rows ⇒ fail closed), `login` is unique
  deployment-wide, every rejection at login answers identically, and the
  throttle counts failures per (client IP, submitted login) so one customer
  cannot lock another out
- secure credential handling
- no hard-coded credentials
- .env must not be committed
- no passwords in logs
- application password stored as a hash
- MT5 password stored encrypted
- read-only trading architecture

## Current Development Philosophy

Correctness > Simplicity > Speed

Prefer the smallest correct architectural change.

Do not implement future functionality early.

Do not add caching, queues, workers, microservices, AI orchestration, or other infrastructure merely because it may be useful later.

## Future Capabilities

Already implemented today:

- market data (resolved through the same instrument boundary since Step 53, so
  a case-insensitive or broker-suffixed request reads the broker's own spelling)
- account information (balance, equity, margin, free margin)
- open positions
- trade history
- instrument-resolved financial research (Step 51): research and the agent's
  research block grade only broker-confirmed instruments, in the broker's own
  spelling, through the same InstrumentService GET /instruments uses — one
  resolution architecture, no catalog logic in the research or agent layers, and
  no MT5 read by the research service itself (it holds the instrument service,
  not a provider)
- instrument discovery (Step 50): every instrument the broker's MT5 account
  offers is resolvable/discoverable through a vendor-neutral contract and a
  deterministic service, so no feature has to hardcode a symbol list. The
  contract is read-only and customer-scoped, works for arbitrary broker symbols
  (with or without a broker suffix), and holds no cache, database catalog,
  scheduler or ingestion. The XAUUSD/USOIL/NASDAQ fundamental relevance profiles
  remain optional intelligence enhancements layered on top of a resolved symbol,
  and — since the Step 54 relevance fix — the profiles document the Brent
  spellings (UKOIL, BRENT, and decorated forms such as UKOIL.) too, so a Brent
  position's calendar relevance comes from its documented factor tiers instead
  of failing closed for having no currency leg. A later read-only coverage audit
  of real broker catalogs extended the same table, as data only, with further
  verified spellings (USCRUDE, XBRUSD, BRENTUSD, UKBRAND for oil; NASDAQ100,
  USTECH, NDXUSD for the index), while futures/ETF tickers (CL, NQ, QQQ) are
  deliberately not roots and no unprofiled instrument was given a profile.
- portfolio intelligence (symbol exposure, directional balance, deterministic
  risk classification)
- financial context (one read-only context for future AI consumption)
- JWT authentication, three roles and user management
  (super_admin CRUD, admin manages customers)
- read-only AI agent (POST /agent) with a deterministic scope guard, a
  per-user daily limit, the deployment's encrypted LLM configuration and an LLM
  router with a free-pool fallback boundary
- economic intelligence composed into every agent request (Step 45: the agent
  receives today's economic calendar alongside the financial context and asks
  the model once; the calendar is mandatory, so a calendar-source failure is the
  existing generic 503 rather than a degraded answer)
- fundamental intelligence (Step 47): a vendor-neutral news source (development/
  test only — Alpha Vantage when its key is configured, otherwise the
  deterministic placeholder feed; no production vendor), deterministic news
  relevance built on the existing calendar classifier and, since Step 48,
  extended into instrument-aware relevance (a shared domain vocabulary plus
  static fundamental profiles for XAUUSD, USOIL/WTI and NASDAQ-100 that grade a
  match as direct, macro or indirect), today's fundamental
  context combining the mandatory calendar with relevant news and each open
  position's factual exposure (explicit UNKNOWN when it cannot be established),
  a JWT-protected GET /fundamental-intelligence/today endpoint, and that same
  context composed into every agent prompt; since the Step 49 follow-up the
  agent also receives the graded research context for the request's named
  instruments when one is present — the look-back window immediately before
  the calendar window, one shared relevance mechanism, bounded and provenance-
  labelled published facts
- graded financial research (Step 49): the same news/relevance architecture
  exposed as a reusable windowed context — a JWT-protected
  GET /financial-research/today endpoint taking an explicit UTC window and
  explicit focus instruments (for example XAUUSD,USOIL) and returning graded
  published-source news with provenance; unlike the fundamental context it
  holds no account, position or customer data, and it reuses the exact same
  classification so the two surfaces can never disagree. Since Step 51 every
  requested instrument is resolved against the authenticated customer's own MT5
  catalog first and only the broker's canonical spelling is graded — a name the
  broker does not offer is a deterministic 404 (`Instrument unavailable for the
  requested symbol`), never an unverified spelling researched anyway — and an
  instrument needs no fundamental profile to be researchable.
- instrument-resolved research in the agent (Step 51): when a request names a
  focus instrument, the agent resolves it through the same instrument service
  GET /instruments uses and researches only what the broker confirmed; if
  nothing resolves, no look-back research is fetched and the prompt is identical
  to a request without one, so the mandatory calendar/fundamental answer is
  never lost to an unconfirmed label.

Eventually the system may support:

- P&L analysis
- technical analysis
- deeper fundamental analysis (reports, historical comparison)
- economic calendar data beyond the development/test source (no production
  calendar provider exists yet; CURRENT_CHECKPOINT.md Known Issues item 9)
- real news beyond the deterministic development/test feed (no production news
  vendor is integrated; CURRENT_CHECKPOINT.md Known Issues item 15)
- daily reports
- natural-language interaction over additional channels (web/mobile/Telegram/WhatsApp)

These are future capabilities and must not be implemented ahead of the current checkpoint.
