"""Run the agent on one SKU.   python run_agent.py SKU-1042 [--llm mock|real] [--qty N]"""
import argparse
import json

from agent.agent import run_agent
from agent.llm import OpenAICompatLLM, ScriptedLLM
from agent.store import load_store
from agent.tools import PurchasingTools

p = argparse.ArgumentParser()
p.add_argument("sku")
p.add_argument("--llm", choices=["mock", "real"], default="mock")
p.add_argument("--qty", type=int, help="recommended qty (default: from mock data)")
a = p.parse_args()

store = load_store()
rec = a.qty or store.data["system_recommendations"][a.sku]
llm = ScriptedLLM() if a.llm == "mock" else OpenAICompatLLM()
print(f"Recommendation: buy {rec} units of {a.sku}   (LLM: {a.llm})\n")
out = run_agent(PurchasingTools(store), a.sku, rec, llm)
for i, c in enumerate(out["trace"], 1):
    args = ", ".join(f"{k}={v}" for k, v in c["args"].items())
    print(f"{i:>2}. {c['name']}({args})")
    print("    " + json.dumps(c["result"], separators=(",", ":"))[:160])
print("\nFINAL:", json.dumps(out["final"], indent=2))
