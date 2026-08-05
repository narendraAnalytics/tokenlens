"""Base agent (phase3.txt Phase 3A §2) — the one shared implementation of
"call the gateway, format tools, parse output" every Phase 3B specialist
agent reuses; each agent module is then just an AgentSpec (system prompt +
tool list), not a reimplementation of this loop.

Tool-calling is text-based/ReAct-style, not native per-provider function
calling (confirmed design decision, phase3.txt Phase 3A §0): the model is
asked to reply either `FINAL_ANSWER: ...` or `TOOL_CALL: {"name": ...,
"arguments": {...}}`, which is parsed, executed, and folded back into the
transcript. This keeps agents/gateway.py's generate() a pure text-in/
text-out call usable identically by a future Claude/Grok/Llama adapter —
each provider's native function-calling schema is a different shape
(Gemini's FunctionDeclaration vs. Anthropic's tool-use JSON vs. OpenAI's),
and baking any one of them into the shared interface would force a rewrite
when the next adapter is added. Revisit only if this text-parsing proves
fragile in practice with real 3B agents.
"""

import json
from dataclasses import replace

from agents import gateway
from agents.registry import AgentSpec, ToolSpec
from agents.tools.bigquery_reader import ToolError as BigQueryToolError
from agents.tools.sql_reader import ToolError as SqlToolError

_MAX_TOOL_ITERATIONS = 4

_TOOL_PROTOCOL_INSTRUCTIONS = """
You have access to the following tools. To call one, reply with EXACTLY
one line in this form (valid JSON on the TOOL_CALL line, nothing else):
TOOL_CALL: {{"name": "<tool_name>", "arguments": {{...}}}}

Once you have enough information to answer, reply instead with:
FINAL_ANSWER: <your answer>

Available tools:
{tool_descriptions}
""".strip()


def _describe_tools(tools: list[ToolSpec]) -> str:
    if not tools:
        return "(no tools available)"
    return "\n".join(
        f"- {tool.name}({tool.parameters}): {tool.description}" for tool in tools
    )


def _render_prompt(spec: AgentSpec, user_message: str, extra_context: str | None) -> str:
    parts = [_TOOL_PROTOCOL_INSTRUCTIONS.format(tool_descriptions=_describe_tools(spec.tools))]
    if extra_context:
        parts.append(f"Context:\n{extra_context}")
    parts.append(f"User question:\n{user_message}")
    return "\n\n".join(parts)


def _parse_tool_call(text: str) -> dict | None:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("TOOL_CALL:"):
            raw = line[len("TOOL_CALL:") :].strip()
            try:
                call = json.loads(raw)
            except json.JSONDecodeError:
                return None
            if isinstance(call, dict) and "name" in call:
                return call
    return None


def _execute_tool(tool: ToolSpec | None, arguments: dict) -> str:
    """Never raises — a tool failure (including an unknown tool name) is
    folded back into the transcript as an observation the model can react
    to on its next turn, rather than crashing the whole agent call."""
    if tool is None:
        return "ERROR: unknown tool"
    try:
        result = tool.fn(**arguments)
    except (BigQueryToolError, SqlToolError) as exc:
        return f"ERROR: {exc}"
    except Exception as exc:  # noqa: BLE001 -- any other tool bug shouldn't crash the agent
        return f"ERROR: tool {tool.name!r} raised: {exc}"
    return json.dumps(result, default=str)


def _strip_final_answer_prefix(response: gateway.GatewayResponse) -> gateway.GatewayResponse:
    """Strips a leading "FINAL_ANSWER:" line prefix from the model's reply
    so callers get a clean answer, not the tool-call-protocol scaffolding
    leaking through."""
    text = response.text.strip()
    if text.upper().startswith("FINAL_ANSWER:"):
        text = text[len("FINAL_ANSWER:") :].strip()
        return replace(response, text=text)
    return response


def run_agent(
    spec: AgentSpec, user_message: str, *, extra_context: str | None = None
) -> gateway.GatewayResponse:
    """Runs spec's tool-calling loop to completion (or until
    _MAX_TOOL_ITERATIONS is exhausted, in which case the last response is
    returned best-effort)."""
    transcript = _render_prompt(spec, user_message, extra_context)
    response: gateway.GatewayResponse | None = None

    for _ in range(_MAX_TOOL_ITERATIONS):
        response = gateway.generate(
            prompt=transcript, system_instruction=spec.system_prompt, model=spec.model
        )
        call = _parse_tool_call(response.text)
        if call is None:
            return _strip_final_answer_prefix(response)

        tool = next((t for t in spec.tools if t.name == call.get("name")), None)
        observation = _execute_tool(tool, call.get("arguments", {}) or {})
        transcript += f"\n\nTOOL_CALL: {json.dumps(call)}\nTOOL_RESULT: {observation}\n"

    return _strip_final_answer_prefix(response) if response is not None else response
