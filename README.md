# TokenLens

**Control plane for enterprise AI agents** — observability, runtime spend
governance, and evidence-based model optimization for LangGraph agents
running in production.

TokenLens doesn't build AI agents; it sits above a customer's existing
LangGraph app. Every run emits telemetry (tokens, cost, latency, tool calls,
retries) into a BigQuery trace warehouse, spend budgets are enforced
in-process with the ability to pause a run mid-execution and resume it after
a human approves — not just kill it — and a nightly analysis pipeline
detects waste and proves cheaper model configurations by replaying real
production traces rather than relying on static benchmarks.

## Status

Pre-alpha. Building phase by phase — see [`phase.txt`](./phase.txt) for the
active phase and its checklist, and the roadmap for what comes after.

## Why this exists / is it worth building

See [`review.txt`](./review.txt) for the full analysis: current competitive
landscape (Langfuse, Braintrust, Portkey, Kosmoy, Datadog LLM Observability),
why the narrower "SDK + runtime budget enforcement + trace-replay
optimization" scope was chosen over the broader multi-agent orchestration
vision, and the specific technical differentiator (in-graph
`interrupt()`/checkpointer resume vs. gateway-level block/allow enforcement).

## Architecture

```
Customer LangGraph application (production)
            |
   TokenLens Python SDK  (wraps every graph node)
            |
   +--------+---------+----------------------+
   |  ASYNC telemetry path     SYNC policy path  |
   |        |                       |
   |  FastAPI ingest (Cloud Run)   in-process budget check
   |        |                       |
   |     Pub/Sub               budget exceeded?
   |        |                       |
   |  BigQuery warehouse    interrupt() -> checkpointer
   |        |                       |
   |  Nightly analyst agents   Runtime Context Agent
   |        |                       |
   |  Replay & Eval Agent     Slack approval card
   |        |                       |
   |  Optimization Planner    resume from checkpoint
   |        |                       |
   |  Policy Compiler  <---  audit log (Cloud SQL)
   |        |
   |  Dashboard + monthly report
```

Full detail, the six agents, worked example, and success metrics:
[`TokenLens-Project-Overview.txt`](./TokenLens-Project-Overview.txt).

## Stack

| Layer | Choice |
|---|---|
| Agent orchestration | LangGraph |
| API layer | FastAPI on Cloud Run |
| Buffer | Pub/Sub (with dead-letter topic) |
| Trace warehouse | BigQuery — day-partitioned, tenant-clustered |
| Control store | Cloud SQL (PostgreSQL) — registry, policy, checkpointer, audit log |
| Model access | Gemini Enterprise Agent Platform — Model Garden (Gemini, Claude, Grok) |
| Agent runtime | Agent Engine (managed) |
| Scheduling | Cloud Scheduler → Cloud Run Jobs |
| Secrets & identity | Secret Manager, Workload Identity Federation / ADC |
| Dashboard | Next.js |

## Google Cloud services used, and how they map to job-market demand

Every GCP service TokenLens actually uses (already enabled on
`tokenlens-504404`, or required by the architecture per `phase.txt`):

| Service | Role in TokenLens | 2026 job-market demand |
|---|---|---|
| **BigQuery** | Trace warehouse — every span lands here, day-partitioned/tenant-clustered; nightly SQL detectors run against it | **Core/High** — repeatedly named the top reason engineers pick GCP; central to data-engineering and analytics-infrastructure hiring |
| **Gemini Enterprise Agent Platform** (formerly Vertex AI — Model Garden + Agent Engine) | Model access (Gemini/Claude/Grok) and managed runtime for all six TokenLens agents | **Core/High** — Vertex AI/Agent Platform experience (train, tune, deploy, monitor) is explicitly named a top-10 AI/ML hiring skill in 2026 job-post analyses |
| **Cloud SQL (PostgreSQL)** | Control store — graph registry, budget policy, LangGraph checkpointer, audit log | **In-demand** — named alongside BigQuery as a sought-after database skill in GCP data-engineering roles |
| **IAM** / Workload Identity Federation / ADC | Auth for every service, no static API keys | **Baseline-expected** — assumed competency for any GCP hire, not called out as separately "hot" but never optional |
| **Cloud Run** | Hosts the FastAPI ingest API and eventually the dashboard backend | **Baseline-expected** — serverless/container skills are standard alongside the data/AI-specific ones |
| **Pub/Sub** | Buffer between ingest API and BigQuery writer, with dead-letter topic | **Supporting** — standard data-engineering plumbing skill, not a headline item on its own |
| **Cloud Scheduler** | Triggers nightly Analyst/detector runs and monthly Executive Report | **Supporting** — orchestration/MLOps-adjacent skill |
| **Secret Manager** | Holds runtime secrets (never committed to `.env`) | **Baseline-expected** |
| **Cloud Logging / Cloud Monitoring / Cloud Trace** | Operational observability of TokenLens's own services (distinct from the customer-agent telemetry TokenLens itself collects) | **Baseline-expected**, increasingly tied to MLOps practice |
| **Artifact Registry** | Stores container images for Cloud Run deploys | **Baseline-expected** |
| **Model Armor** | Enabled on the project; available for prompt/response safety screening if needed later | **Supporting** |

**Why this stack lines up well with hiring demand right now**: research
across 2026 job-posting analyses consistently names BigQuery and Vertex AI
(Agent Platform) as the two GCP skills employers ask for most — both are
core to TokenLens's architecture, not incidental. MLOps — the discipline of
running ML/agent systems in production with automation and monitoring — is
called the #1 capability Google-adjacent AI/ML teams hire for, which is
functionally what TokenLens's whole Monitor/Govern/Optimize loop is. Cloud
SQL, IAM, and Cloud Run round out the "baseline-expected" skills every GCP
job posting assumes. The main GCP certification signal (Professional Cloud
Architect, then Associate Cloud Engineer as the common starting point) is a
broader signal, not tied to one service, but worth knowing if this project
doubles as a portfolio/learning vehicle.

Sources: [ituonline.com – GCP skills rising in job postings](https://www.ituonline.com/blogs/what-is-gcp-and-why-google-cloud-skills-are-rising-fast-in-job-postings/) · [dev.to – Cloud AI & Data Engineering hiring](https://dev.to/jackm_345442a09fb53b/inside-google-jobs-series-part-1-cloud-ai-data-engineering-26l4) · [medium.com – 10 most wanted AI/ML skills from 500+ GCP job posts](https://medium.com/@huanzidage/inside-500-google-cloud-job-posts-the-10-most-wanted-ai-ml-skills-230a4804b5b4) · [hakia.com – GCP certifications guide 2026](https://hakia.com/skills/google-cloud-certifications/) · [analyticsinsight.net – Top 10 GCP certifications 2026](https://www.analyticsinsight.net/amp/story/top-list/top-10-google-cloud-certifications-for-career-growth-in-2026)

## Cloud project

GCP project: `tokenlens-504404` (Gemini Enterprise Agent Platform / BigQuery
/ IAM already enabled). Not to be confused with `nidhiflow-ai-platform`, a
separate existing project in the same account.

## Repo layout

```
tokenlens/
├── backend/                        FastAPI + LangGraph service (see backend/CLAUDE.md)
├── phase.txt                       Active build tracker — phase by phase, with checkboxes
├── review.txt                      Project viability review + scope decision
├── TokenLens-Project-Overview.txt  v1 product spec (source of truth for what we're building)
├── tokenlenssummary.txt            Broader future-platform vision (not v1)
├── cloudGcp&Docker.txt             Deployment pattern reference (from a sibling project)
├── CLAUDE.md                       Working conventions for AI-assisted development
└── README.md                       This file
```

## Local development

Backend setup lives in [`backend/CLAUDE.md`](./backend/CLAUDE.md). Short
version:

```bash
cd backend
uv sync
gcloud config set project tokenlens-504404
gcloud auth application-default login
uv run uvicorn main:app --reload
```

Local dev authenticates against GCP via Application Default Credentials —
no static API keys are used for Gemini/Claude/Grok access, and the same
auth pattern carries over unchanged when deployed to Cloud Run under a
dedicated service account.
