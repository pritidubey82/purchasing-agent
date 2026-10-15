import unittest

from agent.store import load_store
from agent.tools import PurchasingTools


def failed(validation):
    return [c["name"] for c in validation["violations"]]


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.t = PurchasingTools(load_store())  # fresh world for every test

    def test_requirement_water(self):
        r = self.t.compute_requirement("SKU-1042")
        self.assertEqual(r["target_position"], 1000)
        self.assertEqual(r["inventory_position"], 700)
        self.assertEqual(r["raw_need"], 300)
        self.assertEqual(r["order_qty"], 300)

    def test_free_storage_water(self):
        self.assertEqual(self.t.get_storage("SKU-1042")["free"], 200)

    def test_no_need_when_stock_is_enough(self):
        self.assertEqual(self.t.compute_requirement("SKU-4004")["order_qty"], 0)

    def test_moq_applied_when_need_is_small(self):
        r = self.t.compute_requirement("SKU-2001", daily_demand=12)
        self.assertEqual(r["raw_need"], 95)
        self.assertEqual(r["order_qty"], 100)
        self.assertTrue(r["moq_applied"])

    def test_missing_forecast_is_an_error_not_a_guess(self):
        self.assertEqual(self.t.get_forecast("SKU-3003")["error"], "no_forecast_available")
        self.assertEqual(self.t.compute_requirement("SKU-3003")["error"], "no_forecast_available")

    def test_300_water_fails_only_on_storage(self):
        po = self.t.create_po_draft("SKU-1042", 300)
        v = self.t.validate_po(po["po_id"])
        self.assertFalse(v["valid"])
        self.assertEqual(failed(v), ["storage"])
        self.assertEqual(v["violations"][0]["max_qty"], 200)

    def test_200_water_passes(self):
        po = self.t.create_po_draft("SKU-1042", 200)
        self.assertTrue(self.t.validate_po(po["po_id"])["valid"])

    def test_moq_and_pack_violations(self):
        low = self.t.create_po_draft("SKU-1042", 150)
        self.assertIn("supplier_moq", failed(self.t.validate_po(low["po_id"])))
        odd = self.t.create_po_draft("SKU-1042", 225)
        self.assertIn("pack_size", failed(self.t.validate_po(odd["po_id"])))

    def test_budget_violation(self):
        po = self.t.create_po_draft("SKU-4004", 200)  # 200 x 25 = 5000 > 4000
        self.assertEqual(failed(self.t.validate_po(po["po_id"])), ["budget"])

    def test_supplier_capacity_violation(self):
        po = self.t.create_po_draft("SKU-1042", 400, supplier_id="SUP-02")
        self.assertIn("supplier_capacity", failed(self.t.validate_po(po["po_id"])))

    def test_submit_is_blocked_when_invalid(self):
        po = self.t.create_po_draft("SKU-1042", 300)
        res = self.t.submit_po(po["po_id"])
        self.assertEqual(res["error"], "validation_failed")
        self.assertEqual(self.t.get_po(po["po_id"])["status"], "draft")

    def test_auto_submit_under_limit_and_read_back(self):
        po = self.t.create_po_draft("SKU-1042", 200)
        res = self.t.submit_po(po["po_id"])
        self.assertEqual(res["status"], "submitted")
        self.assertEqual(res["approved_by"], "auto")
        self.assertEqual(self.t.get_budget("SKU-1042")["remaining"], 2600)
        self.assertEqual(self.t.get_po(po["po_id"])["qty"], 200)

    def test_needs_human_approval_over_limit(self):
        po = self.t.create_po_draft("SKU-2001", 800)  # 3200 > 3000
        self.assertEqual(self.t.submit_po(po["po_id"])["status"], "pending_approval")
        self.assertEqual(self.t.submit_po(po["po_id"], approved_by="buyer")["status"], "submitted")

    def test_cancel_draft(self):
        po = self.t.create_po_draft("SKU-1042", 300)
        self.assertEqual(self.t.cancel_po(po["po_id"])["status"], "cancelled")

    def test_dispatcher_logs_calls_and_handles_unknown_tool(self):
        self.t.call("get_inventory", sku="SKU-1042")
        self.assertEqual(self.t.call("nope")["error"], "unknown_tool")
        self.assertEqual(len(self.t.trace), 2)


if __name__ == "__main__":
    unittest.main()
