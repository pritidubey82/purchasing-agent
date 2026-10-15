"""Tool descriptions in the OpenAI 'function calling' format.

This is what the LLM sees. Note what is NOT exposed: submit_po has no
`approved_by` argument, so the agent cannot approve purchases itself.
"""


def _spec(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


_S = {"type": "string"}
_I = {"type": "integer"}
_N = {"type": "number"}
_SKU = {"sku": _S}
_PO = {"po_id": _S}

TOOL_SPECS = [
    _spec("get_inventory", "On-hand stock and safety stock for a SKU.", _SKU, ["sku"]),
    _spec("get_open_pos", "Submitted purchase orders still on their way for a SKU.", _SKU, ["sku"]),
    _spec("get_forecast", "Daily demand forecast and recent 7-day sales. Returns an error if no forecast exists.", _SKU, ["sku"]),
    _spec("get_supplier_terms", "Primary supplier terms: unit cost, lead time, MOQ, pack size, max available.", _SKU, ["sku"]),
    _spec("find_alternate_suppliers", "Other suppliers that can supply the SKU.", _SKU, ["sku"]),
    _spec("get_budget", "Remaining purchasing budget for the SKU's category.", _SKU, ["sku"]),
    _spec("get_storage", "Storage capacity, on hand, incoming and free space for the SKU.", _SKU, ["sku"]),
    _spec("compute_requirement", "Deterministic calculation of how many units are really needed. Always use this instead of doing the math yourself.",
          {"sku": _S, "daily_demand": _N}, ["sku"]),
    _spec("create_po_draft", "Create a DRAFT purchase order. Does not send it to the supplier.",
          {"sku": _S, "qty": _I, "supplier_id": _S}, ["sku", "qty"]),
    _spec("validate_po", "Check a draft PO against all rules. Returns violations with max_qty / min_qty hints.", _PO, ["po_id"]),
    _spec("submit_po", "Submit a PO. Only call after validate_po returned valid=true.", _PO, ["po_id"]),
    _spec("cancel_po", "Cancel a draft PO.", _PO, ["po_id"]),
    _spec("get_po", "Read back a PO to verify what the system actually holds.", _PO, ["po_id"]),
    _spec("escalate_to_buyer", "Hand the decision to the human buyer, with a reason.",
          {"sku": _S, "reason": _S, "details": {"type": "object"}}, ["sku", "reason"]),
]
