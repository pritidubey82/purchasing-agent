"""Tools the purchasing agent can call.

Design rules:
  * Every tool returns a plain dict (easy to log and to hand to an LLM).
  * Errors are returned as {"error": ...}, never raised, so the agent can see
    them and react.
  * Anything that needs math or rule checking is plain code, not the LLM.
  * The agent can only change the world through create_po_draft /
    cancel_po / submit_po / escalate_to_buyer. submit_po re-runs validation
    itself, so a wrong LLM decision cannot bypass the rules.
"""
import math

from .store import Store

# Only submitted POs count as stock that is on its way.
INCOMING_STATUSES = ("submitted",)


def _err(code, **detail):
    return {"error": code, **detail}


def _round_up(x, step):
    return int(math.ceil(x / step) * step)


class PurchasingTools:
    def __init__(self, store: Store):
        self.store = store
        self.trace = []  # every tool call is recorded here (observability)
        self._tools = {
            name: getattr(self, name)
            for name in (
                "get_inventory", "get_open_pos", "get_forecast",
                "get_supplier_terms", "find_alternate_suppliers",
                "get_budget", "get_storage", "compute_requirement",
                "create_po_draft", "validate_po", "submit_po",
                "cancel_po", "get_po", "escalate_to_buyer",
            )
        }

    # ------------------------------------------------------------ dispatcher
    def call(self, name, **kwargs):
        """Single entry point used by the agent loop. Logs every call."""
        fn = self._tools.get(name)
        if fn is None:
            result = _err("unknown_tool", tool=name)
        else:
            try:
                result = fn(**kwargs)
            except TypeError as e:
                result = _err("bad_arguments", detail=str(e))
        self.trace.append({"tool": name, "args": kwargs, "result": result})
        return result

    # --------------------------------------------------------------- helpers
    def _sku(self, sku):
        return self.store.data["skus"].get(sku)

    def _offers(self, sku):
        return self.store.data["suppliers"].get(sku, [])

    def _incoming(self, sku, exclude_po=None):
        return sum(
            po["qty"] for po in self.store.pos.values()
            if po["sku"] == sku and po["status"] in INCOMING_STATUSES
            and po["po_id"] != exclude_po
        )

    def _budget_left(self, sku):
        return self.store.data["budgets"][self._sku(sku)["category"]]

    def _free_storage(self, sku, exclude_po=None):
        rec = self._sku(sku)
        return rec["storage_capacity"] - rec["on_hand"] - self._incoming(sku, exclude_po)

    # ------------------------------------------------------------ read tools
    def get_inventory(self, sku):
        rec = self._sku(sku)
        if not rec:
            return _err("unknown_sku", sku=sku)
        return {"sku": sku, "name": rec["name"], "node": rec["node"],
                "on_hand": rec["on_hand"], "safety_stock": rec["safety_stock"]}

    def get_open_pos(self, sku):
        if not self._sku(sku):
            return _err("unknown_sku", sku=sku)
        pos = [
            {"po_id": p["po_id"], "supplier_id": p["supplier_id"], "qty": p["qty"],
             "eta_days": p["eta_days"], "unit_cost": p["unit_cost"]}
            for p in self.store.pos.values()
            if p["sku"] == sku and p["status"] in INCOMING_STATUSES
        ]
        return {"sku": sku, "open_pos": pos, "total_incoming": sum(p["qty"] for p in pos)}

    def get_forecast(self, sku):
        if not self._sku(sku):
            return _err("unknown_sku", sku=sku)
        f = self.store.data["forecast"].get(sku)
        if not f:
            return _err("no_forecast_available", sku=sku)
        return {"sku": sku, **f}

    def get_supplier_terms(self, sku):
        offers = self._offers(sku)
        if not offers:
            return _err("no_supplier", sku=sku)
        return {"sku": sku, **offers[0]}  # first offer = primary supplier

    def find_alternate_suppliers(self, sku):
        return {"sku": sku, "alternates": self._offers(sku)[1:]}

    def get_budget(self, sku):
        if not self._sku(sku):
            return _err("unknown_sku", sku=sku)
        return {"sku": sku, "category": self._sku(sku)["category"],
                "remaining": self._budget_left(sku)}

    def get_storage(self, sku):
        rec = self._sku(sku)
        if not rec:
            return _err("unknown_sku", sku=sku)
        incoming = self._incoming(sku)
        return {"sku": sku, "capacity": rec["storage_capacity"], "on_hand": rec["on_hand"],
                "incoming": incoming, "free": self._free_storage(sku)}

    # ----------------------------------------------------- deterministic math
    def compute_requirement(self, sku, daily_demand=None):
        """How many units are really needed? Plain formula, no LLM.

        target   = daily_demand * (lead_time + review_period) + safety_stock
        position = on_hand + incoming (submitted POs)
        need     = target - position, rounded up to pack size, at least MOQ.
        """
        rec = self._sku(sku)
        if not rec:
            return _err("unknown_sku", sku=sku)
        offers = self._offers(sku)
        if not offers:
            return _err("no_supplier", sku=sku)
        if daily_demand is None:
            f = self.store.data["forecast"].get(sku)
            if not f:
                return _err("no_forecast_available", sku=sku)
            daily_demand = f["daily_demand"]
        offer = offers[0]
        review = self.store.data["policy"]["review_period_days"]
        cover_days = offer["lead_time_days"] + review
        target = math.ceil(daily_demand * cover_days + rec["safety_stock"])
        position = rec["on_hand"] + self._incoming(sku)
        raw_need = max(0, target - position)
        order_qty = 0 if raw_need == 0 else _round_up(max(raw_need, offer["moq"]), offer["pack_size"])
        return {
            "sku": sku, "daily_demand_used": daily_demand, "cover_days": cover_days,
            "target_position": target, "inventory_position": position,
            "raw_need": raw_need, "order_qty": order_qty,
            "moq_applied": 0 < raw_need < offer["moq"],
        }

    # ------------------------------------------------------------ write tools
    def create_po_draft(self, sku, qty, supplier_id=None):
        """Creates a DRAFT only. Nothing is sent to the supplier yet."""
        if not self._sku(sku):
            return _err("unknown_sku", sku=sku)
        offers = self._offers(sku)
        offer = next((o for o in offers if supplier_id in (None, o["supplier_id"])), None)
        if offer is None:
            return _err("unknown_supplier", sku=sku, supplier_id=supplier_id)
        po = {"po_id": self.store.next_po_id(), "sku": sku, "supplier_id": offer["supplier_id"],
              "qty": qty, "unit_cost": offer["unit_cost"],
              "value": round(qty * offer["unit_cost"], 2),
              "eta_days": offer["lead_time_days"], "status": "draft", "approved_by": None}
        self.store.pos[po["po_id"]] = po
        return po

    def validate_po(self, po_id):
        """Independent rule check (the feedback loop). Returns which checks
        failed and the max/min quantity that WOULD pass, so the agent can re-plan."""
        po = self.store.pos.get(po_id)
        if not po:
            return _err("unknown_po", po_id=po_id)
        if po["status"] not in ("draft", "pending_approval"):
            return _err("not_validatable", po_id=po_id, status=po["status"])
        offer = next(o for o in self._offers(po["sku"]) if o["supplier_id"] == po["supplier_id"])
        qty, value = po["qty"], po["qty"] * po["unit_cost"]
        budget = self._budget_left(po["sku"])
        free = self._free_storage(po["sku"], exclude_po=po_id)
        checks = [
            {"name": "positive_quantity", "passed": qty > 0, "detail": f"qty {qty}"},
            {"name": "supplier_moq", "passed": qty >= offer["moq"],
             "detail": f"qty {qty} vs MOQ {offer['moq']}", "min_qty": offer["moq"]},
            {"name": "pack_size", "passed": qty % offer["pack_size"] == 0,
             "detail": f"qty {qty} vs pack size {offer['pack_size']}", "pack_size": offer["pack_size"]},
            {"name": "supplier_capacity", "passed": qty <= offer["max_available"],
             "detail": f"qty {qty} vs supplier max {offer['max_available']}",
             "max_qty": offer["max_available"]},
            {"name": "budget", "passed": value <= budget,
             "detail": f"value {value:.2f} vs budget left {budget:.2f}",
             "max_qty": int(budget // po["unit_cost"])},
            {"name": "storage", "passed": qty <= free,
             "detail": f"qty {qty} vs free storage {free}", "max_qty": free},
        ]
        violations = [c for c in checks if not c["passed"]]
        return {"po_id": po_id, "valid": not violations, "checks": checks, "violations": violations}

    def submit_po(self, po_id, approved_by=None):
        """Sends the PO. Re-validates first, so the LLM cannot skip the rules.
        Above the auto-approve limit it waits for a human (approved_by)."""
        po = self.store.pos.get(po_id)
        if not po:
            return _err("unknown_po", po_id=po_id)
        v = self.validate_po(po_id)
        if "error" in v:
            return v
        if not v["valid"]:
            return _err("validation_failed", po_id=po_id, violations=v["violations"])
        limit = self.store.data["policy"]["auto_approve_limit"]
        if po["value"] > limit and not approved_by:
            po["status"] = "pending_approval"
            return {"po_id": po_id, "status": "pending_approval",
                    "reason": f"value {po['value']:.2f} is above auto-approve limit {limit}"}
        po["status"] = "submitted"
        po["approved_by"] = approved_by or "auto"
        category = self._sku(po["sku"])["category"]
        self.store.data["budgets"][category] -= po["value"]
        return po

    def cancel_po(self, po_id):
        po = self.store.pos.get(po_id)
        if not po:
            return _err("unknown_po", po_id=po_id)
        if po["status"] not in ("draft", "pending_approval"):
            return _err("cannot_cancel", po_id=po_id, status=po["status"])
        po["status"] = "cancelled"
        return po

    def get_po(self, po_id):
        """Read-back: what does the system actually hold for this PO now?"""
        po = self.store.pos.get(po_id)
        return po if po else _err("unknown_po", po_id=po_id)

    def escalate_to_buyer(self, sku, reason, details=None):
        ticket = {"ticket_id": f"ESC-{len(self.store.escalations) + 1}", "sku": sku,
                  "reason": reason, "details": details or {}}
        self.store.escalations.append(ticket)
        return ticket
