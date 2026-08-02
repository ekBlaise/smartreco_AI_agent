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
                     ├─► rendered on /dashboard (Alpine polls for updates)
                     └─► Celery Beat 16:00 ──► personalised email digest
```

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

89 tests, no network and no external services: SQLite, `fakeredis`, an in-memory
Qdrant, and a deterministic fake in place of Mesh. The fake still goes through
the real `chat_model(...).with_structured_output(...).invoke(...)` surface, so
the wiring is genuinely exercised rather than bypassed.

### A note on model compatibility

The agent uses structured output for every Mesh call. Not every model behind the
gateway supports it — `openai/gpt-4o` (the default) does; some smaller models
reject the request with a `400 invalid_request`. If you switch
`MESH_CHAT_MODEL`, check that the model supports JSON-schema structured output.

---

## Seeing it work

1. Sign in as `user@smartreco.dev`.
2. Open three or four **Agentic AI** courses, search for `langgraph agents`,
   search again for `multi agent orchestration`, and click *Enroll now* on one.
3. Watch the worker log. You will see **one** generation fire — not one per
   click — with the reason it fired and the node path it walked.
4. `/dashboard` shows the recommendation: a narrative naming what you actually
   browsed, and four real courses each with their own reason.
5. Click the same things again. The log shows the signature short-circuit and
   **no new Mesh call**.
6. `/admin/agent-runs` lists every run — node path, retrieval attempts, Mesh
   calls, latency, and a LangSmith trace link when tracing is on.
7. `/admin/products`: add a course, then search for it semantically — it is
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

| Bonus | Status | Where |
|---|---|---|
| ⭐ Structured agent framework (LangGraph) | ✅ | `app/agent/graph.py` — 7 nodes, conditional self-correction edge |
| ⭐ Scheduled proactive delivery | ✅ | Celery Beat daily digest, `app/workers/{celery_app,tasks,email}.py` |
| ⭐ Observability (LangSmith) | ✅ | `app/llm/mesh.py:configure_tracing`, trace URLs stored per run, `/admin/agent-runs` |
| ⭐ Retrieval polish | ✅ | Multi-query fan-out + reciprocal rank fusion + metadata filtering + LLM re-ranking + query refinement loop |

Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` to see full agent traces.

---

## Layout

```
app/
  agent/        LangGraph agent — state, prompts, nodes, graph
  api/          FastAPI routes, deps, middleware, templating
  ingest/       event buffering + the LLM-free trigger gates
  llm/mesh.py   the ONLY place a model client is constructed
  vector/       Qdrant store + dual-write sync
  workers/      Celery app, Beat tasks, email digest
  templates/    Jinja2 pages + the digest email
  static/       tracker.js, CSS, vendored Alpine
  models.py     users · products · events · recommendations · agent_runs
  service.py    trigger → agent → persistence, shared by API and workers
seed.py         catalog + demo accounts, dual-written to both stores
tests/          84 tests, no network required
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
