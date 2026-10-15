"""The agent loop: the LLM proposes tool calls, we execute them, repeat.

Guardrails that live OUTSIDE the LLM:
  * the agent cannot self-approve (approved_by is stripped from submit_po),
  * submit_po re-validates every rule itself (see tools.py),
  * a hard step limit; on overrun the case is escalated to the buyer.
"""
import json

from .tool_specs import TOOL_SPECS

SYSTEM_PROMPT = """You are an AI purchasing agent assisting a buyer. A purchasing system gives you a recommended order quantity for one SKU. The recommendation may be wrong: never assume it is correct.

Process:
1. INVESTIGATE: gather inventory, open POs, forecast, supplier terms, budget and storage with the tools. If key data (such as the forecast) is missing, do not guess: call escalate_to_buyer and stop.
2. CALCULATE: call compute_requirement for the real need. Never do the math yourself.
3. DECIDE: accept the recommendation only if it equals the real need; modify it if it differs but a purchase is still needed; reject it if no purchase is needed.
4. ACT: create_po_draft, then ALWAYS validate_po before submit_po. Never submit a PO that has not passed validate_po.
5. If validate_po fails, re-plan using the max_qty / min_qty hints: cancel the failed draft and create a new one. After 3 failed attempts, or if no valid quantity exists, call escalate_to_buyer.
6. VERIFY: after submit_po, call get_po and confirm quantity and status match your intention. If part of the real need could not be ordered, call escalate_to_buyer with the remaining gap.
You cannot approve purchases yourself. POs above the auto-approve limit wait for a human buyer.
When finished, reply with ONLY a JSON object: {"decision": "accept|modify|reject|investigate", "final_qty": <int>, "po_id": <string or null>, "explanation": "<the key factors>"}"""


def parse_final(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return {"decision": "unparseable", "raw": text}


def run_agent(tools, sku, recommended_qty, llm, max_steps=20):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Review this purchasing recommendation and take the appropriate action.\n"
                                    + json.dumps({"sku": sku, "recommended_qty": recommended_qty})},
    ]
    final = None
    for _ in range(max_steps):
        msg = llm.chat(messages, TOOL_SPECS)
        messages.append(msg)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            final = parse_final(msg.get("content"))
            break
        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except ValueError:
                args = {}
            if name == "submit_po":
                args.pop("approved_by", None)  # only humans approve
            result = tools.call(name, **args)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result)})
    if final is None:
        tools.call("escalate_to_buyer", sku=sku, reason="Agent exceeded the step limit without finishing.")
        final = {"decision": "investigate", "final_qty": 0, "po_id": None,
                 "explanation": "Agent did not finish within the step limit. Escalated to the buyer."}
    return {"sku": sku, "recommended_qty": recommended_qty, "final": final,
            "trace": tools.trace, "messages": messages}
