"""Local test for the model gateway (phase3.txt Phase 3A §1): calls
agents.gateway.generate() a few times and prints the full GatewayResponse
so token counts / cost / latency can be eyeballed for correctness.

No Postgres/checkpointer needed -- the gateway itself has no DB dependency.

Run: uv run python -m examples.gateway_probe
"""

from agents import gateway
from tokenlens_sdk.pricing import estimate_cost_usd

PROMPTS = [
    "Reply with exactly one word: hello.",
    "What is 2 + 2? Answer with just the number.",
    "Write a two-sentence explanation of what a LangGraph checkpointer does.",
]


def main() -> None:
    for prompt in PROMPTS:
        response = gateway.generate(prompt=prompt)
        expected_cost = estimate_cost_usd(
            response.model, response.input_tokens, response.output_tokens
        )
        print("-" * 60)
        print(f"prompt: {prompt!r}")
        print(f"text: {response.text!r}")
        print(f"provider={response.provider} model={response.model}")
        print(
            f"input_tokens={response.input_tokens} output_tokens={response.output_tokens} "
            f"latency_ms={response.latency_ms:.1f} cost_usd={response.cost_usd}"
        )
        assert response.input_tokens > 0, "expected nonzero input tokens"
        assert response.output_tokens > 0, "expected nonzero output tokens"
        assert response.cost_usd == expected_cost, "cost_usd must match pricing table exactly"

    print("-" * 60)
    print("gateway_probe: all assertions passed")


if __name__ == "__main__":
    main()
