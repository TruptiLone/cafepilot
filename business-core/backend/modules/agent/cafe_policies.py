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
            "default_supplier_id": "WILLOW_RIDGE",
            "latest_delivery_before_opening": "06:00",
            # Orders are rounded up to whole packs (base units per pack).
            "pack_sizes": {
                "WHOLE_MILK": {"size": "1890", "name": "half gallon (1.89 L)"},
                "OAT_MILK": {"size": "6000", "name": "case of 6 × 1 L"},
                "ALMOND_MILK": {"size": "6000", "name": "case of 6 × 1 L"},
                "SKIM_MILK": {"size": "6000", "name": "case of 6 × 1 L"},
                "COFFEE_BEANS": {"size": "340", "name": "12 oz bag (340 g)"},
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
                "out_of_stock": [{"ingredient_id": "OAT_MILK", "until": "2026-11-09T00:00:00"}],
                "items": {
                    "WHOLE_MILK": {"item_number": "BD-1175", "description": "Whole Milk, half gallon (1.89 L)", "pack_size": "1890", "pack_name": "half gallon (1.89 L)", "pack_price_cents": 429},
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
                    "WHOLE_MILK": {"item_number": "WR-1175", "description": "Whole Milk, half gallon (1.89 L)", "pack_size": "1890", "pack_name": "half gallon (1.89 L)", "pack_price_cents": 449},
                    "COFFEE_BEANS": {"item_number": "WR-2210", "description": "Coffee Beans, 12 oz bag", "pack_size": "340", "pack_name": "12 oz bag (340 g)", "pack_price_cents": 1299},
                    "BUTTER_CROISSANT": {"item_number": "WR-3305", "description": "Butter Croissants, baked", "pack_size": "1", "pack_name": "croissant", "pack_price_cents": 175},
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


# Test only: a supplier for the end-to-end glasses test (one real 1.7 L carton). It is kept out of
# CAFE_POLICIES, so the planner and find_suppliers never see it and the demo plans are unchanged;
# only an order that names it (scripts/glasses_test_order.py) reaches it.
TEST_SUPPLIERS: dict[str, list[dict[str, Any]]] = {
    "CAFE001": [
        {
            "supplier_id": "GLASSES_TEST",
            "name": "Glasses Test Dairy (test)",
            "respond_after_seconds": 8,
            "earliest_delivery": {d: "00:00" for d in range(7)},
            "items": {
                "WHOLE_MILK": {"item_number": "GT-1700", "description": "Whole Milk, 1.7 L carton", "pack_size": "1700", "pack_name": "carton of 1 × 1.7 L", "pack_price_cents": 499},
            },
        },
    ],
}


def policies_for(cafe_id: str) -> dict[str, Any]:
    return CAFE_POLICIES.get(cafe_id, {})


def event_required_staff(cafe_id: str, event_type: str) -> tuple[int, str]:
    """People needed during an event, and the policy it comes from. Holidays count as normal days
    (the same split the café status panel uses)."""
    staffing = policies_for(cafe_id).get("staffing", {})
    normal = int(staffing.get("normal_required_staff", 2))
    if event_type == "holiday":
        return normal, "normal_required_staff"
    return int(staffing.get("busy_event_required_staff", normal)), "busy_event_required_staff"


def opening_hours(cafe_id: str) -> dict[int, tuple[int, int]]:
    return policies_for(cafe_id).get("opening_hours", {d: (7, 19) for d in range(7)})
