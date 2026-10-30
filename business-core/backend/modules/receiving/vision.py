"""Muse Spark reads the packing slip and the delivery photo (images from glasses or a phone).

The model only transcribes what it sees into JSON. Matching to the order, unit conversion and
every decision about what to accept happen in code (``service.py``).
"""

from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from backend.modules.agent.planner import _log_call  # shared model-call log

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


class SlipLine(BaseModel):
    item_number: str | None = None
    description: str | None = None
    pack: str | None = None
    ordered: float | None = None
    shipped: float | None = None
    backordered: float | None = None


class SlipRead(BaseModel):
    supplier: str | None = None
    packing_slip_number: str | None = None
    customer_po_number: str | None = None
    delivery_date: str | None = None
    delivery_window: str | None = None
    lines: list[SlipLine] = Field(default_factory=list)
    total_cases_shipped: float | None = None
    notes: str | None = None


class PhotoItem(BaseModel):
    product: str | None = None
    label_text: str | None = None
    visible_units: float = 0
    container: str | None = None
    pack_label: str | None = None
    possibly_hidden_units: bool = False
    confidence: str | None = None


class PhotoRead(BaseModel):
    items: list[PhotoItem] = Field(default_factory=list)
    visible_damage: str | None = None
    notes: str | None = None


class VisionError(RuntimeError):
    """The model could not read the image (API error or unreadable answer)."""


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _image_part(image: bytes, mime: str) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(image).decode()}"}}


def _ask(client: Any, model: str, purpose: str, prompt: str, image: bytes, mime: str) -> tuple[dict, int]:
    started = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, _image_part(image, mime)]}],
            temperature=0,
        )
    except Exception as error:
        _log_call({"at": datetime.now(timezone.utc).isoformat(), "purpose": purpose, "model": model,
                   "error": f"{type(error).__name__}: {error}"})
        raise VisionError(f"Muse couldn't read the image: {str(error).splitlines()[0][:200]}") from error
    latency = round((time.monotonic() - started) * 1000)
    text = response.choices[0].message.content or ""
    _log_call({"at": datetime.now(timezone.utc).isoformat(), "purpose": purpose, "model": model,
               "latency_ms": latency, "response": text})
    match = _FENCE.match(text)
    try:
        return json.loads(match.group(1) if match else text), latency
    except json.JSONDecodeError as error:
        raise VisionError("Muse's answer wasn't readable JSON; try a clearer photo.") from error


def read_slip(client: Any, model: str, image: bytes, mime: str) -> tuple[SlipRead, int]:
    data, latency = _ask(client, model, "read_slip", SLIP_PROMPT, image, mime)
    try:
        return SlipRead.model_validate(data), latency
    except ValidationError as error:
        raise VisionError("Muse read the slip but the fields didn't make sense; try again.") from error


def read_photo(client: Any, model: str, image: bytes, mime: str) -> tuple[PhotoRead, int]:
    data, latency = _ask(client, model, "read_delivery_photo", PHOTO_PROMPT, image, mime)
    try:
        return PhotoRead.model_validate(data), latency
    except ValidationError as error:
        raise VisionError("Muse read the photo but the counts didn't make sense; try again.") from error
