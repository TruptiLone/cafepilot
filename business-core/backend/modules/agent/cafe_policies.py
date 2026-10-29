"""Café operating rules the planner should use instead of guessing.

Kept in code for the hackathon; a settings table can replace this later.
"""

from typing import Any

CAFE_POLICIES: dict[str, dict[str, Any]] = {
    "CAFE001": {
        "staffing": {
            "normal_required_staff": 2,
            "busy_event_required_staff": 4,
            "espresso_roles": ["barista", "shift_lead"],
            "note": "Use busy_event_required_staff for markets, festivals and large catering days.",
        },
        "ordering": {
            "simulated_supplier_id": "SIM_SUPPLIER",
            "latest_delivery_before_opening": "06:00",
        },
    }
}


def policies_for(cafe_id: str) -> dict[str, Any]:
    return CAFE_POLICIES.get(cafe_id, {})
