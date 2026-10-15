# AI Purchasing Agent (work in progress)

Step 1 of the build: mock data, tools, deterministic validator. No LLM yet.

## Layout
- `data/mock_data.json`: SKUs, inventory, forecast, suppliers, budgets, open POs
- `agent/store.py`: in-memory state, loaded fresh for every run/test
- `agent/tools.py`: all tools the agent can call (read, calculate, write, escalate)
- `demo_manual.py`: Scenario 1 + 4 walked through by hand, no LLM
- `tests/test_tools.py`: unit tests for tools and validation rules

## Run (Python 3.10+, no installs needed for step 1)
    python -m unittest -v
    python demo_manual.py

## Assumptions
- Inventory position = on hand + submitted POs (drafts and pending approvals are not counted).
- 1 unit = 1 storage slot; storage capacity is allocated per SKU.
- Order target = daily demand x (lead time + 7 day review period) + safety stock.
- POs above 3000 USD need buyer approval; below that they are auto-submitted if valid.
- All money is in USD.
