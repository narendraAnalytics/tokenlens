"""Cost computation — runs in the worker (Pub/Sub -> BigQuery), never in
the SDK, per the Overview doc and backend/CLAUDE.md's async/sync path
notes.

PLACEHOLDER RATES: the numbers below are illustrative, not verified
published pricing. Do not treat tokenlens_cost as accurate for real billing
until these are replaced with real per-model rates (and ideally loaded from
a config/table that's easy to update without a code deploy, e.g. a small
Cloud SQL table once the control store exists) — flagged here so it isn't
mistaken for production-grade pricing later.
"""

# model name substring (lowercase) -> (usd_per_1k_input, usd_per_1k_output)
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


def compute_cost(
    model: str | None, input_tokens: int, output_tokens: int
) -> float:
    """Best-effort USD cost for one span. Returns 0.0 for an unrecognized
    or missing model rather than raising — a span with no cost data is
    still worth storing for latency/reliability analysis."""
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
