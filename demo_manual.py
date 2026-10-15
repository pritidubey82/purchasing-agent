"""Step 1 demo: Scenario 1 + 4 walked through by hand, NO LLM.

This is exactly the investigate -> decide -> act -> validate loop that the
LLM will drive in step 2. Run:  python demo_manual.py
"""
import json

from agent.store import load_store
from agent.tools import PurchasingTools

store = load_store()
t = PurchasingTools(store)
SKU = "SKU-1042"


def show(name, **args):
    result = t.call(name, **args)
    print(f"-> {name}({', '.join(f'{k}={v}' for k, v in args.items())})")
    print("   " + json.dumps(result, separators=(",", ":"))[:300])
    return result


recommended = store.data["system_recommendations"][SKU]
print(f"System recommends buying {recommended} units of {SKU}\n")

print("1) INVESTIGATE")
show("get_inventory", sku=SKU)
show("get_open_pos", sku=SKU)
show("get_forecast", sku=SKU)
show("get_supplier_terms", sku=SKU)
show("get_budget", sku=SKU)
show("get_storage", sku=SKU)
req = show("compute_requirement", sku=SKU)

need = req["order_qty"]
print(f"\n2) DECIDE: recommended {recommended}, real need {need} -> MODIFY\n")

print("3) ACT + 4) VALIDATE (retry loop)")
qty = need
for attempt in range(1, store.data["policy"]["max_retries"] + 1):
    print(f"\n-- attempt {attempt}: draft for {qty} units")
    po = show("create_po_draft", sku=SKU, qty=qty)
    v = show("validate_po", po_id=po["po_id"])
    if v["valid"]:
        break
    show("cancel_po", po_id=po["po_id"])
    caps = [c["max_qty"] for c in v["violations"] if "max_qty" in c]
    terms = t.get_supplier_terms(SKU)
    new_qty = (min(caps) // terms["pack_size"]) * terms["pack_size"] if caps else 0
    if new_qty < terms["moq"]:
        show("escalate_to_buyer", sku=SKU, reason="cannot satisfy constraints", details={"violations": v["violations"]})
        raise SystemExit("Escalated to buyer.")
    qty = new_qty
else:
    raise SystemExit("Out of retries")

print("\n5) SUBMIT + READ-BACK")
show("submit_po", po_id=po["po_id"])
back = show("get_po", po_id=po["po_id"])
print("\nOutcome matches expectation:", back["qty"] == qty and back["status"] == "submitted")
gap = need - qty
if gap > 0:
    show("escalate_to_buyer", sku=SKU, reason=f"{gap} units still needed; follow-up PO after storage frees up")
