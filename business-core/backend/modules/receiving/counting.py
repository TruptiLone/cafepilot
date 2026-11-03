"""Count a delivery photo with SAM 3.1 + Muse Spark; code adds up the totals.

For each kind of item on the order (or the slip, when there is no order):
  1. SAM 3.1 finds every item for a short name code picks ("milk carton", "muffin").
  2. Code drops weak and duplicate boxes, numbers them, and flags unusually big or small ones.
  3. Muse Spark checks the numbered boxes: one item? two merged? not an item? a duplicate? which
     product (only when several look alike)? and points at items without a box.
  4. SAM confirms each pointed spot; only a new, confident box counts.
  5. Code totals each product. It is "confident" only when SAM and Spark agree and SAM is sure.

The result has the same shape as Muse's plain photo read (``PhotoRead``), so ``service.recompare``
compares it with the order unchanged. A count that isn't confident is marked "may hide more", so a
shortfall becomes "please count it" instead of a short delivery.
"""

from __future__ import annotations

import base64
import io
import json
import re
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from backend.modules.agent.ai_calls import call_model, is_timeout, record as record_ai_call
from backend.modules.receiving.vision import PhotoItem, PhotoRead, VisionError

SAM_MODEL = "sam-3.1"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

# What SAM looks for, per ingredient: a short noun phrase, one concept per request. Items that
# look alike share a phrase (one SAM call); Spark then says which product each box is.
NOUNS = {
    "WHOLE_MILK": "milk carton",
    "OAT_MILK": "milk carton",
    "SKIM_MILK": "milk carton",
    "ALMOND_MILK": "milk carton",
    "BANANA_NUT_MUFFIN": "muffin",
    "BLUEBERRY_MUFFIN": "muffin",
    "CHOCOLATE_CHIP_MUFFIN": "muffin",
    "BUTTER_CROISSANT": "croissant",
    "BANANA_BREAD": "loaf of bread",
    "LEMON_LOAF": "loaf of bread",
}

MIN_CONFIDENCE = 0.5  # SAM matches below this are dropped
SURE = 0.8  # every box at least this sure for a "confident" count
DUPLICATE_IOU = 0.7  # two boxes covering nearly the same spot are one item
BIG, SMALL = 1.8, 0.35  # box area vs the median: maybe two merged items / a fragment
MAX_PER_BOX = 4  # cap on Spark's "items in this box"

_BOX = re.compile(r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=([\d.]+))?\|>")
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

CHECK_PROMPT = """A detector drew numbered red boxes (1 to {n}) on this photo of a café delivery, one per
{noun} it found.
{flags}Check its work. For each numbered box, say how many separate {noun}s it covers: 1 normally,
2 or more if it merged touching {noun}s, 0 if it is not a {noun}. If a box covers the same {noun} as an
earlier box, set duplicate_of to that box's number.{choose}
Then list every {noun} that has NO box at all, with its approximate center as a percentage of the photo
width and height (0 = left/top, 100 = right/bottom).
Return ONLY a JSON object, no prose:
{{"boxes": [{{"n": 1, "items": 1, "duplicate_of": null{product_field}}}],
 "missed": [{{"x_pct": number, "y_pct": number{product_field}, "note": str}}],
 "confidence": "high" | "medium" | "low"}}"""

CHOOSE = """
Say which product each box is, using exactly one of these names: {names}. Read the label if you can."""


@dataclass
class Product:
    """One thing to count: the order (or slip) line's product and the phrase SAM looks for."""

    name: str
    noun: str


@dataclass
class GroupCount:
    """What happened for one SAM phrase (one or more products that look alike)."""

    noun: str
    products: list[str]
    counts: dict[str, int] = field(default_factory=dict)
    sam_boxes: int = 0
    min_conf: float | None = None
    missed_pointed: int = 0
    missed_confirmed: int = 0
    check_confidence: str | None = None
    confident: bool = False
    seconds: float = 0.0
    error: str | None = None


def noun_for(ingredient_id: str | None, description: str) -> str:
    """Code picks SAM's phrase: the table first, else the product name ('Plain Bagels, dozen' → 'plain bagel')."""
    if ingredient_id in NOUNS:
        return NOUNS[ingredient_id]
    name = description.split(",")[0].strip().lower()
    return name[:-1] if name.endswith("s") and not name.endswith("ss") else name


def _data_url(image: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(image).decode()}"


def _area(box: dict) -> int:
    return (box["x2"] - box["x1"]) * (box["y2"] - box["y1"])


def _iou(a: dict, b: dict) -> float:
    ix = max(0, min(a["x2"], b["x2"]) - max(a["x1"], b["x1"]))
    iy = max(0, min(a["y2"], b["y2"]) - max(a["y1"], b["y1"]))
    inter = ix * iy
    union = _area(a) + _area(b) - inter
    return inter / union if union else 0.0


def sam(client: Any, image: bytes, mime: str, prompt: str, summary: str) -> tuple[list[dict], float]:
    """Run SAM 3.1 with a phrase or a point; return its boxes (with confidence) and the seconds it took."""
    started = time.monotonic()
    try:
        response = client.responses.create(
            model=SAM_MODEL,
            metadata={"include_confidence": "true", "mask_encoding": "one_bit"},
            input=[{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": _data_url(image, mime)},
            ]}],
        )
    except Exception as error:
        seconds = time.monotonic() - started
        record_ai_call(cafe_id=None, purpose="other", model=SAM_MODEL, latency_ms=int(seconds * 1000), request=summary,
                       outcome="timeout" if is_timeout(error) else "failed", error=f"{type(error).__name__}: {error}")
        raise VisionError(f"SAM couldn't read the photo: {str(error).splitlines()[0][:200]}") from error
    seconds = time.monotonic() - started
    record_ai_call(cafe_id=None, purpose="other", model=SAM_MODEL, latency_ms=int(seconds * 1000), request=summary, outcome="ok")
    boxes = [
        {"x1": int(m[1]), "y1": int(m[2]), "x2": int(m[3]), "y2": int(m[4]), "c": float(m[7]) if m[7] else None}
        for m in _BOX.finditer(getattr(response, "output_text", "") or "")
    ]
    return boxes, seconds


def clean(boxes: list[dict]) -> list[dict]:
    """Drop weak matches and duplicates; number top to bottom; flag boxes much bigger or smaller than the rest."""
    kept = sorted((b for b in boxes if b["c"] is None or b["c"] >= MIN_CONFIDENCE), key=lambda b: -(b["c"] or 0))
    unique: list[dict] = []
    for box in kept:
        if all(_iou(box, other) < DUPLICATE_IOU for other in unique):
            unique.append(box)
    unique.sort(key=lambda b: (b["y1"], b["x1"]))
    median = statistics.median(_area(b) for b in unique) if unique else 0
    for number, box in enumerate(unique, start=1):
        box["n"] = number
        ratio = _area(box) / median if median else 1
        box["flag"] = "unusually large" if ratio > BIG else "unusually small" if ratio < SMALL else None
    return unique


def numbered_image(image: bytes, boxes: list[dict]) -> bytes:
    picture = Image.open(io.BytesIO(image)).convert("RGB")
    draw = ImageDraw.Draw(picture)
    size = max(24, picture.width // 40)
    try:
        font: Any = ImageFont.truetype(FONT, size)
    except OSError:
        font = ImageFont.load_default()
    for box in boxes:
        draw.rectangle([box["x1"], box["y1"], box["x2"], box["y2"]], outline=(230, 30, 30), width=max(3, size // 8))
        label = str(box["n"])
        left, top, right, bottom = draw.textbbox((box["x1"] + 6, box["y1"] + 4), label, font=font)
        draw.rectangle([left - 6, top - 4, right + 6, bottom + 4], fill=(230, 30, 30))
        draw.text((box["x1"] + 6, box["y1"] + 4), label, fill="white", font=font)
    out = io.BytesIO()
    picture.save(out, format="PNG")
    return out.getvalue()


def check(client: Any, model: str, marked: bytes, boxes: list[dict], noun: str, names: list[str]) -> dict:
    """Muse Spark checks SAM's numbered boxes. Its answer is only read; code does the totals."""
    flags = "".join(f"Box {b['n']} is {b['flag']} compared with the others. " for b in boxes if b["flag"])
    several = len(names) > 1
    prompt = CHECK_PROMPT.format(
        n=len(boxes), noun=noun, flags=f"Note: {flags}\n" if flags else "",
        choose=CHOOSE.format(names=", ".join(f'"{n}"' for n in names)) if several else "",
        product_field=', "product": str' if several else "",
    )
    summary = f"Photo count: Muse checks the {noun} boxes"
    try:
        response, latency = call_model(
            client, model=model, temperature=0,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": _data_url(marked, "image/png")}},
            ]}],
        )
    except Exception as error:
        record_ai_call(cafe_id=None, purpose="other", model=model, latency_ms=getattr(error, "latency_ms", 0), request=summary,
                       outcome="timeout" if is_timeout(error) else "failed", error=f"{type(error).__name__}: {error}")
        raise VisionError(f"Muse couldn't check the count: {str(error).splitlines()[0][:200]}") from error
    record_ai_call(cafe_id=None, purpose="other", model=model, latency_ms=latency, request=summary, outcome="ok")
    text = response.choices[0].message.content or ""
    match = _FENCE.match(text)
    try:
        answer = json.loads(match.group(1) if match else text)
    except json.JSONDecodeError:
        return {}
    return answer if isinstance(answer, dict) else {}


def _pick(name: Any, names: list[str]) -> str:
    """Spark's product name, only if it is one of the choices code gave it (else the first choice)."""
    if isinstance(name, str):
        for choice in names:
            if name.strip().lower() == choice.lower():
                return choice
    return names[0]


def count_group(client: Any, model: str, image: bytes, mime: str, noun: str, names: list[str]) -> GroupCount:
    result = GroupCount(noun=noun, products=names, counts={n: 0 for n in names})
    width, height = Image.open(io.BytesIO(image)).size
    raw, seconds = sam(client, image, mime, noun, f"Photo count: SAM finds each {noun}")
    boxes = clean(raw)
    result.sam_boxes = len(boxes)
    confidences = [b["c"] for b in boxes if b["c"] is not None]
    result.min_conf = round(min(confidences), 3) if confidences else None
    if not boxes:
        result.seconds = round(seconds, 1)
        return result  # nothing found: zero, not confident, so the owner is asked to count

    started = time.monotonic()
    answer = check(client, model, numbered_image(image, boxes), boxes, noun, names)
    seconds += time.monotonic() - started
    result.check_confidence = answer.get("confidence") if isinstance(answer.get("confidence"), str) else None
    verdicts = {v.get("n"): v for v in answer.get("boxes") or [] if isinstance(v, dict)}
    for box in boxes:
        verdict = verdicts.get(box["n"], {"items": 1})  # no verdict: keep SAM's box
        if verdict.get("duplicate_of"):
            continue
        try:
            items = max(0, min(int(verdict.get("items", 1)), MAX_PER_BOX))
        except (TypeError, ValueError):
            items = 1
        result.counts[_pick(verdict.get("product"), names)] += items

    missed = [m for m in answer.get("missed") or [] if isinstance(m, dict)]
    result.missed_pointed = len(missed)
    added: list[dict] = []
    for spot in missed:
        try:
            x = round(float(spot["x_pct"]) / 100 * width)
            y = round(float(spot["y_pct"]) / 100 * height)
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= x < width and 0 <= y < height):
            continue
        found, took = sam(client, image, mime, f"<0f>1<|point;x={x};y={y};w={width};h={height}|>",
                          f"Photo count: SAM checks a {noun} Muse pointed at")
        seconds += took
        for box in found:
            if (box["c"] is None or box["c"] >= MIN_CONFIDENCE) and all(_iou(box, o) < 0.5 for o in boxes + added):
                added.append(box)
                result.counts[_pick(spot.get("product"), names)] += 1
                break
    result.missed_confirmed = len(added)

    agree = sum(result.counts.values()) == len(boxes) and not missed and bool(verdicts)
    result.confident = agree and (result.min_conf or 0) >= SURE and result.check_confidence != "low"
    result.seconds = round(seconds, 1)
    return result


def count_delivery(client: Any, model: str, image: bytes, mime: str, products: list[Product]) -> tuple[PhotoRead, list[GroupCount]]:
    """Count each product in the photo. Returns a PhotoRead for the receiving comparison, plus the details."""
    groups: dict[str, list[str]] = {}
    for product in products:
        names = groups.setdefault(product.noun, [])
        if product.name not in names:
            names.append(product.name)

    items: list[PhotoItem] = []
    details: list[GroupCount] = []
    for noun, names in groups.items():
        try:
            group = count_group(client, model, image, mime, noun, names)
        except VisionError as error:
            group = GroupCount(noun=noun, products=names, counts={n: 0 for n in names}, error=str(error))
        details.append(group)
        for name in names:
            items.append(PhotoItem(
                product=name, label_text=None, visible_units=group.counts.get(name, 0),
                container=None, pack_label=None, possibly_hidden_units=not group.confident,
                confidence="high" if group.confident else "low",
            ))
    notes = "; ".join(
        f"{g.noun}: " + (f"couldn't count ({g.error})" if g.error else
                         f"SAM {g.sam_boxes}, total {sum(g.counts.values())}, "
                         + ("confident" if g.confident else "ask owner to count"))
        for g in details
    )
    return PhotoRead(items=items, visible_damage=None, notes=f"Counted with SAM 3.1 + Muse Spark. {notes}"), details
