"""Flow 1 — the "customer chat" surface (phase3.txt Phase 3A §4). A REAL,
small LangGraph graph (not another toy graph) backing POST /v1/chat,
instrumented with the EXISTING tokenlens_sdk (dogfooding — TokenLens
monitoring itself, the same SDK the whole product is built around).

Two nodes:
  ingest_attachment — no LLM call. Extracts text from an uploaded PDF
                       (pymupdf) or validates an uploaded image (pillow).
                       Zero gen_ai.* usage, still gets a span — same shape
                       as toy_graph.py's classify_intent node.
  answer             — calls Gemini via the new agents/gateway.py, using
                       any extracted text / image as additional context.

`gateway_meta` is stored as a plain JSON-safe dict, not the GatewayResponse
dataclass — only keys declared on this graph's own State TypedDict survive
LangGraph checkpointing (backend/CLAUDE.md's documented limitation), and a
dataclass instance isn't a plain-JSON-safe value to smuggle through it.
"""

import io
from typing import TypedDict

import fitz  # pymupdf
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from PIL import Image

from agents import gateway
from tokenlens_sdk.handler import find_handler

_MAX_EXTRACTED_TEXT_CHARS = 20_000


class ChatState(TypedDict):
    message: str
    file_bytes: bytes | None
    file_mime_type: str | None
    extracted_text: str | None
    image_bytes: bytes | None
    image_mime_type: str | None
    answer: str
    gateway_meta: dict


def ingest_attachment(state: ChatState) -> dict:
    file_bytes = state.get("file_bytes")
    mime_type = state.get("file_mime_type")
    if not file_bytes or not mime_type:
        return {}

    if mime_type == "application/pdf":
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        return {"extracted_text": text[:_MAX_EXTRACTED_TEXT_CHARS]}

    if mime_type.startswith("image/"):
        # Validation only (raises on a corrupt/unsupported image) — the raw
        # bytes are passed straight through to Gemini as an image part, no
        # OCR/preprocessing needed.
        Image.open(io.BytesIO(file_bytes)).verify()
        return {"image_bytes": file_bytes, "image_mime_type": mime_type}

    return {}


def answer(state: ChatState, config: RunnableConfig | None = None) -> dict:
    prompt_parts = [state["message"]]
    if state.get("extracted_text"):
        prompt_parts.append(f"\n\nAttached document text:\n{state['extracted_text']}")

    response = gateway.generate(
        prompt="\n".join(prompt_parts),
        image_bytes=state.get("image_bytes"),
        image_mime_type=state.get("image_mime_type"),
    )

    # agents/gateway.py calls google.genai directly (not a LangChain
    # Runnable), so it never fires on_llm_start/on_llm_end -- the callback
    # handler would otherwise record zero usage for a node that made a
    # real, billed call. Manually fold the usage in instead (see
    # tokenlens_sdk/handler.py's record_llm_call docstring).
    handler = find_handler(config)
    if handler is not None:
        handler.record_llm_call(
            request_model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

    return {
        "answer": response.text,
        "gateway_meta": {
            "provider": response.provider,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "latency_ms": response.latency_ms,
            "cost_usd": response.cost_usd,
        },
    }


def build_graph() -> StateGraph:
    graph = StateGraph(ChatState)
    graph.add_node("ingest_attachment", ingest_attachment)
    graph.add_node("answer", answer)
    graph.set_entry_point("ingest_attachment")
    graph.add_edge("ingest_attachment", "answer")
    graph.add_edge("answer", END)
    return graph
