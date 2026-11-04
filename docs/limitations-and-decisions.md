# Decisions, trade-offs and honest limits

What we planned, what we shipped, what is simulated or scripted, and why. Written so a reviewer can
see exactly where the line is.

## 1. Planned vs built

The original design lives in the project's design document. This table says where the shipped system
differs and why.

| Planned | Built | Why |
|---|---|---|
| LightGBM sales forecast with quantiles and an accuracy comparison (MAPE vs a baseline) | **Transparent averages of comparable past days** with low / typical / high scenarios and a confidence label | In 10 days, a method the owner can inspect and we can test by hand beat a model we could not validate. No accuracy score is claimed. See [forecasting-and-math.md](forecasting-and-math.md) |
| Stock "belief engine" (stock as a distribution, stock-out risk) | Stock estimates from counts and movements; **unknown stock is reported, never guessed**; uncertain delivery counts become "please count it" | Same honesty goal, much smaller surface |
| Policy engine with auto-approval limits | **Every write waits for the owner**; policies (buffer %, pack sizes, staffing levels, opening hours) are in code | Safer for a demo and simpler to explain. Auto-approval is future work |
| Spark event finder with search grounding | **Scripted discovery in the demo**: after the first Ask question, the Farmers Market is added through `POST /api/v1/agent/events/discover-demo` | Reliable, repeatable demo. Marked in the app and docs |
| Muse Voice Transcribe command bar | Browser speech recognition and text-to-speech | See [mobile-app.md](mobile-app.md#6-what-the-platform-did-not-give-us-and-what-we-did-instead) |
| Video shelf counts with SAM tracking | Single photo: SAM + Spark verification + code totals | Video tracking was a stated risk; the still-photo fallback was already in the plan |
| Real supplier and staff integrations | **Simulated** suppliers and staff that answer a few seconds after a request | No real accounts or money; shows the full lifecycle |
| Wonder only for replanning, Spark for the plan (fallback if Wonder was slow) | Wonder plans and replans | We measured it: about twice as fast as Spark on our requests |
| Posting promos | **Drafts only**, never posted; code sets all text and numbers; Muse Image only paints the background | An owner approves and posts it themselves |

## 2. Key decisions

| Decision | Why |
|---|---|
| The model proposes; code validates and does the math | Models are good at reading and planning, not at being the source of truth for quantities. See [agentic-planning.md](agentic-planning.md) |
| A plan graph (DAG), not a free tool loop | Whole plan validated before anything runs; safe pauses at writes; auditable |
| In-process service calls, not REST, from the executor | One validation path, idempotent writes, no credentials on the model path |
| Fixed action registry (23 actions) | The model can only do what we registered. `create_sale` and `create_employee` are excluded |
| One retry on a rejected plan, with the reasons | Fixes the common slips (time offsets, missing prerequisite) without looping |
| Honest statuses | Review feedback: "Done" over-claimed. Now *Proposed → Approved → Sent → Waiting → Confirmed / Rejected* |
| Two models for counting | SAM finds, Spark checks, code totals; neither is trusted alone |
| Local Wi-Fi photo sync | No cloud, no tunnel, no port forwarding; the photo never leaves the owner's network except the model call |
| Every model call logged | `ai_calls` rows: model, outcome, attempts, timing. No prompts or keys |

## 3. Problems we hit with the models and how we handled them

| Problem | Handling |
|---|---|
| Function-calling with `tool_choice="auto"` may skip the tool | Replaced by structured extraction and, later, a validated plan |
| Prompt-requested JSON is not schema-enforced | Pydantic schemas and a validator on everything the model returns; fenced-JSON handling |
| Plans with the wrong UTC offset around daylight saving (a Sunday delivery written with Thursday's offset landed an hour off) | Code computes `utc_offset_by_date`; the validator rejects `wrong_time_offset` and states the correct value |
| Plans that invented quantities or demand | Validator rejects `unverified_quantity` and `unverified_demand` |
| Slow or timed-out calls | Logged with the outcome; plain-language error; retry by the owner; promo images fall back to a code-drawn background |
| SAM merges or misses stacked items | Spark verification, code totals, "please count it" when unsure |
| Slip digits misread | Match by PO number found by code, owner sees slip and order together |

## 4. What is simulated, scripted or synthetic

| Item | Status |
|---|---|
| Café, menu, recipes, sales history, staff, suppliers, orders, documents | **Synthetic** |
| Supplier answers (confirm / reject / out of stock) and staff replies | **Simulated** by a built-in simulator |
| Event discovery in the demo | **Scripted** (after the first question in Ask) |
| Demand estimates | Averages of comparable past days in the synthetic history |
| Instagram posts | **Drafts**; never posted |
| Demo clock | Fixed (Thu Nov 5 2026, 6 PM PT) so results are repeatable |
| Replan after a supplier decline | **Built and tested**; not triggered by the current demo data (the demo shows a short delivery and a reorder) |
| Voice in Ask | Browser speech recognition and text-to-speech, not a Meta model |

## 5. Known limits

- Demand estimation is simple on purpose: no weather, promotion or price effects, no accuracy score.
- Counting can fail on heavily stacked or hidden goods; the system says so instead of guessing.
- The glasses app is Developer Mode only and works on a local network.
- One café, one currency, one time zone in the demo data.
- Model calls can be slow; the app shows progress and logs every call.
- The model output is checked by code, but the quality of a plan (for example "valid" vs "best") is not
  measured; "valid" means it passed the validator.

## 6. What we would do next

1. A real forecast (quantile regression) evaluated on held-out event days against a simple baseline.
2. Optional auto-approval policies with budget limits and expiry.
3. Real supplier and scheduling integrations.
4. Voice on the glasses once the platform exposes it for our use case.
5. Production device registration and a hosted receiver instead of a Mac on the same Wi-Fi.
6. A larger planner benchmark with several runs per request.
