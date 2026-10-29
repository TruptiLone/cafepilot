"""Events Muse has detected near the café: the trigger that starts a plan.

For the hackathon the feed is a small built-in list, clearly labeled as demo data. A real
feed (city events calendar, Muse web search) can replace ``detected_events`` later without
changing the API.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from backend.modules.sales.schemas import SaleItemV2Create


class ExpectedItem(BaseModel):
    label: str = Field(description="Plain words for the owner, e.g. '40 extra whole-milk lattes'.")
    item: SaleItemV2Create


class DetectedEvent(BaseModel):
    event_id: str
    cafe_id: str
    title: str
    category: str
    starts_at: datetime
    ends_at: datetime
    location: str
    distance_note: str | None = None
    source: str
    is_demo: bool = Field(description="True when the event comes from built-in demo data.")
    detected_at: datetime
    knowledge_as_of: datetime = Field(description="Stock and staffing are checked as of this time.")
    expected_demand: list[ExpectedItem]
    demand_basis: str = Field(description="Why Muse expects this demand, in plain words.")
    muse_message: str = Field(description="Muse's opening line in the chat.")
    request: str = Field(description="The request sent to the planner when the owner says yes.")


_EVENTS: list[DetectedEvent] = [
    DetectedEvent(
        event_id="EVT_SC_FARMERS_MARKET_20261101",
        cafe_id="CAFE001",
        title="Santa Clara Farmers Market",
        category="farmers_market",
        starts_at=datetime.fromisoformat("2026-11-01T08:00:00-08:00"),
        ends_at=datetime.fromisoformat("2026-11-01T14:00:00-08:00"),
        location="Franklin Square, Santa Clara",
        distance_note="two blocks from the café",
        source="Santa Clara community events (demo)",
        is_demo=True,
        detected_at=datetime.fromisoformat("2026-10-29T17:30:00-07:00"),
        knowledge_as_of=datetime.fromisoformat("2026-10-29T18:00:00-07:00"),
        expected_demand=[
            ExpectedItem(
                label="40 extra whole-milk lattes",
                item=SaleItemV2Create(variation_id="COF003_REG", quantity=40),
            ),
            ExpectedItem(
                label="20 extra oat-milk lattes",
                item=SaleItemV2Create(
                    variation_id="COF003_REG", quantity=20, modifiers=[{"modifier_id": "MILK_OAT", "quantity": 1}]
                ),
            ),
        ],
        demand_basis="Owner's estimate for a market Sunday (demo assumption, not a forecast).",
        muse_message=(
            "There's a farmers market this Sunday, Nov 1, 8 AM–2 PM, two blocks away. Markets like this "
            "usually bring about 40 extra whole-milk and 20 extra oat-milk lattes. "
            "Want me to check if we're ready?"
        ),
        request=(
            "Farmers market this Sunday Nov 1, 8 AM-2 PM. Expect about 40 extra whole-milk lattes and 20 "
            "extra oat-milk lattes (regular 12 oz). Using stock as of Thursday 6 PM, order milk for a 6 AM "
            "Sunday delivery if we'll run short, and add baristas for any hours below our event staffing target."
        ),
    )
]


def detected_events(cafe_id: str) -> list[DetectedEvent]:
    return sorted((e for e in _EVENTS if e.cafe_id == cafe_id), key=lambda e: e.starts_at)


def get_event(event_id: str) -> DetectedEvent | None:
    return next((e for e in _EVENTS if e.event_id == event_id), None)
