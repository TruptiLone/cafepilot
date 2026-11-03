"""Promote this event: Instagram-style post drafts. Nothing is ever posted anywhere.

The model proposes, the code decides:
  - Muse Spark writes a caption (at most 3 sentences) and 5–8 hashtags from the event record and the
    owner's offer. Code checks it: every number, date and time must appear in the event fields or the
    offer, months and weekdays must be the event's, and relative days ("tomorrow") are out. If any
    check fails, code writes a plain caption instead.
  - Muse Image paints the background only (no text, people, logos or brands). Each variant gets a
    45 s timeout and one retry, then a code-drawn background (seasonal gradient and simple shapes).
  - Code sets every word on the image with Pillow: headline, date and time in café time, place, the
    owner's offer and the café name, all from the event record and the owner's text.

Drafts are files under var/promo-posts/<id>/ (draft.json + image.jpg). Committed samples live in
demo-assets/promo-samples/ and are listed as "AI-generated draft (demo)".
"""

from __future__ import annotations

import base64
import io
import json
import math
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from sqlalchemy.orm import Session

from backend.db.config import get_settings
from backend.models.cafe import Cafe
from backend.modules.agent.ai_calls import call_image, call_model, is_timeout, record as record_ai_call
from backend.modules.agent.summaries import clock
from backend.modules.events.models import CafeEvent

ROOT = Path(__file__).resolve().parents[3]  # business-core/
DRAFT_DIR = ROOT / "var" / "promo-posts"
SAMPLE_DIR = ROOT / "demo-assets" / "promo-samples"
FONT_DIR = ROOT / "assets" / "fonts"

OFFER_MAX = 80
DEFAULT_OFFER = "10% off a pastry with any coffee"
SIZE = 1080
MARGIN = 72
VARIANTS = 2
IMAGE_TIMEOUT_S = 45
IMAGE_TRIES = 2  # one retry
CAPTION_TIMEOUT_S = 45  # 8 calls at once (4 captions + 4 images) took Spark up to 30 s
SAMPLE_LABEL = "AI-generated draft (demo)"
DRAFT_LABEL = "AI-generated draft"

_ID = re.compile(r"^(PROMO|SAMPLE)-[A-Z0-9-]{3,40}$")
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
_HASHTAG = re.compile(r"^#[A-Za-z][A-Za-z0-9_]{1,39}$")
_UNSAFE_OFFER = re.compile(r"[<>{}\[\]\\]|https?://|www\.|[\x00-\x1f\x7f]", re.IGNORECASE)

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
NUMBER_WORDS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
                "thirteen", "fourteen", "fifteen", "twenty", "thirty", "forty", "fifty", "hundred", "dozen", "half"]
RELATIVE_DAYS = ["today", "tonight", "tomorrow", "yesterday", "this weekend", "next week", "this week"]

NO_TEXT = ("no text, no letters, no words, no numbers, no signs, no people, no hands, no faces, no logos, "
           "no brands, no characters")
STYLES = ["warm golden light, inviting", "soft bright morning light, airy and fresh"]
SCENES = {
    "farmers_market": "a cozy farmers-market café scene with produce crates, pastries and coffee cups",
    "community_festival": "a cozy café corner at an evening craft fair with string lights and handmade ornaments",
    "holiday": "a cozy café table with seasonal treats and coffee",
}
KEYWORD_SCENES = {
    "thanksgiving": "a cozy autumn café table with pumpkin pie, coffee and fall leaves",
    "halloween": "a cozy café counter with carved pumpkins, candles and autumn pastries",
    "holiday lights": "a cozy café corner with warm string lights, pine branches and hot drinks",
    "farmers market": SCENES["farmers_market"],
}
CATEGORY_TAGS = {"farmers_market": "#FarmersMarket", "community_festival": "#CraftFair", "holiday": "#HolidaySeason"}


class OfferError(ValueError):
    """The owner's offer text can't be used (too long, or not plain text)."""


class EventNotPromotable(LookupError):
    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Facts: everything the post may say comes from here
# ---------------------------------------------------------------------------


@dataclass
class Facts:
    event_id: str
    title: str
    category: str
    starts: datetime  # café time
    ends: datetime
    location: str | None
    cafe_name: str
    offer: str

    @property
    def date_line(self) -> str:  # "Sunday, November 8"
        return f"{self.starts.strftime('%A, %B')} {self.starts.day}"

    @property
    def time_line(self) -> str:  # "8 AM – 2 PM"
        return f"{clock(self.starts)} – {clock(self.ends)}"

    @property
    def place(self) -> str | None:
        return None if not self.location or self.location.lower() == "citywide" else self.location

    def text(self) -> str:
        """Every field the caption may draw on, in the forms it may use."""
        short = f"{self.starts.strftime('%a %b')} {self.starts.day}"
        return " ".join(filter(None, [
            self.title, self.category.replace("_", " "), self.date_line, short, self.time_line,
            clock(self.starts), clock(self.ends), str(self.starts.year), self.location, self.cafe_name, self.offer,
        ]))


def clean_offer(text: str | None) -> str:
    offer = " ".join((text or "").split())
    if not offer:
        return DEFAULT_OFFER
    if len(offer) > OFFER_MAX:
        raise OfferError(f"The offer can be at most {OFFER_MAX} characters (it has {len(offer)}).")
    if _UNSAFE_OFFER.search(text or ""):
        raise OfferError("The offer must be plain text: no links, line breaks or markup.")
    return offer


def facts_for(session: Session, event_id: str, offer: str, *, allow_past: bool = False) -> Facts:
    event = session.get(CafeEvent, event_id)
    if event is None:
        raise EventNotPromotable("Event not found.", 404)
    if event.status != "upcoming" and not allow_past:
        raise EventNotPromotable("This event has already happened; only upcoming events can be promoted.", 409)
    cafe = session.get(Cafe, event.cafe_id)
    tz = ZoneInfo(cafe.timezone if cafe else "UTC")
    return Facts(event_id=event.event_id, title=event.name, category=event.event_type,
                 starts=event.starts_at.astimezone(tz), ends=event.ends_at.astimezone(tz), location=event.location,
                 cafe_name=cafe.name if cafe else "Our café", offer=offer)


# ---------------------------------------------------------------------------
# Caption: Muse writes, code checks
# ---------------------------------------------------------------------------

CAPTION_PROMPT = """Write an Instagram caption for a small independent café promoting a local event.
Use ONLY these facts. Do not add any other number, date, time, price or day.
{facts}
Tone: {tone}. At most 3 short sentences, no hashtags inside the caption, no emoji.
Then give 5 to 8 hashtags (letters and digits only, each starting with #).
Return ONLY a JSON object, no prose: {{"caption": str, "hashtags": [str]}}"""
TONES = ["warm and neighbourly", "short, upbeat and inviting"]


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def check_caption(caption: str, hashtags: list[str], facts: Facts) -> list[str]:
    """What's wrong with a model caption; empty means it can be used as is."""
    problems: list[str] = []
    text = " ".join(caption.split())
    known = facts.text()
    known_numbers = set(re.findall(r"\d+", known))
    known_words = _words(known)
    everything = f"{text} {' '.join(hashtags)}"

    for number in sorted(set(re.findall(r"\d+", everything)) - known_numbers):
        problems.append(f"number {number} isn't in the event or the offer")
    words = _words(text)
    for word in NUMBER_WORDS:
        if word in words and word not in known_words:
            problems.append(f'"{word}" isn\'t in the event or the offer')
    month, weekday = MONTHS[facts.starts.month - 1], WEEKDAYS[facts.starts.weekday()]
    for name in MONTHS:
        if name != month and (name in words or (name[:3] in words and name[:3] not in ("mar", "may"))):
            problems.append(f"mentions {name.title()}, but the event is in {month.title()}")
    for name in WEEKDAYS:
        if name != weekday and (name in words or name[:3] in words):
            problems.append(f"mentions {name.title()}, but the event is on a {weekday.title()}")
    for phrase in RELATIVE_DAYS:
        if re.search(rf"\b{phrase}\b", text, re.IGNORECASE):
            problems.append(f'"{phrase}" depends on when the post goes up')
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not text:
        problems.append("empty caption")
    elif len(sentences) > 3:
        problems.append(f"{len(sentences)} sentences (at most 3)")
    if "#" in text:
        problems.append("hashtags inside the caption")
    if not 5 <= len(hashtags) <= 8:
        problems.append(f"{len(hashtags)} hashtags (5 to 8)")
    return problems


def clean_hashtags(raw: Any) -> list[str]:
    tags: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        tag = "#" + str(item).strip().lstrip("#")
        if _HASHTAG.match(tag) and tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
    return tags


def _camel(text: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z0-9]+", text))


def code_caption(facts: Facts) -> tuple[str, list[str]]:
    """The plain caption code writes when the model's doesn't pass (or doesn't answer)."""
    where = f" at {facts.place}" if facts.place else ""
    offer = facts.offer.rstrip(" .!")
    caption = (f"Join us for {facts.title} on {facts.date_line}, {clock(facts.starts)} to {clock(facts.ends)}{where}. "
               f"{offer[:1].upper()}{offer[1:]}. See you at {facts.cafe_name}!")
    tags = ["#" + _camel(facts.title), CATEGORY_TAGS.get(facts.category, "#LocalEvents")]
    if facts.place and "santa clara" in facts.place.lower():
        tags.append("#SantaClara")
    tags += ["#" + _camel(facts.cafe_name), "#Coffee", "#Pastries", "#SupportLocal", "#ShopLocal"]
    return caption, clean_hashtags(tags)[:8]


@dataclass
class CaptionResult:
    caption: str
    hashtags: list[str]
    source: str  # "muse" | "code"
    note: str | None
    model: str | None
    latency_ms: int


def write_caption(client: Any, model: str, facts: Facts, variant: int) -> CaptionResult:
    facts_lines = "\n".join(f"- {k}: {v}" for k, v in [
        ("event", facts.title), ("type", facts.category.replace("_", " ")), ("date", facts.date_line),
        ("hours", facts.time_line), ("place", facts.location or "nearby"), ("café", facts.cafe_name),
        ("offer", facts.offer)])
    prompt = CAPTION_PROMPT.format(facts=facts_lines, tone=TONES[variant % len(TONES)])
    summary = f"Promo: caption for {facts.title}"
    try:
        response, latency = call_model(_timed(client, CAPTION_TIMEOUT_S), model=model, temperature=0.7,
                                       messages=[{"role": "user", "content": prompt}])
    except Exception as error:
        latency = getattr(error, "latency_ms", 0)
        record_ai_call(cafe_id=None, purpose="other", model=model, latency_ms=latency, request=summary,
                       outcome="timeout" if is_timeout(error) else "failed", error=f"{type(error).__name__}: {error}")
        caption, tags = code_caption(facts)
        return CaptionResult(caption, tags, "code", f"Muse didn't answer ({type(error).__name__})", None, latency)
    record_ai_call(cafe_id=None, purpose="other", model=model, latency_ms=latency, request=summary, outcome="ok")
    text = response.choices[0].message.content or ""
    match = _FENCE.match(text)
    try:
        answer = json.loads(match.group(1) if match else text)
    except json.JSONDecodeError:
        answer = {}
    caption = " ".join(str(answer.get("caption") or "").split()) if isinstance(answer, dict) else ""
    tags = clean_hashtags(answer.get("hashtags")) if isinstance(answer, dict) else []
    problems = check_caption(caption, tags, facts) if caption else ["unreadable answer"]
    if problems:
        code, code_tags = code_caption(facts)
        return CaptionResult(code, code_tags, "code", "Muse's caption was replaced: " + "; ".join(problems[:3]),
                             model, latency)
    return CaptionResult(caption, tags, "muse", None, model, latency)


# ---------------------------------------------------------------------------
# Background: Muse Image, else drawn by code
# ---------------------------------------------------------------------------


def image_prompt(facts: Facts, variant: int) -> str:
    title = facts.title.lower()
    scene = next((s for k, s in KEYWORD_SCENES.items() if k in title), None) or SCENES.get(
        facts.category, "a cozy independent café counter with coffee and pastries")
    return f"{scene}, {STYLES[variant % len(STYLES)]}, photographic, square composition, {NO_TEXT}"


@dataclass
class BackgroundResult:
    image: Image.Image
    source: str  # "muse-image" | "code-drawn"
    note: str | None
    model: str | None
    latency_ms: int
    attempts: int


def _timed(client: Any, seconds: float) -> Any:
    with_options = getattr(client, "with_options", None)
    return with_options(timeout=seconds, max_retries=0) if callable(with_options) else client


def muse_background(client: Any, model: str, facts: Facts, variant: int) -> BackgroundResult:
    prompt = image_prompt(facts, variant)
    summary = f"Promo: background for {facts.title}"
    total, last_error = 0, None
    for attempt in range(1, IMAGE_TRIES + 1):
        try:
            response, latency = call_image(
                _timed(client, IMAGE_TIMEOUT_S), model=model, prompt=prompt, n=1, size="1024x1024",
                output_format="png", response_format="b64_json",
                extra_body={"tool_enablement": {"enable_image_search": False, "enable_web_search": False,
                                                "enable_shell": False}})
            total += latency
            picture = Image.open(io.BytesIO(base64.b64decode(response.data[0].b64_json))).convert("RGB")
            record_ai_call(cafe_id=None, purpose="other", model=model, latency_ms=total, request=summary,
                           outcome="ok" if attempt == 1 else "retried", attempts=attempt)
            return BackgroundResult(picture, "muse-image", None, model, total, attempt)
        except Exception as error:  # timeout, API error or an unreadable image: retry once, then draw
            total += getattr(error, "latency_ms", 0)
            last_error = error
    record_ai_call(cafe_id=None, purpose="other", model=model, latency_ms=total, request=summary,
                   outcome="timeout" if last_error is not None and is_timeout(last_error) else "failed",
                   attempts=IMAGE_TRIES, error=f"{type(last_error).__name__}: {last_error}")
    note = f"Muse Image didn't deliver after {IMAGE_TRIES} tries ({type(last_error).__name__}); drawn by code"
    return BackgroundResult(drawn_background(facts, variant), "code-drawn", note, None, total, IMAGE_TRIES)


SEASONS = {  # month → (top colour, bottom colour, accent)
    (9, 10, 11): ((92, 46, 24), (196, 110, 44), (246, 196, 120)),
    (12, 1, 2): ((18, 52, 44), (120, 28, 36), (250, 220, 150)),
    (3, 4, 5): ((40, 96, 82), (226, 170, 120), (255, 236, 200)),
    (6, 7, 8): ((22, 84, 120), (240, 168, 88), (255, 240, 190)),
}


def drawn_background(facts: Facts, variant: int) -> Image.Image:
    """A seasonal gradient with soft light circles: the fallback when Muse Image can't deliver."""
    top, bottom, accent = next(v for k, v in SEASONS.items() if facts.starts.month in k)
    if variant % 2:
        top, bottom = bottom, top
    picture = Image.new("RGB", (SIZE, SIZE))
    draw = ImageDraw.Draw(picture)
    for y in range(SIZE):
        t = y / (SIZE - 1)
        draw.line([(0, y), (SIZE, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    lights = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    glow = ImageDraw.Draw(lights)
    rng = random.Random(f"{facts.event_id}:{variant}")
    for _ in range(26):
        r = rng.randint(18, 90)
        x, y = rng.randint(-40, SIZE + 40), rng.randint(-40, int(SIZE * 0.6))
        glow.ellipse([x - r, y - r, x + r, y + r], fill=(*accent, rng.randint(40, 110)))
    picture.paste(lights.filter(ImageFilter.GaussianBlur(14)), (0, 0), lights.filter(ImageFilter.GaussianBlur(14)))
    return picture


# ---------------------------------------------------------------------------
# Composition: every word on the image is set by code
# ---------------------------------------------------------------------------


def font(name: str, size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:  # missing or unreadable font file: Pillow's own
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # Pillow < 10.1
            return ImageFont.load_default()


def _width(draw: ImageDraw.ImageDraw, text: str, face: Any) -> float:
    return draw.textlength(text, font=face)


def fit(draw: ImageDraw.ImageDraw, text: str, name: str, start: int, smallest: int, width: int,
        max_lines: int) -> tuple[Any, list[str]]:
    """Largest size from start down to smallest at which the text wraps into max_lines within width.
    At the smallest size, overflow is cut with an ellipsis so nothing ever runs off the image."""
    for size in range(start, smallest - 1, -2):
        face = font(name, size)
        lines = _wrap(draw, text, face, width)
        if len(lines) <= max_lines and all(_width(draw, l, face) <= width for l in lines):
            return face, lines
    face = font(name, smallest)
    wrapped = _wrap(draw, text, face, width)
    lines = wrapped[:max_lines]
    if len(wrapped) > max_lines:
        last = lines[-1]
        while last and _width(draw, last + "…", face) > width:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return face, lines


def _wrap(draw: ImageDraw.ImageDraw, text: str, face: Any, width: int) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        if lines and _width(draw, f"{lines[-1]} {word}", face) <= width:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            while _width(draw, word, face) > width and len(word) > 1:  # one very long word: break it
                cut = max(1, math.floor(len(word) * width / _width(draw, word, face)) - 1)
                lines.append(word[:cut] + "-")
                word = word[cut:]
            lines.append(word)
    return lines or [""]


def _cover(picture: Image.Image) -> Image.Image:
    w, h = picture.size
    side = min(w, h)
    return picture.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2)).resize(
        (SIZE, SIZE), Image.LANCZOS)


def compose(background: Image.Image, facts: Facts) -> tuple[Image.Image, list[tuple[str, tuple[int, int, int, int]]]]:
    """The final 1080×1080 post. Returns the image and every text box (for tests: nothing overflows)."""
    picture = _cover(background.convert("RGB")).convert("RGBA")
    draw = ImageDraw.Draw(picture)
    width = SIZE - 2 * MARGIN
    boxes: list[tuple[str, tuple[int, int, int, int]]] = []

    def put(label: str, lines: list[str], face: Any, y: int, fill=(255, 255, 255), gap: int = 8) -> int:
        for line in lines:
            box = draw.textbbox((MARGIN, y), line, font=face)
            draw.text((MARGIN, y), line, font=face, fill=fill)
            boxes.append((label, box))
            y = box[3] + gap
        return y

    # Café name, top-left, on a dark pill
    name_face, name_lines = fit(draw, facts.cafe_name, "Lato-Bold.ttf", 34, 24, width - 40, 1)
    nb = draw.textbbox((MARGIN + 20, MARGIN), name_lines[0], font=name_face)
    draw.rounded_rectangle([nb[0] - 20, nb[1] - 14, nb[2] + 20, nb[3] + 14], radius=28, fill=(18, 12, 8, 190))
    draw.text((MARGIN + 20, MARGIN), name_lines[0], font=name_face, fill=(255, 255, 255))
    boxes.append(("cafe", (nb[0] - 20, nb[1] - 14, nb[2] + 20, nb[3] + 14)))

    # Bottom block, laid out top-down from a computed start so it always ends inside the margin
    head_face, head_lines = fit(draw, facts.title, "Lato-Bold.ttf", 88, 48, width, 3)
    when_face = font("Lato-Bold.ttf", 44)
    place_face, place_lines = fit(draw, facts.place or "", "Lato-Regular.ttf", 34, 26, width, 1)
    offer_face, offer_lines = fit(draw, facts.offer, "Lato-Bold.ttf", 40, 28, width - 56, 2)
    line_h = lambda face: draw.textbbox((0, 0), "Ag", font=face)[3] + 8  # noqa: E731
    offer_h = len(offer_lines) * line_h(offer_face) + 36
    block = (len(head_lines) * line_h(head_face) + 18 + 2 * line_h(when_face)
             + (line_h(place_face) if facts.place else 0) + 28 + offer_h)
    y = SIZE - MARGIN - block
    # Shade under the text: a soft ramp just above the headline, then dark enough for white text
    # (at least 4.5:1 even on a white background). A light veil keeps the top of the photo visible.
    shade = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    paint = ImageDraw.Draw(shade)
    ramp = 170
    for row in range(SIZE):
        t = min(1.0, max(0.0, (row - (y - ramp)) / ramp))
        paint.line([(0, row), (SIZE, row)], fill=(18, 12, 8, round(36 + 184 * t)))
    picture.alpha_composite(shade)
    draw = ImageDraw.Draw(picture)
    nb_box = next(box for label, box in boxes if label == "cafe")  # redraw the café pill over the veil
    draw.rounded_rectangle(nb_box, radius=28, fill=(18, 12, 8, 190))
    draw.text((MARGIN + 20, MARGIN), name_lines[0], font=name_face, fill=(255, 255, 255))
    y = put("headline", head_lines, head_face, y) + 18
    y = put("date", [facts.date_line], when_face, y, fill=(255, 226, 170))
    y = put("time", [facts.time_line], when_face, y, fill=(255, 226, 170))
    if facts.place:
        y = put("place", place_lines, place_face, y, fill=(236, 228, 218))
    y += 28
    pill = [MARGIN, y, MARGIN + max(_width(draw, l, offer_face) for l in offer_lines) + 56, y + offer_h - 8]
    draw.rounded_rectangle(pill, radius=24, fill=(250, 206, 120))
    oy = y + 14
    for line in offer_lines:
        box = draw.textbbox((MARGIN + 28, oy), line, font=offer_face)
        draw.text((MARGIN + 28, oy), line, font=offer_face, fill=(40, 24, 10))
        boxes.append(("offer", box))
        oy = box[3] + 8
    boxes.append(("offer-pill", tuple(round(v) for v in pill)))  # type: ignore[arg-type]
    return picture.convert("RGB"), boxes


# ---------------------------------------------------------------------------
# Drafts: files only; nothing is posted
# ---------------------------------------------------------------------------


def _jpeg(picture: Image.Image, limit_kb: int | None = None) -> bytes:
    for quality in (88, 82, 76, 70, 64, 58, 52):
        out = io.BytesIO()
        picture.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
        if limit_kb is None or out.tell() <= limit_kb * 1024:
            return out.getvalue()
    return out.getvalue()


def _draft_out(draft: dict[str, Any]) -> dict[str, Any]:
    return {**draft, "image_url": f"/api/v1/agent/promo-posts/{draft['id']}/image"}


def make_variant(client: Any, facts: Facts, variant: int, *, caption_model: str, image_model: str) -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=2) as pool:  # caption and background don't depend on each other
        caption_job = pool.submit(write_caption, client, caption_model, facts, variant)
        background_job = pool.submit(muse_background, client, image_model, facts, variant)
        caption, background = caption_job.result(), background_job.result()
    picture, _ = compose(background.image, facts)
    return {
        "variant": variant + 1, "caption": caption.caption, "hashtags": caption.hashtags,
        "caption_source": caption.source, "caption_note": caption.note,
        "image_source": background.source, "image_note": background.note,
        "model": {"caption": caption.model, "image": background.model},
        "latency_ms": {"caption": caption.latency_ms, "image": background.latency_ms,
                       "image_attempts": background.attempts},
        "_jpeg": _jpeg(picture),
    }


def create_drafts(client: Any, facts: Facts, *, variants: int = VARIANTS, folder: Path | None = None) -> list[dict]:
    settings = get_settings()
    folder = folder or DRAFT_DIR
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=variants) as pool:
        made = list(pool.map(lambda v: make_variant(client, facts, v, caption_model=settings.vision_model,
                                                    image_model=settings.image_model), range(variants)))
    drafts = []
    for item in made:
        draft_id = f"PROMO-{uuid4().hex[:12].upper()}"
        jpeg = item.pop("_jpeg")
        draft = {
            "id": draft_id, "status": "draft", "label": DRAFT_LABEL, "sample": False,
            "event_id": facts.event_id, "event_title": facts.title, "offer_text": facts.offer,
            "created_at": datetime.now(timezone.utc).isoformat(), **item,
            "total_ms": int((time.monotonic() - started) * 1000),
        }
        target = folder / draft_id
        target.mkdir(parents=True, exist_ok=True)
        (target / "image.jpg").write_bytes(jpeg)
        (target / "draft.json").write_text(json.dumps(draft, indent=2, ensure_ascii=False))
        drafts.append(_draft_out(draft))
    return drafts


def list_drafts(folder: Path | None = None, samples: Path | None = None) -> list[dict[str, Any]]:
    """The owner's drafts (newest first), then the committed demo samples."""
    folder, samples = folder or DRAFT_DIR, samples or SAMPLE_DIR
    drafts = []
    for path in folder.glob("PROMO-*/draft.json") if folder.exists() else []:
        try:
            drafts.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    drafts.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    manifest = samples / "samples.json"
    sample_list = json.loads(manifest.read_text()) if manifest.exists() else []
    return [_draft_out(d) for d in drafts + sample_list]


def image_path(draft_id: str, folder: Path | None = None, samples: Path | None = None) -> Path | None:
    if not _ID.match(draft_id):
        return None
    folder, samples = folder or DRAFT_DIR, samples or SAMPLE_DIR
    path = (samples / f"{draft_id.lower()}.jpg") if draft_id.startswith("SAMPLE-") else folder / draft_id / "image.jpg"
    return path if path.is_file() else None
