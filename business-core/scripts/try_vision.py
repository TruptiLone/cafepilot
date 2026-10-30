"""Check whether the Meta Model API reads images and PDFs, using the receiving demo files.

Run from business-core/ (needs MODEL_API_KEY in .env):
    PYTHONPATH=. python scripts/try_vision.py

Tries two image requests (glasses and phones produce images) and prints what worked:
  1. packing slip photo   (OpenAI-style "image_url" with a base64 data URL)
  2. delivery photo       (count what is visible)
Results are saved to tests/fixtures/receiving/vision_live.json so we can build on the real output.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from backend.db.config import get_settings
from backend.modules.agent.planner import PlannerUnavailableError, create_planner_client

ASSETS = Path("demo-assets/receiving")
OUT = Path("tests/fixtures/receiving/vision_live.json")

SLIP_PROMPT = """Read this packing slip. Return ONLY a JSON object, no prose:
{"supplier": str, "packing_slip_number": str, "customer_po_number": str, "delivery_date": str,
 "delivery_window": str, "lines": [{"item_number": str, "description": str, "pack": str,
 "ordered": number, "shipped": number, "backordered": number}], "total_cases_shipped": number,
 "notes": str}
Copy values exactly as printed. Use null for anything you cannot read."""

PHOTO_PROMPT = """This photo shows a delivery at a café's door. Count only what you can actually see.
Return ONLY a JSON object, no prose:
{"items": [{"product": str, "label_text": str, "visible_units": number, "container": str,
 "pack_label": str | null, "possibly_hidden_units": bool, "confidence": "high" | "medium" | "low"}],
 "visible_damage": str | null, "notes": str}
Do not guess units you cannot see; set possibly_hidden_units true when a case or crate may hold more."""


def data_url(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def ask(client, model: str, name: str, content: list) -> dict:
    started = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": content}], temperature=0
        )
        text = response.choices[0].message.content or ""
        ms = round((time.monotonic() - started) * 1000)
        try:
            parsed = json.loads(text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        except json.JSONDecodeError:
            parsed = None
        print(f"\n✅ {name}: worked in {ms} ms")
        print(json.dumps(parsed, indent=2) if parsed is not None else text[:1500])
        return {"ok": True, "latency_ms": ms, "raw": text, "parsed": parsed}
    except Exception as error:  # show the API's own message
        ms = round((time.monotonic() - started) * 1000)
        detail = str(error).splitlines()[0][:400]
        print(f"\n❌ {name}: {type(error).__name__} after {ms} ms\n   {detail}")
        return {"ok": False, "latency_ms": ms, "error": f"{type(error).__name__}: {detail}"}


def main() -> None:
    try:
        client = create_planner_client()
    except PlannerUnavailableError as error:
        print(f"Can't run: {error}")
        return
    model = get_settings().planner_model
    print(f"Model: {model}")
    results = {
        "model": model,
        "slip": ask(client, model, "1. Packing slip photo", [
            {"type": "text", "text": SLIP_PROMPT},
            {"type": "image_url", "image_url": {"url": data_url(ASSETS / "packing-slip-PS-58213.png", "image/png")}},
        ]),
        "photo": ask(client, model, "2. Delivery photo", [
            {"type": "text", "text": PHOTO_PROMPT},
            {"type": "image_url", "image_url": {"url": data_url(ASSETS / "delivery-photo.jpg", "image/jpeg")}},
        ]),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2))
    worked = [k for k, v in results.items() if isinstance(v, dict) and v.get("ok")]
    print(f"\nSaved to {OUT}. Worked: {', '.join(worked) or 'none'}")


if __name__ == "__main__":
    main()
