
import json
from pathlib import Path

# Locate the data folder from the project root.
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def load_inventory():
    """Load the latest synthetic inventory snapshot."""
    with open(DATA_DIR / "inventory.json", encoding="utf-8") as file:
        return json.load(file)


def check_inventory(ingredient_requirements):
    """
    Compare required ingredients against available inventory.

    Expected input:
    {
        "OAT_MILK": {
            "required_quantity": 2400,
            "unit": "mL"
        }
    }

    Returns a readiness report without modifying inventory.
    """
    inventory_data = load_inventory()

    # Build a dictionary for quick ingredient lookup.
    inventory_lookup = {
        item["ingredient_id"]: item
        for item in inventory_data["inventory"]
    }

    results = []
    has_shortage = False
    has_unknown = False

    for ingredient_id, requirement in ingredient_requirements.items():
        required = requirement["required_quantity"]
        required_unit = requirement["unit"]

        if (
            not isinstance(required, (int, float))
            or isinstance(required, bool)
            or required < 0
        ):
            raise ValueError(
                f"Invalid required quantity for {ingredient_id}"
            )

        # Check if the ingredient exists in our inventory.
        if ingredient_id not in inventory_lookup:
            has_unknown = True

            results.append({
                "ingredient_id": ingredient_id,
                "required_quantity": required,
                "available_quantity": None,
                "shortage_quantity": None,
                "unit": required_unit,
                "status": "unknown",
                "reason": "Ingredient missing from inventory"
            })
            continue

        inventory_item = inventory_lookup[ingredient_id]
        available = inventory_item["usable_quantity"]
        available_unit = inventory_item["unit"]

        # A unit mismatch should not produce a misleading result.
        if required_unit != available_unit:
            has_unknown = True

            results.append({
                "ingredient_id": ingredient_id,
                "required_quantity": required,
                "available_quantity": None,
                "shortage_quantity": None,
                "unit": required_unit,
                "status": "unknown",
                "reason": (
                    f"Unit mismatch: required {required_unit}, "
                    f"inventory uses {available_unit}"
                )
            })
            continue

        if (
            not isinstance(available, (int, float))
            or isinstance(available, bool)
            or available < 0
        ):
            has_unknown = True

            results.append({
                "ingredient_id": ingredient_id,
                "required_quantity": required,
                "available_quantity": None,
                "shortage_quantity": None,
                "unit": required_unit,
                "status": "unknown",
                "reason": "Invalid or unknown inventory quantity"
            })
            continue

        # A shortage is only positive when demand exceeds stock.
        shortage = max(0, required - available)

        if shortage > 0:
            status = "shortage"
            has_shortage = True
        else:
            status = "sufficient"

        results.append({
            "ingredient_id": ingredient_id,
            "name": inventory_item["name"],
            "required_quantity": required,
            "available_quantity": available,
            "shortage_quantity": shortage,
            "unit": required_unit,
            "status": status
        })

    # Unknown data takes precedence over a conclusive readiness result.
    if has_unknown:
        overall_status = "insufficient_data"
    elif has_shortage:
        overall_status = "shortage"
    else:
        overall_status = "sufficient"

    return {
        "cafe_id": inventory_data["cafe_id"],
        "snapshot_date": inventory_data.get("snapshot_date"),
        "is_synthetic": inventory_data.get("is_synthetic", True),
        "overall_status": overall_status,
        "items": results
    }
