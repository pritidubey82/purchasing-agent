# AI Purchasing Agent (work in progress)

An agent that reviews a purchasing recommendation, investigates the data, decides
(accept / modify / reject / investigate), acts by creating a PO, and validates the result.

## Layout
- `data/mock_data.json`: SKUs, inventory, forecast, suppliers, budgets, open POs
- `agent/store.py`: in-memory state, loaded fresh for every run/test
- `agent/tools.py`: tools the agent can call (read, calculate, write, escalate) + validator
- `agent/tool_specs.py`: tool descriptions the LLM sees
- `agent/agent.py`: system prompt + the tool-calling loop + guardrails
- `agent/llm.py`: real LLM client (any OpenAI-compatible API) and an offline scripted stand-in
- `evals/`: eval cases and harness
- `run_agent.py`: run the agent on one SKU
- `demo_manual.py`: the same flow walked through by hand, no LLM
- `tests/`: unit tests

## Run (Python 3.10+, no installs needed)
    python -m unittest -v                    # unit tests
    python demo_manual.py                    # manual walkthrough
    python run_agent.py SKU-1042             # agent run, offline scripted LLM
    python -m evals.run_eval                 # eval harness, offline

## Run with a real LLM
1. Copy `.env.example` to `.env` and fill in a key from any OpenAI-compatible provider.
2. `python run_agent.py SKU-1042 --llm real`
3. `python -m evals.run_eval --llm real`

## Assumptions
- Inventory position = on hand + submitted POs (drafts and pending approvals are not counted).
- 1 unit = 1 storage slot; storage capacity is allocated per SKU.
- Order target = daily demand x (lead time + 7 day review period) + safety stock.
- POs above 3000 USD need buyer approval; below that they are auto-submitted if valid.
- All money is in USD.
