
from backend.services.inventory_service import check_inventory
from backend.services.recipe_service import calculate_ingredients


def test_catering_inventory_shortage():
    """A catering order should identify the oat-milk shortage."""

    order = [
        {
            "product_id": "COF003",
            "size": "regular",
            "quantity": 20,
            "milk": "whole"
        },
        {
            "product_id": "COF003",
            "size": "regular",
            "quantity": 10,
            "milk": "oat"
        },
        {
            "product_id": "COF002",
            "size": "regular",
            "quantity": 10
        }
    ]

    # First calculate ingredients from recipes.
    requirements = calculate_ingredients(order)

    # Then check those ingredients against inventory.
    report = check_inventory(requirements)

    # Convert results into a dictionary for easy access.
    items = {
        item["ingredient_id"]: item
        for item in report["items"]
    }

    assert report["overall_status"] == "shortage"
    assert items["COFFEE_BEANS"]["status"] == "sufficient"
    assert items["WHOLE_MILK"]["status"] == "sufficient"

    assert items["OAT_MILK"]["required_quantity"] == 2400
    assert items["OAT_MILK"]["available_quantity"] == 2000
    assert items["OAT_MILK"]["shortage_quantity"] == 400


def test_sufficient_inventory():
    """A smaller order should have enough stock."""

    requirements = {
        "WHOLE_MILK": {
            "required_quantity": 500,
            "unit": "mL"
        }
    }

    report = check_inventory(requirements)

    assert report["overall_status"] == "sufficient"
    assert report["items"][0]["shortage_quantity"] == 0


def test_missing_inventory():
    """Missing stock data must be unknown, not zero."""

    requirements = {
        "UNKNOWN_INGREDIENT": {
            "required_quantity": 500,
            "unit": "mL"
        }
    }

    report = check_inventory(requirements)

    assert report["overall_status"] == "insufficient_data"
    assert report["items"][0]["status"] == "unknown"
    assert report["items"][0]["available_quantity"] is None
    assert report["items"][0]["shortage_quantity"] is None


def test_unit_mismatch():
    """A unit mismatch cannot be compared directly."""

    requirements = {
        "OAT_MILK": {
            "required_quantity": 2,
            "unit": "g"
        }
    }

    report = check_inventory(requirements)

    assert report["overall_status"] == "insufficient_data"
    assert report["items"][0]["status"] == "unknown"
