"""Make the committed promo samples: one AI-generated draft per demo event (run once, by hand).

Run from business-core/ (needs MODEL_API_KEY in .env):
    PYTHONPATH=. python scripts/make_promo_samples.py

Each sample goes through the same pipeline as POST /api/v1/agent/promo-posts (Muse caption checked by
code, Muse Image background or the code-drawn fallback, every word set by code) and is saved as a JPEG
under 300 KB in demo-assets/promo-samples/ with samples.json. They are drafts for the demo gallery,
labelled "AI-generated draft (demo)", and were never posted anywhere.
"""

from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import backend.main  # noqa: F401  (loads every model so the session can read events and cafés)
from backend.db.config import get_settings
from backend.db.session import SessionLocal
from backend.modules.agent import promo
from backend.modules.agent.planner import create_planner_client
from PIL import Image

EVENTS = {  # sample id → event (Halloween has already happened; the sample shows it anyway)
    "SAMPLE-HALLOWEEN": "EVT_HALLOWEEN_20261031",
    "SAMPLE-THANKSGIVING": "EVT_THANKSGIVING_20261126",
    "SAMPLE-HOLIDAY-FAIR": "EVT_HOLIDAY_FAIR_20261205",
    "SAMPLE-FARMERS-MARKET": "EVT_SC_FARMERS_MARKET_20261108",
}
LIMIT_KB = 300


def main() -> None:
    settings = get_settings()
    client = create_planner_client()
    with SessionLocal() as session:
        facts = {sid: promo.facts_for(session, eid, promo.DEFAULT_OFFER, allow_past=True) for sid, eid in EVENTS.items()}
    with ThreadPoolExecutor(max_workers=len(facts)) as pool:
        made = dict(zip(facts, pool.map(
            lambda f: promo.make_variant(client, f, 0, caption_model=settings.vision_model,
                                         image_model=settings.image_model), facts.values())))
    promo.SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    samples = []
    for sample_id, item in made.items():
        jpeg = item.pop("_jpeg")
        picture = Image.open(io.BytesIO(jpeg))
        small = promo._jpeg(picture, limit_kb=LIMIT_KB)
        (promo.SAMPLE_DIR / f"{sample_id.lower()}.jpg").write_bytes(small)
        f = facts[sample_id]
        samples.append({
            "id": sample_id, "status": "draft", "label": promo.SAMPLE_LABEL, "sample": True,
            "event_id": f.event_id, "event_title": f.title, "offer_text": f.offer,
            "created_at": datetime.now(timezone.utc).isoformat(), **item,
        })
        print(f"{sample_id}: {len(small) // 1024} KB · caption {item['caption_source']} · image {item['image_source']}"
              f" · {item['latency_ms']}")
    (promo.SAMPLE_DIR / "samples.json").write_text(json.dumps(samples, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
