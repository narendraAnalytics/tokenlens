# TokenLens backend — CLAUDE.md

Backend-specific conventions. Read the root `CLAUDE.md` first for the
overall project context, phase-based build process, and GCP setup — this
file only covers what's specific to working inside `backend/`.

## Stack

- Python >=3.12, dependency management via `uv` (not pip/poetry directly —
  `uv sync`, `uv add <pkg>`, `uv export --no-hashes --format requirements-txt
  -o requirements.txt` when a requirements.txt is needed for Docker).
- FastAPI app instance lives in `backend/main.py` at the repo root of
  `backend/` (not inside an `app/` package). Run as `main:app`, e.g.
  `uvicorn main:app --reload` locally. Cloud Run injects `$PORT` — never
  hardcode a port in the run command used for deployment.
- LangGraph is the agent orchestration layer once Phase 1 dependencies are
  added (see `phase.txt`). Don't introduce a second orchestration framework.
- Postgres via `psycopg` + SQLAlchemy + Alembic for the control store
  (registry, budget policy, checkpointer, audit log) — not for trace data.
  `db.py` (repo root of `backend/`) holds the one shared, lazily-created
  SQLAlchemy engine (`get_engine()`, `lru_cache`d) — every control-store
  reader/writer (`tokenlens_sdk/budget.py`, the future audit-log writer in
  Phase 2 §5) should reuse it rather than creating its own engine.
- BigQuery for trace/span data once Phase 1 lands — access via
  `google-cloud-bigquery`, writes go through the ingest pipeline
  (FastAPI → Pub/Sub → BigQuery), never written directly from the SDK.
- `google-cloud-aiplatform` has been a pyproject dependency since Phase 1
  but was unused until Phase 2's Runtime Context Agent (`phase.txt` Phase 2
  §3) — its first real call site, hitting Gemini Flash through Agent
  Platform's Model Garden (the 2026 Vertex AI rebrand; same SDK/endpoints).
  If you find this dependency and no obvious caller, check Phase 2's status
  before assuming it's dead weight.

## Environment

- Config comes from `backend/.env` (gitignored) loaded via `python-dotenv` /
  `pydantic-settings`. A `.env.example` with empty placeholders should exist
  and be kept in sync with real keys used — commit that file, never `.env`.
- GCP auth is ADC-based, not API keys: `gcloud auth application-default
  login` locally, attached service account in Cloud Run. Don't add
  `GOOGLE_APPLICATION_CREDENTIALS` pointing at a downloaded JSON key unless
  there's a specific reason ADC doesn't work — it's an anti-pattern here.
- Before running anything against GCP, confirm the active project:
  `gcloud config get-value project` should say `tokenlens-504404`.

## Two async/sync paths — don't blur them

Per the product spec, TokenLens has two paths out of the SDK that must
never be confused:
- **Async telemetry path** — spans → FastAPI ingest → Pub/Sub → BigQuery.
  Fire-and-forget. Must never block or add meaningful latency to the
  customer's agent.
- **Sync policy path** — in-process budget check inside the SDK. Must block:
  if a budget is exceeded, it calls LangGraph's `interrupt()`, persists to
  the checkpointer, and waits for human approval before resuming.

When adding SDK code, be explicit about which path a piece of code is on.
A budget check that accidentally becomes async, or a telemetry emit that
accidentally blocks the hot path, breaks the product's core promise
(sub-1ms added latency per node — see review.txt / Overview doc §7 success
metrics).

## Sync policy path implementation (Phase 2 §2)

`TokenLens.instrument()` (`tokenlens_sdk/client.py`) does two separate
things to a compiled graph, not one:

1. Attaches `TokenLensCallbackHandler` via `config["callbacks"]` — this is
   the *observational* path (Phase 1), used for span emission and for
   tracking the in-process gating counter (`handler.cumulative_cost_usd`,
   priced via `tokenlens_sdk/pricing.py`'s local rate-table copy — never
   `tokenlens_cost`, which is ingest-computed and official-billing-grade).
2. Replaces every real node's `PregelNode.bound` in place with a wrapper
   (`_InstrumentedGraph._budget_gate`) — this is the *enforcement* path.

These are separate because `interrupt()` **must be called from literally
inside a node's own execution** (LangGraph resolves it via a contextvar the
Pregel runtime sets while running that node's task) — a callback handler
observes node execution but is not part of the node's call stack, so
`interrupt()` cannot be called from one. This is why a node-wrapper exists
at all, not just a richer callback handler.

The wrapper checks budget **before** running the real node body, not after
the node that caused the breach finishes. Per LangGraph's own docs,
resuming an interrupted node re-executes it from the top — gating at the
*end* of the node that broke the budget would re-run that node's (possibly
expensive) real work a second time on resume. Gating at the *start* of the
next node means the only thing that ever re-runs on resume is the cheap
pre-check itself. `PregelNode.bound` is replaced with a
`langchain_core.runnables.RunnableLambda`, not `langgraph._internal
._runnable.RunnableCallable` — the latter is what LangGraph uses
internally for its own nodes, but it's a private (`_internal`) module and
not something to depend on; `RunnableLambda` is public API and was verified
to receive the identical Pregel-populated `config` dict, which is what
propagates the contextvar `interrupt()` needs.

**Durable spend, not the in-process counter, is what actually gates**
(`tokenlens_sdk/spend_ledger.py`): `handler.cumulative_cost_usd` resets to
zero on every fresh invocation, including every resume — `Command(resume=
...)` IS a new `.invoke()` call with a brand-new handler. Confirmed by
direct testing that gating on the in-process counter alone lets a resumed
run's pre-check see "$0 spent" and never re-block. The fix is a `run_spend`
table (one row per `thread_id`), written synchronously in `on_llm_end`
whenever a real LLM call happens (never per node — most nodes make no paid
call), and read by the node wrapper's pre-check instead of the handler's
counter. A hidden-state-key approach (stash the running total in the
graph's own checkpointed State) was tried and rejected: confirmed by direct
testing that LangGraph silently drops any key a node returns that isn't
declared in the customer's own State `TypedDict` — there's no way to smuggle
extra tracking through checkpointed state without requiring the customer to
declare it, which breaks the "three lines, no other code changes" promise.

**`interrupt()` memoizes per call-site once resumed — this changes what
"re-checking" can mean.** Confirmed via its source
(`langgraph.types.interrupt`): the *first* `interrupt()` call within a
given node-task, once given a resume value, returns that same value on any
later re-execution of that exact call site rather than raising again. So
resuming node N's pre-check and finding it still over cap does **not**
re-prompt at node N's own check — it silently proceeds, because calling
`interrupt()` a second time there just replays the stored answer. This
isn't a bug to fix, it's what "Approve" *means*: a human resuming is
explicit permission for that one node to proceed, cap or no cap. The real
safety property is at the **next** node: a never-before-resumed call site
gets its own fresh check against current durable spend and interrupts
independently if still over cap — verified directly (a 3-node probe graph
where node 2 proceeded on a plain resume, then node 3 immediately
re-interrupted since the cap was never raised, then a second resume with
the cap actually raised completed fully). Each node boundary is its own
approval checkpoint; there is no single global unlock.

**Budget cap lookup** (`tokenlens_sdk/budget.py`): looked up from
`budget_policies` fresh on every `.invoke()`/`.stream()` call, not on the
60s poll cadence used for `payload_capture_mode`. An Approve-and-raise-cap
decision must take effect on the very next resume, not up to 60s later — a
stale spend cap is a security-relevant gap, not just UX lag. A missing
policy row or unreachable control store both fail open (no cap enforced,
warning logged) — revisit before this gates real production spend.

**`thread_id` = `run_id`** (Phase 2 §0): `_with_tokenlens_callback` only
mints a new `run_id` when the caller didn't already pass one via
`config["configurable"]["thread_id"]`. A fresh run gets a new one; a resume
call (`app.invoke(Command(resume=...), config={"configurable":
{"thread_id": <original>}})`) reuses the interrupted run's own thread_id so
the checkpointer resumes the correct state — this is also why the caller
performing a resume must know the original run's thread_id, e.g. from the
audit trail (Phase 2 §5, not yet built).

**Known limitation**: gating is node-boundary granularity. A single node
that makes several expensive LLM calls in a row can't be interrupted
mid-node — the breach is only caught at the start of the *next* node. Fine
for the toy graph and matches the Overview doc's node-level design
boundary; revisit if a real customer node turns out to internally loop
over multiple paid calls.

## Runtime Context Agent (Phase 2 §3)

`backend/agents/runtime_context.py` — `summarize_halted_run()`, called
from `client.py`'s `_budget_gate` right before `interrupt()` fires on a
budget breach. Produces the 3-line summary folded into the interrupt
payload's `"summary"` key.

**Call surface**: uses `google.genai.Client(vertexai=True, project=...,
location="global")` directly, NOT `agentplatform.Client` (the installed
`google-cloud-aiplatform` package's "Gemini Enterprise Agent Platform"
wrapper) — confirmed by direct inspection that `agentplatform.Client`'s
top-level surface is scoped to `agent_engines`/RAG/model-garden-catalog
management and does not expose `.models.generate_content`. `google-genai`
is what actually backs content generation; added as an explicit pyproject
dependency since this is now a direct import, not just a transitive one
pulled in by `google-cloud-aiplatform`.

**Region**: `location="global"`, not `settings.gcp_region`
(`"asia-south1"`, used for BigQuery/Pub/Sub). Google's own Model Garden
docs recommend the global endpoint for Gemini calls without a data
residency requirement — better availability, capacity-aware routing,
fewer `429`s under load. This call only ever sends span metadata and
spend numbers, never raw customer documents, so residency isn't a
concern. Verified empirically (not just per docs) that `"global"` serves
`gemini-2.5-flash` for this project before committing to it.

**Auth**: ADC, same as every other GCP call in this codebase (see
"Environment" above) — no API key, confirmed working. If you find an API
key for Agent Platform sitting in `.env` or the GCP console, it's not
what this call site uses; don't wire it in without a specific reason ADC
stops working.

**Thinking mode adds real latency for this prompt**: measured directly,
Gemini 2.5 Flash's default "thinking" mode added ~10s+ (16-20s total) to
this specific 3-line-summary prompt vs. ~5-7s with it off — same output
quality either way for a task this short. `_GENERATE_CONFIG` in
`runtime_context.py` sets `ThinkingConfig(thinking_budget=0)` accordingly.
Worth re-checking if the prompt shape changes significantly (e.g. if a
future revision asks for more elaborate reasoning).

**"Last N spans"**: `TokenLensCallbackHandler._node_spans`
(`tokenlens_sdk/handler.py`) pops each node's record once its span
finishes — nothing about a completed node survived past that point before
this phase. Added a bounded `deque(maxlen=20)` of small plain dicts
(`recent_spans` property), appended in `_finish_span` — deliberately not
the full `_NodeSpanRecord`, which holds a live OTel `Span` object and full
unredacted `input_state`, neither of which belongs in an LLM prompt.

**Topology/remaining-steps are heuristic, not a general solver**:
`client.py`'s `_graph_topology()` reads `self._graph.get_graph()` for a
flat nodes/edges snapshot; `runtime_context.estimate_remaining_steps()`
does a longest-path node count from the halted node to the graph's end.
Neither attempts conditional-edge branch prediction or subgraphs — fine
for a toy-graph-scale DAG, revisit if a real customer graph needs it.

**Never blocks or fails the gate**: `summarize_halted_run()` is a total
function — wrapped in a `ThreadPoolExecutor` with an 8s timeout, falling
back to a deterministic non-LLM summary (built from the same structured
inputs) on any timeout or exception. The budget gate's `interrupt()` must
fire regardless of whether the Gemini call succeeds.

## Slack approval card (Phase 2 §4)

`backend/slack_notify/approval_card.py` — `send_approval_card()`, called
from `client.py`'s `_budget_gate` right alongside the Runtime Context
Agent call, both right before `interrupt()` fires. Builds a Block Kit
message (`_build_blocks()`) and sends it via `chat.postMessage` using a
cached `slack_sdk.WebClient` singleton (same `lru_cache` pattern as
`agents/runtime_context.py`'s Gemini client).

**Card layout**: header, a graph/tenant/node section, the Runtime Context
Agent's 3-line summary as its own section, a context line with
spend/cap/run id, then three action buttons — Approve (`primary` style),
Approve & Raise Cap (default style, with a `confirm` dialog since it
changes the budget policy), Kill (`danger` style, with a `confirm` dialog
since it's destructive/permanent). Confirm dialogs on the two
consequential actions follow Slack's own Block Kit guidance for
expensive/destructive buttons. Validated against Slack's public
`blocks.validate` endpoint (no auth needed) before being wired in.

**Button `value` = `thread_id`** (= `run_id`, same ID used everywhere
else in Phase 2 per §0's decision) — this is what phase.txt Phase 2 §5's
webhook handler will use to resume the correct checkpointed run.
`thread_id` is new on the interrupt payload dict as of this work; nothing
carried it before since `_budget_gate` only needed it internally.

**Never blocks or fails the gate**: `send_approval_card()` never raises.
A missing `SLACK_BOT_TOKEN`/`SLACK_APPROVAL_CHANNEL` (both empty by
default) or a live `SlackApiError` both just log a warning and return —
the run still pauses correctly via `interrupt()` either way; a human
simply doesn't get a Slack notification for that particular breach. Same
fail-open philosophy as `runtime_context.summarize_halted_run()` at the
same call site, for the same reason: nothing about notifying a human
should be allowed to break the actual budget enforcement.

**Local dev secret**: `SLACK_BOT_TOKEN`/`SLACK_APPROVAL_CHANNEL` in
`backend/.env` (empty in `.env.example`, gitignored like every other
local secret) — per phase.txt §4, this moves to Secret Manager once past
pure local testing, not before. Creating the actual Slack app and bot
token is a one-time, human-interactive step (api.slack.com, `chat:write`
scope, install to a workspace) — not something to script or fake; the
code path is written and tested for both the "not configured" and
"configured" cases, waiting on real credentials to exercise a live send.

## Testing

- `pytest` + `pytest-asyncio` are already in the dev dependency group.
- Test the toy LangGraph app locally end-to-end before deploying anything
  (see Phase 1 step 6 in `phase.txt`) — local dev talks to the *real*
  `tokenlens-504404` Pub/Sub and BigQuery via ADC, there's no meaningful
  local emulator substitute for this phase.

## Span attribute naming — OTel GenAI conventions

All span-emitting code (the SDK, the ingest API) uses OpenTelemetry's GenAI
semantic conventions (`gen_ai.*`) for anything the spec defines — model
name, token counts, operation name, agent/tool identity — rather than
inventing vendor-specific attribute names. This is what keeps TokenLens
compatible with Cloud Trace and other OTel-GenAI-aware backends later via a
standard exporter, with no instrumentation rewrite.

Anything the spec doesn't define (cost, cached_tokens, retry_count,
tenant_id, confidence) goes under a TokenLens-owned `tokenlens.*` namespace
— never bolted onto `gen_ai.*`, which belongs to the spec itself. The exact
attribute list is enumerated in Phase 1 §4 of `phase.txt`; treat it as
binding for new span code the same way the PII policy below is binding.

## SDK implementation

`tokenlens_sdk/` (Phase 1 §4) instruments a compiled LangGraph app via a
LangChain callback handler (`handler.TokenLensCallbackHandler`), not by
monkeypatching LangGraph internals — this is the same integration point
LangSmith uses, and it means the SDK works regardless of what a node's
function body actually does internally. `client.TokenLens.instrument()`
returns a transparent proxy (`__getattr__` passthrough) so the wrapped
graph is a true drop-in replacement. See `examples/toy_graph.py` for a
working 3-node reference and the exact "three lines" integration shape.

One current limitation, fine for Phase 1, worth fixing before Phase 5's
dashboard needs real trace hierarchy: node spans currently correlate via
the shared `tokenlens.run_id` attribute, not OTel's native trace/parent
context — each node span is its own OTel trace root. Revisit if/when a
UI needs to render a run as a proper span tree rather than a flat list
filtered by `tokenlens.run_id`.

## Ingest pipeline (Phase 1 §5)

`backend/ingest/` — FastAPI router (`routes.py`) mounted in `main.py`,
`POST /v1/traces`. Accepts a batch of spans in the wire format defined by
`schemas.SpanIn`, which deliberately mirrors the BigQuery `spans` table
columns 1:1 (see `scripts/setup_bigquery.py`) — the worker inserts with no
translation beyond adding `ingested_at` and `tokenlens_cost`, the two
fields computed server-side. `tokenlens_sdk/exporter.py`'s
`HTTPSpanExporter` is the client side of this contract: it converts an OTel
`ReadableSpan`'s `gen_ai.*`/`tokenlens.*` attributes into the same wire
dict. If either side's field names change, change both.

`scripts/bigquery_worker.py` pulls from the `tokenlens-traces-worker`
subscription (created by `scripts/setup_pubsub.py`), computes
`tokenlens_cost` via `ingest/pricing.py`'s placeholder rate table (flagged
there as illustrative — replace before this feeds real billing), and
streams the row into BigQuery. A failed insert is nacked, not silently
dropped — Pub/Sub redelivers up to the subscription's
`max_delivery_attempts` before the message lands on the
`tokenlens-traces-dlq` dead-letter topic.

Local dev: run `uv run uvicorn main:app --reload`, point
`TokenLens(..., ingest_url="http://localhost:8000/v1/traces")` at it (see
`examples/toy_graph.py`), then drain manually with
`uv run python scripts/bigquery_worker.py --once`. This whole path was
verified end-to-end this way before being marked done in `phase.txt`.

## Deployment (Phase 1 §7)

`Dockerfile` + `.dockerignore` follow the pattern in the sibling NidhiFlow
guide (`../cloudGcp&Docker.txt`): `python:3.12-slim`, non-root user,
shell-form `CMD` so `${PORT:-8080}` actually expands at container start.
`requirements.txt` is regenerated from `uv.lock` via `uv export --no-hashes
--format requirements-txt -o requirements.txt` before building — keep it in
sync if dependencies change, don't hand-edit it.

Deployed via `gcloud run deploy tokenlens-ingest --source .` (Cloud Build
handles the image build/push automatically). Service account
`tokenlens-ingest@tokenlens-504404.iam.gserviceaccount.com` has ONLY
`roles/pubsub.publisher` — deliberately not `aiplatform.user` or BigQuery
roles, since `POST /v1/traces` only publishes to Pub/Sub and does nothing
else. BigQuery/Pub/Sub-subscriber access belongs to `bigquery_worker.py`,
which is not deployed to Cloud Run (stays a locally/Scheduler-run script
for now — see root `CLAUDE.md`'s deploy-sequencing note).

**Known issue:** the service's public URL 404s on every request (Google's
own generic branded 404 page), even though the service itself is
`Ready=True` with correct IAM. This was diagnosed thoroughly, not assumed:
an authenticated identity-token request still 404s (rules out IAM), the
TLS cert is a genuine Google Trust Services cert for `*.a.run.app` (rules
out a local proxy/MITM), Cloud Logging shows **zero** requests ever
reaching the container across 10+ attempts over 5+ minutes, and a
completely fresh second service (`tokenlens-ingest-v2`, deployed clean then
deleted) hit the identical symptom — so it isn't specific to this one
service either. This matches a bug reported on Google's own developer
forums (Ready=True, correct IAM, `ingress=all`, public URL still 404s with
zero request logs, reproduced across multiple projects/accounts). It's a
Cloud Run edge-routing platform bug — file a GCP support ticket if it needs
resolving, don't keep re-debugging locally.

**Verifying code correctness independent of that bug:** pull the exact
image Cloud Run built (`docker pull <image>@<sha256-digest-from-gcloud-run-
services-describe>`) and `docker run` it locally with the same env vars,
mounting a copy of your ADC file
(`~/.config/gcloud/application_default_credentials.json` /
`%APPDATA%\gcloud\application_default_credentials.json`) at
`GOOGLE_APPLICATION_CREDENTIALS`. This was done once already and confirmed
`/healthz` → 200 and `POST /v1/traces` → 202, genuinely publishing to
Pub/Sub and landing in BigQuery — i.e. the container is provably correct,
fully decoupled from Cloud Run's routing. On Windows/Git Bash, prefix that
`docker run` with `MSYS_NO_PATHCONV=1` or the POSIX-style paths in
`-v`/`-e` args get silently mangled into Windows paths first.

## Budget-gating policy (Phase 2 §0)

Decided in Phase 2 §0 (`phase.txt`), binding for the SDK's sync policy path.

**Spend tracking for the sync gate**: an in-process running-token counter
inside the SDK, priced via a local copy of the pricing table logic
(`ingest/pricing.py`'s rates, not a second table) — purely for gating,
explicitly **not** the official billing figure (that's `tokenlens_cost`,
computed async at ingest). This keeps the budget check on the hot path at
effectively zero added latency, matching the sub-1ms promise above. The
tradeoff: it only sees the current run, not other concurrent runs by the
same tenant/team — so it enforces a per-run cap correctly but has no
cross-run visibility.

**Scope enforced in Phase 2**: per-run cap only. Daily/per-team caps need
cross-run visibility the in-process counter doesn't have, so they're
deferred to a follow-up phase rather than blocking Phase 2's interrupt/
approve/resume demo. Don't add unenforced `daily_cap_usd`/`team_cap_usd`
columns to `budget_policies` speculatively — add them via a new Alembic
migration when that follow-up actually lands.

**thread_id**: reuses `tokenlens_sdk`'s existing per-invocation `run_id` as
the LangGraph `thread_id` — one ID for both telemetry correlation and
checkpoint resume, not two identifiers to keep in sync.

## PII / payload-capture policy

Decided in Phase 1 §2 (`phase.txt`), binding for all SDK and ingest-API
code. Don't invent a different redaction approach ad hoc in a later
feature — update this section instead if the policy needs to change.

**What a span captures**: full node input/output state (all tool
responses, not summaries) by default. This is a deliberate, load-bearing
choice: trace replay (the Replay & Eval Agent, TokenLens's core
differentiator) is only honest if the recorded span contains everything
the node actually saw and produced. A summarized or truncated capture would
quietly make every future "proven savings" number fake.

**Where redaction runs**: in the SDK, before a span ever leaves the
customer's environment — not after arrival at the ingest API. The FastAPI
ingest endpoint runs a second-pass validation/scrub as a backstop (catches
a missing or misconfigured SDK redaction rule), but it must never be the
*only* place redaction happens. Rationale: "we never see raw PII" is a
materially stronger, more auditable claim to an enterprise security
reviewer than "we redact it after receiving it" — and it shrinks the blast
radius if the ingest service itself is ever compromised, since raw PII
never transits the network or touches TokenLens infrastructure at all.

**How redaction identifies what to scrub** — hybrid, two layers:

1. *Field-level rules* — the customer marks specific LangGraph state keys
   as sensitive (e.g. `ssn`, `patient_name`, `account_number`) in their
   SDK config. Deterministic, no false positives/negatives on structured
   fields they've told us about.
2. *Regex/pattern fallback* — a fixed set of PII patterns (emails, phone
   numbers, card numbers, common government-ID formats) runs over
   free-text fields to catch PII the customer didn't think to mark as a
   dedicated field. This is a backstop for unstructured leakage, not the
   primary mechanism — field-level rules take precedence where both apply.

**Default capture stance**: full capture is the default for a new tenant,
with a per-tenant opt-out (not opt-in) to metadata-only capture. This
matches the product's core value prop — evidence-based optimization only
works with full-state traces — and is defensible *because* it's paired
with mandatory hybrid redaction above, not despite it. Redaction quality is
therefore load-bearing from day one; don't treat it as a nice-to-have.

**Per-tenant opt-out mechanism**: a `payload_capture_mode` field on the
tenant record in the Cloud SQL control store (`full` | `metadata_only`),
read by the SDK the same way it already polls routing policy (per the
Overview doc's 60-second policy poll at graph-compile time) — one
polling mechanism, not a second bespoke config path. `metadata_only` mode
drops span input/output payloads entirely (tokens/cost/latency/status
still captured) but forfeits trace-replay eligibility for that tenant's
runs — this tradeoff should be visible to the tenant when they opt out,
not silent.
