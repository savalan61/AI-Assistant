# CURRENT_CHECKPOINT.md

## Current Status

Step 54 — General Broker Decoration Resolution (this checkpoint)
+ Step 53 — Market Data Symbol Resolution
+ Step 52 — Safe Broker-Suffix Resolution
+ Step 51 — Instrument Resolution in Financial Research
+ Step 50 — MT5 Instrument Discovery & Resolution
+ Step 49 Follow-up — Financial Research in the Agent Pipeline
+ Step 49 — Graded Financial Research Context
+ Step 48 — Instrument-Aware Fundamental Relevance
+ Step 47A — Alpha Vantage News Source (DEVELOPMENT/TEST ONLY)
+ Step 47 — News & Fundamental Intelligence
+ Step 46 — Explicit Economic-Calendar Source Configuration
+ Step 45 — Economic Intelligence in the Agent Pipeline
+ Step 44 — QuantGist Economic Calendar Source (development/test)
+ Step 43 — Tenant-Safe Login
+ Step 42 — One User Identity (`login`)
+ Step 41 — Stabilize & commit Step 40 + the trade-history field fix
+ Step 40 — Super Admin User CRUD
+ Fix — MT5 trade-history SL/TP field names
+ Step 39 — Live MT5 Investor-Password Verification
+ Step 39A/39B — configuration startup hardening & Pylance `_env_file` fix
+ Step 38 — MT5 Investor / Read-Only Credential Provisioning
+ Step 37 — Decimal Money & Financial Numeric Representation
+ maintenance — role-migration ordering fix & development user seed

Status:

Step 54: VERIFIED + COMMITTED (b454784 — "fix(instruments): resolve decorated
broker spellings without a suffix list"; local, not pushed)
Step 53: VERIFIED + COMMITTED (e884767 — "fix(market-data): resolve symbols
through instrument catalog"; local, not pushed)
Step 52: VERIFIED + COMMITTED (9ab52d5 — "feat(instruments): safely resolve
unique broker symbol suffixes"; local, not pushed)
Step 51: VERIFIED + COMMITTED (c9a5a39 — "feat(research): resolve instruments
through MT5 catalog"; local, not pushed)
Step 50: VERIFIED + COMMITTED (6c2df3e — "feat(instruments): add MT5 instrument
discovery and resolution"; local, not pushed)
Step 49 Follow-up: VERIFIED + COMMITTED (9081b5f — "feat(agent): integrate
financial research context"; local, not pushed)
Step 49: VERIFIED + COMMITTED (df3c4ef — "feat(research): add graded financial
research context"; local, not pushed)
Step 48: VERIFIED (implementation, tests and documentation; committed together by the Step 48 commit "feat(fundamental): generalize instrument-aware relevance"; local, not pushed)
Step 47A: VERIFIED + COMMITTED (e434659 — "feat(news): add Alpha Vantage development source" + its checkpoint-status commit; local, not pushed)
Step 47: VERIFIED + COMMITTED (c44953d — "feat(fundamental): add news and fundamental intelligence" + its checkpoint-status commit; local, not pushed)
Step 46: VERIFIED + COMMITTED (ebbb86b — "feat(calendar): add explicit source configuration")
Step 45: VERIFIED + COMMITTED (b9785cb + its checkpoint-status commit 9ba0d95; local, not pushed)
Step 44: VERIFIED + COMMITTED + PUSHED (638f972)
Step 43: VERIFIED + COMMITTED + PUSHED (5afd89510af4ec5e63d4bcbf805bc9e73405f1e9)
Step 42: VERIFIED + COMMITTED + PUSHED (95d00d9)
Steps 12–41: COMMITTED + PUSHED; the Step 41 commit is 1577672

Checkpoint commit:

The Step 54 change set (the extended decoration rule in
`_is_broker_suffix_variant` with its documented three-form bound, the
re-pointed Step 52 bounds tests, the new decoration/ambiguity/partial-name
service cases, the end-to-end suffixed-broker regression in the research suite,
and this documentation) — the change set this checkpoint describes — is
implemented, verified and committed as ONE focused commit
("fix(instruments): resolve decorated broker spellings without a suffix list")
carrying the diff, the tests and this documentation together, following the
established single-commit convention. Resolution still lives entirely inside
the instrument service: no provider, resolution order, research/agent/market-data
path, configuration, LLM or schema change beyond the decoration rule itself.

The Step 53 change set (the optional InstrumentService on MarketDataService and
its resolve-then-read body, the composition-root wiring of the existing
instrument seam into the market-data service, the service-level resolution
cases, the HTTP-level resolution cases, the market-data/lifecycle test seams
that now provide the tenant's catalog, and this documentation) — the change set
this checkpoint describes — is implemented, verified and committed as ONE
focused commit ("fix(market-data): resolve symbols through instrument catalog")
carrying the diff, the tests and this documentation together, following the
established single-commit convention. No resolution rule, provider, response
schema, configuration value, research/agent path, LLM change or schema change is
involved: the market-data service asks the existing instrument boundary and
passes the broker's spelling to the unchanged candle provider.

The Step 52 change set (the bounded broker-suffix rule in InstrumentService.resolve
with its separator/length bounds and its docstring contract, the 25 new Step 50
resolution cases, the research-service/research-API/agent regression cases for a
suffixed broker, and this documentation) — the change set this checkpoint
describes — is implemented, verified and committed as ONE focused commit
("feat(instruments): safely resolve unique broker symbol suffixes") carrying
implementation, tests and documentation together, following the established
single-commit convention. Resolution still lives entirely inside the instrument
service: no provider, research-service, agent, prompt, configuration, LLM or
schema change beyond the resolution rule itself.

The Step 51 change set (the optional InstrumentService on
FinancialResearchService, the FocusResolution step and the context's
unresolved_symbols field, the research API's resolve-then-research flow with its
deterministic 404, the agent's resolve-before-research composition, the
composition-root wiring of the existing instrument service into the research
service, the extended offline tests and this documentation) — the change set
this checkpoint describes — is implemented, verified and committed as ONE
focused commit ("feat(research): resolve instruments through MT5 catalog") that
carries the implementation, the tests and this documentation together,
following the Step 48/50 convention of a single commit; no separate
checkpoint-status commit follows it. No database table, cache, scheduler,
ingestion, provider redesign, LLM change, prompt-semantics change or trading path
is involved, and the Step 48 profiles remain optional enhancements applied to an
already-resolved symbol.

The Step 50 change set (the vendor-neutral Instrument/InstrumentProvider contract
with its TradeMode enum, the read-only MT5InstrumentProvider over the existing
tenant-scoped MT5 session, the deterministic FakeInstrumentProvider catalog, the
InstrumentService boundary in app/services/instruments with symbol
normalisation/resolution/searching/ordering and the MAX_INSTRUMENTS bound, the
get_instrument_service composition seam, the JWT-protected GET /instruments and
GET /instruments/{symbol} endpoints, the three new test modules and this
documentation) — the change set this checkpoint describes — is implemented,
verified and committed as ONE focused commit ("feat(instruments): add MT5
instrument discovery and resolution") that carries the implementation, the tests
and this documentation together, following the Step 48 convention of a single
commit; no separate checkpoint-status commit follows it, so this document
records the state rather than a hash. No database table, cache, scheduler,
background job, ingestion path, provider redesign, LLM change or trading path is
involved, and the XAUUSD/USOIL/NASDAQ relevance profiles remain optional
intelligence enhancements rather than a prerequisite for resolving an
instrument (CURRENT_CHECKPOINT.md known issue 20 records the remaining gap).

The Step 49 follow-up (the financial-research context composed into the Agent
pipeline: the optional financial_research_service constructor parameter, the
research composition in AgentService.handle — built ONLY when the request names
a focus instrument and AGENT_RESEARCH_LOOKBACK_DAYS is positive, for the
half-open look-back span immediately BEFORE the calendar window so research and
fundamental news never overlap — the bounded research block and its reduction-
ladder rung in the prompt builder, the single shared _news_line renderer both
blocks use, the get_financial_research_service wiring in the composition root,
and the 23-case focused suite) — the change set this checkpoint describes — is
implemented, verified and committed as ONE focused commit ("feat(agent):
integrate financial research context"). No calendar was added to
FinancialResearchContext, FundamentalContext is unchanged, no second relevance
mechanism exists (the research block renders the SAME graded
FundamentalNewsItem values the Step 48 news_intelligence classification
produces), and no provider, API endpoint, schema or trading path changed. No
live API request was made: provider behaviour is untouched.

Before it, Step 49 (the graded financial-research context: FinancialResearchService and
FinancialResearchContext in app/services/fundamental_intelligence/research.py, the
public news_intelligence classification shared with the fundamental context, the
get_financial_research_service composition seam reusing the existing news seam
unchanged, and the JWT-protected GET /financial-research/today endpoint with the
explicit UTC window and focus-symbol parameters) — the change set this checkpoint
describes — is implemented, verified and committed as ONE focused commit
df3c4ef ("feat(research): add graded financial research context") that carries the
implementation, the tests and this documentation together, following the Step 48
convention of a single commit; no separate checkpoint-status commit follows it, so
this document records that hash here. The service is a reusable slice
of the fundamental-intelligence layer: graded published-source news for an explicit
half-open UTC window and explicit focus instruments, with NO account, position or
tenant data (it holds no MT5 provider, reads no positions and accepts no tenant
identity — the response's only tenant fact is the caller's own broker_id echo).
Classification is the SAME news_intelligence grading the fundamental context uses,
so the two surfaces can never disagree about an item; no provider, configuration
value, agent path or schema changed, no live API request was made, and Alpha
Vantage, the fake, the NEWS_SOURCE matrix and the agent pipeline are untouched.
Before it, Step 48 (instrument-aware fundamental relevance: the shared domain vocabulary and
instrument profiles under app/services/instrument_intelligence, the graded
per-instrument classification in the news and calendar relevance layers, the
profile-based calendar rule for symbols with no currency leg, the exposure factor
attribution, the prompt's factor rendering and the new test modules) — the change
set this checkpoint describes — is implemented, verified and committed as ONE
focused commit ("feat(fundamental): generalize instrument-aware relevance") that
carries the implementation, the tests and this documentation together, on the
instruction that Step 48 be a single commit; no separate checkpoint-status commit
follows it, so this document records the state rather than a hash.
Before it, Step 47A (the Alpha Vantage development news source: the NewsProvider
implementation behind the Step 47 contract, the explicit alphavantage source
option, the environment matrix, the key-redaction measure and the single live
smoke request) — the change set this checkpoint describes — is implemented,
verified and committed by e434659 ("feat(news): add Alpha Vantage development
source"), which carries the implementation, the tests, .env.example and the
documentation updates (PROJECT_CONTEXT.md, knowledge.md and this document); this
checkpoint-status commit records that hash here.
Before it, Step 47 (news and fundamental intelligence: the NewsProvider contract and its
deterministic development/test source, the explicit NEWS_SOURCE selection, the
deterministic news relevance, the FundamentalIntelligenceService context, the
per-position fundamental exposure, the JWT-protected
GET /fundamental-intelligence/today endpoint and the fundamental block in the
agent prompt) — the change set this checkpoint describes — is implemented,
verified and committed by c44953d ("feat(fundamental): add news and fundamental
intelligence"), which carries the implementation, the tests, .env.example and the
documentation updates (PROJECT_CONTEXT.md, knowledge.md and this document); this
checkpoint-status commit records that hash here.
Before it, Step 46 (explicit economic-calendar source configuration, with a deliberate
production seam) — the change set the previous checkpoint describes — is implemented,
verified and committed by ebbb86b ("feat(calendar): add explicit source
configuration"), which carries the configuration, the composition-root seam, its
tests, .env.example and the documentation updates (PROJECT_CONTEXT.md,
knowledge.md and this document). Before it, Step 45 (economic intelligence in
the agent pipeline) was committed by b9785cb ("feat(agent): compose economic
intelligence into the agent prompt"), which carries the implementation, the
tests and the documentation updates (PROJECT_CONTEXT.md, knowledge.md and this
document), and its checkpoint-status commit 9ba0d95, which records that hash
here. All three are local: they are NOT pushed, so origin/master stays at the
Step 44 commit until they are. Before them, Step 44 (the QuantGist
development/test economic-calendar source) was
committed by "feat(calendar): add QuantGist development/test source" (638f972,
which carries the adapter, its tests, the wiring, the configuration and the
documentation) and pushed. Step 43 is
5afd89510af4ec5e63d4bcbf805bc9e73405f1e9 ("feat(auth): make login
tenant-safe"), which carries the implementation, the tests and the four
documentation updates (AGENTS.md, PROJECT_CONTEXT.md, knowledge.md and this
document); it is pushed, together with its checkpoint-status commit (9dbfb7e)
and the knowledge-base alignment commit (74cbba5).
The prior commit is Step 42 — "refactor(users): collapse username and mt5_login
into one login identity" (95d00d9), the one-identity `login` rename across the
model, migration, auth, user management, MT5 credential handling, seed scripts,
tests and its document update; it is pushed as well. Before that, Step 41 —
"feat(users): complete super admin user crud" (1577672) — which carries Step 40
together with the trade-history field fix; it is pushed too. Before these,
"fix(config): harden environment settings loading"
(Steps 39A/39B), which carries the tolerant, secret-safe settings loading and
the statically visible env-file selection; before that "feat(mt5): add investor
credential provisioning" (Step 38, the per-user MT5 account fields, the
provisioning endpoints and the new migration), then "fix(db): correct user role
migration ordering" (3a63af9, the reordered role migration and the development
user seed script), the Step 37 checkpoint ("feat(financial): harden numeric
representation"), the Step 36 MT5 tenant-session commit, the Step 35 security
hardening commit and b95eaa1 ("feat(ai): add broker llm routing and agent
controls", Steps 29–33).

Step 47A adds Alpha Vantage as the real development news provider behind the
Step 47 contract — a NewsProvider implementation and nothing else: no service,
API, relevance, agent or schema change, and the fake remains the deterministic
fallback. `NEWS_SOURCE` gains one value (`alphavantage`), resolved at the same
composition root: in development, `auto` now selects Alpha Vantage when
ALPHA_VANTAGE_API_KEY is configured and the deterministic fake otherwise, and
`alphavantage` names it explicitly; anywhere else `auto` still means no news
source, and an explicit `alphavantage` outside development refuses with the same
generic 503 as the other development/test sources. A configured key never makes a
non-development deployment serve it, an explicitly selected source without a key
refuses instead of falling back to the fake, and the production slot still
refuses until a real vendor is registered — so no production or licensing claim
is made: Alpha Vantage's free tier is explicitly a development/test stand-in
(small daily quota, personal-use terms) whose provenance marker
(`alphavantage-free-development`) travels into every API response and agent
prompt. The adapter performs one bounded NEWS_SENTIMENT query per call (the
caller's half-open UTC window sent as the vendor's own time filter and re-applied
locally, `sort=LATEST`, the requested result cap clamped to the vendor maximum),
parses only the fields our NewsItem contract carries (title, publisher, URL, the
vendor's own excerpt bounded to 400 characters plus a marker, an aware UTC
`published_at`, the vendor's topic labels as categories) and drops the vendor's
sentiment scores/labels and ticker tags — relevance stays exactly where it was,
with the existing deterministic classifier deciding from currency legs and the
documented keyword map, so XAUUSD keeps its USD/gold/Fed/inflation mapping. It
fails closed (generic RuntimeError → the established 503) on a missing key, a
transport or timeout failure, a non-200 response, an invalid-JSON body, a
malformed envelope or row, and on the vendor's HTTP-200 `Information`/`Note`
bodies (invalid key, exhausted quota) — the vendor payload is never echoed. Since
the vendor requires the key as a query parameter, the adapter also installs (once,
for the configured value only) a redaction filter on the httpx logger, so the
HTTP client's own INFO-level request line can never put the key into a log; the
adapter itself never emits a record, never formats the URL into an error message
and never chains the original exception. No caching, retry, scheduler, scraping,
article fetch, database table, news pool, MCP or tool/function calling was added.

Step 47A verification: the new provider/source/security tests were run focused
(423 passed across the news, fundamental, agent, calendar and config suites); the
full suite passed 1213 with 2 pre-existing third-party warnings (1134 → 1213)
WITH OUTBOUND NETWORKING HARD-DISABLED in the test process (socket.getaddrinfo
and socket.create_connection patched to raise), which is also the proof that no
automated test can reach Alpha Vantage or any other network; compileall over app
and tests clean; pyright 0 errors / 0 warnings on the new provider and every
changed file, including the new test module; git diff --check clean; trading-safety
greps confirm no order/position-mutation function exists anywhere in app/; a
secret sweep for the configured key value across the whole repository (excluding
.env itself) found zero files; and ONE live smoke request was made after all
offline tests passed, with no retry: the composition-root seam selected
`alphavantage-free-development`, the request returned 20 real articles (the
service's own cap) with aware UTC `published_at` values inside the requested
24-hour window, bounded excerpts (400 characters plus the marker), populated
publishers/URLs/topics, chronological ordering preserved, and no key in any
output. An offline probe with 20 real-shaped items then confirmed the prompt
stays within its existing bound (10,121 characters against the 24,000 limit) with
both public blocks intact and no sentiment anywhere.

Step 47 delivers news and fundamental intelligence as one vertical slice, so a
customer's question can be answered with what is happening to an instrument and
to their own positions today, and not only with the calendar and their numbers.
A vendor-neutral `NewsProvider` contract (app/providers/news.py) carries bounded
factual items only — UTC publication time, publisher, title, a bounded excerpt,
an optional link, declared instrument/currency/category tags — with the source's
provenance marker, window-bounded retrieval and a caller-supplied cap, exactly
mirroring the calendar boundary. The wired implementation is a deterministic
development/test fake (`fake-development-placeholder`, no network, no clock, a
fixed catalog materialized on the requested UTC dates), and there is NO
production news vendor: nothing was invented, nothing scrapes, and no scheduler,
cache or retry was added. Which source a deployment serves is explicit
(`NEWS_SOURCE`: auto | development_fake | production, default auto) and resolved
at the composition root beside the calendar seam; the development source is
served inside development only, and `production` is the deliberate seam a real
vendor is registered behind, refusing until one exists. The deliberate
difference from the calendar is what an unconfigured deployment means: the
calendar is mandatory, so an unusable calendar source refuses (503), while no
news source is a supported state that the fundamental context reports as
explicitly UNAVAILABLE with its reason — never as "no news" — and an explicitly
selected but unusable news source still refuses with its own generic 503 detail
rather than falling back.

Step 47 keeps exactly one relevance mechanism: `is_metal_instrument` and the
level ranking were exported from the existing calendar classifier, and news
relevance reuses the same `RelevanceLevel` vocabulary, the same symbol
currency-leg tokenizer and the same conservatism (POTENTIALLY_RELEVANT is the
strongest level a symbol string may evidence; RELEVANT stays reserved for a
future instrument catalog). Declared source tags win; when an item declares
none, a small, documented, whole-word keyword map detects a currency reference
from the title, so an untagged "Federal Reserve minutes due" item is still
considered for XAUUSD instead of being silently ignored, while an item with no
identifiable reference stays NOT_OBVIOUSLY_RELEVANT for every instrument
together with the factual reason. XAUUSD is the first use case, handled through
the same rules as the calendar (USD is its quote currency because it is a
USD-denominated metal; XAU is its base metal). Relevance is a discrete category:
there is no score, no probability, no direction and no recommendation anywhere in
the layer.

`FundamentalIntelligenceService` composes the MANDATORY calendar context the
request already has with relevant news and the caller's own positions into a
`FundamentalContext` carrying `as_of`, the same half-open UTC window, the
instruments in play (the requested focus instrument plus what the caller
actually holds), each source's provenance, the per-item relevance and matched
instruments, and one factual exposure record per open position. It performs no
MT5 read of its own: the positions travel inside the calendar context (which now
also exposes the ordered snapshot it was built from), so one request still
performs exactly one calendar read and one position read. Each position's
exposure lists the calendar event ids and news item ids that were found relevant
to it, and is `UNKNOWN` with a stated reason whenever it cannot be established —
a symbol with no identifiable currency leg, or a day with no calendar drivers
and no news source — because missing information must never read as an absence
of risk. The deterministic layer emits facts, timestamps, provenance, relevance,
exposure and UNKNOWN only; any interpretation remains the LLM layer's job on top
of that labelled context.

GET /fundamental-intelligence/today (JWT-protected, `?symbol=` optional) exposes
that context for the authenticated user only: tenant identity comes solely from
the database user (no broker_id/user_id parameter exists, and the focus symbol is
a label for relevance, never a scope), the mandatory calendar context is built
first and always, the blocking composition runs through the existing
run_mt5_call boundary, a blank symbol is 422, and MT5/calendar/news
infrastructure failures become the endpoint's own generic 503 with no provider
internals in the detail. In the agent, the fundamental context is composed
AROUND the same calendar context and rendered as a second public-data block that
labels its news as published source facts (so the model can tell source material
from its own interpretation), states an unavailable source as unavailable, states
an empty feed as this source publishing nothing today, renders UNKNOWN as
UNKNOWN, carries both sources' provenance, and bounds each excerpt. The agent's
size discipline gained one ordered step between the existing two: the trade block
is dropped first, then the fundamental block, then the calendar block — each
omission stated in the body — and only then does PromptTooLargeError fail the
request. Calendar data and news are public information with no account identity,
so the three LLM_SEND_* egress switches keep governing exactly the customer
financial data they always did; no protection was weakened, no scope guard,
usage limiter, router or read-only guarantee changed, no tool/function calling or
agent framework was added, the QuantGist adapter is untouched, and there is no
schema change and no migration.

Step 47 verification: the six new test modules and the three extended ones were
run focused (485 passed) and the trading-safety, secret-safety, tenant-isolation
and configuration set passed 444; full suite 1134 passed, 2 warnings (both
pre-existing third-party deprecations); compileall over app and tests clean;
pyright 0 errors / 0 warnings on every changed application file and on all six
new test modules, with the two extended legacy API test files keeping only their
pre-existing `object()`-session/`dict[str, object]` pattern (25 diagnostics
before and after, one of which is the new wiring test written identically to its
six neighbours — no suppression anywhere); git diff --check clean; a read-only
probe rendered the real fundamental block for the flagship question (XAUUSD plus
a held EURUSD: five placeholder news items with discrete relevance and matched
instruments, both sources' provenance, and KNOWN exposure with driver counts per
position); trading-safety greps confirm no order/position-mutation function
exists anywhere in app/; and secret scans of the new modules and of every
response/log line find no API key or credential. No schema change and no
migration (alembic/ and app/db/ are untouched).

Step 46 makes which economic-calendar source a deployment serves an explicit
configuration value (`ECONOMIC_CALENDAR_SOURCE`). The calendar is mandatory for
every agent request, so a source is never chosen implicitly and an unusable one
fails closed (503) with the established generic detail instead of degrading:
`auto` (the default, which preserves every existing deployment) is the
historical environment-driven selection — in development the QuantGist free tier
when an API key is configured, otherwise the deterministic fake, and anywhere
else a refusal, because no production source is configured; `development_fake`
and `quantgist` name a development/test source explicitly and are served inside
development only; and `production` is the deliberate production seam a real
vendor is registered behind at the single resolution point, refusing until one
exists rather than quietly serving development data as production data. An
explicitly selected source with missing configuration (for example `quantgist`
without a key) is also a refusal, never a fallback. An invalid value is a
startup configuration error that names the setting and never echoes the value,
and the operator gets the precise, value-free reason in a log line while the
client always receives the same generic detail. No production vendor was
invented, the QuantGist adapter is untouched, and no retry, cache, background
job, tool calling or new provider was added. No schema change and no migration.

Step 46 verification: focused suites 274 passed (the new cases are the full
source x environment matrix at the composition root, the production-seam and
missing-configuration refusals, the value-free logging and secret hygiene, the
invalid/valid source values at settings loading, and one API case per affected
endpoint: GET /economic-intelligence/today and POST /agent both refuse in
production); full suite 948 passed, 2 warnings (both pre-existing third-party
deprecations); compileall over app and tests clean; pyright 0 errors / 0 warnings
on config.py, dependencies.py and the three changed application-adjacent test
files (test_agent_api.py keeps only its 14 pre-existing diagnostics, none at or
after the added lines); git diff --check clean; and a read-only runtime probe
exercised the whole 32-cell matrix (4 environments x 4 sources x key set/unset),
confirming the table above, 27 value-free log lines and no API key in any log
line. The QuantGist provider and alembic/ are untouched (git diff --stat shows no
lines).

Step 45 wires the existing economic intelligence into the agent, so a customer's
question is answered with today's economic calendar in the same prompt. The agent
composes, it does not fetch: AgentService takes an injected
EconomicIntelligenceService (the same service GET /economic-intelligence/today
uses, reached through the same composition-root path), resolves ONE reference
instant per request and passes it to both the financial context and today's UTC
calendar window, so the trade-history window and the calendar day can never
disagree about when "now" is. The prompt gained a dedicated economic block that
renders each event's timestamp, currency, impact, forecast/previous/actual exactly
as published (null renders as "-", no precision is invented), the deterministic
relevance level and the source's provenance marker — public market data with no
account identity, which is why the three LLM_SEND_* egress switches keep
governing exactly the customer financial data they always did, with no protection
weakened and no account identity added. The existing size discipline gained one
ordered step: the trade block is dropped first, then the economic block (each
omission stated in the body, never a silent truncation), and only then does
PromptTooLargeError fail the request. The economic calendar remains MANDATORY on
every request: the composition root always supplies the service, a
calendar/provider failure propagates unchanged and surfaces as the endpoint's
existing generic 503, and no tool/function calling, agent framework, multi-turn
loop, retry, cache or new provider was added. No schema change and no migration.

Step 45 verification: focused suites 249 passed (the new
 tests/test_agent_economic_context.py holds 23 composition cases: the calendar
reaching the prompt, provenance and relevance preservation, the shared `now`,
the empty calendar, the failure path, and unchanged agent behaviour); the agent
area including the API boundary passed 75; full suite 924 passed, 2 warnings
(both pre-existing third-party deprecations); compileall over app and tests clean;
pyright (via npx, the project's venv interpreter) reports 0 errors / 0 warnings
across the three changed application files and the new test file, with only the
edited test files' pre-existing diagnostics left (no suppression added);
git diff --stat confirmed the QuantGist adapter and alembic/ were untouched. The
tests were written first: 25 cases failed before the implementation existed.

Step 43 makes login tenant-safe. `login` is the MT5 account/login number, and
MT5 account numbers are unique per broker rather than globally, so two brokers
may legitimately hold the same number (Broker A and Broker B can both have
80009). `POST /auth/login` therefore names its tenant: `{ "broker": <broker
code>, "login": ..., "password": ... }`. The broker code is resolved
case-insensitively to exactly one Broker row and is used only to scope the
credential lookup to that broker_id — never as an authorization fact, never as
a JWT claim. `broker` is mandatory on every request (the pre-Step-43 login-only
body is refused with 422), and the request shape deliberately does not change
when a login happens to be duplicated, so a client cannot learn from the request
or the response that a login exists at more than one broker. The login
brute-force throttle's login bucket is now `(broker, login)` instead of login
alone, so one tenant's failures can no longer lock out another tenant's
identical number, while the IP bucket stays IP-only. Every rejection path
(unknown broker, ambiguous broker code, unknown login, wrong password, inactive
user, inactive broker) returns the identical generic 401, and the paths that
have no stored hash to check now spend an equivalent dummy bcrypt verification
instead of returning early, so response timing cannot reveal which broker codes
or logins exist. The JWT is untouched (`sub = str(User.id)`, no broker_id and no
role claim) and post-authentication identity and authorization keep coming from
the database. No schema change, no migration, and no change to `User.login`
semantics or the `(broker_id, login)` uniqueness constraint.

Step 43 verification: focused suite 81 cases passed (test_auth_login.py 35,
test_login_throttle.py 28, test_security.py 18); full suite 827 passed, 2
warnings (both pre-existing third-party deprecations); `python -m compileall app
tests scripts` clean. Static/type verification: pyright was executed for the
first time in this project (via npx, explicitly approved by the operator — it is
still not installed in .venv), reporting 0 errors / 0 warnings across the 7
changed files after fixing the one real defect it found (the test fixture
`auth_db` was annotated with its yielded type instead of
`Iterator[SeededAuthDb]`). A whole-project run (115 files) reports 61
pre-existing diagnostics in 19 files this step does not touch (the same
generator-fixture annotation pattern plus a few Literal/enum mismatches); none
is in a changed file and none was fixed or suppressed here. No `# type: ignore`
and no checker configuration change.

Step 40 completes tenant-scoped user management for the broker's super_admin:
read one user, partially update one user (including role) and delete one user,
all inside the authenticated broker, with the broker's only super_admin
protected from demotion and deletion and no path to creating a second one. User
creation gained an optional explicit role — omitted means customer (never a
silent admin), "admin" requires a super_admin caller, and "super_admin" is
refused for every caller. Admin and customer behaviour is unchanged.

The trade-history fix corrects a real defect found by live verification: the
protective levels of a historical MT5 order are exposed as `sl` and `tp`, not
`price_sl`/`price_tp`, so the previous attribute access raised AttributeError
inside the provider and the endpoint failed. The application contract is
unchanged — stop_loss / take_profit, nullable, Decimal, JSON numbers on the
wire — and the read-only boundary is untouched.

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
password. The legacy derivation (a numeric identity + Broker.mt5_server) is
preserved as the fallback, so every pre-existing row keeps working unchanged;
that identity is `User.login`, because Step 42 later collapsed the former
`username` and `mt5_login` columns into the single `login` value. There is still
no master/trading-password support anywhere.

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

pytest tests/ -q → 1381 passed, 2 warnings (both pre-existing third-party
deprecation warnings: the anyio BlockingPortal alias and the starlette
testclient httpx notice); the Step 47A figure of 1213 was re-verified on the
tree BEFORE Step 48 with outbound networking hard-disabled, so it also proved
the suite reached no network (neither Alpha Vantage nor anything else), and the
Step 49 run re-proved the same property for the two new suites: both pass with
DNS resolution and outbound connections patched to raise. Step 49 added the
research-service and research-API suites (1341 → 1381) and made NO live API
request of any kind. Step 44 rewrote the QuantGist cases
against the verified live API and took the suite to 897; Step 45 added the
agent/calendar composition (924); Step 46 added the source x environment matrix
and the production-seam cases (948); Step 47 added the news provider/service,
relevance, fundamental-service, fundamental-API, agent-fundamental and news
source-configuration suites (948 → 1134); Step 47A added the Alpha Vantage
provider, source-matrix and secret-hygiene cases (1134 → 1213). Step 40 took it to 799 and Step 39A
removed the former Pydantic class-config deprecation, which is why the warning
count is 2 rather than 3.

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

CLEAN — the Step 49 follow-up change set (the agent research composition, the
research prompt block and its reduction rung, the shared news-line renderer, the
AGENT_RESEARCH_LOOKBACK_DAYS setting, the composition-root wiring, the new test
module and the wiring test, and this documentation) is committed by the follow-up
commit, so nothing from that change set is left modified, staged or uncommitted.
The Step 49 change set (the research service and context, the shared
news_intelligence classification, the composition seam and the
GET /financial-research/today endpoint, the two new test modules and this
documentation) is committed by the Step 49 commit, so nothing from that change
set is left modified, staged or uncommitted. The Step 48 change set (the shared
domain vocabulary and instrument profiles, the graded per-instrument news and
calendar relevance, the no-currency-leg profile rule, the exposure factors, the
prompt rendering and the three new test modules) is committed by the Step 48
commit. The Step 47A change set (the
Alpha Vantage news provider, the alphavantage NEWS_SOURCE value and its matrix,
the key-redaction measure, the new and extended tests, .env.example and the
documentation) is committed by the Step 47A commit and its checkpoint-status
commit. The Step 47 change set (the news provider
contract, the deterministic development news source, the explicit NEWS_SOURCE
selection, the fundamental intelligence service and its relevance/exposure layer,
the GET /fundamental-intelligence/today endpoint, the agent prompt composition,
the tests and the documentation) was committed by the Step 47 commit and its
checkpoint-status commit. Before it, the Step 46 change set
(explicit economic-calendar source configuration, the deliberate production
seam, its tests and .env.example) was committed by ebbb86b.

The Step 42 `login` rename and its document update were carried by the Step 42
checkpoint commit. Steps 41 (`1577672`, "feat(users): complete super admin user
crud"), 42 (`95d00d9`), 43 (`5afd895`), the two documentation commits after it
(`9dbfb7e`, `74cbba5`) and Step 44 (`638f972`) are pushed: origin/master is
638f972, and local HEAD is eighteen commits ahead of it, none of them pushed:
b9785cb (Step 45 implementation), 9ba0d95 (its checkpoint-status commit),
ebbb86b (Step 46), c95ae6c (Step 46 checkpoint-status commit), 94b1858 (the
authoritative roadmap), c84d334 (the roadmap reorder that puts fundamental
intelligence ahead of technical analysis), the two Step 47 commits (the
implementation and its checkpoint-status record), the two Step 47A commits (the
Alpha Vantage development source and its checkpoint-status record), the
Step 48 commit (instrument-aware fundamental relevance), the Step 49 commit (the
graded financial-research context), the Step 49 follow-up commit (`9081b5f`, the
research context composed into the agent), the Step 50 commit (`6c2df3e`, MT5
instrument discovery and resolution), the Step 51 commit (`c9a5a39`, instrument
resolution inside financial research), the Step 52 commit (`9ab52d5`, safe
broker-suffix resolution), the Step 53 commit (`e884767`, market-data symbol
resolution through the instrument catalog) and the Step 54 commit (`b454784`,
general broker-decorated spellings).

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
  resolving the former Known Issues item 9 debt from Step 17 (that item was the
  MT5 blocking-call debt and is recorded as resolved; today's item 9 is a
  different, still-open issue — the economic-calendar data source)
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

- the MT5 data plane had no tenant scoping (all brokers shared one terminal
  account) — known issue 1 at the time, and the product blocker it implied.
  RESOLVED by Step 36: MT5 sessions are tenant-scoped now, so this bullet is
  the historical finding, not the current state
- an SSRF vector through the broker-supplied LLM base_url
- uncontrolled egress of customer financial data to third-party LLMs
- no login brute-force protection
- no token revocation; exp was verified-if-present rather than required
- broker suspension was enforced only at login
- the role-evolution migration can fail on duplicate per-broker admins
- money modelled as float throughout the domain contracts

Step 35 implemented the fixes the audit identified as required now; Step 36
resolved the MT5 tenant-scoping finding and Step 37 the money-representation
finding. Findings that are still open are tracked in Known Issues and Next Step
below, and every closed one appears in the Resolved list.

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
  protection keyed on the client IP and on the submitted login (extended by
  Step 43 with the broker code, so the key is now per `(broker, login)` and
  tenants never share a counter) (failures
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
  and the get_mt5_credentials dependency): the numeric user identity is the MT5
  login — that is `User.login`, which absorbed the former `username` and
  `mt5_login` columns in Step 42 — user.mt5_password_encrypted the ciphertext,
  Broker.mt5_server the server — all from the authenticated database user, never
  from a request body, query parameter or token claim. Resolution never
  decrypts; an incomplete record (non-numeric login, missing server,
  missing/undecryptable password, missing broker) fails closed inside the
  session boundary with a message that never names which value was missing.
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


### Step 54 — General Broker Decoration Resolution (READ-ONLY)
Status: VERIFIED + COMMITTED (the Step 54 commit "fix(instruments): resolve
decorated broker spellings without a suffix list"; one focused commit carrying
implementation, tests and documentation — the established single-commit
convention)

Brokers decorate their variants in more shapes than the Step 52 rule accepted:
`XAUUSD.p`, a trailing separator with no token at all (`XAUUSD.`, `UKOIL.`,
`US100.`), and delimiter-free lowercase tags (`XAUUSDm`, `XAUUSDpro`). A base
request against such a catalog stayed unresolvable even though exactly one
candidate existed. Resolution now accepts any of these decoration forms — with
NO list of known suffixes and NO guessing.

Includes:

- app/services/instruments/instrument_service.py: `_is_broker_suffix_variant`
  accepts three decoration forms after the unchanged exact case-insensitive
  prefix bound (no known-suffix list, no fuzzy or substring matching):
  1. separator + empty tail (`XAUUSD.`) — new;
  2. separator + short alphanumeric tail (`XAUUSD.r`, `XAUUSD.p`, `XAUUSD.cash`,
     `XAUUSD_m`, `XAUUSD-m`, `XAUUSD#1`) — the Step 52 form, unchanged;
  3. a short ALPHABETIC LOWERCASE tag glued to the base (`XAUUSDm`,
     `XAUUSDpro`) — new. Lowercase letters are the broker tag convention; an
     uppercase tail is how a genuinely different base symbol is spelled
     (`XAUUSDX`, `XAUUSDXAUUSD`), a digit tail marks nothing (`XAUUSD1`), and
     the alphabetic bound keeps a partial name from reading a longer symbol as
     its decoration (`US500.cash` from `US`, `NICKEL25` from `NICKEL`).
  Everything else is unchanged: the exact-then-case-insensitive-then-decoration
  order, the one-candidate requirement (zero → unknown, several → ambiguous,
  never a guess), the broker's own spelling in the result, the single catalog
  read, the provider contract and the resolve() docstring contract. A catalog
  read is still at most one extra vendor call per resolve.
- tests/test_instrument_service.py: the Step 52 bounds tests were re-pointed
  onto tails that are still not decorations (uppercase undelimited tails,
  digit-only tails, compound/oversized tails, a different base); the separator
  forms test now covers the full decoration set including the empty tail and
  the lowercase tags; new cases pin exact-wins-over-tag, ambiguity across
  separator and tag forms, and partial names never resolving even when the tail
  shape would otherwise fit.
- tests/test_financial_research.py (1 new case): the end-to-end regression —
  a broker whose catalog is `UKOIL.`, `US100.` and `XAUUSD.` resolves all three
  base requests and grades real news for each.

Untouched: the resolution order, the Instrument contract, both instrument
providers, the market-data/research/agent composition (they keep delegating to
the same InstrumentService), NewsProvider/Alpha Vantage, config, schema, and
the read-only/no-symbol_select posture. No live API request.


### Step 53 — Market Data Symbol Resolution (READ-ONLY)
Status: VERIFIED + COMMITTED (the Step 53 commit "fix(market-data): resolve
symbols through instrument catalog"; one focused commit carrying the diff, the
tests and documentation — the established single-commit convention, so no
separate hash-recording commit follows)

Makes GET /market-data/{symbol} use the resolution semantics the instrument and
research surfaces already use, so ``xauusd`` reads the broker's ``XAUUSD`` and a
broker whose catalog only lists ``XAUUSD.r`` is read for ``XAUUSD.r`` instead of
returning 404.

Includes:

- app/services/market/market_data_service.py: MarketDataService takes an
  OPTIONAL InstrumentService (None = the exact behaviour before resolution) and
  its get_market_data() resolves the requested symbol FIRST, passing the
  broker's own canonical spelling to the unchanged candle provider. No
  resolution logic is duplicated: the service asks the existing instrument
  boundary and uses its answer; the ValueError/RuntimeError contract is
  unchanged, so an unknown/ambiguous symbol stays a client error and an
  MT5/catalog failure stays an availability error — both decided before any
  candle is read.
- app/core/dependencies.py: get_market_data_service composes the SAME
  instrument service GET /instruments uses (one resolution architecture, one
  credential path, provider selection still explicit at the composition root).
- tests/test_market_data_service.py (11 new cases): case-insensitive requests
  reading the broker's canonical spelling, a suffixed-only broker catalog
  resolving the base symbol, several variants failing closed before any candle
  read, unknown symbol and catalog failure likewise, determinism, exact and
  unrelated symbols unaffected, the no-instrument-service pass-through, and the
  bounded cost (one symbol lookup, and at most one catalog scan).
- tests/test_market_data_auth.py (8 new cases): the same guarantees at the HTTP
  boundary — case-insensitive resolution for XAUUSD, a suffixed-only catalog,
  multiple variants and unknown symbols staying the existing 404 (with no candle
  read), a catalog failure staying the existing generic 503, and the response
  schema/credential-free body unchanged; the fixture now installs the tenant's
  catalog at the composition seam.
- tests/test_mt5_lifecycle.py: the per-request provider test now also asserts
  the instrument provider is built per request for the SAME tenant and that the
  candle read was asked for the broker's own spelling; the recording market-data
  fixture supplies the catalog seam.

Untouched: InstrumentService and its resolution rules, the Instrument contract,
the MT5/fake instrument and market-data providers, the Candle contract and the
market-data response schema, financial research, the agent, the prompt builder,
the fundamental/economic services, NewsProvider/Alpha Vantage, the configuration
matrix, the LLM contract, trading paths, schema and migrations. The affected
suites are fully offline and no live API request was made.


### Step 52 — Safe Broker-Suffix Resolution (READ-ONLY)
Status: VERIFIED + COMMITTED (the Step 52 commit "feat(instruments): safely
resolve unique broker symbol suffixes"; one focused commit carrying
implementation, tests and documentation — the established single-commit
convention, so no separate hash-recording commit follows)

Makes a broker that suffixes its whole instrument catalog usable. Focus
detection (and a user) names a base symbol such as XAUUSD; a broker may only
list XAUUSD.r. Until now that name was unresolvable, so research and the agent's
research block were dropped for such a broker. Resolution now accepts a UNIQUE
broker-suffixed spelling of the requested base symbol — and nothing else.

Includes:

- app/services/instruments/instrument_service.py: resolve() gained a third,
  final step after the two existing ones (exact spelling, then unique
  case-insensitive match, both unchanged): a unique suffix variant of the
  requested base symbol. The rule is a FORM rule with two deliberate bounds, and
  is implemented as `_is_broker_suffix_variant` over the requested name and the
  broker's own catalog:
  - the candidate must begin with the requested name (case-insensitive) and its
    remainder must START with a broker separator (`.`, `_`, `-`, `#`) — so an
    undelimited tail (`XAUUSDm`) or simply a longer symbol (`XAUUSDX`) is never
    read as a suffix; and
  - the tail after the separator must be a short (1-8) alphanumeric token, so a
    descriptive or compound tail (`XAUUSD.verylongsuffix`, `XAUUSD.r.x`) is not
    one either.
  Exactly one suffix candidate resolves; zero or several stay a ValueError
  (unknown / ambiguous, with the existing messages), so a broker listing both
  XAUUSD.r and XAUUSD.m is never a coin flip. The returned
  Instrument.symbol is always the broker's own spelling, nothing about the
  symbol's meaning is inferred, and the rule adds no catalog read (the same
  single extra catalog scan the case-insensitive fallback already performed).
  No substring, prefix, alias or fuzzy matching exists anywhere: a partial name
  (`US`, `USO`, `GOL`) never resolves to the longer instrument.
- tests/test_instrument_service.py (25 new cases): a unique suffixed spelling
  resolving for a requested base (also when only the case differs), the
  documented separator forms, exact and case-insensitive-exact still winning
  over a suffixed variant, several variants staying ambiguous (in any catalog
  order), no variant staying unresolved, a variant of a DIFFERENT base not
  matching (`XAUUSDT.r` for `XAUUSD`), seven tail shapes that must never match,
  partial names never resolving (substring/prefix guard), unrelated catalog
  symbols not interfering, and determinism.
- tests/test_financial_research.py (3 new cases): the suffixed broker now
  resolving the base symbol, several variants staying unresolved, and the
  Step 48 profile still grading a suffix-resolved spelling as RELEVANT; the two
  Step 51 cases whose intent was "this broker does not offer it" were moved onto
  a catalog with no variant of the requested symbol, so their meaning is
  unchanged.
- tests/test_financial_research_api.py (2 new cases): a suffixed-only broker
  answering 200 with the broker's canonical symbols and graded items, and two
  suffixed variants producing the deterministic 404.
- tests/test_agent_research_context.py (3 new cases + 2 re-pointed): the agent
  builds the research block for a suffixed broker, the whole real pipeline
  renders the broker's spelling (`focus: XAUUSD.r`) into the prompt, and several
  variants build no research at all instead of guessing — while the existing
  "the broker does not offer it" and byte-identical-prompt cases keep their
  meaning on a catalog that really lacks the symbol.
- tests/test_agent_api.py (1 new case): the reported symptom end to end through
  POST /agent — a broker listing only XAUUSD.r still yields the research block
  focused on XAUUSD.r.

Untouched: the Instrument contract, the providers (MT5 and fake), the research
service, the agent service, the prompt builder, FundamentalIntelligenceService,
EconomicIntelligenceService, NewsProvider/Alpha Vantage, the configuration
matrix, the LLM contract, the schema and migrations. The Step 51 resolution
architecture is unchanged in shape: the same single InstrumentService boundary,
the same FocusResolution contract (broker spelling preserved), the same 404/503
mapping. GET /instruments/{symbol} gains the same suffix resolution (it is the
same service), which is the intended single-resolution semantics. The affected
suites are fully offline: 280 cases pass with every non-loopback connect and all
non-local DNS resolution blocked, and no live API request was made.


### Step 51 — Instrument Resolution in Financial Research (READ-ONLY)
Status: VERIFIED + COMMITTED (the Step 51 commit "feat(research): resolve
instruments through MT5 catalog"; one focused commit carrying implementation,
tests and documentation — the Step 48/50 single-commit convention, so no
separate hash-recording commit follows)

Makes financial research work for any instrument the tenant's broker actually
offers: requested symbols are resolved through the Step 50 instrument boundary
before anything is researched, and the broker's own canonical spelling is what
travels. Existing code paths keep working unchanged, because resolution is a
pass-through when no instrument service is wired.

Includes:

- app/services/fundamental_intelligence/research.py: an OPTIONAL
  `instrument_service` on FinancialResearchService (default None = the exact
  Step 49 behaviour), a FocusResolution NamedTuple (requested / resolved /
  unresolved) and the public `resolve_focus_symbols()` step that produces it,
  and `unresolved_symbols` on FinancialResearchContext. build_research() now
  grades only broker-confirmed spellings: a requested name the catalog cannot
  identify (unknown, ambiguous or unusable) is reported as unresolved and is
  never graded or presented as a real instrument, while an MT5/catalog
  availability failure still propagates as RuntimeError (the established 503).
  No catalog or provider logic is duplicated here: resolution goes only through
  the instrument service, which owns the tenant-scoped session and the
  deterministic presentation rules.
- app/services/fundamental_intelligence/__init__.py: exports FocusResolution.
- app/api/fundamental_intelligence_router.py: the research endpoint resolves the
  requested symbols first (off the event loop, through the consolidated MT5
  blocking boundary), returns the deterministic 404
  ("Instrument unavailable for the requested symbol", the same contract
  GET /instruments/{symbol} and GET /market-data/{symbol} use) when any name is
  not offered, and then researches only the resolved canonical symbols — which
  build_research verifies again, so no caller can bypass resolution. 422 (blank
  symbol list / unusable window) and the generic 503 (catalog or news failure)
  are unchanged, and `focus_symbols` in the response is documented as the
  broker's canonical spelling.
- app/services/agent/agent_service.py: when the request names a focus
  instrument, the agent resolves it through the research service FIRST and
  researches only what the broker confirmed; if nothing resolves, no research is
  built at all (no look-back news is fetched) and the prompt is byte-identical
  to the same request without a research service — the mandatory calendar and
  fundamental context still answer it. The agent owns no catalog logic.
- app/core/dependencies.py: get_financial_research_service now lives below the
  authentication boundary (it needs this tenant's MT5 identity) and composes the
  SAME instrument service GET /instruments uses, so there is one resolution
  architecture and one credential path; get_agent_service passes the credentials
  it already resolved. It still reads no positions and holds no tenant identity.
- tests/test_financial_research.py (11 new cases): resolution to the broker's
  canonical spelling, unresolved reporting (alone and beside a resolved symbol),
  case-only differences resolving to the broker's spelling, a case collision the
  broker lists twice staying unresolved, deterministic normalization, the
  no-catalog pass-through (unverified ≠ unresolved), an unprofiled symbol
  staying researchable, profile grading after resolution, and catalog failure
  failing closed with no fabricated result.
- tests/test_financial_research_api.py (12 new cases): the broker-canonical
  spelling echoed, profile grading after resolution, arbitrary broker symbols
  (AAPL/LVMH/BTCUSD/COFFEE/NICKEL) researchable without a profile, 404 for an
  unknown instrument and for one unknown among several (no partial result), the
  generic 503 for a failing catalog and for a tenant without a usable MT5
  session, and per-tenant credential binding of the catalog read; the suite now
  patches the instrument provider CLASS seam so the real resolution runs offline.
- tests/test_agent_research_context.py (7 new cases): the detected instrument
  resolved before research, no research for a name the broker does not offer
  (with the mandatory context still answering), the byte-identical prompt when
  nothing resolves, catalog failure propagation with the LLM never asked, no
  catalog read for a request naming nothing, the Step 49 pass-through without a
  catalog, and profile grading on a resolved broker spelling.
- tests/test_agent_api.py (3 new cases + the catalog seam in its fixture): the
  named instrument is resolved off the event loop for the request's own tenant,
  a catalog failure is the generic 503 with no LLM call, and a request naming no
  instrument never consults the catalog.
- tests/test_agent_wiring.py: the research wiring assertion now also proves the
  research service resolves through a broker catalog.

Untouched: the Instrument contract, InstrumentService, MT5InstrumentProvider and
FakeInstrumentProvider (Step 50 is unchanged — no new exception type, no changed
resolution rule), NewsProvider/Alpha Vantage/QuantGist/fake news, the news
service, FundamentalIntelligenceService, EconomicIntelligenceService, the
fundamental endpoint, the LLM contract, the prompt-rendering semantics, the
configuration matrix, egress, schema and migrations. The affected suites are
fully offline: 246 cases pass with every non-loopback connect and all non-local
DNS resolution blocked.


### Step 50 — MT5 Instrument Discovery & Resolution (READ-ONLY)
Status: VERIFIED + COMMITTED (the Step 50 commit "feat(instruments): add MT5
instrument discovery and resolution"; one focused commit carrying implementation,
tests and documentation — the Step 48 single-commit convention, so no separate
hash-recording commit follows)

Makes every instrument a broker's own MT5 account offers discoverable and
resolvable through a generic contract, so research, relevance and any future
per-instrument feature can be driven by what the broker actually lists instead of
by a hardcoded symbol list. XAUUSD remains an important case, not a special one.

Includes:

- app/providers/instrument.py (NEW): the vendor-neutral contract — the
  seven-field read-only Instrument NamedTuple (symbol, name, asset_class,
  base_currency, quote_currency, digits, trade_mode) and the InstrumentProvider
  ABC (get_instrument / list_instruments). The contract carries identity and
  metadata only: no price, no position, no relevance and no fundamental field.
  `symbol` is the broker's own spelling and is never re-cased (broker names are
  case-sensitive and carry suffixes such as XAUUSD.r / US500.cash); every other
  field is nullable because brokers genuinely omit them, and None means "the
  broker did not provide it". TradeMode (StrEnum: DISABLED / LONG_ONLY /
  SHORT_ONLY / CLOSE_ONLY / FULL) is the availability answer, mapped by value
  from MT5's documented numeric constants so an unrecognised mode fails loudly
  rather than being guessed.
- app/providers/mt5_instruments.py (NEW): the read-only MT5 implementation over
  the EXISTING tenant-scoped MT5SessionManager path (no second credential
  mechanism), reading inside `acquire` exactly like the account/positions/
  market-data providers. The only MT5 calls are symbol_info and symbols_get;
  symbol_select is deliberately NOT called because it mutates terminal state.
  Mapping rules: name ← MT5's `description`, asset_class ← the broker's own
  top-level symbol-group path segment, base/quote currency ← MT5's
  currency_base/currency_profit passed through verbatim (not parsed into an FX
  pair — MT5 uses them for a security ticker and settlement currency too),
  whitespace-only vendor text → None, digits → int, malformed records →
  RuntimeError (never a false "unknown instrument"), unknown symbol
  (symbol_info → None) → ValueError.
- app/providers/fake_instrument.py (NEW): the deterministic placeholder catalog
  (no network, no MT5) spanning forex, metals, energies, an index, shares, crypto
  and a soft commodity — including a broker-suffix spelling, a restricted trade
  mode and one record with no metadata at all — delivered in terminal (unsorted)
  order so the service's ordering is what makes a response reproducible. No
  example symbol is special-cased anywhere in production logic.
- app/services/instruments/ (NEW): InstrumentService — the boundary API →
  service → provider → MT5, holding the deterministic presentation rules:
  normalize_symbol (trim; reject blank, control characters and >64 characters;
  never re-case), resolve (exact match first, then a UNIQUE case-insensitive
  match over the catalog, so "xauusd.r" resolves while the returned symbol is
  always the broker's spelling; unknown or ambiguous → ValueError; an MT5
  outage propagates as RuntimeError and never falls back to the catalog), and
  list_instruments(search) (literal case-insensitive substring over symbol and
  description — deliberately not MT5's group-mask syntax — ordered by symbol,
  capped at MAX_INSTRUMENTS = 200, reporting total + truncated so the bound is
  stated rather than hidden).
- app/api/instruments_router.py (NEW): GET /instruments/{symbol} → one resolved
  instrument (200), unknown/blank symbol → 404, MT5 availability failure → the
  existing generic 503; GET /instruments?search= → the bounded catalog
  {instruments, total, truncated} (200, never 404 for an empty match), an
  out-of-bound search → 422. Both routes are JWT-protected and tenant-scoped:
  the MT5 identity comes from the authenticated database user, and no
  account/broker parameter is accepted from the client.
- app/core/dependencies.py: get_instrument_service — the explicit, testable
  provider seam built from the shared get_mt5_credentials path (no new setting:
  MT5 is the tenant's own broker connection, exactly as for positions/account).
- app/main.py: the router is mounted; app/providers/__init__.py exports the new
  contract, provider and fake.
- tests/test_mt5_instruments.py (NEW, 28 cases): field-by-field mapping, all five
  trade modes, unknown mode / malformed record / invalid digits / empty symbol
  failing closed, missing optional metadata → None, broker-spelling preservation,
  unknown symbol vs outage, catalog mapping and empty/None catalog handling,
  tenant-scoped authentication for one and two tenants, fail-closed incomplete
  credentials (nothing read), and the read-only guarantee (the called-function
  set is a subset of {initialize, login, last_error, symbol_info, symbols_get}
  and disjoint from trading AND state-mutating functions).
- tests/test_instrument_service.py (NEW, 31 cases): normalisation, exact and
  case-insensitive resolution with broker spelling preserved, unknown/ambiguous/
  blank/oversized input, an outage NOT being treated as an unknown symbol,
  deterministic ordering and reproducibility, search by symbol and description,
  blank search = no search, empty match ≠ error, the catalog cap with total +
  truncated, and generic coverage of arbitrary symbols (AAPL, LVMH, BTCUSD,
  NICKEL, COFFEE, XAUUSD.r, USOIL, NAS100).
- tests/test_instruments_api.py (NEW, 31 cases): resolution contract (exactly
  seven fields, trade-mode words, null optionals), case-insensitive resolution,
  generic symbol coverage, raw-MT5/provider-internal leak checks, the catalog
  contract and its bound, 404/422/503 mapping, 401 for unauthenticated/invalid
  tokens and unknown users, tenant isolation (two tenants compose their own MT5
  identity; client-supplied login/broker_id/account_id are ignored), no secret
  material in any response, the blocking boundary (the provider runs off the
  event-loop thread), and a roll-call that the new routes and all pre-existing
  feature routes are still mounted.

Untouched: every existing provider (Alpha Vantage, fake news, QuantGist,
positions/account/trade-history/market-data), the news, economic and fundamental
intelligence layers, FinancialResearchService, the agent pipeline and prompt, the
LLM contract, the API surface of every existing endpoint, the configuration
matrix, the egress policy and the schema (no migration). The three suites are
fully offline: all 90 cases pass with every non-loopback connect and all
non-local DNS resolution blocked.


### Step 49 Follow-up — Financial Research in the Agent Pipeline (READ-ONLY)
Status: VERIFIED + COMMITTED (the follow-up commit "feat(agent): integrate
financial research context"; one focused commit, implementation + tests +
documentation)

Composes the Step 49 graded research context into the Agent WITHOUT duplicating
anything: one relevance mechanism (the SAME news_intelligence grading renders in
both blocks), one news fetch per window per request, and the mandatory
calendar/fundamental path untouched.

Includes:

- app/services/agent/agent_service.py: an optional financial_research_service
  constructor parameter (None keeps the previous behaviour exactly). In
  handle() the research context is composed ONLY when it adds information the
  mandatory contexts do not already carry: the request must name a focus
  instrument (detect_focus_symbols, bounded to 3 per request), the fundamental
  block must actually be in the prompt, and AGENT_RESEARCH_LOOKBACK_DAYS must
  be positive. The window is the half-open span immediately BEFORE the
  calendar window, [window_from - lookback, window_from): research and
  fundamental news can never overlap, so no item is fetched twice. The same
  `now` drives every context as before.
- app/core/config.py: AGENT_RESEARCH_LOOKBACK_DAYS (default 2, 0 disables the
  block entirely — no news fetch, no prompt block).
- app/services/agent/prompt.py: the research block ("Financial research
  (published source facts, not analysis; look-back window ... BEFORE the
  calendar window above; news source: ...; focus: ...)") renders the graded
  items through the NEW single _news_line renderer that the fundamental block
  also uses, so one item can never render differently in the two blocks. The
  empty and unavailable states are stated exactly as the fundamental block
  states them, and the window is stated in the header so look-back facts can
  never be read as belonging to today. The reduction ladder gains one rung:
  trades → fundamental → RESEARCH → calendar → PromptTooLargeError, so the
  mandatory calendar and the exposure it feeds survive longer than the
  look-back.
- app/core/dependencies.py: get_agent_service passes
  get_financial_research_service() — the same news seam GET
  /financial-research/today uses (one source selection, one failure
  behaviour); the service holds no MT5 provider and no tenant identity.
- tests/test_agent_research_context.py (NEW, 23 cases): research facts,
  Step 48 grading (RELEVANT direct / POTENTIALLY_RELEVANT macro /
  NOT_OBVIOUSLY_RELEVANT) and provenance in the prompt; facts-not-analysis
  labelling; the look-back window statement; no account/position/tenant data
  in the research block; built only for the request's own focus instruments,
  not built without one, not built when the look-back is 0; the exact
  half-open look-back span; multiple focus instruments graded together; the
  focus bound; empty vs unavailable; bounded excerpts; the research reduction
  rung (research goes BEFORE the mandatory calendar); failure propagation
  (LLM never asked); build order fundamental → research; unchanged prompt and
  envelope without a research service; determinism; the full real-source
  pipeline.
- tests/test_agent_wiring.py: the composition-root wiring test for the new
  parameter over the development fake source.

Untouched: every provider (Alpha Vantage, fake, QuantGist),
FinancialResearchService/FinancialResearchContext themselves, the
FundamentalContext contract, the fundamental/economic services, the API
surface, NEWS_SOURCE/NEWS_MAX_ITEMS, the egress policy, the scope guard, the
usage limiter, the LLM contract and the schema. No live API request was made.


### Step 48 — Instrument-Aware Fundamental Relevance (READ-ONLY)
Status: VERIFIED + COMMITTED (the Step 48 commit "feat(fundamental): generalize
instrument-aware relevance"; one focused commit per instruction, so no separate
hash-recording commit follows it)

Answers the question the product actually needs — not "is this article about
XAUUSD?" but "is this factual item/event about a fundamental factor that can
reach this instrument?" — without a numeric score, a sentiment model, a learning
step, a new provider, a database table or an LLM call.

Includes:

- app/services/instrument_intelligence/ (NEW, a leaf package: it imports nothing
  from the intelligence layers, so both can depend on one vocabulary without an
  import cycle):
  - domains.py — the shared fundamental-factor vocabulary. Twelve domains
    (precious metals, crude oil, energy supply, the US dollar, monetary policy,
    inflation, labor market and growth, rates and yields, geopolitical risk,
    technology sector, major technology companies, trade and tariffs), each with
    a human label and a whole-word/phrase keyword list; text is normalized
    (lower-case, hyphens treated as spaces) before matching, results come back in
    table order, and a match reports which keyword matched.
  - profiles.py — RelevanceKind (DIRECT / MACRO / INDIRECT) and the static
    instrument profiles: XAUUSD (direct: precious metals; macro: monetary policy,
    inflation, labor/growth, rates/yields, the dollar; indirect: geopolitical
    risk, crude oil, energy supply, trade/tariffs), USOIL/WTI (direct: crude oil,
    energy supply, geopolitical risk; macro: growth, policy, inflation, the
    dollar; indirect: rates/yields, trade/tariffs) and NASDAQ-100 (direct:
    technology sector, major technology companies, trade/tariffs; macro: policy,
    inflation, growth, rates/yields; indirect: energy supply, crude oil,
    geopolitical risk, the dollar). Each profile declares its symbol roots
    (prefix match, so XAUUSD.r / USOIL.cash / NAS100.i resolve), its canonical
    label and the explicit instrument names a question may contain.
- app/services/fundamental_intelligence/relevance.py: ``classify_instrument_relevance``
  grades one item against one instrument through its profile — a DIRECT match is
  RELEVANT, a MACRO or INDIRECT transmission is POTENTIALLY_RELEVANT — and
  returns the kind, the matched domains and a factual reason naming both. With no
  profile, or when the profile matches nothing, the Step 47 symbol-string
  classifier decides, so EURUSD/US30/any unprofiled instrument behaves exactly as
  before (the declared-tag path deliberately keeps its conservative level: a
  vendor tag is a labelling claim, not evidence about the underlying asset).
- app/services/economic_intelligence/relevance.py: the calendar layer now uses the
  SAME vocabulary. For a symbol with a currency leg the currency-scoped level
  contract is untouched and the profile only attributes the factor ("FOMC Rate
  Decision" and "Fed officials signal fewer rate cuts" resolve to the same
  monetary-policy domain); for a symbol with NO currency leg (an index CFD) the
  profile decides instead of the old "cannot be established" verdict, which is the
  only route by which a calendar classification may assert RELEVANT.
- app/services/fundamental_intelligence/fundamental_intelligence_service.py: each
  news item carries its per-instrument classification (so one item can be
  RELEVANT to USOIL and POTENTIALLY_RELEVANT to XAUUSD), matched instruments are
  derived from the per-instrument levels, and each position exposure gains
  ``factors`` — the documented subject areas its own drivers matched, in
  vocabulary order — stated in the human-readable reason. Exposure for a symbol
  with no currency leg is now KNOWN when its profile found drivers (and still
  UNKNOWN with the unchanged reason when the symbol has neither a currency leg nor
  a profile, or when nothing could be assessed).
- app/services/fundamental_intelligence/focus.py: focus detection gains explicit
  instrument names (USOIL, WTI, XTIUSD, OILUSD, NASDAQ, NAS100, US100, USTEC,
  reported under the profile's canonical symbol). Commodity words and bare
  currencies are still refused, so "what is happening with gold/oil/USD" never
  becomes an instrument by accident.
- app/services/agent/prompt.py: a news line whose relevance came from a profile
  states the relationship, scope and factor — "(direct factor for XAUUSD:
  precious metals)" — so the model can explain WHY an item matters instead of
  guessing from keywords; an item classified by the symbol view renders exactly as
  before. No contract field was added to any HTTP response.
- tests: tests/test_instrument_profiles.py (58), tests/test_instrument_relevance.py
  (43) and tests/test_fundamental_multi_instrument.py (27) — the 22-scenario matrix
  (XAUUSD, USOIL, NASDAQ), the cross-instrument negatives, multi-domain matches,
  whole-word/hyphen determinism, the direction-free vocabulary, the shared
  calendar/news domain, the no-currency-leg rule, one item at different levels per
  instrument, the integrated multi-instrument context with driver ids and
  provenance, the realistic user questions and the prompt's factor rendering.
- ONE pre-existing assertion updated (tests/test_alphavantage_news.py): the Step
  47A test that pinned the real feed's gold headline at POTENTIALLY_RELEVANT now
  expects RELEVANT, because Step 48 deliberately grades a DIRECT asset match as
  the strongest level. The test's intent (the deterministic mapping reaches
  XAUUSD, with no sentiment involved) is unchanged.

No provider, API, dependency, configuration, schema, migration or trading file
changed: this step is purely the intelligence/relevance layer, so Alpha Vantage
and every other provider is untouched and no live API request was made.


### Step 47A — Alpha Vantage News Source (DEVELOPMENT/TEST ONLY)
Status: VERIFIED + COMMITTED (e434659)

Makes the news source real for development: Alpha Vantage's free-tier News &
Sentiment feed becomes the provider the Step 47 contract is served by, with the
deterministic fake kept as the offline fallback. Nothing above the provider
boundary changed — the news service, the fundamental context, the relevance
layer, the API endpoints, the agent prompt, tenant isolation and the read-only
boundary are all untouched, and no production or licensing claim is made.

Includes:

- app/providers/alphavantage_news.py: AlphaVantageNewsProvider (source =
  "alphavantage-free-development"). One bounded NEWS_SENTIMENT query per call:
  the caller's half-open UTC window sent as the vendor's own time filter
  (YYYYMMDDTHHMM) and re-applied locally, sort=LATEST, the requested result cap
  clamped to the vendor maximum (1000, default 50), and a non-empty declared-tag
  filter pushed to the vendor's ticker parameter. Parsing is exact (a lenient
  strptime partial match is rejected by re-formatting the result) and fails closed
  on a missing title/publisher/publication time, an unusable timestamp shape, an
  invalid-JSON body, a non-dict envelope, a `feed` that is not a list, or a row
  that is not an object. Mapping carries title, publisher, the article URL when
  present, the vendor's own excerpt bounded to 400 characters plus a marker
  (strictly inside NewsService's 600-character boundary), an aware UTC
  `published_at`, the vendor's topic labels as categories and a stable article id
  (sha256 of the URL, or of the title and instant when no URL exists). The
  vendor's sentiment scores/labels, authors, banner images and ticker tags are
  dropped, and no article page is ever fetched.
- Secret hygiene: the vendor requires the key as a query parameter, and httpx logs
  the full request URL at INFO. The adapter therefore installs, once per
  configured value, a redaction filter on the httpx logger that removes that exact
  key from any record (message and arguments, including non-string arguments such
  as httpx.URL); the filter never drops a record and changes no logging level. The
  adapter itself emits no record, never formats the URL into an error message and
  never chains the original httpx exception.
- app/core/config.py: NewsSource gains ALPHAVANTAGE, plus ALPHA_VANTAGE_API_KEY
  (default empty — no key is committed or defaulted), ALPHA_VANTAGE_BASE_URL and
  ALPHA_VANTAGE_TIMEOUT_SECONDS. An invalid value for any of them is reported by
  setting name only, never by value.
- app/core/dependencies.py: get_news_service resolves the new source at the same
  seam. In development, auto selects Alpha Vantage when the key is configured and
  the deterministic fake otherwise; alphavantage requires a key and refuses
  ("News data source is not configured") without one, never falling back. Outside
  development, auto still means no news source and an explicit alphavantage
  refuses with the same generic detail, so a configured key alone never makes a
  non-development deployment serve it. production still refuses everywhere.
- .env.example: documents the new NEWS_SOURCE value, the Alpha Vantage settings
  and the development/test-only posture (including the redaction measure).
- tests: tests/test_alphavantage_news.py (provider contract, request shape and
  bounds, timestamp/window normalization, mapping, id stability, excerpt
  bounding, malformed envelope/row/JSON, HTTP-200 Information/Note bodies,
  transport/timeout/non-200 translation, key-absence, redaction, and the
  NewsService → FundamentalIntelligence → agent-prompt integration); the Alpha
  Vantage cells in tests/test_news_source_configuration.py (auto/explicit
  selection, missing key, non-development refusal, value-free logging,
  constructor wiring); and the settings cases in tests/test_config_settings.py.
  Every suite pins the key empty unless a test configures a test-only marker, so
  no automated test can reach the network (verified by running the full suite
  with outbound networking disabled).
- verification: focused 423 passed; full suite 1213 passed with 2 pre-existing
  third-party warnings, run with socket.getaddrinfo/create_connection disabled;
  compileall clean; pyright 0 errors / 0 warnings on the new provider and all
  changed files; git diff --check clean; no trading function anywhere in app/; a
  repository-wide sweep for the configured key value found no file outside .env;
  and ONE live smoke request (no retry) returned 20 real articles through the
  real composition-root seam, with aware UTC timestamps inside the requested
  window, bounded excerpts and no key in any output.
- unchanged: the NewsProvider contract, NewsService, the relevance layer, the
  fundamental intelligence service/endpoint, the agent pipeline, tenant
  isolation, the scope guard, the usage limiter, the egress policy, the LLM
  router, the run_mt5_call boundary, the QuantGist adapter, database schema and
  migrations. No caching, retry, scheduler, scraping, article fetch, news pool,
  MCP or tool/function calling. The AI remains strictly READ-ONLY.

### Step 47 — News & Fundamental Intelligence (READ-ONLY)
Status: VERIFIED + COMMITTED (c44953d)

Adds the first fundamental-intelligence capability as a real vertical slice:
relevant news beside the mandatory calendar, deterministic relevance for the
instruments actually in play (XAUUSD first), each open position's factual
fundamental exposure, an HTTP endpoint for it, and the same context composed into
every agent prompt. It is deterministic, read-only and has no production news
vendor (see Known Issues item 15).

Includes:

- app/providers/news.py: the vendor-neutral NewsProvider contract with the
  typed NewsItem snapshot (item_id, published_at, publisher, title, a bounded
  summary, an optional url, declared instruments/currencies/categories) and a
  bounded get_news(from, to, instruments=(), limit=None) read. Every field is
  factual; there is no sentiment, score, forecast or vendor object.
- app/providers/fake_news.py: FakeNewsProvider, the deterministic
  development/test source (source = "fake-development-placeholder"): a fixed
  placeholder catalog materialized on the UTC dates the requested half-open
  window touches, with date-scoped ids, no network, no clock, no files, and a
  deliberate mix of tag shapes (tagged items, an untagged central-bank item, and
  an item with no identifiable reference at all) so the relevance layer is
  exercised honestly.
- app/services/news/: NewsService, the single business entry point — UTC
  window validation (naive or inverted windows are rejected), provider payload
  validation that fails closed on a naive timestamp or an excerpt above
  MAX_SUMMARY_CHARS (600), a local re-filter against the half-open window so a
  vendor's filtering semantics cannot leak an out-of-window item, deterministic
  ordering by (published_at, item_id), and a hard cap at the deployment's
  NEWS_MAX_ITEMS.
- app/core/config.py + app/core/dependencies.py: NewsSource (auto |
  development_fake | production, default auto) plus NEWS_MAX_ITEMS (default 20),
  and get_news_service()/get_fundamental_intelligence_service() at the
  composition root. auto in development serves the deterministic fake; auto
  anywhere else means NO news source (a supported state the context reports as
  unavailable); development_fake outside development and production everywhere
  refuse with the generic 503 detail "News data source is not configured" while
  logging one precise, value-free reason for the operator. No fallback ever
  happens, and the news seam reads no credential setting.
- app/services/fundamental_intelligence/relevance.py: deterministic news
  relevance built ON the existing calendar classifier (RelevanceLevel, the
  symbol tokenizer, the shared is_metal_instrument rule and the level ranking),
  with declared tags winning, a small documented whole-word keyword map for
  untagged items, and NOT_OBVIOUSLY_RELEVANT plus a factual reason whenever
  relevance cannot be established.
- app/services/fundamental_intelligence/focus.py: deterministic focus-instrument
  detection for the agent request (a token qualifies only when it reconstructs
  exactly from known currency tokens, so XAUUSD qualifies and a bare USD, a
  commodity name like "gold" or a suffix variant like XAUUSD1 does not). It is a
  label for relevance and never a scope decision.
- app/services/fundamental_intelligence/fundamental_intelligence_service.py:
  FundamentalIntelligenceService.build_context(calendar, focus_symbol) →
  FundamentalContext (as_of, the calendar's own half-open window, focus symbol,
  instruments in play, the calendar context, news availability/provenance/
  reason, per-item relevance and matched instruments, and one
  PositionFundamentalExposure per open position with KNOWN/UNKNOWN status, a
  factual reason and the calendar/news driver ids). No MT5 read of its own and
  no LLM call; positions arrive with the calendar context.
- app/api/fundamental_intelligence_router.py + app/main.py:
  GET /fundamental-intelligence/today?symbol=<optional>. JWT-protected, tenant
  identity only from the authenticated user, calendar built first and always,
  composition offloaded through run_mt5_call, 422 for a blank symbol, and the
  generic 503 ("Fundamental intelligence service temporarily unavailable") for
  MT5/calendar/news infrastructure failures.
- app/services/agent/: the fundamental context is built from the SAME calendar
  context the request already has and rendered as a labelled public-data block
  (news facts with relevance, matched instruments, provenance and a bounded
  excerpt, plus factual position exposure); the reduction ladder gained one
  ordered step (trades → fundamental → calendar → PromptTooLargeError).
- app/services/economic_intelligence/: exports is_metal_instrument and
  relevance_rank (reuse, not a parallel classifier) and carries the ordered
  positions snapshot in EconomicIntelligenceContext (defaulted, so existing
  construction is unchanged and the HTTP contract of GET
  /economic-intelligence/today is untouched).
- .env.example: documents NEWS_SOURCE and NEWS_MAX_ITEMS (including that no news
  source is a supported state and that no production vendor exists), corrects the
  stray leading line, and updates the prompt-reduction comment to the new ladder.
- tests: tests/test_news_provider.py (25), tests/test_fundamental_relevance.py
  (35), tests/test_fundamental_intelligence.py (43),
  tests/test_fundamental_intelligence_api.py (24),
  tests/test_agent_fundamental_context.py (30) and
  tests/test_news_source_configuration.py (17), plus extended cases in
  tests/test_agent_api.py, tests/test_agent_wiring.py and
  tests/test_config_settings.py. All deterministic and offline: no MT5, no
  database beyond the per-test SQLite auth fixture, no news vendor, no network,
  no LLM.
- verification: focused 485 passed; safety/tenant/config set 444 passed; full
  suite 1134 passed, 2 warnings (both pre-existing third-party deprecations);
  compileall clean; pyright 0 errors / 0 warnings on the changed application
  files and all six new test modules (the two extended legacy API test files keep
  only their pre-existing diagnostics); git diff --check clean; read-only probe of
  the rendered block; no API key or credential in any response, log line or new
  module.
- unchanged: the QuantGist adapter, the Agent API contract, the scope guard,
  usage limiter, egress policy, LLM router, run_mt5_call boundary, database
  schema and migrations. No production vendor, scraping, scheduler, caching,
  retry, tool/function calling or agent framework was added. The AI remains
  strictly READ-ONLY.

### Step 46 — Explicit Economic-Calendar Source Configuration (production seam)
Status: VERIFIED + COMMITTED (ebbb86b)

Makes the economic-calendar source an explicit, validated configuration value.
The calendar is MANDATORY for every agent request, so the source is never chosen
implicitly and an unusable one fails closed instead of degrading. This step does
NOT resolve Known Issues item 9: it makes "which source is this deployment
serving?" answerable, and gives the eventual production vendor a single place to
be registered, without inventing one.

Includes:

- app/core/config.py: EconomicCalendarSource (StrEnum: auto, development_fake,
  quantgist, production) plus ECONOMIC_CALENDAR_SOURCE, defaulting to auto. An
  invalid value is a startup configuration error rendered by the existing
  sanitized loader (setting name only, never the value).
- app/core/dependencies.py: get_economic_calendar_service resolves the configured
  source and _calendar_provider_for is the single point where a source name
  becomes a provider — the production seam. Behavior matrix (verified by tests
  and by a read-only 32-cell runtime probe): auto keeps the historical selection
  (development: QuantGist when QUANTGIST_API_KEY is configured, else the fake;
  anywhere else: refuse); development_fake/quantgist are served inside
  development only; production refuses everywhere because no production vendor is
  implemented; an explicitly selected source with missing configuration refuses
  as well. Every refusal answers the same generic 503 detail the endpoint has
  always returned ("Economic calendar data source is not configured") and logs one
  precise, value-free line naming the source, the environment and the reason, so
  an operator can distinguish a misconfiguration from an outage.
- .env.example: documents ECONOMIC_CALENDAR_SOURCE and its four values, and the
  updated QuantGist section (the key only matters when the resolved source is
  QuantGist; an empty key with source=quantgist refuses).
- tests: the full source x environment matrix and the production-seam,
  missing-key, logging and secret-hygiene cases in
  tests/test_quantgist_economic_calendar.py; the invalid/valid source values in
  tests/test_config_settings.py; one end-to-end refusal each in
  tests/test_economic_intelligence_api.py and tests/test_agent_api.py (with the
  source pinned in both files' autouse fixtures, so a developer's local .env
  cannot change what they describe). Nothing touches the network.
- verification: focused 274 passed; full suite 948 passed, 2 warnings (both
  pre-existing third-party deprecations); compileall clean; pyright 0 errors / 0
  warnings on the two changed application files and the changed test files except
  test_agent_api.py's 14 pre-existing diagnostics (none at or after the added
  lines); git diff --check clean.
- unchanged: the QuantGist adapter, the Agent API contract, the scope guard,
  usage limiter, egress policy, LLM router, run_mt5_call boundary, database
  schema and migrations. No retry, caching, background job, tool calling or new
  provider. The AI remains strictly READ-ONLY.

### Step 45 — Economic Intelligence in the Agent Pipeline (READ-ONLY)
Status: VERIFIED + COMMITTED (b9785cb)

Composes the EXISTING economic intelligence into the existing agent pipeline. It
adds no calendar logic to the agent, no tool/function calling, no agent
framework, no retry, no cache and no new provider — the agent composes, the
EconomicIntelligenceService fetches.

Includes:

- app/services/agent/agent_service.py: an OPTIONAL injected
  EconomicIntelligenceService. handle() resolves ONE reference instant
  (now or datetime.now(UTC)) and passes that same instant to the financial
  context and to EconomicIntelligenceService.build_today_context(now=...), so
  the trade-history window and today's UTC calendar window can never disagree.
  The service is a dependency, never constructed here, and failures propagate
  unchanged (the API maps them to its existing 503). Without it (an existing
  caller, or a deployment with no calendar capability) the prompt is exactly
  what it was before this step.
- app/services/agent/prompt.py: build_prompt(request, context, policy, economic)
  adds a dedicated economic block rendering the existing contract only —
  timestamp, currency, impact, forecast/previous/actual exactly as published
  (null renders as "-"), the event's deterministic relevance level, and the
  source's provenance marker (data_source) so delayed or placeholder data
  cannot be read as live market data. An empty calendar renders an explicit
  "no economic events are published for today" line. Per-position relevance
  reasons stay in the contract and are deliberately not rendered.
- size discipline preserved and extended by exactly one ordered step: the trade
  block is still dropped first, then the economic block, and only after that
  does PromptTooLargeError fail the request — each omission stated in the body
  rather than truncating silently.
- app/core/dependencies.py: get_agent_service composes the injected calendar
  through the EXISTING get_economic_intelligence_service(credentials) path (the
  one GET /economic-intelligence/today uses), so there is one calendar
  architecture and one relevance classifier, and the calendar stays MANDATORY
  for every agent request.
- tests: tests/test_agent_economic_context.py (new, 23 cases), three
  end-to-end cases in tests/test_agent_api.py (the calendar reaching the prompt,
  a calendar failure becoming 503, and the unchanged response contract) plus the
  calendar-source pin in that file's autouse fixture, and one composition-root
  case in tests/test_agent_wiring.py. Nothing touches the network.
- verification: focused 249 passed; agent area 75 passed; full suite 924
  passed, 2 warnings (both pre-existing third-party deprecations); compileall
  clean; pyright 0 errors / 0 warnings on the three changed application files
  and the new test file. Tests were written first (25 failures before the
  implementation). No schema change, no migration, no documentation change
  outside this checkpoint.
- read-only and safety: the prompt still carries no account identity, the three
  LLM_SEND_* egress switches keep governing the customer financial data they
  always did (calendar data is public market data and carries no account
  identity), and no trading capability of any kind was added or changed.

### Step 44 — QuantGist Economic Calendar Source (DEVELOPMENT/TEST ONLY)
Status: VERIFIED + COMMITTED + PUSHED (638f972)

Connects a real HTTP economic-calendar source behind the existing provider
abstraction as a temporary development/test stand-in. It does NOT resolve Known
Issues item 9: the QuantGist free tier is delayed and quota-limited and is
explicitly not this project's commercial vendor, so no production calendar source
exists after this step.

Includes:

- app/providers/quantgist_economic_calendar.py: QuantGistEconomicCalendarProvider
  implementing the unchanged EconomicCalendarProvider interface (source marker
  "quantgist-free-development", get_events(from, to) -> tuple[EconomicEvent, ...]).
  The adapter is written against the VERIFIED LIVE API (a one-request smoke test
  on 2026-09-16): events live under a paginated "data" envelope, every date
  filter is IGNORED, and the free feed is release_time-ascending. It therefore
  walks GET /v1/calendar?page=N — one request per page it actually needs,
  stopping at the first event at or past the window end — instead of one request
  per UTC day, and the half-open [from, to) window is enforced entirely locally,
  so the vendor's ignored/inclusive date filtering cannot leak an event at or
  past the window end. Pagination is bounded by total_pages/has_more and the
  envelope is validated fail-closed (bools rejected as ints, the echoed page
  must match, per_page/total_pages >= 1, total >= 0) whenever a whole page is
  read without reaching the window end. The key travels only in the X-API-Key
  header.
- the EconomicEvent contract is unchanged: vendor id -> event_id, release_time ->
  timezone-aware UTC timestamp, impact low|medium|high -> EventImpact, and numeric
  forecast/previous/actual rendered as published strings without inventing
  precision (null stays null). Vendor extensions (surprise_pct, sentiment_score,
  tags) never cross the boundary.
- error handling at the provider boundary only: a missing key or blank base URL
  fails before any request; transport failures, every non-200 response, invalid
  JSON, structurally wrong payloads, unmapped impacts, naive timestamps and
  unusable value types all raise RuntimeError, which the existing API layer maps to
  its generic 503. The API key never reaches a message, a URL or a log line.
- configuration: QUANTGIST_API_KEY / QUANTGIST_BASE_URL / QUANTGIST_TIMEOUT_SECONDS
  (empty key default). The key comes only from the environment/.env (git-ignored),
  never hardcoded and never committed; .env.example documents all three and the
  source's development/test-only status.
- composition root: get_economic_calendar_service() keeps its fail-closed guard
  (503 outside development) and now selects QuantGist when a key is configured,
  otherwise the deterministic fake, so development behaviour and the existing
  suite are unchanged. No caching, no retry, no multi-provider framework, and no
  change to the service, API, schema, or JWT.
- tests: tests/test_quantgist_economic_calendar.py (92 cases in that file and
  the Step 45 composition file together, fully offline through an injected
  httpx.MockTransport: live-shape mapping, provenance, request shape, pagination
  with the early stop, the local half-open window filter, envelope validation,
  every failure mode, the key never being echoed, both verified live timestamp
  shapes, and the composition-root selection), one end-to-end test in
  tests/test_economic_intelligence_api.py proving the configured source reaches
  GET /economic-intelligence/today as data_source, and the QUANTGIST_API_KEY pin
  in that file's autouse fixture so a developer's .env cannot change what its
  existing tests describe.
- verification: focused 136 passed; full suite 897 passed, 2 warnings (both
  pre-existing third-party deprecations); compileall clean; pyright 0 errors / 0
  warnings across the three changed Python files; git diff --check clean; one
  live smoke request (a throwaway script, deleted afterwards) returned 3 real
  events with correct UTC timestamps, impact mapping, provenance marker and no
  API key in any output. The earlier fictional {"events": ...} payload shape and
  the per-UTC-day fan-out were removed along with the tests that pinned them.
- read-only: GET requests only. No trading action, no order, no position mutation
  and no recommendation; the AI remains strictly READ-ONLY, and no position/trade
  data is sent to the calendar provider.

### Step 43 — Tenant-Safe Login
Status: VERIFIED + COMMITTED + PUSHED (5afd89510af4ec5e63d4bcbf805bc9e73405f1e9)

Fixes a real cross-tenant defect. Because `login` is unique only per broker
while the endpoint matched on it alone, the second broker to register an MT5
login number made the first broker's identical number permanently unloggable
(`len(users) != 1` → generic 401), and the throttle's login bucket was shared
across tenants, so failures aimed at one broker could lock out another broker's
user. Both are now structurally impossible.

- `POST /auth/login` request is `{"broker", "login", "password"}`; the broker is
  mandatory (missing → 422), so no fallback can resolve a login ambiguously and
  no request shape reveals whether a login exists at more than one broker.
- Tenant resolution: `WHERE lower(Broker.code) = strip(lower(submitted))` —
  case-insensitive and whitespace-tolerant. Exactly one match proceeds; zero
  matches (unknown broker) and more than one match (two codes differing only by
  case, i.e. a data-integrity violation) both fail closed with the generic 401
  rather than silently selecting a tenant.
- Credential lookup is broker-scoped: `WHERE User.broker_id = <resolved
  broker>.id AND User.login = <submitted login>`, keeping the existing
  `len(...) != 1` refusal as a defensive backstop. The login is still compared
  exactly as before — the lookup was narrowed, never broadened.
- The resolved database row gates the login as well (the broker must be
  active); `broker_id` and `role` still come from the database User record and
  the JWT is unchanged (`sub = str(User.id)` only).
- Throttle: `LoginThrottle.check/record_failure/record_success` now take the
  broker, and the buckets are `("ip", address, "")` and `("login", normalized
  broker code, normalized login)` — a three-part key, so no submitted value can
  be shaped to collide with another tenant's bucket. The broker part is
  normalized exactly like the resolution above, so casing/padding cannot create
  a second bucket, and the check still runs before any database work. A
  successful login clears only that client's IP bucket and that tenant's login
  bucket.
- Timing hardening: `app/core/security.py` gains
  `dummy_password_verification()`, which pays one real bcrypt check against a
  lazily built, otherwise unused dummy hash on the paths where no stored hash
  exists (unknown/ambiguous broker, unknown login). Every other rejection
  condition already ran bcrypt against the stored hash.
- Uniform failures preserved: unknown broker, unknown login, wrong password,
  inactive user and inactive broker all return the same `401 {"detail":
  "Incorrect login or password"}` with `WWW-Authenticate: Bearer`, and every
  rejection counts against both throttle keys.
- Deliberately unchanged: database schema, `User.login` semantics,
  `(broker_id, login)` uniqueness, MT5 credential architecture, JWT structure,
  and every unrelated endpoint.
- Tests: two-broker coverage in `tests/test_auth_login.py` (both tenants share
  one login number; the named broker authenticates and the other does not; the
  broker code resolves case-insensitively; failure responses stay
  indistinguishable; one broker's throttle neither blocks nor is cleared by the
  other; login-only and per-field-missing bodies → 422; the dummy verification
  is asserted on exactly the paths that need it), broker-aware cases in
  `tests/test_login_throttle.py`, and the dummy-verification primitives in
  `tests/test_security.py`.


### Step 42 — One User Identity (`login`)
Status: VERIFIED + COMMITTED (the prior checkpoint)

A user had TWO identity columns describing the same fact: `username` (the
application login) and `mt5_login` (the MT5 account number). They are now one
column, `login`, which is both — the application login and the MT5 account
number. This is a deliberate, breaking API change: there is no compatibility
`username` field, no alias and no dual-write.

- `app/db/models/user.py`: `username` → `login` (String(100), NOT NULL);
  `mt5_login` removed; the tenant-scoped unique constraint is now
  `uq_users_broker_login` (unique per broker, same semantics). `mt5_server` and
  the encrypted MT5 INVESTOR password are unchanged, and there is still no
  trading/master password anywhere.
- Migration `a5d92c41f7be` (revises `c4a91f2e6d77`) is a true column RENAME,
  not an add-and-copy, so no obsolete `username` column can survive beside the
  new one. It is guarded and ordered: a pre-flight check aborts loudly if any
  row's `mt5_login` disagrees with the identity (instead of silently dropping a
  divergent account number), then renames the column, moves the unique
  constraint, and drops `mt5_login`. `downgrade()` restores `username` and
  writes the identity back into a recreated `mt5_login`, so the round trip is
  lossless.
- `POST /auth/login` takes `login` + the application password; the throttle
  keys on the submitted login (per IP and per login) with identical behaviour
  for existing and unknown logins. The generic 401 detail is now "Incorrect
  login or password". (Superseded by Step 43: the broker code is now mandatory
  on every login request and the throttle's login key is per `(broker, login)`.)
- User CRUD uses `login` throughout: create (`POST /users`, `POST
  /users/admins`), list, get, partial update (changing the login changes the
  application login AND the MT5 account number at once), responses and the
  duplicate-conflict path. The 4-12 ASCII-digit shape is unchanged; it is now
  documented as what it is — the MT5 account-number format.
- MT5 credential provisioning uses the SAME `User.login` as the MT5 login:
  `PUT /users/{id}/mt5-credentials` accepts only `mt5_server` +
  `mt5_investor_password`. The account-number field was removed from the
  request (so a client can never aim a credential at a different account) and
  the response is now `user_id, login, mt5_server, mt5_configured`.
- `effective_mt5_login()` now reads `user.login` (numeric → the MT5 account
  number, non-numeric → fails closed at the session boundary). The obsolete
  `mt5_login` preference/fallback logic is gone; the broker's `mt5_server`
  fallback remains, because that is still a real tenant-level configuration.
- Seed scripts (`create_dev_user.py`, `create_dev_users.py`) and every affected
  test were migrated to `login`.
- Verification: full suite **799 passed, 2 warnings**; compileall clean; the
  migration was applied to the local development database and round-tripped
  (upgrade → downgrade → upgrade) with all four rows and their credentials
  preserved, and the new divergent-`mt5_login` guard was exercised and observed
  to abort cleanly with the schema unchanged. Live end-to-end on the real app +
  Postgres + MT5: all three development accounts authenticate with `login`
  (200), an old `username` payload is refused (422), and GET /account-info
  (200, USD), GET /positions (200, 2) and GET /trade-history (200, 4 trades)
  are unchanged, as is the new 4-field credential status contract.
- Static/type verification: no mypy/pyright/basedpyright/pytype is installed and
  Pylance has no CLI, so a focused manual review was performed (reported, not
  hidden). No type suppression of any kind was added.


### Step 40 — Super Admin User CRUD
Status: VERIFIED + COMMITTED (in the Step 41 commit)

Completes user management for the broker's super_admin, entirely inside the
authenticated tenant. broker_id is always taken from the database User, never
from a request body or path, and every response keeps the same non-sensitive
projection (id, broker_id, login, email, phone, role, is_active) —
password_hash and mt5_password_encrypted are structurally absent.

- GET /users/{user_id} (super_admin): one user of the caller's broker; a user
  id belonging to another broker is reported exactly like a non-existent one
  (404), so ids cannot be enumerated across tenants.
- PATCH /users/{user_id} (super_admin): partial update of the fields user
  management supports — login, password, email, phone, role, is_active (the
  identity field is `login`: Step 42 collapsed the former `username` into it).
  An omitted field is unchanged; an explicit null clears email/phone only
  (login, password, role and is_active may not be nulled). Duplicate
  login/email/phone inside the tenant → generic 409, verified at the commit
  boundary.
- DELETE /users/{user_id} (super_admin): 204 No Content. The broker's only
  super_admin cannot be deleted (409).
- POST /users now accepts an OPTIONAL explicit role: omitted or null means
  customer (the least-privilege default — never a silent admin), "admin"
  requires a super_admin caller (403 for an admin caller), and "super_admin"
  is refused outright by the schema validator (422) for every caller. The
  database partial unique index (uq_users_broker_super_admin) remains the final
  backstop, so a second super_admin cannot be created through the API at all.
- POST /users/admins keeps a dedicated role-less request model
  (CreateAdminRequest), so its role-is-not-a-field behaviour is byte-identical
  to before; only POST /users gained the optional role.
- Demotion protection: the broker's only super_admin cannot be demoted (409),
  checked before any write; promotions are impossible because the schema
  refuses the super_admin value.
- Authorization is unchanged for everyone else: admin still manages customers
  only, customer still cannot manage users, and cross-broker targets remain
  unreachable (404). The MT5 credential provisioning endpoints and their rules
  are untouched by this step.
- Verification: focused suite tests/test_users_crud.py (25 cases) plus the
  re-run user surface (121 cases); full suite 799 passed. The only contract
  change to an existing test is deliberate and documented in
  tests/test_users_create.py: the admin-caller/role="admin" case is now an
  authorization 403 rather than a 422, matching Step 22's matrix, and that
  branch is covered by test_users_crud.py with a real admin caller.

### Fix — MT5 Trade-History Protective-Level Fields (`sl` / `tp`)
Status: VERIFIED + COMMITTED (this checkpoint)

- Defect: MT5TradeHistoryProvider._protective_levels() read order.price_sl and
  order.price_tp. Those attributes do not exist on MT5's historical order
  object, so any closing deal with a related order raised AttributeError inside
  the provider and GET /trade-history failed.
- Verified against the installed package (MetaTrader5==5.0.6180): the object
  returned by history_orders_get() is a TradeOrder with 24 fields — ticket,
  time_setup, time_setup_msc, time_done, time_done_msc, time_expiration, type,
  type_time, type_filling, state, magic, position_id, position_by_id, reason,
  volume_initial, volume_current, price_open, sl, tp, price_current,
  price_stoplimit, symbol, comment, external_id. hasattr(price_sl) and
  hasattr(price_tp) are both False; the protective levels are sl and tp.
- Fix: read order.sl / order.tp. Both mappings were wrong; both are corrected.
  Nothing else changed — the zero-sentinel rule (level <= 0 → None, compared
  Decimal-to-Decimal with no float entering the comparison), the Decimal(str())
  conversion at the MT5 boundary, and the application contract (stop_loss /
  take_profit as nullable DecimalAsNumber JSON numbers) are all preserved.
- Test doubles in tests/test_mt5_trade_history.py were mirroring the fictional
  field names, which is why the suite had passed; they now use the real
  attributes (SimpleNamespace(sl=..., tp=...)). The API leak assertion in
  tests/test_trade_history_api.py that pinned "price_sl" was replaced with the
  real raw MT5 order attribute names, so it is no longer vacuous.
- Live verification against the demo MT5 account: GET /account-info,
  GET /positions and GET /trade-history all return 200 with real data (the
  trade history returned 4 executed trades). The nonzero path was proven
  separately by running the real provider method through the real tenant
  session boundary over a real order carrying levels: it returned
  Decimal('29640.0') / Decimal('29440.0'), matching the raw ordinate values,
  while an order ticket of 0 still short-circuits to (None, None).


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

POST /auth/login  { "broker": <broker code>, "login": <account/login number>, "password": <app password> }
    ↓
broker code resolved case-insensitively to exactly one Broker row
(unknown or ambiguous code → the same generic 401 as any other failure)
    ↓
credential lookup scoped to that broker_id  (User.broker_id + User.login)
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

Client migration note (breaking change, Step 43): `broker` is now mandatory on
every login request and there is no optional or legacy form. A request without
it is refused with 422, and the previous `{"login", "password"}` body no longer
authenticates anything. Clients send the broker code they belong to
(case-insensitive, surrounding whitespace tolerated). Failure behaviour is
otherwise unchanged: the same generic 401 detail for every rejection, and 429
when the throttle trips.

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
users.mt5_server / users.mt5_password_encrypted (ciphertext)
The account number is NOT provisioned: it is the target user's own login.

Then, on every MT5-backed read:

authenticated user
    ↓
get_mt5_credentials → effective login/server: the user's login (numeric) and
    ↓                 the user's mt5_server, else Broker.mt5_server
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
get_mt5_credentials (User.login + mt5_password_encrypted + Broker.mt5_server, from the database)
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
EconomicCalendarService → EconomicCalendarProvider
        ├── FakeEconomicCalendarProvider (default, no QUANTGIST_API_KEY)
        └── QuantGistEconomicCalendarProvider (development/test, when configured)
        (both are development/test sources only — no production source yet)
    ↓
PositionService → PositionProvider → MT5PositionProvider → MT5
    ↓
deterministic relevance classification (relevance.py)
    ↓
AI-ready economic context

## Current Fundamental Intelligence Flow

Authenticated request (GET /fundamental-intelligence/today?symbol=<optional>)
    ↓
get_current_user()
    ↓
run_mt5_call (blocking boundary, app/core/blocking.py)
    ↓
EconomicIntelligenceService (the MANDATORY calendar/intelligence path above)
    ↓
FundamentalIntelligenceService (app/services/fundamental_intelligence/)
    ├── news: NewsService → NewsProvider
    │       ├── AlphaVantageNewsProvider (development/test source: the free-tier
    │       │   News & Sentiment feed, selected when its key is configured)
    │       └── FakeNewsProvider (deterministic development/test source)
    │           (no production news vendor; the production slot refuses 503)
    ├── deterministic news relevance (the existing calendar classifier's
    │   symbol-string rules, extended since Step 48 by the shared domain
    │   vocabulary + the instrument's fundamental profile — one mechanism)
    └── position exposure from the positions the calendar context already read
        (since Step 48 each exposure names the fundamental factors it matched)
    ↓
FundamentalContext (facts + provenance + relevance + exposure + UNKNOWN)

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
one reference instant (UTC) for the whole request (Step 45)
    ↓
FinancialContextService → FinancialContext (existing flows above)
    ↓
EconomicIntelligenceService → EconomicIntelligenceContext (today's UTC calendar
    window; the existing calendar/intelligence path — Step 45)
    ↓
FundamentalIntelligenceService.build_context(calendar, focus_symbol)
        → FundamentalContext (Step 47: relevant news, deterministic relevance,
          per-position factual exposure; built from the SAME calendar context,
          so no second calendar read and no second MT5 read; an unconfigured
          news source and an UNKNOWN exposure stay explicit)
    ↓
build_prompt(request, context, policy, economic, fundamental) → LLMPrompt
    (agent layer; account identity omitted, both sources' provenance preserved,
     published material labelled as source facts)
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

## Fundamental Intelligence API Contract

GET /fundamental-intelligence/today?symbol=<optional instrument> returns today's
fundamental context for the authenticated user:

    {
      "broker_id": 1,
      "as_of": "2026-09-16T09:15:00Z",
      "window_from": "2026-09-16T00:00:00Z",
      "window_to": "2026-09-17T00:00:00Z",
      "focus_symbol": "XAUUSD",
      "instruments": ["EURUSD", "XAUUSD"],
      "calendar": {
        "data_source": "fake-development-placeholder",
        "events": [ {"event": {...}, "overall_relevance": "POTENTIALLY_RELEVANT",
                      "positions": [ {"ticket": ..., "symbol": ..., "type": "BUY",
                                       "relevance": "...", "reason": "..."} ]} ]
      },
      "news": {
        "available": true,
        "data_source": "fake-development-placeholder",
        "unavailable_reason": null,
        "items": [ {"item": {"item_id": "...", "published_at": "...",
                              "publisher": "...", "title": "...", "summary": "...",
                              "url": null, "instruments": [...], "currencies": [...],
                              "categories": [...]},
                     "overall_relevance": "POTENTIALLY_RELEVANT",
                     "matched_instruments": ["XAUUSD"], "reason": "..."} ]
      },
      "positions": [ {"ticket": 123456789, "symbol": "XAUUSD", "type": "BUY",
                      "volume": 0.10, "relevance": "POTENTIALLY_RELEVANT",
                      "status": "KNOWN", "reason": "...",
                      "calendar_event_ids": [...], "news_item_ids": [...]} ]
    }

- The mandatory calendar context is always part of the answer (the same context
  and contract GET /economic-intelligence/today exposes), provenance included.
- news.available=false means no news source is configured for this deployment:
  items is empty and unavailable_reason explains that news could not be assessed.
  It is deliberately different from a source that published nothing.
- relevance is RELEVANT / POTENTIALLY_RELEVANT / NOT_OBVIOUSLY_RELEVANT; status
  is KNOWN or UNKNOWN. UNKNOWN means the exposure could not be established (with
  the reason stated), never that there is no risk.
- Provenance is always explicit and never production-looking. The calendar's
  data_source is "fake-development-placeholder" (or "quantgist-free-development"
  when that development source is selected); the news data_source is
  "alphavantage-free-development" when the Alpha Vantage development source is
  configured and "fake-development-placeholder" otherwise. All of them are
  deterministic or free-tier development/test data, NOT live financial data
  presented as production, and news has no production vendor (Known Issues
  items 15 and 16).
- Tenant identity comes only from the authenticated user; no broker_id/user_id
  parameter exists, and `symbol` labels the relevance computation without
  widening what is read. A blank symbol → 422; unauthenticated → 401; an unusable
  news source → 503 "News data source is not configured"; MT5/calendar/news
  failure → 503 "Fundamental intelligence service temporarily unavailable".
- No BUY/SELL action, recommendation, probability or price prediction is
  returned anywhere in the payload.

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
      "login": "20002",
      "password": "application password",
      "email": "optional@example.com",
      "phone": "+12345678901"
    }

→ 201 with the non-sensitive projection:

    {
      "id": 3,
      "broker_id": 1,
      "login": "20002",
      "email": "optional@example.com",
      "phone": "+12345678901",
      "role": "customer",
      "is_active": true
    }

- role is OPTIONAL and explicit (Step 40): omitted/null → customer; "admin"
  requires a super_admin caller (403 otherwise); "super_admin" is refused by
  the schema validator for every caller (422), so no second super_admin can be
  created through the API. extra="forbid" still rejects broker_id with 422;
  tenant-scoped duplicates → 409.

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
  every response; UserResponse exposes exactly: id, broker_id, login,
  email, phone, role, is_active

GET /users/{user_id} (super_admin only):

- the same non-sensitive projection for one user of the caller's broker
- another broker's user id → 404, indistinguishable from a non-existent id
- admin/customer callers → 403; unauthenticated → 401

PATCH /users/{user_id} (super_admin only):

    { "login": ..., "password": ..., "email": ..., "phone": ...,
      "role": "admin" | "customer", "is_active": true }

- partial semantics: an omitted field is unchanged; an explicit null clears
  email/phone ONLY (login, password, role and is_active may not be null) →
  422; an empty body → 422. Changing the login changes the application login
  AND the MT5 account number at once, because they are one value.
- "super_admin" as a role value → 422 for every caller (promotion impossible);
  demoting the broker's only super_admin → 409
- a supplied password is hashed immediately and never returned or logged
- duplicate login/email/phone inside the tenant → generic 409
- cross-broker target → 404; admin/customer → 403

DELETE /users/{user_id} (super_admin only):

- 204 No Content on success (no body, nothing sensitive to return)
- deleting the broker's only super_admin → 409
- cross-broker target → 404; admin/customer → 403

## Current Verified Facts

- Application authentication exists.
- Login endpoint exists (POST /auth/login; tenant-safe since Step 43: broker
  code + login + application password).
- JWT access tokens exist.
- get_current_user() exists.
- Market-data, account-info, positions, and trade-history endpoints all require authentication.
- User broker_id comes from the database.
- MT5 password is separate from application password.
- No broker_id is trusted from JWT claims.
- POST /auth/login requires the broker code; it is resolved against the
  database and the credential lookup is scoped to that broker_id, so the same
  login number at two brokers resolves to two different users (never to an
  ambiguous match).
- The login throttle counts failures per client IP and per (broker, login), so
  one tenant's failures cannot lock out another tenant's identical login.
- Rejection paths with no stored hash to check (unknown or ambiguous broker,
  unknown login) still perform a bcrypt verification, so response timing does
  not reveal which broker codes or logins exist.
- No trading/order functionality exists.
- No BUY/SELL/OPEN/CLOSE/MODIFY functionality exists.
- MT5 providers remain read-only (verified by code inspection: no trading
  function exists anywhere in app/).
- All MT5 blocking calls (market-data, account-info, positions, trade-history) are kept
  outside the event loop through the consolidated run_mt5_call boundary.
- super_admin / admin / customer roles exist; exactly one super_admin per
  Broker is enforced by the database partial unique index
  (uq_users_broker_super_admin).
- POST /users lets a super_admin or admin create users in their own tenant
  with an OPTIONAL explicit role: omitted/null → customer (never a silent
  admin), "admin" requires a super_admin caller (403 otherwise), "super_admin"
  refused for every caller (422). POST /users/admins lets a super_admin create
  Admin Users (role-less request model, server-side role, same tenant).
  GET /users provides role-based, tenant-scoped listing (super_admin:
  admins+customers; admin: customers). PUT/GET /users/{user_id}/
  mt5-credentials provision and read the MT5 investor credential.
- Full super-admin user CRUD exists (Step 40): GET /users/{user_id} (404 for
  another tenant, indistinguishable from non-existent), PATCH /users/{user_id}
  (partial update including role; duplicates → generic 409), DELETE
  /users/{user_id} (204). The broker's only super_admin cannot be demoted or
  deleted (409), and a second super_admin cannot be created through the API.
  password_hash and mt5_password_encrypted never appear in any response.
- The MT5 trade-history provider reads the real historical-order protective
  level fields (sl / tp — not price_sl / price_tp), so GET /trade-history works
  against the real terminal; the contract still exposes nullable stop_loss /
  take_profit as JSON numbers.
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
  and is read-only: it orchestrates FinancialContextService, an injected
  EconomicIntelligenceService (Step 45) and an injected LLMProvider.
- Economic intelligence is part of EVERY agent request (Step 45, mandatory, not
  optional): the agent passes ONE reference instant to both the financial
  context and today's UTC calendar window, renders the calendar as its own
  prompt block (timestamp, currency, impact, forecast/previous/actual as
  published, deterministic relevance level and the source's provenance marker)
  and never fetches calendar data itself. A calendar/provider failure propagates
  unchanged and becomes the endpoint's existing generic 503, and when the prompt
  must shrink the trade block is dropped first, then the calendar block, each
  omission stated in the body.
- The economic-calendar source is a development/test source only (Step 44) and
  its provenance marker travels into both the API responses and the agent
  prompt, so delayed or placeholder data cannot be presented as live market
  data.
- Which calendar source a deployment serves is an explicit configuration value
  (Step 46): ECONOMIC_CALENDAR_SOURCE (auto | development_fake | quantgist |
  production, default auto) is resolved at the composition root, the
  development/test sources are served inside development only, the production
  slot refuses until a real vendor is registered at that single seam, and every
  unusable selection answers the same generic 503 with the precise reason logged
  server-side and no configuration value disclosed.
- News (NewsItem/NewsProvider contract, FakeNewsProvider,
  AlphaVantageNewsProvider, NewsService, GET /fundamental-intelligence/today)
  exists and is read-only: bounded factual items with UTC publication times,
  declared instrument/currency/category tags and an explicit provenance marker,
  retrieved for a half-open UTC window with a caller-supplied cap. The wired
  sources are development/test sources only (Alpha Vantage's free News &
  Sentiment feed when its key is configured, otherwise the deterministic fake);
  no production news vendor exists (Known Issues item 15), and neither source may
  be presented as live/production news data (its provenance marker travels into
  every response and prompt).
- Alpha Vantage is the real development news provider (Step 47A), selected only
  inside development: NEWS_SOURCE=auto takes it when ALPHA_VANTAGE_API_KEY is
  configured (otherwise the fake), NEWS_SOURCE=alphavantage names it explicitly,
  and anywhere else auto still means no news source while an explicit
  alphavantage refuses with the generic 503 - a configured key never makes a
  non-development deployment serve it, and a selected source without a key
  refuses rather than falling back to the fake. The adapter performs one bounded
  NEWS_SENTIMENT query per call (half-open UTC window sent as the vendor's own
  time filter and re-applied locally, sort=LATEST, the caller's cap clamped to
  the vendor maximum), parses only the contract's fields (title, publisher, URL
  when present, the vendor's own excerpt bounded to 400 characters plus a marker,
  an aware UTC published_at, the vendor's topic labels as categories, a stable
  sha256-based article id), and drops the vendor's sentiment scores/labels and
  ticker tags: relevance is still decided solely by the existing deterministic
  classifier, so XAUUSD keeps its USD/gold/Fed/inflation/labor mapping.
- The Alpha Vantage adapter fails closed (generic RuntimeError → the established
  503) on a missing key, a transport/timeout failure, a non-200 response, invalid
  JSON, a malformed envelope or row, and on the vendor's HTTP-200
  "Information"/"Note" bodies (invalid key, exhausted quota); the vendor payload is
  never echoed and no article page is ever fetched (title plus the vendor's own
  excerpt only). Its key is never logged: the adapter emits no record itself, and
  it installs a redaction filter on the httpx logger (once per configured value)
  so the HTTP client's own INFO-level request line cannot carry the key either.
- The Alpha Vantage development source was verified LIVE once (Step 47A, a single
  request, no retry): the composition-root seam selected
  "alphavantage-free-development", 20 real articles (the service's own cap) parsed
  into NewsItem with aware UTC publication times inside the requested window,
  bounded excerpts, populated publishers/URLs/topics, preserved chronological
  ordering and no key in any output. An offline probe with 20 real-shaped items
  confirmed the prompt stays inside its existing bound (10,121 of 24,000
  characters) with both public blocks intact.
- Fundamental intelligence (FundamentalContext, FundamentalIntelligenceService,
  the news relevance layer and the position exposure records) exists and is
  read-only and deterministic: it composes the MANDATORY calendar context the
  request already has with relevant news and the caller's own positions, adds no
  MT5 read of its own, and reports facts, provenance, discrete relevance,
  exposure and UNKNOWN only — never a forecast, probability or recommendation.
- Which news source a deployment serves is an explicit configuration value
  (Step 47, extended by Step 47A): NEWS_SOURCE
  (auto | development_fake | alphavantage | production, default auto).
  Unlike the mandatory calendar, no news source is a supported state: the
  fundamental context reports news as explicitly UNAVAILABLE with its reason
  (never as "no news"), while an explicitly selected but unusable source refuses
  with the generic 503 detail "News data source is not configured" and logs one
  value-free reason for the operator. Nothing ever falls back to another source.
- News and calendar data are public information with no account identity, so they
  are not governed by the LLM_SEND_* egress switches; those switches keep
  governing exactly the customer financial data they always did, and no account
  identity is sent to the model under any policy.
- Fundamental intelligence is composed into every agent request beside the
  mandatory calendar block (Step 47): the fundamental context is built from that
  same calendar context (no second calendar or MT5 read), the block labels its
  news as published source facts, states an unavailable source as unavailable, an
  empty feed as that source publishing nothing today and UNKNOWN exposure as
  UNKNOWN, and bounds each excerpt. The prompt reduction ladder is now trades →
  fundamental → calendar, each omission stated in the body, before
  PromptTooLargeError fails the request.
- The agent detects the instrument a request is about deterministically from its
  text (a token that reconstructs exactly from known currency tokens, e.g.
  XAUUSD); that label only decides which instruments are described in the
  fundamental block and never widens which positions may be read.
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
  (broker code, login), and the lockout is identical for existing and unknown
  logins.
- Agent input is bounded (message length at the validation boundary; trade
  block capped with the omission count stated; total prompt size guarded), and
  the user's text is separated from system framing as untrusted data.
- Outbound LLM data is governed by an explicit, configurable
  OutboundDataPolicy resolved at the composition root; account identity is
  never sent regardless of policy.
- The development economic calendar fails closed outside APP_ENV=development,
  and since Step 46 the selected source is explicit: no source is ever chosen
  implicitly for a deployment, and no unusable selection degrades to another
  source (it refuses with 503).
- Money, price and volume fields are Decimal in every provider/domain contract
  and in portfolio aggregation (exact arithmetic), converted at the MT5 boundary
  with Decimal(str(...)); account margin_level and candle tick volume
  intentionally remain float. API JSON still exposes numbers, not strings, via
  the shared DecimalAsNumber serializer.
- Instrument-aware fundamental relevance (Step 48) exists and is deterministic:
  one shared domain vocabulary (app/services/instrument_intelligence/domains.py)
  is used by BOTH the calendar and news relevance layers, and a static,
  vendor-independent instrument profile states which factors reach an instrument
  and how (direct / macro / indirect transmission). Profiles exist for XAUUSD,
  USOIL/WTI and NASDAQ-100 (NAS100/NASDAQ/US100/USTEC/NDX); an instrument without
  a profile keeps the previous symbol-string behaviour exactly. A DIRECT profile
  match (the instrument's own underlying asset or market) is the only route to
  the strongest level RELEVANT; macro and indirect matches are
  POTENTIALLY_RELEVANT; nothing matched stays NOT_OBVIOUSLY_RELEVANT. Relevance is
  still a discrete category with a factual explanation (factor + relationship),
  never a score, a direction or a probability, and the LLM never decides it.
- The calendar layer's currency scoping is unchanged for symbols that have a
  currency leg (a JPY event is still not escalated against a gold position); the
  profile explains which factor such an event concerns. For a symbol with NO
  currency leg (an index CFD), the profile is used instead of the previous
  "cannot be established" verdict, so US CPI now reaches NAS100.
- Steps 18–47, the role-migration ordering fix, the development user seed and
  the trade-history field fix are committed (latest: the Step 47 implementation
  and its checkpoint-status commit, which follow the roadmap commits 94b1858 and
  c84d334; before them the Step 46 pair ebbb86b/c95ae6c and the Step 45 pair
  b9785cb/9ba0d95; before it Step 44 "feat(calendar): add QuantGist development/test
  source" (638f972), Step 43 "feat(auth): make login tenant-safe" (5afd895),
  Step 42 (95d00d9) and Step 41 "feat(users): complete super admin user crud"
  (1577672); before those "fix(config): harden environment settings loading"
  (Steps 39A/39B), "feat(mt5): add investor credential provisioning" (Step 38),
  "fix(db): correct user role migration ordering" (3a63af9), the Step 37
  checkpoint "feat(financial): harden numeric representation", the Step 36 MT5
  tenant-session commit, the Step 35 security hardening commit and b95eaa1).
- A user has exactly ONE identity: `login`, which is both the application
  login and the MT5 account number (a single column since Step 42). It is a
  numeric string for real accounts; a non-numeric login is not an MT5 account
  and fails closed at the session boundary. The former `username` and
  `mt5_login` columns are gone, and there is no compatibility alias.
- A user also carries `mt5_server` (nullable, explicit) plus
  mt5_password_encrypted holding the ciphertext of the MT5 INVESTOR (read-only)
  password. An admin may provision these for a customer and a super_admin for
  any user in its broker; a customer can neither provision nor read them, and no
  endpoint ever returns the password. The account number is never provisioned —
  it is the user's own `login` — and a request supplying an account-number
  field is refused (422). The broker's `mt5_server` remains the fallback when
  the user's own server is NULL. No master/trading password is accepted or
  stored anywhere.
- The development database (local PostgreSQL, APP_ENV=development) is at
  migration head and holds the developer Broker's accounts: the three seeded
  role accounts (super_admin / admin / customer) created by
  scripts/create_dev_users.py with documented development-only credentials,
  plus an additional customer account with a real provisioned MT5 INVESTOR
  credential. The three seeded logins are non-numeric, so the MT5-backed
  endpoints fail closed (503) for them by design; they exercise authentication,
  roles and the agent surface. SECRET_ENCRYPTION_KEY is now configured in the
  local environment, so credential provisioning succeeds there — the earlier
  "provisioning returns 503" observation (Step 38) no longer applies locally.
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
- Test suite verified 2026-09-16 on this exact tree: pytest tests/ -q → 1213 passed, 2 warnings (run with outbound networking hard-disabled, so no test reaches a network).
- Live end-to-end verification on the demo MT5 account: GET /account-info,
  GET /positions and GET /trade-history all answer 200 with real data (no
  credential, server or symbol detail is recorded anywhere).
- The 2 warnings are pre-existing third-party deprecation warnings (anyio
  PortalFactoryType and starlette testclient). The former Pydantic class-based
  Config warning was eliminated by Step 39A.
- compileall over app, tests, and scripts is clean.
- git diff --check is clean.
- Working tree is clean. The Step 47 commits, the two roadmap commits
  (94b1858, c84d334), the Step 46 pair (ebbb86b, c95ae6c) and the Step 45 pair
  (b9785cb, 9ba0d95) are local: they have NOT been pushed, so local HEAD is eight
  commits ahead of origin/master (638f972) until they are.

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
   to a development/test source only — FakeEconomicCalendarProvider, or the
   QuantGist free tier when QUANTGIST_API_KEY is configured (Step 44), delayed
   data with a small daily quota, explicitly NOT a commercial vendor — and since
   Step 46 which one a deployment serves is an explicit configuration value
   (ECONOMIC_CALENDAR_SOURCE, default auto) whose production slot refuses until
   a real vendor is registered at that seam. There is NO production
   economic-calendar provider, and neither development source's responses may
   be presented as live financial data. MT5's Python
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
    - Login throttling is in-process and per worker. Its lockout is keyed on the
      submitted (broker code, login) as of Step 43, so an attacker flooding one
      account can lock that account out for the configured window — but only
      that one tenant's account, never another tenant's identical login number.
      The window is short and configurable. The per-worker limitation is
      unchanged: multiple workers still share no counters.
    - The real Free LLM Pool and real economic-calendar source are still absent
      (items 9 and 11), so a non-development deployment refuses those
      capabilities (503) instead of degrading.
14. Each agent request performs two position reads: FinancialContextService
    reads positions for its own snapshot and the injected
    EconomicIntelligenceService reads them again to score per-position
    relevance. Both reads stay inside the existing run_mt5_call boundary and the
    single tenant session, so tenant safety is unchanged, but a request costs
    one extra serialized MT5 read. Reusing one snapshot would mean changing the
    economic-intelligence service contract and was deliberately out of Step 45's
    scope.
15. There is no production news source. Fundamental intelligence is wired to
    development/test sources only: since Step 47A, the Alpha Vantage free tier
    when its key is configured in development, and otherwise the deterministic
    fake (FakeNewsProvider), which is explicitly NOT a vendor, publishes
    clearly-marked placeholder items and never reaches a network. Alpha Vantage's
    free tier has a small daily quota and personal-use terms, so it is a
    development stand-in rather than a production feed. A deployment selects the source explicitly (NEWS_SOURCE, default
    auto) and the production slot refuses (503) until a real, licensed vendor is
    registered at that seam; an unconfigured deployment reports news as
    explicitly unavailable rather than as "no news". Choosing and licensing a
    news vendor — including redistribution rights for the broker's customers —
    is an open architectural/product decision, exactly like the economic-calendar
    vendor in item 9. Nothing in this step may be described as a production news
    capability.
16. The Alpha Vantage NEWS_SENTIMENT feed is unfiltered, so in development the
    fundamental context is dominated by equity/position-filing copy: in the one
    live smoke sample (20 articles) the existing relevance layer could tie none of
    them to XAUUSD, and they are rendered with an explicit
    NOT_OBVIOUSLY_RELEVANT label. This is truthful data rather than a defect, and
    the relevance layer is deliberately conservative, but it means the
    development feed is not instrument-focused. Narrowing it (a topic/ticker
    filter pushed to the vendor, or a relevance-based local filter) is a
    deliberate product decision that Step 47A did not take, because both choices
    change coverage and could drop a genuinely relevant headline. The adapter's
    `instruments` argument already maps to the vendor's ticker parameter, so the
    mechanism exists; choosing the policy does not. Step 48 does not change this:
    relevance is never used to filter the feed, and an unrelated article is still
    returned and labelled NOT_OBVIOUSLY_RELEVANT rather than dropped.
17. Calendar relevance LEVELS remain currency-scoped for symbols that have a
    currency leg. A JPY central-bank event is still NOT_OBVIOUSLY_RELEVANT to a
    gold position even though monetary policy is a documented factor for gold;
    Step 48 attributes the factor for events that already qualified but does not
    broaden the escalation, because that contract is what the calendar layer's
    tests pin. Only a symbol with NO currency leg (an index CFD) is now assessed
    through its profile. Broadening the currency-scoped escalation is a
    deliberate future product decision, not an oversight.
18. The domain vocabulary and the instrument profiles are static, hand-maintained
    tables: there is no learned or statistical matching, no synonym expansion
    beyond the listed keywords, and the "major technology companies" list is a
    fixed sample of large listed technology companies. An unusually phrased
    headline can therefore miss a factor (a documented false-negative risk, never
    a false positive by design). Extending coverage is a data edit in
    app/services/instrument_intelligence/, not a code change, and no profile or
    vocabulary value is customer-specific or persisted.
19. Relevance levels are now graded by evidence strength, so RELEVANT can appear
    on GET /fundamental-intelligence/today and GET /economic-intelligence/today
    (it never did in Steps 45-47). No response field was added or removed — the
    keyword sets and contract shapes are unchanged — but a consumer that assumed
    "RELEVANT is never emitted" must accept it. The three discrete levels are
    still the only values, and there is still no score anywhere.
20. Instrument discovery reads the broker's live MT5 catalog on every request and
    holds nothing: there is no database instrument catalog, no cache, no
    scheduler and no ingestion (Step 50 deliberately adds none of them), so
    GET /instruments reads the tenant's terminal each time and its per-request
    cost is the terminal's own symbols_get cost, serialized process-wide behind
    the single tenant session like every other MT5 read. A catalog listing is
    capped at 200 instruments with total/truncated reported, so the API never
    returns a whole broker catalog at once. A production deployment that wants
    persistent discovery (and with it a cheaper lookup and a stable per-broker
    symbol inventory) needs that decision made explicitly; nothing today depends
    on it, and the three fundamental relevance profiles remain optional
    enhancements applied on top of a resolved symbol.
21. The relevance layer labels a matched item with the instrument name it
    compared under UPPER CASE (`matched_instruments`), while the research
    context reports the broker's own spelling in `focus_symbols`. Step 51 makes
    those two spellings diverge for the first time when a broker lists its own
    casing (a request for `xauusd.r` is researched as `XAUUSD.r` and the graded
    item is labelled `XAUUSD.R`). Matching is case-insensitive and no relevance
    level changed, so this is presentation only, but a client comparing the two
    strings by equality must normalise case first. The labelling contract itself
    is pre-existing (a broker-spelled position symbol already matched under
    upper case in Steps 47-48, and the fundamental endpoint's output is
    unchanged), which is why Step 51 leaves it alone deliberately.
22. Broker-suffix resolution (Step 52, extended in Step 54) requires the
    candidate to be the requested name plus a decoration: a separator with an
    empty or short alphanumeric tail, or a short alphabetic lowercase tag. An
    undelimited UPPERCASE tail (`XAUUSDX`), a digit-only tail (`XAUUSD1`) and
    tails longer than eight characters stay unresolvable: the catalog carries no
    marker that such a tail is a variant rather than a different instrument, and
    matching them would be guessing. A broker that lists several variants of one
    base (XAUUSD.r and XAUUSD.m) likewise stays ambiguous by design — the
    caller must name the spelling it wants, and GET /instruments lists the
    catalog to find it.
24. Every market-data request now performs one extra MT5 read (the symbol
    lookup that resolves it, plus at most one catalog scan when the spelling is
    not an exact match) before the candle read itself. That is the cost of
    resolving through the tenant's own catalog, and it matters operationally
    because the MT5 Python API serializes every read on the one process-wide
    terminal session: a deployment that needs cheaper per-candle reads would
    want a later decision (a per-request resolution cache, or asking the caller
    for broker spellings), which Step 53 deliberately does not introduce. No
    cache, scheduler or second catalog read was added.
23. A research request naming several instruments fails closed (404) when ANY
    of them is not offered by the tenant's broker: there is no partial research
    response, and the caller is expected to re-ask with the instrument the
    broker actually lists (GET /instruments resolves the spelling). The
    alternative — researching the confirmed subset and reporting the rest — was
    rejected deliberately so a response can never look complete while a named
    instrument was silently dropped.

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
- (Open item) Login tenant discriminator: `login` is unique per broker while the
  endpoint matched on it alone, so a cross-tenant collision made a user
  unloggable (and let one tenant's failures throttle another tenant's identical
  number) — RESOLVED by Step 43: the request names its broker, the credential
  lookup is scoped to the resolved broker_id, and the throttle's login bucket is
  per (broker, login).

These issues are known and must NOT be fixed automatically.

They should be addressed one controlled stage at a time.

## Next Step

Steps 12–44, the role-migration ordering fix, the development user seed and the
trade-history field fix are complete, committed and pushed (origin/master is
638f972, Step 44).

Step 48 (instrument-aware fundamental relevance) is committed as one focused
commit, which records the shared domain vocabulary, the three instrument
profiles, the graded relevance in both intelligence layers, the no-currency-leg
rule, the exposure factors, the prompt rendering, the new test modules and this
documentation.
Before it, Step 47A (the Alpha Vantage development news source) is committed by
the Step 47A implementation commit plus its checkpoint-status commit, which
record the provider, the explicit alphavantage NEWS_SOURCE value, the environment
matrix, the key-redaction measure, the new tests, .env.example and the
documentation. Before them, Step 47 (news and fundamental intelligence) is
committed by its implementation commit (c44953d) and its checkpoint-status
commit, the roadmap commits (94b1858 and c84d334), Step 46 (ebbb86b and c95ae6c)
and Step 45 (b9785cb and 9ba0d95) are local: origin/master remains 638f972 until
they are pushed, so the working tree is clean.

The immediate next action is deliberately NOT fixed here. The current candidates
are (a) deciding the development feed's coverage policy — narrow the Alpha
Vantage request by topic/ticker, or filter locally by relevance, now that the
profile layer can say which articles matter — which is what would make the
fundamental block XAUUSD-focused (known issue 16); (b) the remaining P1 surface (a
portfolio/report level fundamental view and any additional channel-facing
surface, both of which the factor attribution now makes more useful); (c) a real
licensed production news vendor behind the existing NEWS_SOURCE production seam
(known issue 15); (d) a real production economic-calendar vendor (known issue 9);
(e) broadening the currency-scoped calendar escalation to profile factors (known
issue 17); and (f) the real free LLM providers. Per the roadmap, P2 (instrument
catalog and multi-timeframe market data) comes after P1's fundamental capability
is usable — note that the static profiles here are deliberately NOT that catalog:
P2 adds the traded-instrument catalog, this step adds the relevance vocabulary.

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
- the news production data source (known issue 15): select and license a real
  news vendor behind the existing NEWS_SOURCE production seam, with the
  redistribution rights a broker product requires — still required before
  fundamental intelligence can carry real news
- development news coverage policy (known issue 16): decide whether the Alpha
  Vantage request should be narrowed by topic/ticker, or the local fundamental
  context should filter by relevance, so the development feed is instrument-
  focused instead of equity-filing-heavy — a product/coverage decision, not a
  defect fix
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
the consolidated blocking boundary (app/core/blocking.py),the intelligence services (app/services/economic_intelligence/,
app/services/fundamental_intelligence/, app/services/news/,
app/services/portfolio_intelligence/, app/services/financial_context/), the
public-data provider contracts (app/providers/economic_calendar.py,
app/providers/news.py) and their deterministic development sources, the
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
