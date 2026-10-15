import json
import unittest

from agent.agent import run_agent
from agent.llm import ScriptedLLM
from agent.store import load_store
from agent.tools import PurchasingTools


class SelfApprovingLLM:
    """Drafts a PO above the auto-approve limit and tries to approve it itself."""

    def chat(self, messages, tools=None):
        n = sum(1 for m in messages if m["role"] == "tool")
        mk = lambda name, **a: {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(a)}}]}
        if n == 0:
            return mk("create_po_draft", sku="SKU-2001", qty=800)   # value 3200 > 3000
        if n == 1:
            po_id = json.loads(messages[-1]["content"])["po_id"]
            return mk("submit_po", po_id=po_id, approved_by="agent")
        return {"role": "assistant", "content": json.dumps({"decision": "accept", "final_qty": 800, "po_id": None, "explanation": ""})}


class LoopingLLM:
    def chat(self, messages, tools=None):
        return {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{len(messages)}", "type": "function",
             "function": {"name": "get_inventory", "arguments": json.dumps({"sku": "SKU-1042"})}}]}


class AgentTests(unittest.TestCase):
    def test_scripted_agent_modifies_water_order(self):
        t = PurchasingTools(load_store())
        out = run_agent(t, "SKU-1042", 800, ScriptedLLM())
        self.assertEqual(out["final"]["decision"], "modify")
        self.assertEqual(out["final"]["final_qty"], 200)
        self.assertEqual(t.get_po(out["final"]["po_id"])["status"], "submitted")

    def test_agent_cannot_self_approve(self):
        t = PurchasingTools(load_store())
        run_agent(t, "SKU-2001", 800, SelfApprovingLLM())
        statuses = [p["status"] for p in t.store.pos.values() if p["po_id"].startswith("PO-2")]
        self.assertEqual(statuses, ["pending_approval"])

    def test_step_limit_escalates(self):
        t = PurchasingTools(load_store())
        out = run_agent(t, "SKU-1042", 800, LoopingLLM(), max_steps=3)
        self.assertEqual(out["final"]["decision"], "investigate")
        self.assertEqual(len(t.store.escalations), 1)


if __name__ == "__main__":
    unittest.main()
