# CaféPilot documentation

Start here. Each page stands on its own; read them in this order for the full story.

| Page | Read it for |
|---|---|
| [architecture.md](architecture.md) | The high-level diagram, the layers, a request end to end (event plan and delivery check), data and state, repo map |
| [agentic-planning.md](agentic-planning.md) | **Why a plan graph (DAG)**, why the model never calls our API or tools itself, the registry, validator, executor, approvals, replanning |
| [forecasting-and-math.md](forecasting-and-math.md) | Where the data comes from and every calculation: demand estimate, scenarios, shortfall, pack rounding, delivery counting |
| [mobile-app.md](mobile-app.md) | The Barista iPhone app for Meta glasses: purpose, how it works, challenges, what the platform didn't support |
| [limitations-and-decisions.md](limitations-and-decisions.md) | Planned vs built, key decisions, what is simulated or scripted, known limits, next steps |

More:

- Planner benchmark (Muse Wonder vs Muse Spark): [`business-core/docs/agentic/planner-benchmark-results.md`](../business-core/docs/agentic/planner-benchmark-results.md)
- Original build plan and demo scenario: [`business-core/docs/agentic/`](../business-core/docs/agentic/)
- Module references (inventory, sales, staffing): [`business-core/docs/`](../business-core/docs/)
- Day-by-day build log: [`development-log/`](../development-log/)
- Glasses app setup and test steps: [`BaristaGlassesAgent/README.md`](../BaristaGlassesAgent/README.md)
