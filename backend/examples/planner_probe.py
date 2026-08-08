"""Local test for the Planner Agent (phase3.txt Phase 3B §1): the 4
canonical questions route to the expected single specialist, plus one
multi-label case merges correctly.

Run: uv run python -m examples.planner_probe
"""

from agents import planner

CANONICAL_CASES = [
    ("why was this expensive", ["spend"]),
    ("why did this fail", ["replay"]),
    ("why was this interrupted", ["policy"]),
    ("which model should I use next time", ["insights"]),
]


def main() -> None:
    for question, expected in CANONICAL_CASES:
        labels = planner.classify_intent(question)
        print(f"{question!r} -> {labels}")
        assert labels == expected, f"expected {expected}, got {labels} for {question!r}"

    multi_question = "why did this fail and was it expensive"
    multi_labels = planner.classify_intent(multi_question)
    print(f"{multi_question!r} -> {multi_labels}")
    assert set(multi_labels) >= {"replay", "spend"}, (
        f"expected replay+spend in multi-label case, got {multi_labels}"
    )

    merged = planner.merge_responses(
        multi_question,
        {
            "replay": "The 'explode' node raised a RuntimeError and the run failed there.",
            "spend": "This run cost $0.0000132, driven entirely by the 'answer' node.",
        },
    )
    print(f"merged answer: {merged.text}")
    assert merged.text.strip(), "expected a non-empty merged answer"

    print("planner_probe: all assertions passed")


if __name__ == "__main__":
    main()
