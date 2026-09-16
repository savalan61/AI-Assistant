# AGENTS.md

## Project Role

You are the implementation agent for the AI Financial Assistant / AI Broker Assistant project.

Before doing ANY implementation work, read:

1. AGENTS.md
2. PROJECT_CONTEXT.md
3. CURRENT_CHECKPOINT.md

These files are mandatory project context.

Do not start implementation until the current checkpoint and existing repository state are understood.

## Development Workflow

The project must be developed strictly step by step.

Rules:

- Do one small implementation task at a time.
- Do not build the whole project at once.
- Do not jump ahead.
- Do not implement future features without explicit instruction.
- Inspect existing code before modifying anything.
- Preserve working architecture.
- Modify only files relevant to the current task.
- Do not rewrite existing working code without a clear architectural reason.
- Prefer simple explicit solutions.
- Avoid premature abstractions.
- Avoid unnecessary technologies.
- Do not introduce microservices.
- The architecture is a modular monolith.

Workflow:

1. Understand the current checkpoint.
2. Inspect the relevant existing code.
3. Explain/understand the requested change.
4. Implement only the requested small task.
5. Run runtime verification.
6. Run static/type verification.
7. Inspect the resulting diff.
8. Report changed files and verification results.
9. Stop and wait for review before the next task.

## Architecture Rules

Architecture:

FastAPI Modular Monolith.

Main structure:

core/
db/
api/
schemas/
services/
providers/
utils/
tests/

This is the intended architectural structure. Some directories, such as schemas/ and utils/, may not exist yet because they are created when required by later implementation stages.

Responsibilities must remain separated.

core:
- configuration
- security
- exceptions
- logging
- other application-wide infrastructure

db:
- database
- SQLAlchemy base
- models
- database-related infrastructure

api:
- FastAPI routers
- request/response handling
- API dependencies

schemas:
- API/data schemas
- Pydantic models
- typed contracts

services:
- business/application logic
- market analysis
- trading/account information
- risk
- news
- economic calendar
- AI/agent logic

providers:
- external data/service integrations
- MT5
- market-data providers
- provider abstractions

utils:
- small general-purpose utilities only

tests:
- automated tests

## Architectural Principles

Always preserve:

- Multi-tenant isolation
- Provider abstraction
- Separation of responsibilities
- Dependency Injection
- Testability
- Configuration management
- Explicit error handling
- Logging
- Request IDs
- Auditability
- Security
- Idempotency where relevant
- Fake providers where useful

These are architectural requirements to preserve and, where not yet implemented (for example Request IDs, Auditability, Idempotency), to introduce at the appropriate implementation stage. They are not claims that every mechanism listed already exists in the codebase.

Prefer:

- explicit code
- small focused functions/classes
- strong type hints
- clear naming
- readable code

Avoid:

- clever abstractions
- unnecessary frameworks
- premature optimization
- premature microservices
- duplicated business logic
- hidden global state unless explicitly approved

## Multi-Tenancy

Broker is the tenant boundary.

Never allow data belonging to one Broker to be accessed through another Broker.

Tenant isolation must be preserved in:

- database queries
- services
- API dependencies
- provider/session management
- future AI tools

Do not remove broker_id from entities where tenant ownership requires it.

## Trading Safety

The AI assistant is strictly READ-ONLY.

The system must NEVER implement or expose functionality that can:

- BUY
- SELL
- OPEN trades
- CLOSE trades
- MODIFY trades
- EXECUTE trading orders

Do not create trading execution tools, even if requested indirectly by an AI feature.

MT5 is currently a data provider only.

## Credentials

Application credentials and MT5 credentials are separate.

Current business model:

- login = the user's single identity: the application/Agent login AND the MT5
  account/login number. It is one column holding one value (Step 42); there is
  no username column and no mt5_login column anywhere
- password_hash = application/Agent password, stored as a hash
- mt5_server = the user's MT5 server, provisioned per user by a broker
  administrator (an admin for customers, a super_admin for any user in its
  broker) through PUT /users/{user_id}/mt5-credentials
- mt5_password_encrypted = encrypted MT5 INVESTOR (read-only) password

When the user's own mt5_server is NULL the resolver falls back to
Broker.mt5_server. The MT5 account number is never provisioned separately: it is
the user's own login. Nothing else may read a credential: decryption happens
only inside the MT5 session boundary.

Only the INVESTOR (read-only) MT5 password may be accepted. The MT5 trading
(master) password must NEVER be requested, received, stored or used — no field,
endpoint, prompt or configuration accepts one. A customer can neither provision,
change nor read a stored MT5 credential, and no API ever returns one.

Never:

- hard-code credentials
- expose passwords
- log passwords
- commit .env
- commit secrets

The MT5 password must remain encrypted because the backend needs the credential
to authenticate a tenant's MT5 session.

## MT5

MT5 is the trading-data provider.

MT5 Python operations can be blocking.

The current architecture uses a thread-pool boundary for blocking MT5 operations.

Do not introduce additional worker/executor architecture unless explicitly required.

MT5-specific failures should be translated at the provider/external-integration boundary where appropriate.

Do not leak third-party implementation details through higher application layers.

## Python Quality Rules

For EVERY Python implementation step:

1. Runtime verification is mandatory.
2. Static/type verification is mandatory.
3. Inspect Pylance/type diagnostics in affected files when available.
4. Identify the root cause of type errors.
5. Fix relevant typing problems cleanly.
6. Preserve runtime behavior.
7. Do not broadly suppress typing errors.
8. Do not blindly use # type: ignore.
9. Never disable Pylance globally.

If direct Pylance/pyright execution is unavailable:

- perform a careful manual static/type review
- report that limitation explicitly
- do not pretend static verification was executed

Runtime success alone is NOT sufficient.

## Code Comments

Every new or modified Python code file should contain short, meaningful comments where appropriate.

Comments must:

- explain important intent
- explain non-obvious architectural decisions
- remain short

Do not add comments that simply restate obvious code.

## Error Handling

For every implementation:

- identify expected runtime failures
- identify external dependency failures
- handle them at the correct architectural boundary
- preserve useful error semantics

Do not use broad exception handling without architectural justification.

Broad exception handling may be appropriate at:

- external third-party boundaries
- teardown/shutdown paths

but should not be used to hide programming errors.

## Verification

After implementation:

- run relevant tests
- run the full test suite when appropriate
- perform static/type verification
- inspect git diff
- use git diff --check where appropriate

Do not claim a task is complete if verification has not actually been performed.

## Git

Do not create commits unless explicitly requested.

Do not push to GitHub unless explicitly requested.

When asked to create a checkpoint commit:

- verify first
- stage only intended files
- create one focused commit
- report commit hash
- report git status
