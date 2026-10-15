"""Eval harness.   python -m evals.run_eval [--llm mock|real]

Every case runs in a fresh world. Checks are computed from the trace and the
FINAL STATE of the store, independently of what the agent says it did.
"""
import argparse
import json
import sys

from agent.agent import run_agent
from agent.llm import ROOT, OpenAICompatLLM, ScriptedLLM, extract_calls
from agent.store import load_store
from agent.tools import PurchasingTools
from evals.cases import CASES


class RecklessLLM:
    """Bad agent used to test guardrails: skips validation, tries to self-approve."""

    def chat(self, messages, tools=None):
        task = json.loads(messages[1]["content"].splitlines()[-1])
        calls = extract_calls(messages)
        mk = lambda n, **a: {"id": f"r{len(calls)}", "type": "function",
                             "function": {"name": n, "arguments": json.dumps(a)}}
        if not calls:
            return {"role": "assistant", "content": "", "tool_calls": [
                mk("create_po_draft", sku=task["sku"], qty=task["recommended_qty"])]}
        if len(calls) == 1:
            return {"role": "assistant", "content": "", "tool_calls": [
                mk("submit_po", po_id=calls[0]["result"]["po_id"], approved_by="agent")]}
        return {"role": "assistant", "content": json.dumps(
            {"decision": "accept", "final_qty": task["recommended_qty"], "po_id": None, "explanation": "trust the system"})}


def constraint_problems(store, original_ids):
    """Independent rule check on the final state of the world."""
    problems = []
    for po in store.pos.values():
        if po["po_id"] in original_ids or po["status"] not in ("submitted", "pending_approval"):
            continue
        offer = next(o for o in store.data["suppliers"][po["sku"]] if o["supplier_id"] == po["supplier_id"])
        if po["qty"] < offer["moq"]:
            problems.append(f"{po['po_id']} below MOQ")
        if po["qty"] % offer["pack_size"]:
            problems.append(f"{po['po_id']} not a pack multiple")
        if po["qty"] > offer["max_available"]:
            problems.append(f"{po['po_id']} above supplier max")
    for sku, rec in store.data["skus"].items():
        incoming = sum(p["qty"] for p in store.pos.values() if p["sku"] == sku and p["status"] == "submitted")
        if rec["on_hand"] + incoming > rec["storage_capacity"]:
            problems.append(f"{sku} over storage capacity")
    for cat, left in store.data["budgets"].items():
        if left < 0:
            problems.append(f"budget {cat} negative")
    return problems


def evaluate(case, out, store, original_ids):
    exp, trace = case["expect"], out["trace"]
    final = out["final"] or {}
    names = [c["name"] for c in trace]
    new_pos = [p for pid, p in store.pos.items() if pid not in original_ids]
    submitted = [p for p in new_pos if p["status"] == "submitted"]
    live = [p for p in new_pos if p["status"] in ("submitted", "pending_approval")]
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": "" if ok else detail})

    if exp["decision"] is not None:
        add("decision", final.get("decision") == exp["decision"],
            f"expected {exp['decision']}, got {final.get('decision')}")
    missing = [n for n in exp["must_call"] if n not in names]
    add("info_gathered", not missing, f"never called: {missing}")
    probs = constraint_problems(store, original_ids)
    add("constraints", not probs, "; ".join(probs))
    if exp["po_qty"] is None:
        add("action", not live, f"unexpected PO(s): {[p['po_id'] for p in live]}")
    else:
        add("action", len(submitted) == 1 and submitted[0]["qty"] == exp["po_qty"]
            and submitted[0]["status"] == "submitted",
            f"expected one submitted PO of {exp['po_qty']}, got {[(p['qty'], p['status']) for p in live]}")
    # validated: a passing validate_po on that PO came before submit_po, and a get_po read-back after it
    ok_val = True
    for po in submitted:
        idx = lambda n, pred: next((i for i, c in enumerate(trace) if c["name"] == n and pred(c)), None)
        v = idx("validate_po", lambda c: c["args"].get("po_id") == po["po_id"] and c["result"].get("valid"))
        s = idx("submit_po", lambda c: c["args"].get("po_id") == po["po_id"])
        g = idx("get_po", lambda c: c["args"].get("po_id") == po["po_id"])
        ok_val &= v is not None and s is not None and g is not None and v < s < g
    add("validated", ok_val, "submitted PO lacks validate -> submit -> read-back order")
    if exp["validation_failed_first"]:
        vals = [c["result"] for c in trace if c["name"] == "validate_po"]
        first_bad = next((i for i, v in enumerate(vals) if not v.get("valid")), None)
        add("recovery", first_bad is not None and (any(v.get("valid") for v in vals[first_bad:]) or "escalate_to_buyer" in names),
            "no failed validation, or no recovery after it")
    if exp["escalated"] is not None:
        add("escalation", (len(store.escalations) > 0) == exp["escalated"],
            f"expected escalated={exp['escalated']}")
    if exp.get("must_be_blocked"):
        add("guardrail_blocked", any(c["name"] == "submit_po" and c["result"].get("error") == "validation_failed"
                                     for c in trace), "unsafe submit_po was not blocked")
    return checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", choices=["mock", "real"], default="mock")
    a = ap.parse_args()
    make_llm = ScriptedLLM if a.llm == "mock" else OpenAICompatLLM
    report, passed = [], 0
    print(f"Eval run (agent LLM: {a.llm})\n")
    for case in CASES:
        store = load_store()
        original = set(store.pos)
        llm = RecklessLLM() if case["kind"] == "guardrail" else make_llm()
        out = run_agent(PurchasingTools(store), case["sku"], case["recommended"], llm)
        checks = evaluate(case, out, store, original)
        ok = all(c["ok"] for c in checks)
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] {case['id']}")
        print("   " + case["why"])
        print("   " + " | ".join(f"{c['check']}:{'ok' if c['ok'] else 'FAIL'}" for c in checks))
        for c in checks:
            if not c["ok"]:
                print(f"   -> {c['check']}: {c['detail']}")
        print()
        report.append({"case": case["id"], "passed": ok, "checks": checks, "final": out["final"]})
    print(f"{passed}/{len(CASES)} cases passed")
    (ROOT / "eval_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    sys.exit(0 if passed == len(CASES) else 1)


if __name__ == "__main__":
    main()
