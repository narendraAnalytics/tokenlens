"""Runtime Context Agent (Overview doc's Agent 1, phase.txt Phase 2 §3).

Explains an interrupted run to the human who has seconds to decide:
Approve / Kill / Approve-and-raise-cap. Called synchronously from
tokenlens_sdk/client.py's `_budget_gate`, right before `interrupt()` fires
on a budget breach — never on the hot per-node path.

Runs on Gemini Flash via the Model Garden global endpoint (see
`_MODEL_GARDEN_LOCATION` below) since latency and cost both matter when a
human is already waiting.
"""

import concurrent.futures
import functools

from google import genai
from google.genai import types
from loguru import logger

from config import settings

MODEL_NAME = "gemini-2.5-flash"  # matches tokenlens_sdk/pricing.py and
                                   # ingest/pricing.py's existing string —
                                   # keep these three in sync if it changes

# Deliberately NOT settings.gcp_region ("asia-south1"): Google's own Model
# Garden docs recommend the global endpoint for Gemini calls that don't
# have a data-residency requirement — better availability, capacity-aware
# routing, fewer 429s under load. This call sends span metadata/spend
# numbers, never raw customer documents, so residency isn't a concern here.
# Verified empirically (not just per docs) that "global" actually serves
# gemini-2.5-flash for this project before this constant was set.
_MODEL_GARDEN_LOCATION = "global"

# Gemini 2.5 Flash's default "thinking" mode adds ~10s+ for this prompt —
# measured directly: ~16-20s with thinking on vs. ~5-6s with it off, same
# output quality for a short structured summary. Not worth the latency for
# a call where a human is already waiting on the result.
_GENERATE_CONFIG = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_budget=0)
)

# 8s: comfortably above the ~5-6s measured cold-call latency with thinking
# disabled, small slice of the Overview doc's 60s breach-to-decision budget.
_DEFAULT_TIMEOUT_S = 8.0

_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="runtime-context-agent"
)


@functools.lru_cache(maxsize=1)
def _get_client() -> genai.Client:
    """One client for the process lifetime — construction does auth/
    endpoint setup, not something to redo per interrupt. Auth is ADC, same
    as every other GCP call in this codebase (root CLAUDE.md's auth
    pattern) — no API key involved."""
    return genai.Client(
        vertexai=True,
        project=settings.gcp_project_id,
        location=_MODEL_GARDEN_LOCATION,
    )


def estimate_remaining_steps(topology: dict, from_node: str) -> int:
    """Longest-path node count reachable forward from from_node to the
    graph's terminal nodes (worst-case, more useful to a human deciding
    "how much more could this cost" than a best-case estimate). Heuristic,
    not a general solver — fine for a toy-graph-scale DAG; doesn't attempt
    conditional-edge branch prediction or subgraphs."""
    edges = topology.get("edges", [])
    nodes = topology.get("nodes", [])
    adjacency: dict[str, list[str]] = {n: [] for n in nodes}
    for source, target in edges:
        adjacency.setdefault(source, []).append(target)

    if from_node not in adjacency:
        return 0

    longest = 0
    stack: list[tuple[str, int, frozenset[str]]] = [(from_node, 0, frozenset())]
    while stack:
        node, depth, visited = stack.pop()
        if node in visited:
            continue  # cycle guard — shouldn't happen in a DAG, but don't hang
        longest = max(longest, depth)
        next_visited = visited | {node}
        for neighbor in adjacency.get(node, []):
            stack.append((neighbor, depth + 1, next_visited))
    return longest


def _build_prompt(
    *,
    recent_spans: list[dict],
    graph_topology: dict,
    current_spend_usd: float,
    cap_usd: float,
    remaining_step_estimate: int,
    halted_node: str,
) -> str:
    return (
        "A LangGraph run has been halted for budget approval. Summarize in "
        "EXACTLY 3 lines, one sentence each, plain text, no markdown:\n"
        "Line 1: what the run was doing (based on recent node activity below).\n"
        "Line 2: current spend vs. cap, and how much more it will likely cost to finish.\n"
        "Line 3: a one-line recommendation (approve / kill / raise cap) with why.\n\n"
        f"Halted at node: {halted_node}\n"
        f"Spend so far: ${current_spend_usd:.4f} of ${cap_usd:.4f} cap\n"
        f"Estimated remaining steps: {remaining_step_estimate}\n"
        f"Graph topology: {graph_topology}\n"
        f"Last {len(recent_spans)} node executions: {recent_spans}\n"
    )


def _fallback_summary(
    *,
    recent_spans: list[dict],
    current_spend_usd: float,
    cap_usd: float,
    remaining_step_estimate: int,
    halted_node: str,
) -> str:
    """Deterministic, non-LLM summary used when the Gemini call is slow or
    fails — the interrupt must fire either way, so this function is total
    and never raises."""
    return (
        f"Run halted at node '{halted_node}' after {len(recent_spans)} steps.\n"
        f"Spend ${current_spend_usd:.4f} has reached/exceeded the ${cap_usd:.4f} cap.\n"
        f"~{remaining_step_estimate} steps remain -- approve, kill, or raise cap to continue."
    )


def summarize_halted_run(
    *,
    recent_spans: list[dict],
    graph_topology: dict,
    current_spend_usd: float,
    cap_usd: float,
    remaining_step_estimate: int,
    halted_node: str,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> str:
    """Returns a 3-line plain-text summary of a halted run, or a
    deterministic fallback if the Gemini call is slow or fails. Never
    raises — a broken LLM call must never block or fail the actual budget
    gate that calls this right before `interrupt()`."""
    prompt = _build_prompt(
        recent_spans=recent_spans,
        graph_topology=graph_topology,
        current_spend_usd=current_spend_usd,
        cap_usd=cap_usd,
        remaining_step_estimate=remaining_step_estimate,
        halted_node=halted_node,
    )

    def _call() -> str:
        response = _get_client().models.generate_content(
            model=MODEL_NAME, contents=prompt, config=_GENERATE_CONFIG
        )
        return (response.text or "").strip()

    try:
        future = _executor.submit(_call)
        summary = future.result(timeout=timeout_s)
        if summary:
            return summary
        logger.warning("Runtime Context Agent returned an empty summary; using fallback")
    except Exception as exc:  # noqa: BLE001 -- must never propagate to the budget gate
        logger.warning(f"Runtime Context Agent call failed, using fallback: {exc}")

    return _fallback_summary(
        recent_spans=recent_spans,
        current_spend_usd=current_spend_usd,
        cap_usd=cap_usd,
        remaining_step_estimate=remaining_step_estimate,
        halted_node=halted_node,
    )
