"""Curriculum outlines and instructor profiles for the seeded catalog.

Kept apart from ``catalog_data`` so the course records stay readable. The
curriculum is stored per product (admins can edit it); instructors are a shared
dimension — fourteen people teach forty courses — so they live in a lookup
rather than being copied onto every row.
"""

from __future__ import annotations

#: slug -> the modules a learner works through, in order.
CURRICULA: dict[str, list[str]] = {
    # --- Agentic AI ---------------------------------------------------------
    "building-ai-agents-langgraph": [
        "State machines for agents",
        "Nodes, edges and conditional routing",
        "Tool calling and retrieval gates",
        "Self-correction loops that terminate",
        "Durable checkpoints and resuming",
        "Ship: a research agent that survives tool failure",
    ],
    "multi-agent-systems-in-production": [
        "When one agent is genuinely not enough",
        "Supervisor patterns and delegation",
        "Handoffs and shared working memory",
        "Runaway loops, cost blowouts, silent tool drift",
        "Evaluating trajectories, not just final answers",
        "Ship: a supervised multi-agent support triage",
    ],
    "tool-calling-and-function-design": [
        "What makes a tool schema legible to a model",
        "Arguments, defaults and validation",
        "Model Context Protocol in practice",
        "Errors the model can actually recover from",
        "Idempotency and retry-safe side effects",
        "Ship: a tool server with a reliability test suite",
    ],
    "agent-memory-architectures": [
        "Short-term buffers and context budgets",
        "Episodic recall across sessions",
        "Semantic consolidation and summarisation",
        "Graph memory and entity linking",
        "When memory actively hurts accuracy",
        "Ship: an agent that remembers a user for weeks",
    ],
    "autonomous-research-agents": [
        "Decomposing a question into a plan",
        "Search, read, and know when to stop",
        "Reflection and self-critique that pays for itself",
        "Citation discipline and grounding",
        "Budgets: time, tokens and tool calls",
        "Ship: a research agent with an audit trail",
    ],
    # --- LLM Engineering ----------------------------------------------------
    "rag-systems-from-scratch": [
        "Why naive chunking fails on real documents",
        "Chunking strategies that survive tables and headings",
        "Embeddings and the retrieval step",
        "Hybrid retrieval: keyword plus semantic",
        "Re-ranking and honest evaluation",
        "Ship: a RAG pipeline with a regression suite",
    ],
    "advanced-retrieval-reranking": [
        "Where first-stage retrieval loses the answer",
        "Cross-encoder re-ranking",
        "HyDE and hypothetical document embeddings",
        "Query expansion and decomposition",
        "Fusing rankings from several retrievers",
        "Ship: a measurable lift over a baseline",
    ],
    "prompt-engineering-for-engineers": [
        "Prompts as interfaces, not incantations",
        "Few-shot selection that generalises",
        "Structured output and JSON schema",
        "Making failure modes explicit",
        "Versioning and diffing prompts",
        "Ship: a prompt with a test suite",
    ],
    "fine-tuning-open-models": [
        "When fine-tuning beats prompting",
        "Dataset construction and cleaning",
        "LoRA, QLoRA and PEFT in practice",
        "Hyperparameters that actually matter",
        "Evaluating against the base model",
        "Ship: a tuned model with a defensible eval",
    ],
    "llm-evaluation-and-testing": [
        "What 'good' means for a generative system",
        "Golden sets and regression testing",
        "LLM-as-judge, and its failure modes",
        "Human review that scales",
        "Wiring evals into CI",
        "Ship: an eval harness that blocks a bad deploy",
    ],
    "structured-output-and-schemas": [
        "JSON mode versus tool calling",
        "Designing schemas a model can satisfy",
        "Pydantic validation and coercion",
        "Repairing malformed output",
        "Streaming partial structures",
        "Ship: a typed extraction endpoint",
    ],
    "cost-and-latency-optimization-llm": [
        "Measuring before optimising",
        "Prompt and response caching",
        "Batching and concurrency limits",
        "Streaming for perceived latency",
        "Routing cheap work to cheap models",
        "Ship: half the cost at the same quality",
    ],
    # --- Vector Databases ---------------------------------------------------
    "vector-databases-deep-dive": [
        "What a vector index actually stores",
        "HNSW, IVF and the recall/latency trade",
        "Metadata filtering without wrecking recall",
        "Qdrant, Pinecone and Chroma compared",
        "Sharding, replication and backups",
        "Ship: a tuned collection with measured recall",
    ],
    "embeddings-explained": [
        "What an embedding vector encodes",
        "Cosine similarity and why it is the default",
        "Dimensionality and normalisation",
        "Choosing a model for your domain",
        "Where embeddings quietly mislead you",
        "Ship: a similarity search you can defend",
    ],
    "graph-rag-knowledge-graphs": [
        "When relationships beat similarity",
        "Entity and relation extraction",
        "Modelling a graph in Neo4j",
        "Traversal as a retrieval strategy",
        "Combining graph and vector retrieval",
        "Ship: a Graph RAG pipeline over real documents",
    ],
    # --- MLOps --------------------------------------------------------------
    "mlops-fundamentals": [
        "Reproducibility as the first requirement",
        "Experiment tracking with MLflow",
        "Model registries and promotion",
        "CI/CD for models, not just code",
        "Rollbacks and safe releases",
        "Ship: a model that deploys itself",
    ],
    "deploying-llms-at-scale": [
        "Inference servers and vLLM",
        "GPU memory, batching and KV cache",
        "Autoscaling on Kubernetes",
        "Latency SLOs under real traffic",
        "Cost per thousand requests",
        "Ship: a served model meeting a p95 target",
    ],
    "monitoring-ml-systems": [
        "The metrics that predict incidents",
        "Data drift and concept drift",
        "Data quality checks at the boundary",
        "Alerting without alert fatigue",
        "Debugging a live regression",
        "Ship: a monitoring stack with real alerts",
    ],
    "feature-stores-in-practice": [
        "The training/serving skew problem",
        "Point-in-time correctness",
        "Offline and online stores",
        "Building on Feast",
        "Backfills and feature versioning",
        "Ship: a feature pipeline serving both paths",
    ],
    # --- Data Engineering ---------------------------------------------------
    "data-pipelines-with-airflow": [
        "DAGs, tasks and dependencies",
        "Scheduling, catchup and backfill",
        "Idempotent tasks that can be re-run",
        "Sensors and external triggers",
        "Testing pipelines locally",
        "Ship: a production DAG with alerting",
    ],
    "streaming-data-kafka": [
        "Topics, partitions and consumer groups",
        "Delivery guarantees and exactly-once",
        "Schema evolution with a registry",
        "Stream processing patterns",
        "Rebalances, lag and backpressure",
        "Ship: an event-driven pipeline",
    ],
    "dbt-analytics-engineering": [
        "Models, sources and refs",
        "Layering staging, intermediate and marts",
        "Tests and documentation as code",
        "Incremental models",
        "Deploying dbt in CI",
        "Ship: a tested analytics project",
    ],
    "postgres-performance-tuning": [
        "Reading a query plan honestly",
        "Indexes: the right one, not more of them",
        "Joins, statistics and the planner",
        "Vacuum, bloat and autovacuum",
        "Connection pooling and locks",
        "Ship: a slow query made fast, measured",
    ],
    "event-driven-architecture": [
        "Events versus commands",
        "Queues, brokers and delivery semantics",
        "Idempotency and deduplication",
        "Celery in production",
        "Failure, retry and dead letters",
        "Ship: an async workflow that survives restarts",
    ],
    # --- Backend Engineering ------------------------------------------------
    "fastapi-production-apis": [
        "Async done right, and when not to",
        "Dependency injection and testability",
        "Pydantic models at the boundary",
        "Structured errors and status codes",
        "Auth, middleware and background tasks",
        "Ship: an API with a test suite and no live DB",
    ],
    "async-python-deep-dive": [
        "The event loop, concretely",
        "Coroutines, tasks and cancellation",
        "Finding the call that blocks it",
        "Concurrency limits and backpressure",
        "Async database and HTTP clients",
        "Ship: a throughput fix you can prove",
    ],
    "api-security-essentials": [
        "AuthN versus AuthZ",
        "Sessions, JWTs and cookie flags",
        "OAuth and third-party identity",
        "Rate limiting and abuse protection",
        "The OWASP API top ten in practice",
        "Ship: an audited, hardened API",
    ],
    "celery-background-jobs": [
        "Brokers, workers and result backends",
        "Designing tasks that can be retried",
        "Beat schedules and periodic work",
        "Routing, queues and priorities",
        "Monitoring and failure handling",
        "Ship: a scheduled job you can trust",
    ],
    "system-design-interview-prep": [
        "Framing requirements and constraints",
        "Designing a feed",
        "Designing a rate limiter",
        "Designing a recommendation service",
        "Caching, sharding and consistency",
        "Ship: trade-offs you can defend out loud",
    ],
    # --- Frontend -----------------------------------------------------------
    "modern-javascript-for-backend-devs": [
        "The language, without the framework noise",
        "The DOM and event delegation",
        "fetch, promises and async/await",
        "Modules and bundling, briefly",
        "Debugging in the browser",
        "Ship: an interactive page with no framework",
    ],
    "frontend-performance-instrumentation": [
        "Core Web Vitals and what moves them",
        "Measuring without changing what you measure",
        "Batching, throttling and debouncing",
        "sendBeacon and the unload path",
        "Tracking that cannot break the page",
        "Ship: an analytics client with zero jank",
    ],
    "htmx-and-hypermedia-apps": [
        "Hypermedia as the engine of state",
        "htmx attributes and partial responses",
        "Alpine.js for local interactivity",
        "Progressive enhancement that degrades well",
        "When you do need a SPA",
        "Ship: a dynamic app rendered on the server",
    ],
    # --- Cloud & Infra ------------------------------------------------------
    "docker-for-developers": [
        "Images, layers and the build cache",
        "Writing a Dockerfile worth keeping",
        "Volumes, networks and compose",
        "Local development that matches production",
        "Slimming images and build times",
        "Ship: a reproducible dev environment",
    ],
    "kubernetes-in-practice": [
        "Pods, deployments and services",
        "Config, secrets and volumes",
        "Health probes and rollouts",
        "Autoscaling and resource limits",
        "Helm and templating",
        "Ship: a deployed, self-healing service",
    ],
    "terraform-infrastructure-as-code": [
        "Providers, resources and state",
        "Modules and composition",
        "Plan, apply and drift",
        "Remote state and locking",
        "Terraform in CI",
        "Ship: an environment you can recreate",
    ],
    "observability-with-opentelemetry": [
        "Traces, metrics and logs as one model",
        "Instrumenting a service end to end",
        "Context propagation across boundaries",
        "Sampling without losing the incident",
        "Dashboards and useful alerts",
        "Ship: a traced request path",
    ],
    # --- Product & Growth ---------------------------------------------------
    "recommendation-systems-foundations": [
        "Content-based versus collaborative filtering",
        "Candidate generation and ranking",
        "The cold-start problem",
        "Offline metrics and their limits",
        "A/B testing a recommender",
        "Ship: a recommender measured against a baseline",
    ],
    "behavioral-analytics-product": [
        "Designing an event taxonomy",
        "Instrumenting without drowning in data",
        "Funnels and drop-off analysis",
        "Cohorts and retention curves",
        "From dashboard to decision",
        "Ship: an analytics plan a team will keep",
    ],
    "conversion-copywriting-for-products": [
        "Writing to a person, not a segment",
        "Objections and how to answer them early",
        "Landing pages that survive scrutiny",
        "Lifecycle email that gets opened",
        "Persuasion without dark patterns",
        "Ship: a rewritten page, tested",
    ],
    "ab-testing-and-experimentation": [
        "Hypotheses worth the traffic",
        "Power, sample size and duration",
        "Guardrail metrics",
        "Reading results without fooling yourself",
        "Sequential testing and peeking",
        "Ship: an experiment plan that holds up",
    ],
}


#: Instructor profiles, keyed by the name on the course record.
INSTRUCTORS: dict[str, dict[str, str]] = {
    "Priya Raman": {
        "title": "Founding Engineer, agent tooling",
        "bio": "Builds agent frameworks for a living and has debugged more runaway loops than she would like to admit.",
    },
    "Daniel Okafor": {
        "title": "Staff Engineer, applied AI platform",
        "bio": "Runs multi-agent systems in production and is unsentimental about which ones deserve to be.",
    },
    "Lena Fischer": {
        "title": "Research Engineer, retrieval and memory",
        "bio": "Works on how systems remember, and on the far harder question of what they should forget.",
    },
    "Marcus Webb": {
        "title": "Principal Engineer, search & retrieval",
        "bio": "Spent a decade on search relevance before RAG made it fashionable again.",
    },
    "Sofia Almeida": {
        "title": "Engineering Lead, LLM products",
        "bio": "Treats prompts as interfaces with tests, versions and owners — because in her team they are.",
    },
    "Kenji Sato": {
        "title": "ML Engineer, model efficiency",
        "bio": "Obsessed with cost per request, and with proving quality did not quietly drop to get it there.",
    },
    "Ana Duarte": {
        "title": "Platform Lead, MLOps",
        "bio": "Has been paged at 3am by enough models to have strong opinions about deployment.",
    },
    "Ravi Menon": {
        "title": "Data Platform Architect",
        "bio": "Builds pipelines that people forget exist, which he considers the highest compliment.",
    },
    "Hannah Cole": {
        "title": "Analytics Engineering Lead",
        "bio": "Turns spreadsheets nobody trusts into models everybody does.",
    },
    "Tomás Ruiz": {
        "title": "Backend Engineer, distributed systems",
        "bio": "Writes async Python and then finds the one call that was blocking the whole event loop.",
    },
    "Nadia Haddad": {
        "title": "Security Engineer, application security",
        "bio": "Reviews APIs for a living and would rather show you the exploit than the checklist.",
    },
    "Grace Lim": {
        "title": "Frontend Engineer, performance",
        "bio": "Makes pages fast and instrumentation invisible, in that order.",
    },
    "Samuel Adeyemi": {
        "title": "Infrastructure Engineer, cloud platform",
        "bio": "Believes an environment you cannot recreate from scratch is an environment you do not have.",
    },
    "Elena Petrova": {
        "title": "Head of Growth, product analytics",
        "bio": "Has killed more of her own experiments than she has shipped, and thinks that is the job.",
    },
}


def instructor_profile(name: str) -> dict[str, str]:
    """Look up an instructor, with a sane fallback for admin-created courses."""
    return INSTRUCTORS.get(name) or {"title": "Instructor", "bio": ""}


def initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"
