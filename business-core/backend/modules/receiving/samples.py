"""Sample photos a visitor can pick instead of taking their own (demo-assets/receiving/samples).

Real photos from the recorded demo, cropped to the slip or the counter. They follow the market story:
the plan's order PO-123 (Willow Ridge) arrives one half gallon short; the reorder PO-124 (Bay Dairy)
brings the last one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from backend.modules.receiving.service import ROOT

SAMPLES_DIR = ROOT / "demo-assets" / "receiving" / "samples"


@dataclass(frozen=True)
class Sample:
    sample_id: str
    kind: Literal["slip", "photo"]
    po_number: str
    title: str
    file: str
    mime: str = "image/jpeg"

    @property
    def path(self) -> Path:
        return SAMPLES_DIR / self.file


SAMPLES: tuple[Sample, ...] = (
    Sample("slip-po-123", "slip", "PO-123", "Willow Ridge slip for PO-123", "slip-PO-123-willow-ridge.jpg"),
    Sample("photo-po-123", "photo", "PO-123", "What arrived for PO-123", "delivery-PO-123-short.jpg"),
    Sample("slip-po-124", "slip", "PO-124", "Bay Dairy slip for PO-124", "slip-PO-124-bay-dairy.jpg"),
    Sample("photo-po-124", "photo", "PO-124", "What arrived for PO-124", "delivery-PO-124.jpg"),
)
_BY_ID = {s.sample_id: s for s in SAMPLES}


def get_sample(sample_id: str) -> Sample | None:
    return _BY_ID.get(sample_id)
