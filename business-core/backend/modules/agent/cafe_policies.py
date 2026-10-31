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
            "default_supplier_id": "BAY_DAIRY",
            "latest_delivery_before_opening": "06:00",
            # Orders are rounded up to whole packs (base units per pack).
            "pack_sizes": {
                "WHOLE_MILK": {"size": "6000", "name": "case of 6 × 1 L"},
                "OAT_MILK": {"size": "6000", "name": "case of 6 × 1 L"},
                "ALMOND_MILK": {"size": "6000", "name": "case of 6 × 1 L"},
                "SKIM_MILK": {"size": "6000", "name": "case of 6 × 1 L"},
                "COFFEE_BEANS": {"size": "1000", "name": "1 kg bag"},
            },
        },
        # Simulated suppliers (demo): they answer a few seconds after an order is sent.
        # earliest_delivery: weekday (Mon=0) -> earliest local delivery time; missing day = no delivery.
        "suppliers": [
            {
                "supplier_id": "BAY_DAIRY",
                "name": "Bay Dairy Co.",
                "respond_after_seconds": 8,
                "earliest_delivery": {d: "05:00" for d in range(7)},
                # Known only to the supplier: revealed when it answers an order (demo).
                "out_of_stock": [{"ingredient_id": "OAT_MILK", "until": "2026-11-02T00:00:00"}],
                "items": {
                    "WHOLE_MILK": {"item_number": "BD-110", "description": "Whole Milk 1 L", "pack_size": "6000", "pack_name": "case of 6 × 1 L", "pack_price_cents": 1860},
                    "OAT_MILK": {"item_number": "BD-410", "description": "Oat Milk Barista 1 L", "pack_size": "6000", "pack_name": "case of 6 × 1 L", "pack_price_cents": 2520},
                    "ALMOND_MILK": {"item_number": "BD-420", "description": "Almond Milk Barista 1 L", "pack_size": "6000", "pack_name": "case of 6 × 1 L", "pack_price_cents": 2700},
                    "SKIM_MILK": {"item_number": "BD-130", "description": "Skim Milk 1 L", "pack_size": "6000", "pack_name": "case of 6 × 1 L", "pack_price_cents": 1800},
                },
            },
            {
                "supplier_id": "WILLOW_RIDGE",
                "name": "Willow Ridge Dairy Supply",
                "respond_after_seconds": 8,
                "earliest_delivery": {d: "05:30" for d in range(7)},
                "items": {
                    "WHOLE_MILK": {"item_number": "WR-1001", "description": "Whole Milk, 1 L carton", "pack_size": "6000", "pack_name": "case of 6 × 1 L", "pack_price_cents": 2100},
                    "OAT_MILK": {"item_number": "WR-2240", "description": "Oat Milk Barista Edition, 1 L carton", "pack_size": "6000", "pack_name": "case of 6 × 1 L", "pack_price_cents": 2820},
                    "ALMOND_MILK": {"item_number": "WR-2310", "description": "Almond Milk Barista, 1 L carton", "pack_size": "6000", "pack_name": "case of 6 × 1 L", "pack_price_cents": 3000},
                    "SKIM_MILK": {"item_number": "WR-1004", "description": "Skim Milk, 1 L carton", "pack_size": "6000", "pack_name": "case of 6 × 1 L", "pack_price_cents": 2040},
                },
            },
        ],
        # Simulated staff replies to shift offers (demo).
        "staff_replies": {"respond_after_seconds": 10, "declines": []},
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
