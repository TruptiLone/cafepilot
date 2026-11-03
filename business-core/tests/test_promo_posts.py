"""Promote this event: Muse writes and paints, code checks the caption and sets every word. Drafts only."""

import base64
import io
import json
import re
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from backend.db.config import get_settings
from backend.db.session import engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.agent import promo
from backend.modules.agent.router import get_planner_client

C = "CAFE001"
MARKET = "EVT_SC_FARMERS_MARKET_20261108"
TZ = ZoneInfo("America/Los_Angeles")
GOOD = {"caption": "Santa Clara Farmers Market is back on Sunday, November 8, 8 AM to 2 PM at Franklin Square. "
                   "Stop by for 10% off a pastry with any coffee!",
        "hashtags": ["#SantaClara", "#FarmersMarket", "#Coffee", "#Pastries", "#ShopLocal", "#SundayMarket"]}


def _png(color=(120, 80, 40)) -> str:
    out = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


class APITimeoutError(Exception):
    """Named like the OpenAI SDK's timeout so ai_calls.is_timeout recognises it."""


class Muse:
    """Fake model API: chat answers with a caption, images answer with a PNG or fail as scripted."""

    def __init__(self, caption: dict | None = None, images: list | None = None) -> None:
        self.caption = caption if caption is not None else GOOD
        self.images = list(images or ["ok"] * 4)
        self.timeouts: list[float] = []
        self.calls: list[str] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))
        self.images_api = SimpleNamespace(generate=self._image)

    def with_options(self, timeout=None, max_retries=None):
        self.timeouts.append(timeout)
        return SimpleNamespace(chat=self.chat, images=self.images_api)

    def _chat(self, **kwargs):
        self.calls.append("chat")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.caption)))])

    def _image(self, **kwargs):
        self.calls.append("image")
        assert "no text" in kwargs["prompt"] and "no people" in kwargs["prompt"] and "no logos" in kwargs["prompt"]
        outcome = self.images.pop(0) if self.images else "ok"
        if outcome == "timeout":
            raise APITimeoutError("Request timed out.")
        if outcome == "error":
            raise RuntimeError("upstream 500")
        return SimpleNamespace(data=[SimpleNamespace(b64_json=_png())])


def _facts(title="Santa Clara Farmers Market", offer=promo.DEFAULT_OFFER, location="Franklin Square, Santa Clara"):
    return promo.Facts(MARKET, title, "farmers_market", datetime(2026, 11, 8, 8, tzinfo=TZ),
                       datetime(2026, 11, 8, 14, tzinfo=TZ), location, "CafePilot Demo Cafe", offer)


@pytest.fixture(autouse=True)
def ai_activity(monkeypatch):
    """What promo records in AI activity. Captured in memory: the two variants record from parallel
    threads, and the shared rolled-back test connection can't take writes from several threads."""
    rows, lock = [], threading.Lock()

    def record(**row):
        with lock:
            rows.append(row)
        return "AIC-TEST"

    monkeypatch.setattr(promo, "record_ai_call", record)
    return rows


@pytest.fixture
def folders(tmp_path, monkeypatch):
    monkeypatch.setattr(promo, "DRAFT_DIR", tmp_path / "promo-posts")
    monkeypatch.setattr(promo, "SAMPLE_DIR", tmp_path / "samples")
    return tmp_path


@pytest.fixture
def api(folders):
    connection = engine.connect()
    transaction = connection.begin()
    db: Session = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        if db.get(Cafe, C) is None:
            pytest.skip("demo data not seeded; run scripts/seed_all.sh")
        app.dependency_overrides[get_db] = lambda: db

        def client(muse: Muse) -> TestClient:
            app.dependency_overrides[get_planner_client] = lambda: muse
            return TestClient(app)

        yield client, db
    finally:
        app.dependency_overrides.clear()
        db.close()
        transaction.rollback()
        connection.close()


# ---------- caption: every number, date and time must come from the event or the offer ----------


def test_a_caption_built_from_the_event_and_offer_is_accepted() -> None:
    assert promo.check_caption(GOOD["caption"], GOOD["hashtags"], _facts()) == []


@pytest.mark.parametrize("caption,problem", [
    ("Join us Sunday, November 8 for 20% off a pastry!", "number 20"),
    ("Join us Saturday, November 8, 8 AM to 2 PM!", "Saturday"),
    ("See you December 8 at the market!", "December"),
    ("See you at the market tomorrow from 8 AM!", "tomorrow"),
    ("Grab two pastries with any coffee on Sunday!", '"two"'),
    ("Doors open at 7 AM on Sunday, November 8.", "number 7"),
    ("One. Two sentences. Three sentences. Four sentences.", "4 sentences"),
])
def test_a_caption_with_anything_not_in_the_event_is_rejected(caption: str, problem: str) -> None:
    problems = promo.check_caption(caption, GOOD["hashtags"], _facts())
    assert any(problem in p for p in problems), problems


def test_hashtags_are_checked_too() -> None:
    assert any("number 2025" in p for p in promo.check_caption(GOOD["caption"], [*GOOD["hashtags"][:5], "#Market2025"], _facts()))
    assert any("3 hashtags" in p for p in promo.check_caption(GOOD["caption"], GOOD["hashtags"][:3], _facts()))
    assert promo.clean_hashtags(["coffee", "#Coffee", "#bad tag", "#ok_1", "#"]) == ["#coffee", "#ok_1"]


def test_code_caption_passes_its_own_check_for_every_event(api) -> None:
    _, db = api
    for event_id in ("EVT_SC_FARMERS_MARKET_20261108", "EVT_THANKSGIVING_20261126", "EVT_HOLIDAY_FAIR_20261205",
                     "EVT_HALLOWEEN_20261031"):
        facts = promo.facts_for(db, event_id, promo.DEFAULT_OFFER, allow_past=True)
        caption, tags = promo.code_caption(facts)
        assert promo.check_caption(caption, tags, facts) == [], (event_id, caption, tags)
        assert facts.title in caption and facts.date_line in caption


def test_a_rejected_model_caption_is_replaced_by_the_code_caption(api) -> None:
    client, _ = api
    muse = Muse(caption={"caption": "Big savings this Saturday: 25% off everything!", "hashtags": GOOD["hashtags"]})
    body = client(muse).post("/api/v1/agent/promo-posts", json={"event_id": MARKET}).json()
    for variant in body["variants"]:
        assert variant["caption_source"] == "code" and "number 25" in variant["caption_note"]
        assert variant["caption"].startswith("Join us for Santa Clara Farmers Market on Sunday, November 8")


# ---------- image: code sets every word, nothing overflows ----------


def _inside(boxes) -> list:
    return [(label, box) for label, box in boxes
            if box[0] < promo.MARGIN - 1 or box[2] > promo.SIZE - promo.MARGIN + 1 or box[1] < promo.MARGIN - 20
            or box[3] > promo.SIZE - promo.MARGIN + 1]


def test_composed_post_is_1080_square_with_every_line_inside_the_margins() -> None:
    picture, boxes = promo.compose(Image.new("RGB", (1600, 1200), (200, 180, 150)), _facts())
    assert picture.size == (1080, 1080) and picture.mode == "RGB"
    assert {label for label, _ in boxes} >= {"cafe", "headline", "date", "time", "place", "offer"}
    assert _inside(boxes) == []


def test_long_names_wrap_and_long_offers_never_overflow() -> None:
    facts = _facts(title="The Extraordinarily Long Annual Downtown Holiday Lights Parade and Winter Craft Fair "
                         "Spectacular Featuring Local Makers", offer="x" * 80)
    picture, boxes = promo.compose(promo.drawn_background(facts, 1), facts)
    assert picture.size == (1080, 1080) and _inside(boxes) == []
    assert 1 < sum(1 for label, _ in boxes if label == "headline") <= 3


def test_the_text_area_is_dark_enough_for_white_text() -> None:
    picture, boxes = promo.compose(Image.new("RGB", (1080, 1080), (255, 255, 255)), _facts())  # worst case: white
    for label in ("headline", "date", "place"):
        x0, y0, x1, y1 = next(box for name, box in boxes if name == label)
        grey = picture.crop((promo.MARGIN, y0, promo.SIZE - promo.MARGIN, y1)).convert("L")
        pixels = list(getattr(grey, "get_flattened_data", grey.getdata)())
        background = sorted(pixels)[len(pixels) // 4]  # the darker quarter is the background
        assert background < 100, label  # white text on it is above 4.5:1


def test_a_missing_font_falls_back_safely(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(promo, "FONT_DIR", tmp_path / "no-fonts")
    picture, boxes = promo.compose(Image.new("RGB", (800, 800)), _facts())
    assert picture.size == (1080, 1080) and _inside(boxes) == []


# ---------- Muse Image: 45 s timeout, one retry, then a code-drawn background ----------


def test_image_timeouts_retry_once_then_fall_back_to_a_drawn_background(api, ai_activity) -> None:
    client, _ = api
    muse = Muse(images=["timeout", "timeout", "timeout", "timeout"])
    body = client(muse).post("/api/v1/agent/promo-posts", json={"event_id": MARKET}).json()
    assert [v["image_source"] for v in body["variants"]] == ["code-drawn", "code-drawn"]
    assert all(v["latency_ms"]["image_attempts"] == 2 and "drawn by code" in v["image_note"] for v in body["variants"])
    assert muse.calls.count("image") == 4 and promo.IMAGE_TIMEOUT_S in muse.timeouts
    rows = [r for r in ai_activity if r["request"].startswith("Promo: background")]
    assert len(rows) == 2 and {r["outcome"] for r in rows} == {"timeout"} and {r["attempts"] for r in rows} == {2}


def test_an_image_that_works_on_the_retry_is_used(api, ai_activity) -> None:
    client, _ = api
    muse = Muse(images=["error", "ok", "error", "ok"])
    body = client(muse).post("/api/v1/agent/promo-posts", json={"event_id": MARKET}).json()
    assert [v["image_source"] for v in body["variants"]] == ["muse-image", "muse-image"]
    assert {r["outcome"] for r in ai_activity if "background" in r["request"]} == {"retried"}
    assert {r["outcome"] for r in ai_activity if "caption" in r["request"]} == {"ok"}  # every call is in AI activity
    assert {r["purpose"] for r in ai_activity} == {"other"}


# ---------- the endpoint: offer limits, drafts only, no secrets ----------


def test_offer_text_limits(api) -> None:
    client, _ = api
    c = client(Muse())
    post = lambda offer: c.post("/api/v1/agent/promo-posts", json={"event_id": MARKET, "offer_text": offer})  # noqa: E731
    assert post("x" * 81).status_code == 422
    assert post("Free cookie\nwith latte").status_code == 422
    assert post("See https://example.com").status_code == 422
    assert post("<b>10% off</b>").status_code == 422
    assert post("y" * 80).json()["offer_text"] == "y" * 80
    body = post("").json()
    assert body["offer_text"] == body["suggested_offer"] == "10% off a pastry with any coffee"
    assert c.post("/api/v1/agent/promo-posts", json={"event_id": "EVT_HALLOWEEN_20261031"}).status_code == 409
    assert c.post("/api/v1/agent/promo-posts", json={"event_id": "NOPE"}).status_code == 404


def test_drafts_are_saved_listed_and_served_but_never_posted(api, folders) -> None:
    client, _ = api
    muse = Muse()
    c = client(muse)
    body = c.post("/api/v1/agent/promo-posts", json={"event_id": MARKET, "offer_text": promo.DEFAULT_OFFER}).json()
    assert body["posted"] is False and len(body["variants"]) == 2
    for v in body["variants"]:
        assert v["status"] == "draft" and v["caption_source"] == "muse" and v["image_source"] == "muse-image"
        assert v["model"] == {"caption": get_settings().vision_model, "image": get_settings().image_model}
        assert set(v["latency_ms"]) == {"caption", "image", "image_attempts"}
        image = c.get(v["image_url"])
        assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
        assert Image.open(io.BytesIO(image.content)).size == (1080, 1080)
    listed = c.get("/api/v1/agent/promo-posts").json()
    assert {d["id"] for d in listed} == {v["id"] for v in body["variants"]}
    assert all(d["status"] == "draft" for d in listed)
    # Only the model was called: caption and background, twice each. Nothing else is reachable from promo.
    assert sorted(muse.calls) == ["chat", "chat", "image", "image"]
    source = Path(promo.__file__).read_text().lower()
    assert not re.search(r"instagram\.com|graph\.facebook|requests\.|httpx|urlopen", source)
    assert c.get("/api/v1/agent/promo-posts/..%2F..%2Fsecrets/image").status_code == 404
    assert c.get("/api/v1/agent/promo-posts/PROMO-NOTHING/image").status_code == 404


def test_no_secrets_in_responses_or_files(api, folders) -> None:
    client, _ = api
    c = client(Muse())
    body = c.post("/api/v1/agent/promo-posts", json={"event_id": MARKET}).text
    files = "".join(p.read_text() for p in folders.rglob("draft.json"))
    key = get_settings().model_api_key
    secrets = [s for s in [key.get_secret_value() if key else ""] if len(s) >= 8]
    for text in (body, files, c.get("/api/v1/agent/promo-posts").text):
        assert not any(s in text for s in secrets)
        assert not re.search(r"sk-[A-Za-z0-9]{8,}|Bearer\s|api[_-]?key", text, re.IGNORECASE)
