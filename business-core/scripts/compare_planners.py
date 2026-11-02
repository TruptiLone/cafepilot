"""Local planning-only benchmark. Never imports the executor or executes proposed tools.

From business-core/: PYTHONPATH=. python scripts/compare_planners.py
Results include frozen inputs, complete responses, validation, JSON, Markdown and HTML.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import statistics
import time
from typing import Any, Callable

from backend.modules.agent.planner import PlanningContext, PlannerError, build_messages, parse_plan
from backend.modules.agent.validator import validate_plan

MODELS = ("muse-spark-1.3", "muse-wonder-1.0")
SETTINGS = {"temperature": 0, "reasoning_effort": "high", "max_tokens": 16384}
CASE_PATH = Path(__file__).with_name("planner_benchmark_cases.json")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def execute_attempt(client: Any, model: str, messages: list[dict], context: PlanningContext,
                    *, clock: Callable = time.perf_counter, secret: str = "") -> dict:
    started = clock()
    attempt: dict[str, Any] = {"model": model, "input_sha256": digest(messages),
                               "messages": deepcopy(messages), "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        response = client.chat.completions.create(model=model, messages=deepcopy(messages), **SETTINGS)
    except Exception as error:
        attempt.update(outcome="api_error", api_ms=round((clock() - started) * 1000, 2),
                       error=(str(error).replace(secret, "[REDACTED]") if secret else str(error))[:3000],
                       error_type=type(error).__name__, status_code=getattr(error, "status_code", None))
        attempt["attempt_ms"] = round((clock() - started) * 1000, 2)
        return attempt
    attempt["api_ms"] = round((clock() - started) * 1000, 2)
    attempt["response"] = response.model_dump(mode="json")
    attempt["usage"] = attempt["response"].get("usage")
    choice = response.choices[0] if response.choices else None
    raw = choice.message.content if choice else None
    attempt["raw_content"] = raw
    attempt["finish_reason"] = choice.finish_reason if choice else None
    if choice is not None and choice.finish_reason == "length":
        attempt.update(outcome="truncated", error="Output-token limit reached, including reasoning tokens.")
    else:
        try:
            if not isinstance(raw, str) or not raw.strip():
                raise PlannerError("model returned no text")
            plan = parse_plan(raw)
            validation = validate_plan(plan, context.cafe_id, timezone=context.timezone)
            attempt.update(plan=plan.model_dump(mode="json"), validation=asdict(validation))
            attempt["outcome"] = ("valid_plan" if validation.valid else "invalid_plan") if plan.status == "plan" else plan.status
        except PlannerError as error:
            attempt.update(outcome="parse_error", error=str(error))
    attempt["attempt_ms"] = round((clock() - started) * 1000, 2)
    return attempt


def run_case(client: Any, model: str, messages: list[dict], context: PlanningContext,
             *, clock: Callable = time.perf_counter, secret: str = "", save_attempt: Callable | None = None) -> dict:
    started = clock()
    attempts = []
    current = deepcopy(messages)
    for number in (1, 2):
        attempt = execute_attempt(client, model, current, context, clock=clock, secret=secret)
        attempt["number"] = number
        # Save before any next API call, including failed/invalid responses.
        if save_attempt:
            save_attempt(attempt)
        attempts.append(attempt)
        if attempt["outcome"] not in {"invalid_plan", "parse_error", "truncated"} or number == 2:
            break
        if attempt["outcome"] == "invalid_plan":
            issues = attempt["validation"]["issues"]
            problems = "\n".join(f"- [{i['code']}]" + (f" step {i['node_id']}" if i['node_id'] else "") + f": {i['message']}" for i in issues)
        else:
            problems = f"- [unparseable] {attempt['error']}"
        current = deepcopy(messages) + [
            {"role": "assistant", "content": attempt.get("raw_content") or ""},
            {"role": "user", "content": "Your plan was rejected by the validator for these reasons:\n"
             f"{problems}\nReturn the complete corrected plan as JSON only."},
        ]
    final = attempts[-1]
    return {"model": model, "initial_input_sha256": digest(messages), "attempts": attempts,
            "initial_api_ms": attempts[0]["api_ms"], "total_api_ms": round(sum(a["api_ms"] for a in attempts), 2),
            "total_wall_ms": round((clock() - started) * 1000, 2), "outcome": final["outcome"],
            "final_plan": final.get("plan"), "final_validation": final.get("validation")}


def compare_pair(left: dict, right: dict) -> dict:
    """Compare action multiplicity and arguments; retain wording separately in full plans."""
    def nodes(result):
        grouped = defaultdict(list)
        plan = result.get("final_plan") or {}
        labels, counts = {}, Counter()
        for node in plan.get("nodes", []):
            counts[node["action"]] += 1
            labels[node["id"]] = f"{node['action']}#{counts[node['action']]}"
        def normalize(value):
            if isinstance(value, dict): return {k: normalize(v) for k, v in value.items()}
            if isinstance(value, list): return [normalize(v) for v in value]
            if isinstance(value, str) and value.startswith("$"):
                import re
                return re.sub(r"^\$([a-z][a-z0-9_]*)", lambda m: "$" + labels.get(m[1], m[1]), value)
            return value
        for node in plan.get("nodes", []): grouped[node["action"]].append(normalize(node["args"]))
        return grouped
    a, b = nodes(left), nodes(right)
    ca, cb = Counter({k: len(v) for k, v in a.items()}), Counter({k: len(v) for k, v in b.items()})
    changes = []
    for action in sorted(a.keys() & b.keys()):
        for i, (av, bv) in enumerate(zip(a[action], b[action]), 1):
            if av != bv: changes.append({"action": action, "occurrence": i, "spark_args": av, "wonder_args": bv})
    return {"spark_only_actions": dict(ca - cb), "wonder_only_actions": dict(cb - ca),
            "argument_differences": changes, "spark_status": (left.get("final_plan") or {}).get("status"),
            "wonder_status": (right.get("final_plan") or {}).get("status"),
            "wonder_minus_spark_initial_ms": round(right["initial_api_ms"] - left["initial_api_ms"], 2)}


def summarize(rows: list[dict]) -> dict:
    summary = {}
    for model in MODELS:
        runs = [r for r in rows if r["model"] == model]
        successful_responses = [r["initial_api_ms"] for r in runs if r["attempts"][0]["outcome"] != "api_error"]
        usage = Counter()
        for r in runs:
            for attempt in r["attempts"]:
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    usage[key] += (attempt.get("usage") or {}).get(key, 0) or 0
        summary[model] = {"cases": len(runs), "api_calls": sum(len(r["attempts"]) for r in runs),
                          "first_pass_valid_plans": sum(r["attempts"][0]["outcome"] == "valid_plan" for r in runs),
                          "final_outcomes": dict(Counter(r["outcome"] for r in runs)),
                          "initial_api_mean_ms": statistics.mean(successful_responses) if successful_responses else None,
                          "initial_api_median_ms": statistics.median(successful_responses) if successful_responses else None,
                          "total_wall_mean_ms": statistics.mean(r["total_wall_ms"] for r in runs) if runs else None,
                          "tokens": dict(usage)}
    return summary


def write_reports(directory: Path, cases: list[dict], rows: list[dict], manifest: dict) -> None:
    summary = summarize(rows)
    lookup = {(r["case_id"], r["model"]): r for r in rows}
    pairs = {}
    for case in cases:
        a, b = (lookup.get((case["id"], m)) for m in MODELS)
        if a and b: pairs[case["id"]] = compare_pair(a, b)
    write_json(directory / "results.json", {"manifest": manifest, "summary": summary, "comparisons": pairs, "results": rows})
    lines = ["# Spark vs Wonder: local planning benchmark", "",
             "Planning only. No tool calls or café writes were executed. One initial run per model per query; not a statistical speed benchmark.", "",
             "Validator acceptance is a structural check, not proof that a plan is feasible or satisfies every user constraint. Clarification is not automatically a failure.", "",
             f"Frozen café time: {manifest['frozen_time']}. Settings: `{json.dumps(SETTINGS)}`. Timeout: {manifest['timeout_seconds']}s; HTTP retries disabled.", "",
             "| Model | Calls | First-pass valid plans | Final outcomes | Mean initial API seconds | Median initial API seconds | Mean total seconds |",
             "|---|---:|---:|---|---:|---:|---:|"]
    seconds = lambda ms: f"{ms / 1000:.2f}" if ms is not None else "—"
    for model, s in summary.items():
        lines.append(f"| {model} | {s['api_calls']} | {s['first_pass_valid_plans']} | {s['final_outcomes']} | {seconds(s['initial_api_mean_ms'])} | {seconds(s['initial_api_median_ms'])} | {seconds(s['total_wall_mean_ms'])} |")
    lines += ["", "## Per-query timings", "", "| Query | Spark initial / total seconds | Wonder initial / total seconds | Spark outcome | Wonder outcome |", "|---|---:|---:|---|---|"]
    for case in cases:
        a, b = (lookup.get((case["id"], m)) for m in MODELS)
        if not a or not b: continue
        lines.append(f"| {case['id']}: {case['title']} | {seconds(a['initial_api_ms'])} / {seconds(a['total_wall_ms'])} | {seconds(b['initial_api_ms'])} / {seconds(b['total_wall_ms'])} | {a['outcome']} | {b['outcome']} |")
    parts = ["<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Cafepilot planner comparison</title>",
             "<style>body{font:16px/1.55 system-ui;max-width:1500px;margin:32px auto;padding:0 24px;color:#172a25;background:#f6f8f7}h1,h2{color:#123f32}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}article,section{background:white;border:1px solid #dce5df;border-radius:10px;padding:20px;margin:16px 0;min-width:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 ui-monospace;background:#f1f5f3;padding:14px;border-radius:6px}nav a{display:inline-block;margin:6px}summary{cursor:pointer;font-weight:600}@media(max-width:850px){.pair{grid-template-columns:1fr}}</style>",
             "<h1>Cafepilot: Spark vs Wonder</h1><p>Live planning-only comparison. No proposed operations were executed. One sample per query/model. Validator acceptance does not prove business correctness.</p>",
             "<p>Full prompts, responses, attempts, validation issues, and token usage are saved beside this report. See <a href='review.md'>review.md</a> for the human assessment.</p>",
             "<details><summary>Run configuration and summary</summary><pre>" + html.escape(json.dumps({"manifest": manifest, "summary": summary}, indent=2)) + "</pre></details><nav>" + " ".join(f"<a href='#{c['id']}'>{c['id']}</a>" for c in cases) + "</nav>"]
    parts.append("<style>table{border-collapse:collapse;width:100%;margin:16px 0;font-size:14px}th,td{text-align:left;padding:10px;border-bottom:1px solid #dce5df}th{background:#e7efeb}.overview{overflow:auto}</style><section class='overview'><h2>Timing summary</h2><table><tr><th>Model</th><th>API calls</th><th>First-pass valid plans</th><th>Mean initial</th><th>Median initial</th><th>Mean total</th></tr>")
    for model, s in summary.items():
        parts.append(f"<tr><td>{model}</td><td>{s['api_calls']}</td><td>{s['first_pass_valid_plans']} / {s['cases']}</td><td>{seconds(s['initial_api_mean_ms'])}s</td><td>{seconds(s['initial_api_median_ms'])}s</td><td>{seconds(s['total_wall_mean_ms'])}s</td></tr>")
    parts.append("</table><p>Valid-plan counts exclude clarification responses, which may be the correct answer. See outcomes and human review.</p><h2>Each query</h2><table><tr><th>Query</th><th>Spark initial / total</th><th>Wonder initial / total</th><th>Spark outcome</th><th>Wonder outcome</th></tr>")
    for case in cases:
        a, b = (lookup.get((case["id"], m)) for m in MODELS)
        if not a or not b: continue
        parts.append(f"<tr><td><a href='#{case['id']}'>{case['id']}</a> {html.escape(case['title'])}</td><td>{seconds(a['initial_api_ms'])}s / {seconds(a['total_wall_ms'])}s</td><td>{seconds(b['initial_api_ms'])}s / {seconds(b['total_wall_ms'])}s</td><td>{html.escape(a['outcome'])}</td><td>{html.escape(b['outcome'])}</td></tr>")
    parts.append("</table></section>")
    for case in cases:
        lines += ["", f"## {case['id']}: {case['title']}", "", case["query"], "", "Review criteria:"] + ["- " + x for x in case["review_checks"]]
        parts.append(f"<section id='{case['id']}'><h2>{case['id']} · {html.escape(case['title'])}</h2><p>{html.escape(case['query'])}</p><p><strong>Review criteria</strong></p><ul>" + "".join("<li>" + html.escape(x) + "</li>" for x in case["review_checks"]) + "</ul><div class='pair'>")
        for model in MODELS:
            r = lookup.get((case["id"], model))
            if not r: continue
            lines += ["", f"### {model}", "", f"Initial API: {seconds(r['initial_api_ms'])}s; total: {seconds(r['total_wall_ms'])}s; outcome: {r['outcome']}; attempts: {len(r['attempts'])}.", "", "```json", json.dumps(r.get("final_plan"), indent=2, ensure_ascii=False), "```"]
            details = {"validation": r.get("final_validation"), "attempts": r["attempts"]}
            parts.append(f"<article><h3>{model}</h3><p>Initial: <b>{seconds(r['initial_api_ms'])}s</b> · Total: <b>{seconds(r['total_wall_ms'])}s</b> · {html.escape(r['outcome'])} · {len(r['attempts'])} attempt(s)</p><pre>" + html.escape(json.dumps(r.get("final_plan"), indent=2, ensure_ascii=False)) + "</pre><details><summary>All attempts, raw responses and validation</summary><pre>" + html.escape(json.dumps(details, indent=2, ensure_ascii=False)) + "</pre></details></article>")
        parts.append("</div>")
        if case["id"] in pairs:
            lines += ["", "Automated differences (arguments matched by action and occurrence; review full plans for semantics):", "", "```json", json.dumps(pairs[case["id"]], indent=2), "```"]
            parts.append("<details><summary>Automated differences</summary><pre>" + html.escape(json.dumps(pairs[case["id"]], indent=2)) + "</pre></details>")
        parts.append("</section>")
    parts.append("</html>")
    (directory / "report.md").write_text("\n".join(lines) + "\n")
    (directory / "report.html").write_text("\n".join(parts))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cafe-id", default="CAFE001")
    parser.add_argument("--as-of", default="2026-10-29T18:00:00-07:00")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    frozen_time = datetime.fromisoformat(args.as_of)
    if frozen_time.tzinfo is None: parser.error("--as-of needs a UTC offset")
    from openai import OpenAI
    from sqlalchemy import text
    from backend.db.config import get_settings
    from backend.db.session import SessionLocal
    from backend.modules.agent.planner import build_planning_context
    settings = get_settings()
    if settings.postgres_host not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("This benchmark only reads a local database; remote databases are refused")
    secret = settings.model_api_key.get_secret_value().strip() if settings.model_api_key else ""
    if not secret: parser.error("MODEL_API_KEY is not configured")
    if settings.model_api_base_url.rstrip("/") != "https://api.meta.ai/v1":
        parser.error("This comparison requires the direct Meta Model API endpoint")
    directory = args.output or Path("var/benchmarks") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory.mkdir(parents=True, exist_ok=False)
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        context = build_planning_context(db, args.cafe_id, frozen_time)
        context = replace(context, now=frozen_time)
        db.rollback()
    cases = json.loads(CASE_PATH.read_text())
    if args.cafe_id != "CAFE001": parser.error("The scenario suite is written for CAFE001")
    manifest = {"started_at": datetime.now(timezone.utc).isoformat(), "frozen_time": frozen_time.isoformat(),
                "cafe_id": context.cafe_id, "timezone": context.timezone, "models": list(MODELS),
                "generation_settings": SETTINGS, "timeout_seconds": args.timeout, "http_retries": 0,
                "context_sha256": digest(asdict(context)), "api_base_url": settings.model_api_base_url,
                "scope": "Read-only context snapshot; planning and static validation only; no execution",
                "limitations": ["One sample per case/model", "No executor/tool results", "Q03 and Q04 are explicitly hypothetical replanning inputs", "Validator does not establish runtime feasibility"],
                "run_order": []}
    write_json(directory / "context.json", asdict(context))
    write_json(directory / "cases.json", cases)
    write_json(directory / "manifest.json", manifest)
    rows = []
    with OpenAI(api_key=secret, base_url=settings.model_api_base_url, timeout=args.timeout, max_retries=0) as client:
        for index, case in enumerate(cases):
            messages = build_messages(case["query"], context)
            write_json(directory / "inputs" / f"{case['id']}.json", messages)
            order = MODELS if index % 2 == 0 else MODELS[::-1]
            for model in order:
                print(f"{case['id']} {model}: requesting plan", flush=True)
                def save_attempt(attempt):
                    path = directory / "attempts" / f"{case['id']}-{model}-{attempt['number']}.json"
                    write_json(path, attempt)
                    print(f"  attempt {attempt['number']}: {attempt['outcome']}, {attempt['api_ms'] / 1000:.2f}s", flush=True)
                result = run_case(client, model, messages, context, secret=secret, save_attempt=save_attempt)
                result.update(case_id=case["id"], title=case["title"])
                rows.append(result)
                manifest["run_order"].append({"case_id": case["id"], "model": model})
                write_json(directory / "manifest.json", manifest)
                write_reports(directory, cases, rows, manifest)
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    write_json(directory / "manifest.json", manifest)
    write_reports(directory, cases, rows, manifest)
    print(f"Completed. Review {directory / 'report.html'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
