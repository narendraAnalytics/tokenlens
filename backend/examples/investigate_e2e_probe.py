"""End-to-end test for POST /v1/investigate (phase3.txt Phase 3B §6):
sends the 4 canonical questions + 1 multi-label case through the real
running endpoint against Flow 1's real run_id, asserting correct routing
and grounded answers.

Needs a real running server: uv run uvicorn main:app --reload
Run: uv run python -m examples.investigate_e2e_probe
"""

import httpx

BASE_URL = "http://localhost:8000"
FLOW1_RUN_ID = "fa965b0035424e0e8e81f81651850d45"
FLOW1_TENANT_ID = "tokenlens-chat-demo"

CASES = [
    ("why was this expensive", ["spend"]),
    ("why did this fail", ["replay"]),
    ("why was this interrupted", ["policy"]),
    ("which model should I use next time", ["insights"]),
]


def main() -> None:
    with httpx.Client(timeout=60.0) as client:
        for question, expected_labels in CASES:
            resp = client.post(
                f"{BASE_URL}/v1/investigate",
                json={"message": question, "run_id": FLOW1_RUN_ID, "tenant_id": FLOW1_TENANT_ID},
            )
            resp.raise_for_status()
            body = resp.json()
            print(f"{question!r} -> routed_to={body['routed_to']}")
            print(f"  answer: {body['answer'][:200]}")
            assert body["routed_to"] == expected_labels, (
                f"expected {expected_labels}, got {body['routed_to']}"
            )
            assert body["answer"].strip(), "expected a non-empty answer"

        multi_question = "why did this fail and was it expensive"
        resp = client.post(
            f"{BASE_URL}/v1/investigate",
            json={"message": multi_question, "run_id": FLOW1_RUN_ID, "tenant_id": FLOW1_TENANT_ID},
        )
        resp.raise_for_status()
        body = resp.json()
        print(f"{multi_question!r} -> routed_to={body['routed_to']}")
        print(f"  answer: {body['answer']}")
        assert set(body["routed_to"]) >= {"replay", "spend"}, (
            f"expected replay+spend, got {body['routed_to']}"
        )

    print("investigate_e2e_probe: all assertions passed")


if __name__ == "__main__":
    main()
