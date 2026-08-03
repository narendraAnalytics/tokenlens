# TokenLens — Root CLAUDE.md

Working conventions for this repo. Read this before making changes anywhere
in the project.

## What this project is

TokenLens is an enterprise AI control plane: it sits above a customer's
*existing* LangGraph agent, meters every node execution, enforces spend
budgets in real time (pause mid-run, ask a human, resume from checkpoint —
not just kill the process), and proves cheaper model configurations by
replaying real production traces rather than static benchmarks.

Two spec docs exist in this repo describing two different scopes:
- `TokenLens-Project-Overview.txt` — **this is the product we are building.**
  Narrow, six-agent, SDK-first.
- `tokenlenssummary.txt` — a broader, 11-agent multi-model orchestration
  vision. Treated as a possible future platform play, not v1. Do not build
  toward this doc unless explicitly told the scope has changed.

See `review.txt` for the full reasoning behind that choice, current
competitive landscape (Portkey, Kosmoy, Langfuse, Braintrust, Datadog LLM
Observability), and the technical differentiator (in-graph interrupt/resume
via LangGraph's checkpointer vs. gateway-level block/allow).

## Build process

We build **phase by phase**, not all at once. `phase.txt` at repo root is
the authoritative tracker: it holds the full roadmap plus a detailed
checkbox list for the currently active phase only. Future phases get
expanded into checkboxes when we reach them, not in advance.

- Before starting work, check `phase.txt` for the active phase and its
  unticked boxes.
- Tick a box (`[ ]` → `[x]`) only once the item is actually done and
  verified — not just written or attempted.
- Don't jump ahead to a later phase's work while earlier boxes in the
  active phase are still open, unless the user explicitly redirects.

**Deploy sequencing (decided 2026-08-03):** not every phase deploys to the
cloud as it's finished. The ingest API (Phase 1) was deployed to Cloud Run
because it's stable and won't change again. Phase 2 onward (the actual
agents — Spend Guard, Analyst, Replay & Eval, Planner, Dashboard) are built
and tested **locally first**, with their own (Agent Engine) deployment
deferred to a later consolidated push — matching the workflow from a prior
project, and because those phases are still actively in flux. One known
exception: the Spend Guard's Slack approval card needs a real public HTTPS
callback, which can't be tested purely local-only — decide between a
tunnel (ngrok), an early single-endpoint deploy, or mocking the approval
when Phase 2 gets there.

## Cloud platform

- GCP project: `tokenlens-504404` (NOT `nidhiflow-ai-platform` — that's a
  different, existing project in the same gcloud account; double-check
  `gcloud config get-value project` before running gcloud/bq/gsutil commands).
- Platform: Gemini Enterprise Agent Platform (the 2026 rebrand of Vertex AI —
  same API endpoints, same SDKs, console UI moved). Model Garden on this
  platform gives access to Gemini, Claude (Anthropic), and Grok (xAI) through
  one IAM identity — no separate provider API keys needed.
- Auth pattern: Application Default Credentials (ADC) everywhere, not static
  API keys. Locally: `gcloud auth application-default login`. In Cloud Run:
  a dedicated service account attached to the service, least-privilege roles
  only. Same code path both places — this is intentional, don't special-case
  local vs. cloud auth in application code.
- Trace warehouse: BigQuery, day-partitioned and tenant-clustered. Control
  state (budget policy, LangGraph checkpointer, audit log): Cloud SQL
  (PostgreSQL). Don't put high-volume trace data in Cloud SQL or low-volume
  control state in BigQuery — the split is deliberate, see review.txt §2.
- Deployed resources so far (Phase 1): BigQuery dataset `tokenlens_traces`
  (table `spans`), Pub/Sub topic `tokenlens-traces` + DLQ
  `tokenlens-traces-dlq` + subscription `tokenlens-traces-worker`, and the
  `tokenlens-ingest` Cloud Run service (service account
  `tokenlens-ingest@tokenlens-504404.iam.gserviceaccount.com`, granted
  ONLY `roles/pubsub.publisher` — least privilege, matches exactly what
  `POST /v1/traces` does and nothing more). See `backend/CLAUDE.md`
  "Deployment" for the known issue with reaching this service's public URL.
- Deployed resources so far (Phase 2 §1): Cloud SQL instance
  `tokenlens-control-dev` (Postgres 16, `db-f1-micro`, `asia-south1`,
  edition `ENTERPRISE` — the project defaults to `ENTERPRISE_PLUS`, which
  rejects shared-core tiers, so `--edition=ENTERPRISE` must be passed
  explicitly on any recreate), database `tokenlens_control`, user
  `tokenlens`. Holds the Alembic-managed control-store tables
  (`graph_registry`, `budget_policies`, `audit_log`) plus LangGraph's own
  `checkpoint_*` tables (created by `PostgresSaver.setup()`, never by a
  hand-written migration).

## Repo layout

- `backend/` — FastAPI + LangGraph service. See `backend/CLAUDE.md` for
  backend-specific conventions.
- `phase.txt` — active build tracker, phase by phase.
- `review.txt` — the project-viability review and the v1-scope decision.
- `TokenLens-Project-Overview.txt` — the v1 product spec (source of truth).
- `tokenlenssummary.txt` — the larger future-platform vision (not v1).
- `cloudGcp&Docker.txt` — deployment guide, originally written for the
  sibling NidhiFlow backend on `nidhiflow-ai-platform`. Useful as a pattern
  reference (Dockerfile shape, Cloud Run flags, cost notes) but its specific
  project ID, Cloud SQL instance, and secrets belong to that other project —
  don't copy those values into TokenLens config.

## Known gotchas from repo history

- `backend/.env` was found still populated with NidhiFlow's values (Firebase,
  Razorpay, `nidhiflow-ai-platform` Cloud SQL) when TokenLens work started.
  Phase 1 step 0 in `phase.txt` replaces this — if you see NidhiFlow values
  in `.env` again, that's stale, not intentional multi-tenancy.
- Never commit `.env` or real secrets. `backend/.gitignore` already excludes
  `.env` and `.venv` — keep it that way.
- The deployed `tokenlens-ingest` Cloud Run service's public URL currently
  404s on every request (Google's generic branded 404, zero requests ever
  reach the container per Cloud Logging) despite `Ready=True`, correct
  IAM, and `ingress=all` — a platform-side Cloud Run routing bug, not a
  config or code issue (methodically ruled out: auth, TLS/MITM, and
  service-specific misconfig — see `phase.txt` Phase 1 §7 for the full
  diagnostic trail, and `backend/CLAUDE.md` "Deployment" for how code
  correctness was verified anyway via a local Docker run of the exact
  deployed image). Needs a GCP support ticket to actually resolve; don't
  burn time re-debugging this locally if it resurfaces.
- On Windows/Git Bash, `docker run -v`/`-e` arguments containing
  POSIX-style absolute paths (e.g. `/tmp/adc.json`) get silently mangled
  into Windows paths by Git Bash's MSYS path conversion before Docker ever
  sees them. Prefix the command with `MSYS_NO_PATHCONV=1` when this
  happens — cost real debugging time once already (see `phase.txt` Phase 1
  §7).
- `tokenlens-control-dev` (Cloud SQL, `db-f1-micro`) can enter Google-side
  `MAINTENANCE` state unannounced, during which `cloud-sql-proxy` accepts
  TCP connections but they get reset mid-handshake ("server closed the
  connection unexpectedly" / "instance closed the connection" in the proxy
  log). Not a config or code issue — check `gcloud sql instances describe
  tokenlens-control-dev --format="value(state)"`; if it says `MAINTENANCE`,
  just wait, it self-resolves in a few minutes. Don't re-provision or
  re-auth in response to this.
