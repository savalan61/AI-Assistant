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

Build a production-oriented B2B AI financial assistant for brokers.

The Broker provides the assistant to its customers.

Future client channels may include:

- Web Chat
- Telegram
- WhatsApp
- Mobile App

The backend and AI agent should remain channel-independent.

## Business Model

The main tenant is Broker.

A Broker creates and manages application users.

Users do not self-register.

Application credentials and MT5 credentials are separate.

Current model:

- login = the user's single identity: the application/Agent login AND the MT5
  account/login number (one column since Step 42; there is no separate username)
- mt5_server = the user's MT5 server (administrator-provisioned)
- password_hash = Agent/application password
- mt5_password_encrypted = encrypted MT5 INVESTOR (read-only) password

Broker initially supplies the credentials.

A Broker may reset an application password but should not see the user's current password.

## Core Entities

Current implemented core entities (database models):

- Broker
- User (super_admin / admin / customer)
- BrokerLLMConfig (a broker's own LLM provider, model, endpoint and encrypted
  API key; exactly one row per broker, enforced by a unique constraint on
  broker_id)

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

Broker is the tenant root.

Current known fields:

- id
- name
- code
- mt5_server
- is_active

## User

Current known fields:

- id
- broker_id
- login
- email
- phone
- password_hash
- mt5_server
- mt5_password_encrypted
- is_active
- role (super_admin / admin / customer)

Important:

- login is BOTH the application/Agent login and the MT5 account/login number;
  it is a string so a leading zero survives. Credential resolution uses it
  directly when it is numeric, and fails closed at the session boundary when it
  is not.
- password_hash is the application/Agent password
- mt5_server holds the user's MT5 server. A broker administrator provisions it
  (admin: customers only; super_admin: any user in its broker) through
  PUT /users/{user_id}/mt5-credentials. It is nullable: when NULL, credential
  resolution falls back to Broker.mt5_server. The MT5 account number is never
  provisioned separately — it is the user's own login.
- mt5_password_encrypted is the encrypted MT5 INVESTOR (read-only) password. The
  trading/master password is never requested, stored or used, a customer can
  neither provision nor read the credential, and no API returns it.
- role follows the three-role model (Step 21A): exactly one super_admin per
  Broker (enforced by a database partial unique index), any number of admins,
  and customers. It was broker_admin/customer before that migration.

User tenant ownership is defined by broker_id.

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
case-insensitive substring over symbol and description, and caps a listing at
200 instruments while reporting `total` and `truncated`.

The same inversion is used for the AI layer: LLMProvider (app/providers/llm.py)
is a vendor-neutral contract whose implementations are FakeLLMProvider,
FakeFreeLLMProvider and OpenAICompatibleLLMProvider, behind the LLMRouter /
provider-pool boundary.

Services delegate to the provider abstractions.

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
app/core/blocking.py (worker threadpool).

The provider contains MT5-specific integration logic.

The application must not expose MT5 implementation details unnecessarily.

## Current MT5 Lifecycle Design

The current architecture includes:

- tenant-scoped MT5 authentication: the request's own (server, login) identity is
  resolved from the authenticated database user and the broker row, and the
  password is decrypted only inside the session boundary
- one process-wide MT5SessionManager (app/core/mt5_session.py) that owns the
  single global terminal connection: it holds the lock for a whole
  acquire → raw-read span, reuses the session when the tenant already matches,
  switches accounts under that lock, and forgets the identity after any failed
  authentication or read
- per-request provider objects carrying one tenant's credentials; the former
  process-wide provider caches are gone
- no startup warm-up (no tenant exists at boot); the session is established
  lazily by the first authenticated MT5 request
- graceful shutdown releasing the single session from the FastAPI lifespan
- blocking MT5 calls through the consolidated run_mt5_call boundary
  (app/core/blocking.py)

The MT5 Python API authenticates ONE account per process, so every MT5 read in
the process is serialized on that single terminal. That is the documented cost
of tenant safety; the production answer (one MT5 worker process per broker)
remains future work.

Do not redesign it unless explicitly instructed.

## API

This is a partial, steadily growing list; CURRENT_CHECKPOINT.md is the
authoritative record of every endpoint and contract.

Current JWT-protected, read-only endpoints:

- GET /market-data/{symbol} → Candle response
- GET /instruments → bounded instrument catalog from the authenticated tenant's
  own MT5 terminal; {instruments, total, truncated}, extra optional fields are
  null when the broker omits them, an empty match is 200 with
  {"instruments": []}, and the query is a literal substring `search`
- GET /instruments/{symbol} → one resolved instrument in the broker's own
  spelling (a "xauusd.r" request resolves to "XAUUSD.r"); unknown or blank
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
- GET /users → role-based, tenant-scoped user listing
- GET/PATCH/DELETE /users/{user_id} → super_admin-only single-user read, partial
  update and delete inside the caller's broker
- POST /agent, GET /economic-intelligence/today,
  GET /fundamental-intelligence/today?symbol=<optional instrument>,
  GET /financial-research/today?from=<UTC ISO>&to=<UTC ISO>&symbol=<instruments>
  (resolved against the caller's own broker catalog since Step 51: the broker's
  canonical symbols are echoed, an instrument the broker does not offer is a 404,
  an unavailable catalog or news source is a 503),
  GET /instruments and GET /instruments/{symbol} → the tenant's own MT5
  instrument catalog,
  GET /portfolio-intelligence, GET/PUT /broker-llm-config → the read-only AI
  surface and broker LLM settings
- PUT /users/{user_id}/mt5-credentials → provision a user's MT5 investor
  (read-only) credential; admin: customers only, super_admin: any user in its
  broker. Write-only password, encrypted at rest, never returned.
- GET /users/{user_id}/mt5-credentials → safe credential metadata only
  (user_id, login, mt5_server, mt5_configured)

Plus unauthenticated infrastructure:

- POST /auth/login → JWT access token. Requires the broker code, the user's
  login (the MT5 account/login number) and the application password: `login` is
  unique only per broker, so the tenant is selected explicitly and the credential
  lookup is scoped to that broker_id (Step 43). The tenant a request is
  authorized for still comes from the database User record, never from the token.
  A missing `broker` is a 422; there is no legacy login-only form.
- GET /health → simple liveness probe (does not reflect MT5 readiness)

Expected error mapping currently includes:

- 401 for unauthenticated/invalid/expired tokens
- 403 for non-admin access to admin-only endpoints
- 404 when requested market data is unavailable
- 409 for duplicate user creation conflicts
- 503 when an MT5-backed service is temporarily unavailable

## Security

Important security principles:

- tenant isolation
- explicit tenant selection at login (broker code + login + application
  password); the lookup is scoped to the resolved broker, unknown/ambiguous
  brokers fail closed, every rejection answers identically, and the throttle
  counts failures per (broker, login) so tenants cannot lock each other out
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

- market data
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
  contract is read-only and tenant-scoped, works for arbitrary broker symbols
  (with or without a broker suffix), and holds no cache, database catalog,
  scheduler or ingestion. The XAUUSD/USOIL/NASDAQ fundamental relevance profiles
  remain optional intelligence enhancements layered on top of a resolved symbol.
- portfolio intelligence (symbol exposure, directional balance, deterministic
  risk classification)
- financial context (one read-only context for future AI consumption)
- JWT authentication, three roles and tenant-scoped user management
  (super_admin CRUD, admin manages customers)
- read-only AI agent (POST /agent) with a deterministic scope guard, a
  per-user daily limit, broker-scoped encrypted LLM configuration and an LLM
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
  holds no account, position or tenant data, and it reuses the exact same
  classification so the two surfaces can never disagree. Since Step 51 every
  requested instrument is resolved against the authenticated tenant's own MT5
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
