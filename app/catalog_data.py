"""The demo course catalog.

Deliberately clustered: several deep tracks (agentic AI, LLM engineering, data
engineering, MLOps, ...) with overlapping vocabulary, so that behaviour-driven
retrieval has something real to discriminate between. A user who only browses
agentic-AI content should not get a generic "most popular" list back.
"""

from __future__ import annotations

COURSES: list[dict] = [
    # --- Agentic AI ---------------------------------------------------------
    {
        "slug": "building-ai-agents-langgraph",
        "title": "Building AI Agents with LangGraph",
        "category": "Agentic AI",
        "level": "intermediate",
        "price": 129.0,
        "instructor": "Priya Raman",
        "duration_hours": 14.0,
        "rating": 4.8,
        "enrollments": 8420,
        "tags": ["langgraph", "agents", "state machines", "tool calling", "python"],
        "description": (
            "Design agents as explicit graphs instead of prompt spaghetti. You build a "
            "research agent node by node: routing, conditional edges, retrieval gates, "
            "self-correction loops and durable checkpoints. Ends with a production agent "
            "that recovers from tool failures and streams its reasoning to a UI."
        ),
    },
    {
        "slug": "multi-agent-systems-in-production",
        "title": "Multi-Agent Systems in Production",
        "category": "Agentic AI",
        "level": "advanced",
        "price": 179.0,
        "instructor": "Daniel Okafor",
        "duration_hours": 18.0,
        "rating": 4.7,
        "enrollments": 3910,
        "tags": ["multi-agent", "orchestration", "supervisor", "handoff", "evaluation"],
        "description": (
            "Supervisor patterns, agent handoffs, shared memory and the failure modes "
            "nobody warns you about: runaway loops, cost blowouts and silent tool drift. "
            "Includes an evaluation harness for scoring multi-agent trajectories, not just "
            "final answers."
        ),
    },
    {
        "slug": "tool-calling-and-function-design",
        "title": "Tool Calling and Function Design for Agents",
        "category": "Agentic AI",
        "level": "intermediate",
        "price": 99.0,
        "instructor": "Priya Raman",
        "duration_hours": 9.0,
        "rating": 4.6,
        "enrollments": 6120,
        "tags": ["tool calling", "function schemas", "mcp", "reliability"],
        "description": (
            "Most agent failures are bad tool design, not bad models. Learn to write tool "
            "schemas models actually call correctly, handle partial arguments, design "
            "idempotent side effects, and expose your systems over the Model Context "
            "Protocol."
        ),
    },
    {
        "slug": "agent-memory-architectures",
        "title": "Agent Memory Architectures",
        "category": "Agentic AI",
        "level": "advanced",
        "price": 149.0,
        "instructor": "Lena Fischer",
        "duration_hours": 11.0,
        "rating": 4.7,
        "enrollments": 2870,
        "tags": ["memory", "episodic", "semantic", "vector", "graph memory"],
        "description": (
            "Short-term buffers, episodic recall, semantic consolidation and graph memory. "
            "Build an agent that remembers a user across weeks without dragging its entire "
            "history into every prompt, and learn when memory actively hurts accuracy."
        ),
    },
    {
        "slug": "autonomous-research-agents",
        "title": "Autonomous Research Agents",
        "category": "Agentic AI",
        "level": "intermediate",
        "price": 119.0,
        "instructor": "Daniel Okafor",
        "duration_hours": 10.0,
        "rating": 4.5,
        "enrollments": 4380,
        "tags": ["research", "planning", "web search", "reflection", "citations"],
        "description": (
            "Build an agent that plans a research question, searches, reads, reflects on "
            "gaps and rewrites its own plan. Heavy focus on grounding: every claim in the "
            "final report traces back to a retrieved source."
        ),
    },
    # --- LLM Engineering ----------------------------------------------------
    {
        "slug": "rag-systems-from-scratch",
        "title": "RAG Systems from Scratch",
        "category": "LLM Engineering",
        "level": "intermediate",
        "price": 139.0,
        "instructor": "Marcus Webb",
        "duration_hours": 16.0,
        "rating": 4.9,
        "enrollments": 12750,
        "tags": ["rag", "retrieval", "chunking", "embeddings", "vector database"],
        "description": (
            "Chunking strategies that survive real documents, hybrid retrieval, re-ranking "
            "and honest evaluation. You build a RAG pipeline end to end and then break it "
            "on purpose to learn which knob actually moves answer quality."
        ),
    },
    {
        "slug": "advanced-retrieval-reranking",
        "title": "Advanced Retrieval: Re-ranking and Query Rewriting",
        "category": "LLM Engineering",
        "level": "advanced",
        "price": 159.0,
        "instructor": "Marcus Webb",
        "duration_hours": 12.0,
        "rating": 4.8,
        "enrollments": 5240,
        "tags": ["reranking", "hyde", "query expansion", "hybrid search", "bm25"],
        "description": (
            "Cross-encoder re-ranking, reciprocal rank fusion, HyDE, query decomposition "
            "and metadata filtering. For teams whose RAG demo worked and whose RAG product "
            "did not."
        ),
    },
    {
        "slug": "prompt-engineering-for-engineers",
        "title": "Prompt Engineering for Engineers",
        "category": "LLM Engineering",
        "level": "beginner",
        "price": 69.0,
        "instructor": "Sofia Almeida",
        "duration_hours": 7.0,
        "rating": 4.5,
        "enrollments": 19340,
        "tags": ["prompting", "structured output", "few-shot", "json schema"],
        "description": (
            "Prompting treated as an engineering discipline: versioned prompts, structured "
            "output with schemas, deterministic parsing, regression tests, and knowing when "
            "to stop prompting and start fine-tuning."
        ),
    },
    {
        "slug": "fine-tuning-open-models",
        "title": "Fine-Tuning Open Models with LoRA",
        "category": "LLM Engineering",
        "level": "advanced",
        "price": 189.0,
        "instructor": "Kenji Sato",
        "duration_hours": 20.0,
        "rating": 4.7,
        "enrollments": 4610,
        "tags": ["fine-tuning", "lora", "qlora", "peft", "datasets"],
        "description": (
            "Dataset construction, LoRA and QLoRA, evaluation against the base model, and "
            "quantized deployment. Includes the decision framework for fine-tune vs RAG vs "
            "better prompting — usually the answer is not fine-tuning."
        ),
    },
    {
        "slug": "llm-evaluation-and-testing",
        "title": "LLM Evaluation and Testing",
        "category": "LLM Engineering",
        "level": "intermediate",
        "price": 129.0,
        "instructor": "Sofia Almeida",
        "duration_hours": 11.0,
        "rating": 4.6,
        "enrollments": 5890,
        "tags": ["evaluation", "llm-as-judge", "regression testing", "observability"],
        "description": (
            "Build eval sets that catch regressions before users do. Golden datasets, "
            "LLM-as-judge (and its biases), pairwise comparison, tracing with LangSmith, "
            "and wiring evals into CI."
        ),
    },
    {
        "slug": "structured-output-and-schemas",
        "title": "Structured Output and Schema-Driven LLM Apps",
        "category": "LLM Engineering",
        "level": "beginner",
        "price": 79.0,
        "instructor": "Sofia Almeida",
        "duration_hours": 6.0,
        "rating": 4.4,
        "enrollments": 8110,
        "tags": ["json mode", "pydantic", "validation", "parsing"],
        "description": (
            "Stop regex-parsing model output. Pydantic schemas, JSON mode, constrained "
            "decoding, retry-on-validation-error, and designing schemas the model finds "
            "easy to fill correctly."
        ),
    },
    {
        "slug": "cost-and-latency-optimization-llm",
        "title": "Cost and Latency Optimization for LLM Apps",
        "category": "LLM Engineering",
        "level": "intermediate",
        "price": 109.0,
        "instructor": "Kenji Sato",
        "duration_hours": 8.0,
        "rating": 4.6,
        "enrollments": 4020,
        "tags": ["caching", "batching", "streaming", "model routing", "token budget"],
        "description": (
            "Semantic caching, prompt caching, model routing by difficulty, batching and "
            "streaming. Turn a demo that costs $4 per user per day into a product that "
            "costs four cents."
        ),
    },
    # --- Vector Databases ---------------------------------------------------
    {
        "slug": "vector-databases-deep-dive",
        "title": "Vector Databases Deep Dive",
        "category": "Vector Databases",
        "level": "intermediate",
        "price": 119.0,
        "instructor": "Marcus Webb",
        "duration_hours": 10.0,
        "rating": 4.7,
        "enrollments": 6430,
        "tags": ["qdrant", "pinecone", "chroma", "hnsw", "indexing"],
        "description": (
            "How HNSW actually works, why your recall dropped after a re-index, payload "
            "filtering, sharding and choosing between Qdrant, Pinecone, Chroma and pgvector "
            "on something other than vibes."
        ),
    },
    {
        "slug": "embeddings-explained",
        "title": "Embeddings Explained",
        "category": "Vector Databases",
        "level": "beginner",
        "price": 59.0,
        "instructor": "Lena Fischer",
        "duration_hours": 5.0,
        "rating": 4.5,
        "enrollments": 14200,
        "tags": ["embeddings", "similarity", "cosine", "dimensionality"],
        "description": (
            "What embedding vectors encode, why cosine similarity is the default, how "
            "dimensionality and normalization change your results, and how to pick an "
            "embedding model without guessing."
        ),
    },
    {
        "slug": "graph-rag-knowledge-graphs",
        "title": "Graph RAG and Knowledge Graphs",
        "category": "Vector Databases",
        "level": "advanced",
        "price": 169.0,
        "instructor": "Lena Fischer",
        "duration_hours": 13.0,
        "rating": 4.6,
        "enrollments": 2340,
        "tags": ["graph rag", "neo4j", "entity extraction", "traversal"],
        "description": (
            "When flat vector search stops answering multi-hop questions, build a graph. "
            "Entity and relation extraction, hybrid graph+vector retrieval, and community "
            "summarization over large corpora."
        ),
    },
    # --- MLOps --------------------------------------------------------------
    {
        "slug": "mlops-fundamentals",
        "title": "MLOps Fundamentals",
        "category": "MLOps",
        "level": "beginner",
        "price": 89.0,
        "instructor": "Ana Duarte",
        "duration_hours": 12.0,
        "rating": 4.4,
        "enrollments": 10800,
        "tags": ["ci/cd", "model registry", "experiment tracking", "mlflow"],
        "description": (
            "Experiment tracking, model registries, reproducible training and deployment "
            "pipelines. The unglamorous scaffolding that decides whether your model ever "
            "reaches a user."
        ),
    },
    {
        "slug": "deploying-llms-at-scale",
        "title": "Deploying LLMs at Scale",
        "category": "MLOps",
        "level": "advanced",
        "price": 199.0,
        "instructor": "Ana Duarte",
        "duration_hours": 17.0,
        "rating": 4.7,
        "enrollments": 3160,
        "tags": ["vllm", "gpu", "autoscaling", "kubernetes", "inference"],
        "description": (
            "Inference servers, continuous batching, KV-cache management, GPU autoscaling "
            "and cost modelling. Take a model from a notebook to a service holding a "
            "latency SLO under real traffic."
        ),
    },
    {
        "slug": "monitoring-ml-systems",
        "title": "Monitoring ML Systems in Production",
        "category": "MLOps",
        "level": "intermediate",
        "price": 129.0,
        "instructor": "Ana Duarte",
        "duration_hours": 9.0,
        "rating": 4.5,
        "enrollments": 4470,
        "tags": ["drift", "observability", "alerting", "data quality"],
        "description": (
            "Data drift, concept drift, silent label decay and the alerting strategy that "
            "tells you about it before your users do. Includes tracing for LLM systems "
            "where 'accuracy' is not a number you have."
        ),
    },
    {
        "slug": "feature-stores-in-practice",
        "title": "Feature Stores in Practice",
        "category": "MLOps",
        "level": "intermediate",
        "price": 119.0,
        "instructor": "Ravi Menon",
        "duration_hours": 8.0,
        "rating": 4.3,
        "enrollments": 2650,
        "tags": ["feature store", "feast", "training serving skew", "point in time"],
        "description": (
            "Online/offline parity, point-in-time correctness and the training-serving skew "
            "that quietly destroys model performance. Built around a real recommendation "
            "use case."
        ),
    },
    # --- Data Engineering ---------------------------------------------------
    {
        "slug": "data-pipelines-with-airflow",
        "title": "Data Pipelines with Airflow",
        "category": "Data Engineering",
        "level": "intermediate",
        "price": 109.0,
        "instructor": "Ravi Menon",
        "duration_hours": 13.0,
        "rating": 4.5,
        "enrollments": 9240,
        "tags": ["airflow", "dags", "orchestration", "backfill", "scheduling"],
        "description": (
            "Idempotent DAGs, backfills that do not melt your warehouse, sensors, dynamic "
            "task mapping and testing pipelines locally before they page you at 3am."
        ),
    },
    {
        "slug": "streaming-data-kafka",
        "title": "Streaming Data with Kafka",
        "category": "Data Engineering",
        "level": "advanced",
        "price": 159.0,
        "instructor": "Ravi Menon",
        "duration_hours": 15.0,
        "rating": 4.6,
        "enrollments": 5610,
        "tags": ["kafka", "streaming", "event driven", "exactly once", "consumers"],
        "description": (
            "Topics, partitions, consumer groups, exactly-once semantics and schema "
            "evolution. Build a real-time behavioural event pipeline — the same shape of "
            "problem as clickstream tracking."
        ),
    },
    {
        "slug": "dbt-analytics-engineering",
        "title": "Analytics Engineering with dbt",
        "category": "Data Engineering",
        "level": "beginner",
        "price": 89.0,
        "instructor": "Hannah Cole",
        "duration_hours": 10.0,
        "rating": 4.6,
        "enrollments": 11400,
        "tags": ["dbt", "sql", "modeling", "testing", "warehouse"],
        "description": (
            "Turn a warehouse full of raw tables into models analysts trust: staging "
            "layers, tests, documentation, incremental models and a review process for SQL."
        ),
    },
    {
        "slug": "postgres-performance-tuning",
        "title": "PostgreSQL Performance Tuning",
        "category": "Data Engineering",
        "level": "advanced",
        "price": 139.0,
        "instructor": "Hannah Cole",
        "duration_hours": 11.0,
        "rating": 4.7,
        "enrollments": 7320,
        "tags": ["postgres", "indexes", "query plans", "vacuum", "partitioning"],
        "description": (
            "Read query plans fluently, design indexes that get used, understand bloat and "
            "vacuum, and partition big event tables before they become the outage."
        ),
    },
    {
        "slug": "event-driven-architecture",
        "title": "Event-Driven Architecture",
        "category": "Data Engineering",
        "level": "intermediate",
        "price": 129.0,
        "instructor": "Tomás Ruiz",
        "duration_hours": 12.0,
        "rating": 4.4,
        "enrollments": 4980,
        "tags": ["events", "queues", "celery", "idempotency", "outbox"],
        "description": (
            "Queues, workers, retries, idempotency keys and the transactional outbox. Learn "
            "to move slow work off the request path without losing it."
        ),
    },
    # --- Backend Engineering ------------------------------------------------
    {
        "slug": "fastapi-production-apis",
        "title": "Production APIs with FastAPI",
        "category": "Backend Engineering",
        "level": "intermediate",
        "price": 99.0,
        "instructor": "Tomás Ruiz",
        "duration_hours": 12.0,
        "rating": 4.8,
        "enrollments": 16700,
        "tags": ["fastapi", "async", "pydantic", "dependency injection", "testing"],
        "description": (
            "Async done right, dependency injection, background tasks, auth, structured "
            "errors and a test suite that runs without a live database. Built as one real "
            "service, not twelve toy endpoints."
        ),
    },
    {
        "slug": "async-python-deep-dive",
        "title": "Async Python Deep Dive",
        "category": "Backend Engineering",
        "level": "advanced",
        "price": 119.0,
        "instructor": "Tomás Ruiz",
        "duration_hours": 10.0,
        "rating": 4.6,
        "enrollments": 6890,
        "tags": ["asyncio", "concurrency", "event loop", "blocking"],
        "description": (
            "The event loop, tasks and cancellation, structured concurrency, and finding "
            "the blocking call that is quietly serializing your whole service."
        ),
    },
    {
        "slug": "api-security-essentials",
        "title": "API Security Essentials",
        "category": "Backend Engineering",
        "level": "intermediate",
        "price": 109.0,
        "instructor": "Nadia Haddad",
        "duration_hours": 9.0,
        "rating": 4.5,
        "enrollments": 7150,
        "tags": ["auth", "jwt", "oauth", "rate limiting", "owasp"],
        "description": (
            "Session vs token auth, JWT pitfalls, OAuth flows, rate limiting, and the OWASP "
            "API top ten applied to code you would actually ship."
        ),
    },
    {
        "slug": "celery-background-jobs",
        "title": "Background Jobs with Celery",
        "category": "Backend Engineering",
        "level": "intermediate",
        "price": 89.0,
        "instructor": "Tomás Ruiz",
        "duration_hours": 7.0,
        "rating": 4.4,
        "enrollments": 5330,
        "tags": ["celery", "redis", "beat", "retries", "scheduling"],
        "description": (
            "Workers, queues, routing, retries with backoff, periodic tasks with Beat, and "
            "monitoring a queue that is silently falling behind."
        ),
    },
    {
        "slug": "system-design-interview-prep",
        "title": "System Design for Senior Engineers",
        "category": "Backend Engineering",
        "level": "advanced",
        "price": 149.0,
        "instructor": "Nadia Haddad",
        "duration_hours": 16.0,
        "rating": 4.7,
        "enrollments": 13900,
        "tags": ["system design", "scalability", "caching", "sharding", "tradeoffs"],
        "description": (
            "Work through real designs — a feed, a rate limiter, a recommendation service — "
            "and learn to defend trade-offs instead of reciting patterns."
        ),
    },
    # --- Frontend -----------------------------------------------------------
    {
        "slug": "modern-javascript-for-backend-devs",
        "title": "Modern JavaScript for Backend Developers",
        "category": "Frontend",
        "level": "beginner",
        "price": 69.0,
        "instructor": "Grace Lim",
        "duration_hours": 8.0,
        "rating": 4.3,
        "enrollments": 8760,
        "tags": ["javascript", "dom", "fetch", "modules", "events"],
        "description": (
            "Enough modern JavaScript to build the interactive parts of your own app: "
            "modules, fetch, event delegation, the DOM, and why your click handler fires "
            "twice."
        ),
    },
    {
        "slug": "frontend-performance-instrumentation",
        "title": "Frontend Performance and Instrumentation",
        "category": "Frontend",
        "level": "intermediate",
        "price": 119.0,
        "instructor": "Grace Lim",
        "duration_hours": 9.0,
        "rating": 4.6,
        "enrollments": 4210,
        "tags": ["web vitals", "sendbeacon", "batching", "throttling", "analytics"],
        "description": (
            "Instrument a site without slowing it down: batched beacons, throttled scroll "
            "handlers, idle callbacks, and measuring Core Web Vitals from real users rather "
            "than from your laptop."
        ),
    },
    {
        "slug": "htmx-and-hypermedia-apps",
        "title": "Hypermedia Apps with HTMX and Alpine",
        "category": "Frontend",
        "level": "beginner",
        "price": 79.0,
        "instructor": "Grace Lim",
        "duration_hours": 7.0,
        "rating": 4.5,
        "enrollments": 5540,
        "tags": ["htmx", "alpine.js", "server rendering", "progressive enhancement"],
        "description": (
            "Build interactive apps with server-rendered HTML and a sprinkle of JavaScript. "
            "Fast, simple, and a genuine alternative to a SPA for most products."
        ),
    },
    # --- Cloud & Infra ------------------------------------------------------
    {
        "slug": "docker-for-developers",
        "title": "Docker for Developers",
        "category": "Cloud & Infra",
        "level": "beginner",
        "price": 69.0,
        "instructor": "Samuel Adeyemi",
        "duration_hours": 8.0,
        "rating": 4.6,
        "enrollments": 21300,
        "tags": ["docker", "compose", "images", "volumes", "networking"],
        "description": (
            "Images, layers, volumes, networks and compose. Get a multi-service stack — app, "
            "Postgres, Redis, a vector database — running with one command."
        ),
    },
    {
        "slug": "kubernetes-in-practice",
        "title": "Kubernetes in Practice",
        "category": "Cloud & Infra",
        "level": "advanced",
        "price": 179.0,
        "instructor": "Samuel Adeyemi",
        "duration_hours": 18.0,
        "rating": 4.5,
        "enrollments": 9120,
        "tags": ["kubernetes", "deployments", "helm", "autoscaling", "ingress"],
        "description": (
            "Deployments, services, ingress, config and secrets, autoscaling and rollouts — "
            "taught by breaking a cluster and fixing it."
        ),
    },
    {
        "slug": "terraform-infrastructure-as-code",
        "title": "Terraform: Infrastructure as Code",
        "category": "Cloud & Infra",
        "level": "intermediate",
        "price": 129.0,
        "instructor": "Samuel Adeyemi",
        "duration_hours": 11.0,
        "rating": 4.4,
        "enrollments": 6740,
        "tags": ["terraform", "iac", "state", "modules", "cloud"],
        "description": (
            "State management, modules, workspaces and drift. Provision a full environment "
            "reproducibly instead of clicking through a console and hoping."
        ),
    },
    {
        "slug": "observability-with-opentelemetry",
        "title": "Observability with OpenTelemetry",
        "category": "Cloud & Infra",
        "level": "intermediate",
        "price": 119.0,
        "instructor": "Nadia Haddad",
        "duration_hours": 9.0,
        "rating": 4.5,
        "enrollments": 3980,
        "tags": ["tracing", "metrics", "logs", "opentelemetry", "spans"],
        "description": (
            "Traces, metrics and logs as one system. Instrument a distributed request path "
            "end to end and answer 'where did the latency go?' in under a minute."
        ),
    },
    # --- Product & Growth ---------------------------------------------------
    {
        "slug": "recommendation-systems-foundations",
        "title": "Recommendation Systems: Foundations",
        "category": "Product & Growth",
        "level": "intermediate",
        "price": 139.0,
        "instructor": "Elena Petrova",
        "duration_hours": 14.0,
        "rating": 4.7,
        "enrollments": 8890,
        "tags": ["collaborative filtering", "ranking", "cold start", "ab testing"],
        "description": (
            "Collaborative filtering, content-based ranking, the cold-start problem, and "
            "offline metrics that actually predict online lift. The classical grounding "
            "underneath every LLM-powered recommender."
        ),
    },
    {
        "slug": "behavioral-analytics-product",
        "title": "Behavioral Analytics for Product Teams",
        "category": "Product & Growth",
        "level": "beginner",
        "price": 79.0,
        "instructor": "Elena Petrova",
        "duration_hours": 7.0,
        "rating": 4.4,
        "enrollments": 6210,
        "tags": ["event tracking", "funnels", "cohorts", "retention", "taxonomy"],
        "description": (
            "Design an event taxonomy you will not regret in six months, then use it: "
            "funnels, cohorts, retention curves and the difference between a metric and a "
            "number."
        ),
    },
    {
        "slug": "conversion-copywriting-for-products",
        "title": "Conversion Copywriting for Digital Products",
        "category": "Product & Growth",
        "level": "beginner",
        "price": 59.0,
        "instructor": "Elena Petrova",
        "duration_hours": 6.0,
        "rating": 4.3,
        "enrollments": 4460,
        "tags": ["copywriting", "persuasion", "landing pages", "email", "ctas"],
        "description": (
            "Write product copy that motivates action without lying: specificity over "
            "adjectives, objection handling, and CTAs that match where someone actually is "
            "in their journey."
        ),
    },
    {
        "slug": "ab-testing-and-experimentation",
        "title": "A/B Testing and Experimentation",
        "category": "Product & Growth",
        "level": "intermediate",
        "price": 109.0,
        "instructor": "Hannah Cole",
        "duration_hours": 9.0,
        "rating": 4.5,
        "enrollments": 5170,
        "tags": ["experimentation", "statistics", "power", "guardrails"],
        "description": (
            "Sample size and power, sequential testing, guardrail metrics and the many ways "
            "an experiment lies to you. Applied to recommendation and onboarding changes."
        ),
    },
]
