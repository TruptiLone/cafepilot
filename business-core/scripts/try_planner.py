"""Run the planner once against the live Meta Model API and save the result.

Usage (from business-core/, with MODEL_API_KEY in business-core/.env):
    PYTHONPATH=. python scripts/try_planner.py
    PYTHONPATH=. python scripts/try_planner.py "your own request"

The plan is printed and saved to tests/fixtures/agent/hero_plan_live.json so later
tests can replay a real model response. Nothing is validated or executed.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from backend.db.session import SessionLocal
from backend.modules.agent.planner import (
    PlannerError,
    PlannerUnavailableError,
    build_planning_context,
    create_planner_client,
    plan_query,
)

HERO_QUERY = (
    "Farmers market this Sunday Nov 1, 8 AM-2 PM, roughly 40 extra whole-milk lattes and "
    "20 extra oat-milk lattes. Check milk and bean stock as of Thursday 6 PM, check Sunday "
    "staff coverage, and add a barista 7:30 AM-2:30 PM if we're short."
)
AS_OF = datetime.fromisoformat("2026-10-29T18:00:00-07:00")
OUTPUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "agent" / "hero_plan_live.json"


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else HERO_QUERY
    with SessionLocal() as session:
        context = build_planning_context(session, "CAFE001", AS_OF)
    try:
        client = create_planner_client()
    except PlannerUnavailableError as error:
        print(f"{error}. Add MODEL_API_KEY=... to business-core/.env and retry.")
        return 1
    print(f"Asking the planner...\n  {query}\n")
    try:
        result = plan_query(query, context, client)
    except PlannerError as error:
        print(f"Planner failed: {error}\n(raw response is in business-core/var/llm_calls.jsonl)")
        return 1

    plan = result.plan
    print(f"model={result.model}  latency={result.latency_ms} ms  usage={result.usage}\n")
    print(f"status: {plan.status}\ngoal:   {plan.goal}")
    for assumption in plan.assumptions:
        print(f"  assumes: {assumption}")
    if plan.question:
        print(f"question: {plan.question}")
    for node in plan.nodes:
        print(f"\n[{node.id}] {node.action}\n   why: {node.reason}")
        if node.only_if:
            print(f"   only if: {node.only_if}")
        print(f"   args: {json.dumps(node.args)}")

    if len(sys.argv) == 1:
        OUTPUT.write_text(
            json.dumps(
                {
                    "query": query,
                    "as_of": AS_OF.isoformat(),
                    "model": result.model,
                    "latency_ms": result.latency_ms,
                    "plan": plan.model_dump(mode="json"),
                },
                indent=2,
            )
            + "\n"
        )
        print(f"\nSaved to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
