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

Current implemented core entities:

- Broker
- User

Future/domain entities planned:

- Position
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
schemas/
services/
providers/
utils/
tests/

No microservices unless there is a future explicit architectural decision.

## Provider Architecture

MarketDataProvider is the abstraction for market-data access.

Current contract:

get_market_data(symbol: str) -> Candle

Candle is a typed market-data contract containing:

- timestamp
- open
- high
- low
- close
- volume

MT5MarketDataProvider implements the provider.

MarketDataService delegates to the provider.

The API exposes market data through the market-data route.

## MT5

MetaTrader5 is the trading-data provider.

Current package:

MetaTrader5==5.0.6180

MT5 operations can block.

The current market-data API therefore keeps provider/service methods synchronous and moves the blocking operation outside the FastAPI event loop using a thread-pool boundary.

The provider contains MT5-specific integration logic.

The application must not expose MT5 implementation details unnecessarily.

## Current MT5 Lifecycle Design

The current architecture includes:

- lazy MT5 provider initialization
- process-wide provider singleton
- thread-safe initialization
- failed initialization is not cached
- provider shutdown
- FastAPI lifespan
- startup warm-up
- graceful shutdown
- blocking MT5 call through threadpool

This process-wide singleton is a known current limitation and is not yet tenant-scoped.

Do not redesign it unless explicitly instructed.

## API

Current market-data endpoint:

GET /market-data/{symbol}

The endpoint returns a Candle response.

Expected error mapping currently includes:

- 404 when requested market data is unavailable
- 503 when the market-data service is temporarily unavailable

Authentication is a known next security concern for this endpoint.

Do not assume authentication is currently implemented. Inspect the repository.

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

Eventually the system may support:

- account information
- balance
- equity
- margin
- positions
- trade history
- P&L
- risk
- exposure
- market data
- technical analysis
- fundamental analysis
- news
- economic calendar
- daily reports
- AI assistant
- natural-language interaction

These are future capabilities and must not be implemented ahead of the current checkpoint.
