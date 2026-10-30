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
            "buffer_pct": 10,
            "simulated_supplier_id": "SIM_SUPPLIER",
            "latest_delivery_before_opening": "06:00",
            # Orders are rounded up to whole packs (base units per pack).
            "pack_sizes": {
                "WHOLE_MILK": {"size": "1000", "name": "1 L carton"},
                "OAT_MILK": {"size": "1000", "name": "1 L carton"},
                "ALMOND_MILK": {"size": "1000", "name": "1 L carton"},
                "SKIM_MILK": {"size": "1000", "name": "1 L carton"},
                "COFFEE_BEANS": {"size": "1000", "name": "1 kg bag"},
            },
        },
        # weekday (Mon=0) -> (open hour, close hour), café local time
        "opening_hours": {0: (7, 19), 1: (7, 19), 2: (7, 19), 3: (7, 19), 4: (7, 19), 5: (7, 17), 6: (7, 17)},
        # Regular orders that arrive without CaféPilot doing anything (sized to normal use).
        "standing_deliveries": [
            {"ingredient_id": "WHOLE_MILK", "quantity": "12000", "unit": "mL", "weekdays": [0, 1, 2, 3, 4, 5, 6], "at": "06:00"},
            {"ingredient_id": "OAT_MILK", "quantity": "5000", "unit": "mL", "weekdays": [0, 1, 2, 3, 4, 5, 6], "at": "06:00"},
            {"ingredient_id": "COFFEE_BEANS", "quantity": "5000", "unit": "g", "weekdays": [0, 3], "at": "06:00"},
            # brewed in-house each morning
            {"ingredient_id": "COLD_BREW_CONCENTRATE", "quantity": "2000", "unit": "mL", "weekdays": [0, 1, 2, 3, 4, 5, 6], "at": "06:00"},
        ],
    }
}


def policies_for(cafe_id: str) -> dict[str, Any]:
    return CAFE_POLICIES.get(cafe_id, {})


def opening_hours(cafe_id: str) -> dict[int, tuple[int, int]]:
    return policies_for(cafe_id).get("opening_hours", {d: (7, 19) for d in range(7)})
