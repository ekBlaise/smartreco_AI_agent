# SmartReco — Behavioural AI Recommendation Agent

A course marketplace that watches how each learner behaves, reasons over that
behaviour with a LangGraph agent, retrieves genuinely relevant courses from a
vector database, and writes a persuasive recommendation grounded in the real
catalog — refreshing as the learner's interests move, and delivered proactively
by email once a day.

Built for the **SmartReco Build Challenge 2026**. Every LLM and embedding call
goes through **Mesh API**.

---

## What it actually does

A signed-in learner browses. `tracker.js` batches their views, searches, clicks,
scroll depth and dwell time and posts them in bulk. The backend buffers those in
Redis and returns `202` immediately — nothing about tracking touches the render
path.

Every batch runs a **cheap, LLM-free check**: is this person's behaviour score
past the threshold, or has their interest signature actually changed? Only if it
has, and the cooldown has elapsed, and no run is already in flight, does a Celery
task wake the agent.

The agent then profiles their intent, plans retrieval queries, searches Qdrant,
**grades its own results**, loops back to search differently if they were weak,
and finally writes a headline, a short narrative referencing what this specific
person did, and a per-course reason. Anything it invents is dropped before it
reaches the database.

```
Browser (Jinja2 + Alpine.js)
   │  tracker.js — batched, throttled, sendBeacon on exit
   ▼
POST /api/events/batch ──► Redis buffer ──(Celery Beat, 10s)──► PostgreSQL
   │
   ├─► trigger gates: score · signature · cooldown · single-flight lock   ← no LLM
   │
   ▼ (only when all four pass)
Celery task ──► LangGraph agent ──► Mesh API (chat + embeddings)
                     │                    │
                     │                    └─► Qdrant (semantic retrieval)
                     ▼
        PostgreSQL: recommendations + items + agent_runs
                     │
                     ├─► Redis pub/sub ──► SSE ──► live "Your Signal" panel
                     ├─► rendered on /dashboard
                     └─► Celery Beat 16:00 ──► personalised email digest
```

---

## "Your Signal" — watching the agent watch you

The thing a behavioural recommender usually gets wrong is that all of it is
invisible. SmartReco puts a live panel in the course-page sidebar showing
exactly what was observed and what the agent did with it.

It carries two streams that arrive by deliberately different routes, because
they are genuinely different kinds of information:

| | What you just did | What the agent did about it |
|---|---|---|
| Source | `tracker.js` observer hook | Celery worker → Redis pub/sub → SSE |
| Latency | instant | seconds to a minute |
| Cost to the page | **zero requests** | one idle `EventSource` |

The panel is shown to **everyone, signed in or not**. Anonymous behaviour is
genuinely tracked — events are keyed by session — so hiding the panel from
signed-out visitors was showing nothing to exactly the people still deciding
whether the site understands them. They see their signal accruing; the agent's
recommendation needs an account, and the panel says so.

The feed carries across pages in `sessionStorage`, so it reads as one continuous
observation rather than resetting to empty on every navigation, and it is a
fixed five-chip tail with the recommendation as one row of two underneath — the
panel lives in a sidebar and must not grow down the page as someone browses.

A chip appears the moment an action is recorded — *"Viewed · Agent Memory
Architectures"*, *"Searched · multi agent supervisor"*, *"Dwell · 30s on…"* —
even though the event itself does not leave the browser until the next batch,
up to five seconds later. Drawing the feed costs nothing, because it renders
from the same in-memory queue the batcher is filling
([`tracker.js` `onEvent`](app/static/js/tracker.js)).

The agent half is real server push. When the tracking endpoint decides the
gates have passed it publishes `agent_state: thinking`, and the panel's status
dot turns amber. When the worker commits a new recommendation it publishes the
payload, and the card swaps in place — no reload, no polling
([`app/realtime.py`](app/realtime.py), [`app/api/routes/signal.py`](app/api/routes/signal.py)).

Degradation is deliberate: if Redis is unreachable the SSE endpoint sends one
`closed` frame and the panel falls back to polling `/api/recommendations`. It
gets slower, never broken.

To keep the feed live without distorting the metrics, dwell time is reported in
slices at 10s/30s/60s/2m/5m rather than one lump on exit. Each slice carries only
the time since the last one, so they still sum to the true total — and milestone
slices are scored **zero**, so chopping a long read into more pieces cannot
quietly make the agent fire more often
([`app/ingest/buffer.py`](app/ingest/buffer.py)).

---

## Cart, and no sign-in wall

Adding a course to a cart needs **no account**. The cart is keyed by session,
not user, so a signed-out visitor gets a real one; the session cookie survives
login, so it is still there afterwards. Sign-in is required only at checkout,
which creates enrolments — there is deliberately no payment step and it never
asks for card details.

Putting something in a basket is the strongest intent signal short of buying,
so it goes through the same buffered event path as every other tracked action
and feeds the agent's profile.

---

## The course page

A course is a real page, not a stub: a generated cover (deterministic hue from
the slug — no image assets to ship), the full description, a **curriculum** of
six modules, and an **instructor** profile. Instructors are a shared dimension —
fourteen people teach forty courses — so they live in a lookup
([`app/catalog_people.py`](app/catalog_people.py)) rather than being copied onto
every row, with a graceful fallback for admin-created courses.

The curriculum is a per-product field, editable in the admin form, and it is
**part of the embedded document**. Module titles are the most literal statement
of what a course teaches and often match a learner's wording better than the
marketing description does; previously they did not exist, so retrieval could
not see them. Editing them correctly invalidates the content hash and triggers a
re-embed.

**Enrolling is real state.** There is deliberately no payment — this is a demo
marketplace and it never asks for card details — but pressing Enrol writes an
`enrollments` row, bumps the public count, and records the strongest behavioural
signal in the system through the same buffered path as every other event. The
button then shows an enrolled state, the course appears under *Your courses*,
and signed-out visitors get "Sign in to enrol" rather than a button that 401s
silently.

The point of storing it is the agent: **an enrolled course is never
recommended.** Owned courses are dropped before grading (so they cannot occupy a
candidate slot and burn an LLM call on the way to being discarded) *and* again
in `finalize`, because the model can still name one from the behaviour summary.
Recommending something a learner already bought is the classic failure that
makes a recommender look like it was never paying attention.

*Students who explored this also looked at* is genuine co-viewership computed
from the `events` table — the sessions that opened this course, and what else
those sessions opened — not "more from this category", which is just the catalog
talking to itself. It falls back to category only when a course is too new to
have been co-viewed with anything.

**Search is live in two places.** The header box on every page opens a
suggestion dropdown as you type — arrow keys walk it, Escape closes it, Enter
still submits to the full results page. The `/search` page filters its grid in
place. Both debounce, both abort the in-flight request so a slow early response
cannot overwrite a fast later one, and both swap in an HTML *fragment* rendered
from a shared partial rather than JSON — so there is one definition of a result
and the tracking attributes come along for free. Neither replaces the plain GET
form, which still works with JavaScript disabled.

Search *tracking* deliberately stays in `tracker.js` rather than being repeated
in each widget; otherwise a single query would be recorded two or three times
and quietly inflate the behaviour score that decides when the agent runs.

---

## Running without Celery

The buffer exists so tracking never blocks a request, and Celery Beat drains it
a few seconds later. That is still the intended path — but it made the app look
broken to anyone who started only `uvicorn`. Events piled up in Redis, Postgres
stayed empty, and every screen that reads it truthfully reported zero while
nothing errored anywhere.

So the API now drains the buffer itself ([`app/ingest/drain.py`](app/ingest/drain.py)),
and dispatches an agent run in-process when no worker is consuming. The second
half matters more than it sounds: Redis is the Celery broker, so with Redis up
and no worker running, `apply_async` **succeeds** into a queue nobody reads and
the recommendation silently never happens. Testing the broker is not enough —
the dispatcher pings for live *workers* (cached, off the request path) and only
hands off when one answers.

Both are fallbacks, not replacements. A Redis lock keeps one drainer at a time,
`LPOP` is atomic so Beat can share the work, and with a worker online the run
still goes to the queue. Set `EVENT_DRAIN_IN_PROCESS=false` to hand the job back
to Beat exclusively.

Related: when Mesh refuses for a reason retrying cannot fix — an empty balance,
a bad key — the agent backs off for ten minutes instead of burning a failing
round-trip and a stack trace every time the trigger fires, and the dashboard
says *"Recommendations paused"* with the reason rather than spinning forever.

---

## The admin side

Three tabs at `/admin`.

**Overview** is an operations page, not a vanity dashboard. It answers three
questions in order:

*Is it healthy?* — every dependency is probed directly
([`app/health.py`](app/health.py)), because four of the five fail *partially and
silently*: without Qdrant recommendations get thinner rather than erroring,
without Redis nothing is deduped, without a Celery worker generation quietly
falls back to inline, and without a Mesh key the agent records an `unconfigured`
run instead of raising. An operator can be badly degraded with no error ever
reaching them. The Celery probe pings the *workers*, not the broker — a live
broker with nothing behind it is the failure that looks fine.

*Is it working?* — events stored and buffered, active learners, live
recommendations, the signal mix, and which categories learners are actually
drawn to (weighted by engagement, not raw clicks).

*What is it costing?* — the **AI spend discipline** panel. Efficiency is a
judged criterion and it is invisible by default: a generation that never happens
leaves no log line, no `agent_runs` row and no bill. So the trigger gates now
count every refusal by reason ([`_decline`](app/ingest/triggers.py)), and the
dashboard reports tracked actions per agent run, Mesh calls per run, and what
share of considered generations were declined before any model call — broken
down by which gate stopped them.

**Catalog** is the product CRUD, with live SQL/Qdrant counts, a per-row vector
state and a force re-index. The title filter is live — it fetches the same table
partial the full page renders, so injected rows keep their Edit bindings, and it
still filters server-side without JavaScript.

**Agent runs** lists every invocation, filterable by outcome. Open a row and it
expands to the node path as a chain — repeated nodes highlighted, which is the
self-correction loop made visible — next to the recommendation that run actually
wrote and the courses it grounded it in, with relevance scores. A LangSmith link
appears when tracing is on.

---

## Where each requirement lives

| Requirement | Implementation |
|---|---|
| **1. Platform** — email/password login, two roles | [`app/security.py`](app/security.py) (bcrypt + JWT in an httpOnly cookie), [`auth_routes.py`](app/api/routes/auth_routes.py), role gate in [`deps.py`](app/api/deps.py) |
| **1. Clean related schema** | [`app/models.py`](app/models.py) — `users`, `products`, `events`, `recommendations`, `recommendation_items`, `agent_runs`, properly foreign-keyed and indexed |
| **2. Admin CRUD** | [`admin_routes.py`](app/api/routes/admin_routes.py) + [`admin/products.html`](app/templates/admin/products.html) |
| **2. Dual-write, kept in sync** | [`app/vector/sync.py`](app/vector/sync.py) — every write goes to Postgres *and* Qdrant; `embedding_hash`/`payload_hash` prove sync, `reconcile_vector_store` repairs drift every 15 min |
| **3. Track views, searches, clicks, time spent** | [`tracker.js`](app/static/js/tracker.js) — 9 event types, including live search and sliced dwell |
| **3. Efficient, non-blocking, batched, throttled** | in-memory queue → batch on size/timer/`pagehide`; `sendBeacon` on exit; scroll bucketed, search debounced, dwell sliced, duplicates dropped; server buffers to Redis and returns `202` |
| **3. Sensible event schema** | `events` table: who, what, when, plus weight, dwell, path, JSON meta |
| **4. Agent consumes activity and reasons** | [`app/agent/`](app/agent/) — LangGraph state machine |
| **4. RAG grounded in the real catalog** | Qdrant retrieval in [`nodes.py`](app/agent/nodes.py); `finalize` drops any id not in the catalog |
| **4. Persuasive personalised narrative** | [`prompts.py`](app/agent/prompts.py) — headline + behaviour-referencing narrative + per-course reason |
| **4. Stored and refreshed** | `recommendations` is versioned; a new version retires the previous one |
| **5. Smart about when to call the AI** | [`app/ingest/triggers.py`](app/ingest/triggers.py) — four gates, zero LLM calls; identical signature serves the stored result |
| **5. Caching** | Redis caches embeddings and retrieval results; unchanged product text skips re-embedding entirely |

---

## The agent

An explicit LangGraph state machine, not a prompt chain:

```
profile_behavior → plan_queries → retrieve → grade_candidates
                                     ▲              │
                                     │              ▼
                              refine_queries ◄─ enough good matches?
                                                    │ yes
                                                    ▼
                                              generate → finalize
```

| Node | What it does | Mesh call? |
|---|---|---|
| `profile_behavior` | Reads raw activity → structured intent, skill level, motivations | yes |
| `plan_queries` | Builds retrieval queries + metadata filters from the profile | **no** — the facts are already there |
| `retrieve` | Multi-query Qdrant search, fused with reciprocal rank fusion | embeddings |
| `grade_candidates` | Scores every candidate 0–1 and names the coverage gap | yes |
| `refine_queries` | Rewrites the queries when retrieval was weak (max 2 extra passes) | only when needed |
| `generate` | Writes the headline, narrative, CTA and per-course persuasion | yes |
| `finalize` | Drops hallucinated product ids, assembles the payload | **no** |

Three Mesh calls on the happy path. The `grade → refine → retrieve` loop is the
self-correction: the agent decides its own retrieval was not good enough and
searches differently rather than writing confident copy about a bad match.

**Grounding is enforced twice.** The generator may only choose from ids that came
back from Qdrant, and `finalize` drops any id that was not in the retrieved set.
`persist_recommendation` then re-checks against the live catalog, in case a
product was deleted mid-run. A course the model invents cannot reach a user.

---

## Not calling the LLM on every click

This is the part most easily faked, so here is exactly how it works
(`app/ingest/triggers.py`, all pure Python, zero model calls):

1. **Volume** — fewer than `RECO_MIN_EVENTS` tracked actions → nothing happens.
2. **Score** — events are weighted (`enroll_intent` 5, `search` 3, sustained
   `dwell` 3, `product_click` 2, `product_view` 1, `page_view` 0). Below
   `RECO_SCORE_THRESHOLD` and with an unchanged interest signature → nothing
   happens.
3. **Signature short-circuit** — the top categories, search terms and viewed
   products are hashed. If that hash matches the one that produced the current
   recommendation, the stored recommendation is served and **Mesh is never
   called**.
4. **Cooldown** — `RECO_COOLDOWN_SECONDS` between runs for one user, enforced
   from both Redis and the stored recommendation's age.
5. **Single-flight lock** — a Redis `SETNX` means ten batches arriving together
   produce one agent run, not ten.

Caching, beyond that: embeddings are cached by content hash for a week, so
re-seeding or re-syncing an unchanged catalog costs nothing.

`tests/test_agent.py::test_below_threshold_activity_makes_no_llm_calls` and
`::test_repeat_generation_is_short_circuited` assert this with a call counter.

---

## Dual-write, and how it stays in sync

Every product write goes to PostgreSQL **and** Qdrant in the same request. The
interesting part is proving they stay in sync.

Each product row stores `embedding_hash` (a hash of the text that was embedded)
and `payload_hash` (a hash of the metadata that was pushed). Comparing those to
the row's current content answers *exactly* whether Qdrant is up to date, and
distinguishes two cases:

* **description edited** → re-embed through Mesh, then upsert
* **price/level/visibility edited** → patch the Qdrant payload only — no
  embedding call, no cost
* **nothing changed** → no work at all

If Qdrant is unreachable when an admin saves, the SQL write still commits, the
row is flagged with the error, and the `reconcile_vector_store` Beat task
(every 15 minutes) re-pushes it. `/admin/products` shows the live sync state and
has a force-reindex button.

> Timestamps were the obvious design and the wrong one: writing
> `vector_synced_at` bumps `updated_at` via `onupdate`, so a row invalidated
> itself the instant it was marked clean. Content hashes have no such race.

---

## Tracking that doesn't slow the site down

`app/static/js/tracker.js`:

* **Batched** — events queue in memory and flush at 10 events, on a 5s timer, on
  `visibilitychange → hidden`, and on `pagehide`.
* **Non-blocking** — `navigator.sendBeacon` on the exit paths (delivered after
  the page is gone), `fetch(keepalive)` otherwise, wrapped in
  `requestIdleCallback`. Never awaited on the render path.
* **Throttled** — scroll depth fires once per 25/50/75/100% bucket; search is
  debounced 600ms so typing "langgraph" is one event, not nine; dwell is measured
  with a visibility-aware timer and emitted once on exit; identical
  `(type, target)` pairs within a second are dropped client-side.
* **Unbreakable** — every path is wrapped; a tracking failure is swallowed, never
  surfaced to the user.

Server side, one bad event in a batch of twenty costs only itself: the endpoint
parses leniently, drops what it can't use, and still returns `202`.

---

## Stack

| Layer | Choice |
|---|---|
| Backend | FastAPI + Jinja2 (server-rendered) |
| Frontend | Jinja2 templates, Alpine.js (vendored), hand-written CSS, vanilla-JS tracker |
| LLM + embeddings | **Mesh API** (OpenAI-compatible) via `langchain-openai` |
| Agent | LangGraph |
| Vector DB | Qdrant |
| Database | PostgreSQL (SQLite fallback for a one-command start) |
| Queue / scheduler | Celery + Celery Beat, Redis broker |
| Cache / locks | Redis |
| Observability | LangSmith |

**Why `langchain-openai` and not the raw OpenAI SDK?** Mesh is OpenAI-compatible,
so `ChatOpenAI(base_url="https://api.meshapi.ai/v1")` talks to Mesh directly —
and going through the LangChain wrapper means every Mesh call appears as a span
inside the agent's LangSmith trace for free. `app/llm/mesh.py` is the only module
in the codebase that constructs a model client; `tests/test_mesh.py` enforces
that, and that no other provider is referenced anywhere.

---

## Running it

### 1. Configure

```bash
cp .env.example .env
```

Set `MESH_API_KEY` to your `rsk_...` key. `.env` is gitignored — nothing secret
is ever committed.

Two things worth knowing before the first run:

* **Embedding models on Mesh are paid.** There are free *chat* models (see
  `is_free` in `GET /v1/models`, e.g. `minimax/m2-her`), but no free embedding
  model — so indexing the catalog needs a small amount of account credit.
  Embedding all 40 courses with `openai/text-embedding-3-small` costs a fraction
  of a cent. On a zero-balance key, `seed.py` fails with a `402
  spend_limit_exceeded` and tells you so explicitly; `python seed.py
  --no-vectors` seeds SQL only.
* **Port conflicts.** If you have PostgreSQL installed locally it will already
  own 5432 and shadow the container, failing with a confusing auth error. Set
  `POSTGRES_PORT=5433` in `.env` (and match it in `DATABASE_URL`).
* **Corporate TLS proxies.** Handled: `app/tls.py` routes Python's certificate
  verification through the OS trust store, so a proxy's private root certificate
  works without disabling verification.

### 2. Everything in Docker

```bash
docker compose up --build
```

Brings up PostgreSQL, Redis, Qdrant, the API, a Celery worker and Celery Beat.
Then seed the catalog:

```bash
docker compose exec api python seed.py
```

Open http://localhost:8000 and sign in as `user@smartreco.dev` / `user1234`
(admin: `admin@smartreco.dev` / `admin1234`).

### 3. Or run natively

Data services in Docker, app processes on the host:

```bash
docker compose up -d postgres redis qdrant
```

```bash
pip install -r requirements.txt
```

```bash
python seed.py
```

Then three terminals:

```bash
uvicorn app.api.main:app --reload
```

```bash
celery -A app.workers.celery_app worker --loglevel=info --pool=solo
```

```bash
celery -A app.workers.celery_app beat --loglevel=info
```

(`--pool=solo` is only needed on Windows.)

### 4. Tests

```bash
pytest -q
```

187 tests, no network and no external services: SQLite, `fakeredis` (sync *and*
async, so the SSE stream is exercised too), an in-memory Qdrant, and a
deterministic fake in place of Mesh. The fake still goes through the real
`chat_model(...).with_structured_output(...).invoke(...)` surface, so the wiring
is genuinely exercised rather than bypassed. The suite is hermetic: it behaves
identically whether or not the docker-compose services happen to be running.

### A note on model compatibility

The agent uses structured output for every Mesh call. Not every model behind the
gateway supports it — `openai/gpt-4o` (the default) does; some smaller models
reject the request with a `400 invalid_request`. If you switch
`MESH_CHAT_MODEL`, check that the model supports JSON-schema structured output.

---

## Seeing it work

1. Sign in as `user@smartreco.dev`.
2. Open any course. The **Your Signal** panel is in the sidebar, dot green on
   `streaming`.
3. Scroll, read for half a minute, open another course, run a search. Chips
   appear in the panel as each action is recorded — *Viewed*, *Read 50%*,
   *Dwell 30s*, *Searched* — with no request made to draw them.
4. Once the gates pass, the dot turns amber on `agent thinking` while the worker
   runs, then the recommendation card **swaps in place**. No reload.
5. Watch the worker log alongside it. You will see **one** generation fire — not
   one per click — with the reason it fired and the node path it walked.
6. `/dashboard` shows the same recommendation in full: a narrative naming what
   you actually browsed, and real courses each with their own reason.
7. Click the same things again. The log shows the signature short-circuit and
   **no new Mesh call**.
8. `/admin/agent-runs` lists every run — node path, retrieval attempts, Mesh
   calls, latency, and a LangSmith trace link when tracing is on.
9. `/admin/products`: add a course, then search for it semantically — it is
   retrievable immediately. Delete it and it leaves both stores.

Trigger the digest by hand:

```bash
python -c "from app.workers.tasks import send_daily_digest; print(send_daily_digest())"
```

With SMTP unconfigured this writes the rendered email to `data/digests/*.html` —
a documented dry-run, so the feature is inspectable without mail credentials.
Set `SMTP_HOST`/`SMTP_USER`/`SMTP_PASSWORD` to actually send.

---

## Bonus features implemented

**⭐ Structured agent framework — LangGraph.** [`app/agent/graph.py`](app/agent/graph.py)
is an explicit `StateGraph`, not a prompt chain. Seven nodes cover exactly the
shape the brief asks for: analyse the activity (`profile_behavior`), decide what
to retrieve (`plan_queries`), retrieve (`retrieve`), **evaluate retrieval
quality** (`grade_candidates`), **refine and go round again** (`refine_queries`,
a conditional edge back to `retrieve`, budgeted at three attempts), then
`generate` and `finalize`. The self-correction loop is real and observable — a
live run against Mesh went `graded 0/12 → refine → 2/12 → refine → 3/12 →
generate`, and the node path of every run is stored on the `agent_runs` row.

**⭐ Scheduled proactive delivery — Celery Beat.** A real scheduler, not a
button: [`app/workers/celery_app.py`](app/workers/celery_app.py) registers three
periodic tasks — flush the event buffer every 10s, reconcile Postgres↔Qdrant
every 15 min, and send the digest at `DIGEST_HOUR`. The digest re-runs the agent
over the day's activity and emails a personalised recap
([`app/workers/email.py`](app/workers/email.py)).

**⭐ Observability — LangSmith.** [`configure_tracing`](app/llm/mesh.py) wires
tracing at import so the API, the worker and the seed script are all covered.
Each run's LangSmith URL is stored on its `agent_runs` row, and `/admin/agent-runs`
shows the node path, Mesh call count, candidate count, latency and status for
every invocation — so the workflow is inspectable even without a LangSmith key.

**⭐ Retrieval polish.** Five things, layered:
multi-query fan-out (2–4 behaviour-derived queries per run) ·
**reciprocal rank fusion** across them ·
**metadata filtering** on the learner's inferred level, as a weighted second pass ·
**LLM re-ranking** in `grade_candidates`, which scores every candidate and drops
anything under the floor ·
**MMR-style diversification** so semantic search cannot return five variations of
one course — a learner deep in agentic AI still sees the MLOps course that gets
their agents deployed.

Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` to see full agent traces.

---

## Layout

```
app/
  agent/        LangGraph agent — state, prompts, nodes, graph
  api/          FastAPI routes, deps, middleware, templating
  ingest/       event buffering + the LLM-free trigger gates
  llm/mesh.py   the ONLY place a model client is constructed
  catalog_people.py  curricula + instructor profiles for the seeded catalog
  health.py     dependency probes for the admin overview
  realtime.py   Redis pub/sub channel behind the live Signal panel
  vector/       Qdrant store + dual-write sync
  workers/      Celery app, Beat tasks, email digest
  templates/    Jinja2 pages + the digest email
  static/       tracker.js, signal.js, CSS, vendored Alpine
  models.py     users · products · events · recommendations · agent_runs
  service.py    trigger → agent → persistence, shared by API and workers
seed.py         catalog + demo accounts, dual-written to both stores
tests/          187 tests, no network required
```

## Data model

| Table | Purpose |
|---|---|
| `users` | email/password auth, `user` and `admin` roles |
| `products` | the catalog, plus the hashes that prove Qdrant is in sync |
| `events` | every tracked behavioural signal, weighted at ingest |
| `recommendations` | stored agent output, versioned, one current per user |
| `recommendation_items` | the recommended products + per-course persuasion |
| `agent_runs` | one row per invocation: node path, Mesh calls, latency, trace |
