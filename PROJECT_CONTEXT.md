# PROJECT_CONTEXT.md

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

- username = MT5 login/account number
- password_hash = Agent/application password
- mt5_password_encrypted = encrypted MT5 password

Broker initially supplies the credentials.

A Broker may reset an application password but should not see the user's current password.

## Core Entities

Current implemented core entities (database models):

- Broker
- User (with broker_admin/customer role)

Current implemented provider contracts (not database models):

- AccountInfo
- Position
- Candle

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
- username
- email
- phone
- password_hash
- mt5_password_encrypted
- is_active
- role (broker_admin / customer)

Important:

- username represents the MT5 login/account number
- password_hash is the application/Agent password
- mt5_password_encrypted is the encrypted MT5 password

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

The provider pattern is established for three MT5 data flows:

- MarketDataProvider: get_market_data(symbol) -> Candle
- AccountInfoProvider: get_account_info() -> AccountInfo (nine fields)
- PositionProvider: get_positions() -> tuple[Position, ...] (READ-ONLY)

Each contract is a typed NamedTuple (Candle, AccountInfo, Position) so raw
MT5 objects never cross the provider boundary.

MT5MarketDataProvider, MT5AccountInfoProvider, and MT5PositionProvider
implement the providers; FakeMarketDataProvider and FakePositionProvider
back the tests.

Services delegate to the provider abstractions.

The API exposes them through the market-data, account-info, and positions
routes; all three are JWT-protected.

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

- lazy MT5 provider initialization
- process-wide provider singleton (one cache per provider type, all
  attaching to the same terminal session)
- thread-safe initialization
- failed initialization is not cached
- provider shutdown
- FastAPI lifespan
- startup warm-up
- graceful shutdown
- blocking MT5 calls through the consolidated run_mt5_call boundary
  (app/core/blocking.py)

This process-wide singleton is a known current limitation and is not yet tenant-scoped.

Do not redesign it unless explicitly instructed.

## API

Current JWT-protected, read-only endpoints:

- GET /market-data/{symbol} → Candle response
- GET /account-info → AccountInfo response (nine fields)
- GET /positions → wrapped positions response; empty result is 200 with
  {"positions": []}, never 404
- POST /users → Broker-Admin-protected customer creation

Plus unauthenticated infrastructure:

- POST /auth/login → JWT access token
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
- JWT authentication and user management

Eventually the system may support:

- trade history
- P&L
- risk
- exposure
- technical analysis
- fundamental analysis
- news
- economic calendar
- daily reports
- AI assistant
- natural-language interaction

These are future capabilities and must not be implemented ahead of the current checkpoint.
