"""In-memory 'database' for the mock purchasing world.

Loads data/mock_data.json and keeps all mutable state (POs, budgets,
escalations). Every test / eval case builds a fresh Store, so runs never
leak state into each other.
"""
import copy
import json
from pathlib import Path

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data" / "mock_data.json"


class Store:
    def __init__(self, data):
        self.data = copy.deepcopy(data)
        self.pos = {po["po_id"]: po for po in self.data["open_pos"]}
        self.escalations = []
        self._po_seq = 2000

    def next_po_id(self):
        self._po_seq += 1
        return f"PO-{self._po_seq}"


def load_store(path=DEFAULT_DATA):
    with open(path, encoding="utf-8") as f:
        return Store(json.load(f))
