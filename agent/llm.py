"""LLM back-ends. Both expose chat(messages, tools) -> assistant message dict.

* OpenAICompatLLM: any OpenAI-compatible endpoint (Gemini, Groq, OpenRouter,
  Ollama...). Configured from .env. Uses only the standard library.
* ScriptedLLM: a rule-based stand-in used for offline tests and the eval
  harness when no API key is available. It is NOT an LLM; it follows the same
  message / tool-call protocol so the agent loop and harness are exercised
  exactly as with a real model.
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class LLMError(Exception):
    pass


def load_env(path=ROOT / ".env"):
    """Tiny .env reader (so no python-dotenv install is needed)."""
    if not Path(path).exists():
        return
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class OpenAICompatLLM:
    def __init__(self, base_url=None, api_key=None, model=None):
        load_env()
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL", "")
        if not (self.base_url and self.api_key and self.model) or self.api_key == "your-key-here":
            raise LLMError("Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in .env (see .env.example).")

    def chat(self, messages, tools=None):
        body = {"model": self.model, "messages": messages, "temperature": 0}
        if tools:
            body.update({"tools": tools, "tool_choice": "auto"})
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}",
                     "User-Agent": "purchasing-agent/1.0"})
        data = None
        for attempt in range(1, 4):  # free tiers rate-limit: back off and retry
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:500]
                if e.code in (429, 500, 503) and attempt < 3:
                    time.sleep(5 * attempt)
                    continue
                raise LLMError(f"HTTP {e.code}: {detail}")
            except urllib.error.URLError as e:
                raise LLMError(f"Network error: {e}")
        msg = data["choices"][0]["message"]
        out = {"role": "assistant", "content": msg.get("content") or ""}
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            calls.append({"id": tc.get("id") or f"call_{int(time.time()*1000)}_{i}", "type": "function",
                          "function": {"name": tc["function"]["name"],
                                       "arguments": tc["function"].get("arguments") or "{}"}})
        if calls:
            out["tool_calls"] = calls
        return out


def extract_calls(messages):
    """Rebuild [{name, args, result}] (in order) from the chat history."""
    pending, calls = {}, []
    for m in messages:
        if m["role"] == "assistant":
            for tc in m.get("tool_calls") or []:
                pending[tc["id"]] = (tc["function"]["name"], json.loads(tc["function"]["arguments"] or "{}"))
        elif m["role"] == "tool":
            name, args = pending[m["tool_call_id"]]
            calls.append({"name": name, "args": args, "result": json.loads(m["content"])})
    return calls


class ScriptedLLM:
    """Deterministic policy that behaves like a well-prompted agent."""

    def __init__(self):
        self._n = 0

    def _tc(self, name, **args):
        self._n += 1
        return {"id": f"call_{self._n}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}

    def _call(self, *tcs):
        return {"role": "assistant", "content": "", "tool_calls": list(tcs)}

    def _final(self, **fields):
        return {"role": "assistant", "content": json.dumps(fields)}

    def chat(self, messages, tools=None):
        task = json.loads(messages[1]["content"].splitlines()[-1])
        sku, rec = task["sku"], task["recommended_qty"]
        calls = extract_calls(messages)
        if not calls:
            return self._call(*[self._tc(n, sku=sku) for n in (
                "get_inventory", "get_open_pos", "get_forecast",
                "get_supplier_terms", "get_budget", "get_storage")])

        def last(name):
            hits = [c for c in calls if c["name"] == name]
            return hits[-1] if hits else None

        names = [c["name"] for c in calls]
        escalated = "escalate_to_buyer" in names

        def escalate(reason, **details):
            return self._call(self._tc("escalate_to_buyer", sku=sku, reason=reason, details=details))

        if "error" in last("get_forecast")["result"]:
            if not escalated:
                return escalate("No forecast available, so the recommendation cannot be verified.")
            return self._final(decision="investigate", final_qty=0, po_id=None,
                               explanation="No forecast data for this SKU. Escalated to the buyer instead of guessing.")
        if last("compute_requirement") is None:
            return self._call(self._tc("compute_requirement", sku=sku))
        req = last("compute_requirement")["result"]
        need = req["order_qty"]
        if need == 0:
            return self._final(decision="reject", final_qty=0, po_id=None, explanation=(
                f"No purchase needed: inventory position {req['inventory_position']} already covers "
                f"the target {req['target_position']}."))
        drafts = [c for c in calls if c["name"] == "create_po_draft"]
        if not drafts:
            return self._call(self._tc("create_po_draft", sku=sku, qty=need))

        lc = calls[-1]
        if lc["name"] == "create_po_draft":
            if "error" in lc["result"]:
                return escalate("Could not create a PO draft.", error=lc["result"]["error"])
            return self._call(self._tc("validate_po", po_id=lc["result"]["po_id"]))

        if lc["name"] == "validate_po":
            v = lc["result"]
            if v.get("valid"):
                return self._call(self._tc("submit_po", po_id=v["po_id"]))
            terms = last("get_supplier_terms")["result"]
            caps = [x["max_qty"] for x in v.get("violations", []) if "max_qty" in x]
            new_qty = (min(caps) // terms["pack_size"]) * terms["pack_size"] if caps else 0
            if len(drafts) >= 3 or new_qty < terms["moq"]:
                return self._call(
                    self._tc("cancel_po", po_id=v["po_id"]),
                    self._tc("escalate_to_buyer", sku=sku, reason="No valid PO possible within constraints.",
                             details={"violations": [x["name"] for x in v.get("violations", [])]}))
            return self._call(self._tc("cancel_po", po_id=v["po_id"]),
                              self._tc("create_po_draft", sku=sku, qty=new_qty))

        if lc["name"] == "submit_po":
            r = lc["result"]
            if "error" in r:
                return escalate("PO submission was blocked.", error=r["error"])
            if r.get("status") == "submitted":
                return self._call(self._tc("get_po", po_id=r["po_id"]))
            return self._finish(sku, rec, need, calls, pending=r)

        if lc["name"] == "get_po":
            gap = need - lc["result"]["qty"]
            if gap > 0 and not escalated:
                return escalate(f"{gap} units of the real need could not be ordered now. Follow-up PO needed.", gap=gap)
        return self._finish(sku, rec, need, calls)

    def _finish(self, sku, rec, need, calls, pending=None):
        req = [c for c in calls if c["name"] == "compute_requirement"][-1]["result"]
        po = pending or next((c["result"] for c in reversed(calls) if c["name"] == "get_po"), None)
        if po is None:
            return self._final(decision="investigate", final_qty=0, po_id=None,
                               explanation="Could not place a valid order within constraints. Escalated to the buyer.")
        failed = [x["name"] for c in calls if c["name"] == "validate_po"
                  for x in c["result"].get("violations", [])]
        qty = po.get("qty") or next(c["args"]["qty"] for c in reversed(calls) if c["name"] == "create_po_draft")
        text = (f"Real need is {need} (target {req['target_position']}, position {req['inventory_position']}); "
                f"system recommended {rec}.")
        if qty != need:
            text += f" Order cut to {qty} because the {', '.join(sorted(set(failed)))} check failed for {need}."
        if po.get("status") == "pending_approval":
            text += " PO is above the auto-approve limit and waits for buyer approval."
        return self._final(decision="accept" if qty == rec else "modify", final_qty=qty,
                           po_id=po["po_id"], explanation=text)
