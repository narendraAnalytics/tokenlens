"""Local copy of the pricing table, for the SDK's in-process budget-gating
counter ONLY — explicitly not the official billing figure (that's
tokenlens_cost, computed at ingest from ingest/pricing.py, never in the
SDK). Decided in Phase 2 §0 (phase.txt): the sync gating path can't wait on
anything async, so it needs its own copy of the rate table rather than
importing across the ingest/SDK boundary. If the real rates in
ingest/pricing.py change, mirror the change here too.
"""

_PRICING_TABLE: list[tuple[str, float, float]] = [
    ("gemini-2.5-pro", 0.00125, 0.005),
    ("gemini-2.5-flash", 0.000075, 0.0003),
    ("gemini", 0.0001, 0.0004),  # generic Gemini fallback
    ("claude-opus", 0.015, 0.075),
    ("claude-sonnet", 0.003, 0.015),
    ("claude", 0.003, 0.015),  # generic Claude fallback
    ("grok-4", 0.003, 0.015),
    ("grok", 0.002, 0.01),  # generic Grok fallback
    ("gpt-5", 0.005, 0.015),
    ("gpt", 0.003, 0.01),  # generic GPT fallback
]


def estimate_cost_usd(
    model: str | None, input_tokens: int, output_tokens: int
) -> float:
    """Best-effort USD cost for one LLM call, for gating only. Returns 0.0
    for an unrecognized or missing model rather than raising — an
    unrecognized model shouldn't itself trip a false budget breach."""
    if not model:
        return 0.0
    lowered = model.lower()
    for hint, input_rate, output_rate in _PRICING_TABLE:
        if hint in lowered:
            return round(
                (input_tokens / 1000) * input_rate
                + (output_tokens / 1000) * output_rate,
                8,
            )
    return 0.0
