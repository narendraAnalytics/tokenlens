"""Toy 3-node LangGraph app — exists purely to exercise the TokenLens SDK
locally (Phase 1 §4). Not a production graph.

Nodes:
  classify_intent  — plain function, no model/tool call. Tests that a
                      "boring" node still gets a span with zero gen_ai.*
                      usage rather than crashing on missing data.
  lookup_account    — calls a real LangChain @tool. Tests gen_ai.tool.name
                      capture via on_tool_start.
  respond           — calls a fake chat model with preset usage_metadata.
                      Tests gen_ai.usage.*/model/system extraction via
                      on_llm_end, without needing live cloud credentials.

State also carries `customer_email`, deliberately unmarked as a sensitive
field, to prove the regex-fallback layer of redaction (backend/CLAUDE.md
PII policy) catches it even though the customer didn't flag it — while
`account_number` IS marked sensitive, to prove the field-level layer works.

Run: uv run python -m examples.toy_graph
"""

from typing import TypedDict

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph

from tokenlens_sdk import TokenLens


class State(TypedDict):
    message: str
    customer_email: str
    account_number: str
    intent: str
    account_balance: float
    response: str


@tool
def get_account_balance(account_number: str) -> float:
    """Look up a customer's account balance by account number."""
    return 4210.50


def classify_intent(state: State) -> dict:
    return {"intent": "balance_inquiry"}


def lookup_account(state: State) -> dict:
    balance = get_account_balance.invoke({"account_number": state["account_number"]})
    return {"account_balance": balance}


def respond(state: State) -> dict:
    model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content=f"Your balance is ${state['account_balance']:.2f}.",
                    usage_metadata={
                        "input_tokens": 58,
                        "output_tokens": 14,
                        "total_tokens": 72,
                    },
                    response_metadata={"model_name": "gemini-2.5-flash"},
                )
            ]
        )
    )
    result = model.invoke(state["message"])
    return {"response": result.content}


def build_graph():
    graph = StateGraph(State)
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("lookup_account", lookup_account)
    graph.add_node("respond", respond)
    graph.set_entry_point("classify_intent")
    graph.add_edge("classify_intent", "lookup_account")
    graph.add_edge("lookup_account", "respond")
    graph.add_edge("respond", END)
    return graph.compile()


def main() -> None:
    app = build_graph()

    # --- TokenLens integration: three lines ---
    tl = TokenLens(
        graph_name="toy_support_agent",
        tenant_id="demo-tenant",
        sensitive_keys={"account_number"},
        ingest_url="http://localhost:8000/v1/traces",
    )
    app = tl.instrument(app)
    # --- end TokenLens integration ---

    result = app.invoke(
        {
            "message": "What's my balance?",
            "customer_email": "jane.doe@example.com",
            "account_number": "4111111111111111",
        }
    )
    print("\nFINAL STATE:", result)


if __name__ == "__main__":
    main()
