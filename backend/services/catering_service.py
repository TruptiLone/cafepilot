
from backend.services.recipe_service import calculate_ingredients
from backend.services.inventory_service import check_inventory


def evaluate_catering_order(order_items):
    """
    Evaluate ingredient readiness for a catering request.

    This operation is read-only:
    - Does not reserve inventory
    - Does not deduct stock
    - Does not place supplier orders
    """
    requirements = calculate_ingredients(order_items)
    inventory_report = check_inventory(requirements)

    return {
        "status": inventory_report["overall_status"],
        "total_drinks_or_items": sum(
            item["quantity"] for item in order_items
        ),
        "ingredients_required": requirements,
        "inventory_report": inventory_report,
        "assessment_basis": "static_inventory_snapshot",
        "limitations": [
            "Synthetic inventory snapshot",
            "Does not reserve or deduct inventory",
            "Does not account for intervening sales or incoming deliveries",
            "Packaging and other omitted recipe components are not checked"
        ]
    }
