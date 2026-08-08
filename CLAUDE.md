# TokenLens — Root CLAUDE.md

Working conventions for this repo. Read this before making changes anywhere
in the project.

## Current status (updated 2026-08-08 — keep this current, don't let it go stale)

- **NEXT SESSION STARTS HERE: Phase 3C (Cloud Deployment & Production
  Verification) is DONE and verified.** Full evidence trail in
  `phase4.txt` at repo root (despite the filename — it holds the Phase
  3C record, not Phase 4); summarized as `phase.txt`'s "PHASE 3C" section
  (folded in 2026-08-08, same precedent as `phase3.txt`'s fold-in).
  - **Deployed service**: `tokenlens-backend` on Cloud Run, region
    `asia-south1`. Service URL:
    `https://tokenlens-backend-418874072229.asia-south1.run.app` — but
    like `tokenlens-ingest`, its public URL 404s (third confirmed
    reproduction of the platform-side Cloud Run edge-routing bug, see
    `backend/CLAUDE.md` "Deployment"). All verification was done via the
    local-image-pull-and-run fallback, extended with a local
    `cloud-sql-proxy` (for Cloud SQL) and a `pyngrok` tunnel (for the
    Slack Interactivity webhook) — see `backend/CLAUDE.md` for the full
    method.
  - **Two real bugs found during verification, both fixed**: (1)
    `backend/.dockerignore` excluded `examples/`, but
    `slack_notify/resume.py`'s demo Slack-approval resume path imports
    from `examples.budget_breach_probe` at runtime — every Approve click
    crashed with `ModuleNotFoundError` in the deployed container until
    fixed. (2) `examples/budget_breach_probe.py`'s own cleanup deletes
    checkpoint state immediately after the interrupt fires, before a
    human can click Approve — a test-script issue, not app code, worked
    around with a scratch variant for manual verification only. Full
    details in `backend/CLAUDE.md` "Deployment" and `phase4.txt` step 12.
  - The `API_PORT=8080` self-telemetry fix (`main.py`'s `lifespan`
    otherwise hardcodes `localhost:8000`, not Cloud Run's actual port)
    was verified for real, not just assumed: a genuine `/v1/chat` call's
    "answer" span was confirmed present in BigQuery with real
    `gen_ai.*` usage post-deploy.
  - `tokenlens-control-dev` was found `STOPPED` during this phase (a new
    failure mode, distinct from the documented `MAINTENANCE` gotcha
    below) — started via `gcloud sql instances patch
    --activation-policy=ALWAYS` with user go-ahead.
- **Phase 3 (Multi-Agent Control Plane) is DONE and verified — 3A
  (Agent Platform Foundation), 3B (the 5 agents), and now 3C (Cloud
  Deployment)** — `phase.txt`'s "PHASE 3" sections are fully ticked
  (folded in from `phase3.txt`/`phase4.txt`, both now superseded
  historical drafts — read `phase.txt` for current Phase 3 state). Built
  in 3B: `agents/spend.py` (7 deterministic SQL detectors + cost/
  comparison tools), `agents/replay.py` (timeline reconstruction +
  failure explanation), `agents/policy.py` (read-only governance Q&A over
  Phase 2's Spend Guard, no new interrupt/Slack path), `agents/planner.py`
  (intent classification + multi-agent merge, plain Python orchestration
  not a LangGraph graph), `agents/insights.py` (Gemini Flash synthesis
  over fed findings), and a new `POST /v1/investigate` endpoint
  (`investigate/`) tying them together — all verified end-to-end against
  real Gemini/BigQuery/Cloud SQL, no mocking. See `backend/CLAUDE.md`'s
  "The 5 agents (Phase 3B)" section for implementation details and one
  real system-prompt fix found along the way (Policy Agent initially
  skipped a needed second tool call).
- **Also explicitly deferred, not forgotten** (per Phase 3's own Scope
  Decision, reconfirmed at the start of the 3B build session when asked
  again, and unaffected by Phase 3C's cloud-deployment scope): Claude/
  Grok/open-source model adapters behind the gateway, the model-selection
  UI, and the frontend itself (chat + upload + compare) — zero UI code
  exists anywhere in this repo. Don't start any of these without an
  explicit new scope decision. Phase 4 (Replay & Evaluation Harness) is
  the next planning pass, not started — see `phase4.txt`'s own
  "DEFERRED — Phase 4" closing note.
- Phase 1 (SDK & Telemetry) and Phase 2 (Spend Guard) are functionally
  done — only Phase 2's 90-second demo recording (a user-only action, not
  code) remains open, and it does not block later phases.
- **Only read `phase.txt`/`phase4.txt`/`summary.txt`/`review.txt` in full
  when you're actually planning or auditing a phase** (`phase3.txt` and
  `phase4.txt` are both superseded, see above — don't treat them as
  current) — for routine
  code changes, this status block plus `backend/CLAUDE.md`'s topic index
  should be enough context to start working.

## What this project is

TokenLens is an enterprise AI control plane: it sits above a customer's
*existing* LangGraph agent, meters every node execution, enforces spend
budgets in real time (pause mid-run, ask a human, resume from checkpoint —
not just kill the process), and proves cheaper model configurations by
replaying real production traces rather than static benchmarks.

Two spec docs exist in this repo describing two different scopes:
- `TokenLens-Project-Overview.txt` — the original v1 spec. Narrow,
  six-agent, SDK-first. Phases 1-2 were built to this doc exactly.
- `summary.txt` — a broader, 5-agent multi-model control-plane vision
  (this file used to be referred to here as `tokenlenssummary.txt`; that
  filename doesn't exist in the repo — `summary.txt` is the real one).

**Scope decision (2026-08-04): `summary.txt`'s vision is greenlit
starting Phase 3.** The "possible future platform play, not v1, don't
build toward this doc" framing that used to be here no longer holds —
the user explicitly asked for Phase 3 to follow `summary.txt` (a 5-agent
control plane: Planner/Spend/Replay/Policy/Insights, plus a customer-
facing multi-model chat/upload surface), confirmed across several
clarifying rounds. See `phase3.txt` for the full Phase 3 design —
scoped down from `summary.txt`'s complete vision to: backend-only (no
frontend yet), Gemini-only for v1 (Claude/Grok/open-source deferred to
a follow-up phase once the gateway abstraction is proven), and the
original roadmap's "Analyst Detectors" phase folded into the new Spend
Agent rather than staying separate. Read `phase3.txt`'s own "Scope
Decision" section before assuming any part of `summary.txt` is in scope
by default — only what's explicitly listed there is.

See `review.txt` for the full reasoning behind the original v1-scope
choice, current competitive landscape (Portkey, Kosmoy, Langfuse,
Braintrust, Datadog LLM Observability), and the technical differentiator
(in-graph interrupt/resume via LangGraph's checkpointer vs. gateway-level
block/allow) — still accurate for Phase 1-2's work and for Phase 3's
Policy Agent, which wraps that same Spend Guard mechanism unchanged.

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
- `phase.txt` — active build tracker, phase by phase (Phase 1, 2, and 3
  — including 3A/3B, both done — with Phase 3's deferred/DEFERRED-later
  items also recorded there).
- `phase3.txt` — SUPERSEDED. Was a pre-expanded draft of Phase 3 written
  ahead of the "expand only when reached" convention; fully folded into
  `phase.txt` on 2026-08-08 once Phase 3A+3B were both done. Left in
  place as a historical record only — read `phase.txt` for current Phase
  3 state.
- `phase4.txt` — **misleadingly named: holds the Phase 3C (Cloud
  Deployment) plan, NOT Phase 4.** Originally an external ChatGPT-authored
  recommendation note; replaced 2026-08-08 with this repo's own Phase 3C
  plan in the normal tracker format after that note's "deploy to Google
  Cloud Agent Platform" suggestion was checked against real docs and found
  not to fit this app's shape (see the file's own "SCOPE DECISION"
  section). ACTIVE — this is what the next session should read first
  (also linked from the Current status block above). Rename/fold into
  `phase.txt` as "PHASE 3C" once built and verified, same precedent as
  `phase3.txt`'s own fold-in.
- `review.txt` — the project-viability review and the original v1-scope
  decision (Phases 1-2).
- `TokenLens-Project-Overview.txt` — the original v1 product spec
  (source of truth for Phases 1-2).
- `summary.txt` — the broader multi-agent/multi-model vision, greenlit
  starting Phase 3 (see the scope decision above and `phase.txt`'s Phase
  3 section).
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
