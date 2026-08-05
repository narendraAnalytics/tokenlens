"""Model gateway (Phase 3A §1) — one call shape every agent (Phase 3B) and
the Flow 1 chat endpoint (Phase 3A §4) uses, regardless of which provider
backs it. Gemini-only implementation this phase (phase3.txt Scope Decision
§3); the interface itself must not leak anything Gemini-specific (e.g. no
`thinking_config` parameter) so Claude/Grok/Llama can slot in later as new
adapters behind the same `generate()` signature and `GatewayResponse`
shape — see backend/CLAUDE.md's "Agent Platform Foundation" section.

Reuses agents/runtime_context.py's already-proven call pattern wholesale:
cached client singleton, Model Garden "global" endpoint, thinking disabled
via ThinkingConfig(thinking_budget=0) for latency. Unlike that module, this
one is called from a request/response path that CAN return an error to its
caller (a chat endpoint, an agent's own turn) — so failures raise
GatewayError rather than falling back to a deterministic summary.
"""

import concurrent.futures
import functools
import time
from dataclasses import dataclass

from google import genai
from google.genai import types

from config import settings
from tokenlens_sdk.pricing import estimate_cost_usd

DEFAULT_MODEL = "gemini-2.5-flash"  # matches tokenlens_sdk/pricing.py and
                                     # ingest/pricing.py's existing string

# Same reasoning as runtime_context.py: no data-residency requirement for
# these calls (chat answers, agent reasoning over already-collected
# telemetry metadata), so the global endpoint's better availability/
# capacity-aware routing wins over pinning to settings.gcp_region.
_MODEL_GARDEN_LOCATION = "global"

_DEFAULT_TIMEOUT_S = 30.0

_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="gateway"
)


@functools.lru_cache(maxsize=1)
def _get_client() -> genai.Client:
    """One client for the process lifetime — same lru_cache(maxsize=1)
    singleton idiom used by runtime_context._get_client(), db.get_engine(),
    and approval_card's WebClient. ADC auth, no API key."""
    return genai.Client(
        vertexai=True,
        project=settings.gcp_project_id,
        location=_MODEL_GARDEN_LOCATION,
    )


@dataclass(frozen=True)
class GatewayResponse:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    provider: str  # matches tokenlens_sdk/handler.py's _SYSTEM_HINTS vocabulary
    model: str
    cost_usd: float  # via tokenlens_sdk.pricing.estimate_cost_usd — no new pricing table


class GatewayError(Exception):
    """Raised on any generation failure or timeout. Unlike
    runtime_context.summarize_halted_run()'s never-raise pattern (which
    exists only because it must not block the budget gate), callers of
    this gateway (agents, /v1/chat) CAN legitimately surface an error to
    whoever asked the question — there's no sensible non-LLM fallback for
    "answer this customer's question," so this raises instead of
    returning a deterministic stand-in."""


def generate(
    *,
    prompt: str,
    system_instruction: str | None = None,
    model: str = DEFAULT_MODEL,
    image_bytes: bytes | None = None,
    image_mime_type: str | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> GatewayResponse:
    """Provider-agnostic text-in/text-out generation call. `image_bytes`/
    `image_mime_type` are kept generic (every major provider's chat API
    accepts a raw image attachment in some form) — only provider-specific
    *tuning* knobs (like Gemini's thinking_config) are disallowed here,
    kept as internal constants in this adapter instead.
    """
    client = _get_client()

    parts: list[types.Part] = [types.Part.from_text(text=prompt)]
    if image_bytes is not None:
        if not image_mime_type:
            raise ValueError("image_mime_type is required when image_bytes is provided")
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type))

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )

    def _call():
        return client.models.generate_content(
            model=model,
            contents=[types.Content(role="user", parts=parts)],
            config=config,
        )

    start = time.perf_counter()
    try:
        future = _executor.submit(_call)
        response = future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError as exc:
        raise GatewayError(f"gateway call to {model} timed out after {timeout_s}s") from exc
    except Exception as exc:  # noqa: BLE001 -- normalize every failure to GatewayError
        raise GatewayError(f"gateway call to {model} failed: {exc}") from exc
    latency_ms = (time.perf_counter() - start) * 1000

    text = (response.text or "").strip()
    usage = response.usage_metadata
    input_tokens = int(usage.prompt_token_count or 0) if usage else 0
    output_tokens = int(usage.candidates_token_count or 0) if usage else 0
    cost_usd = estimate_cost_usd(model, input_tokens, output_tokens)

    return GatewayResponse(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        provider="google",
        model=model,
        cost_usd=cost_usd,
    )
