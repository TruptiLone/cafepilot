
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.services.menu_service import get_menu
from backend.services.recipe_service import load_recipes


class OrderItem(BaseModel):
    """Strictly validate one extracted catering item."""

    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1)
    size: Literal["regular", "large", "each"]
    quantity: int = Field(strict=True, gt=0)
    milk: Literal["whole", "skim", "oat", "almond"] = "whole"
    extra_shots: int = Field(default=0, strict=True, ge=0, le=1)


class ExtractedOrder(BaseModel):
    """Validate the structure returned by Muse Spark."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "needs_clarification", "out_of_scope"]
    items: list[OrderItem]
    question: str | None = None


def validate_order(extracted_data):
    """
    Validate model-extracted order data against the live
    menu and recipe configuration.

    Returns normalized order data.
    Does not modify inventory or place orders.
    """

    # First validate the JSON structure and field types.
    order = ExtractedOrder.model_validate(extracted_data)
    
    if order.status == "out_of_scope":
        if order.items:
            raise ValueError(
                "Out-of-scope requests cannot contain executable items"
            )
        return {
            "status": "out_of_scope",
            "items": []
        }
    if order.status == "needs_clarification":
        if not order.question or not order.question.strip():
            raise ValueError("A clarification question is required")

        if order.items:
            raise ValueError(
                "Clarification cannot contain executable order items"
            )

        return {
            "status": "needs_clarification",
            "question": order.question.strip(),
            "items": []
        }

    if not order.items:
        raise ValueError("Ready order must contain at least one item")

    # Load product IDs from the current menu, not from a prompt.
    products = {
        product["id"]: product
        for product in get_menu()
    }

    # Load recipe definitions for supported menu products.
    recipe_data = load_recipes()
    recipes = {
        recipe["product_id"]: recipe
        for recipe in recipe_data["recipes"]
    }

    validated_items = []

    for item in order.items:
        product_id = item.product_id

        if product_id not in products:
            raise ValueError(f"Unknown product ID: {product_id}")

        if product_id not in recipes:
            raise ValueError(f"Missing recipe for: {product_id}")

        product = products[product_id]
        recipe = recipes[product_id]

        # Validate portion/size against recipe configuration.
        if item.size not in recipe["portions"]:
            raise ValueError(
                f"Unsupported size '{item.size}' for {product['name']}"
            )

        # Find ingredients for the selected portion.
        ingredients = recipe["portions"][item.size]

        ingredient_ids = {
            ingredient["ingredient_id"]
            for ingredient in ingredients
        }

        has_milk = "WHOLE_MILK" in ingredient_ids
        has_espresso = "COFFEE_BEANS" in ingredient_ids

        # Milk substitution must replace an existing milk ingredient.
        if item.milk != "whole" and not has_milk:
            raise ValueError(
                f"Milk substitution not supported for {product['name']}"
            )

        # Extra espresso shots only apply to espresso-based drinks.
        if item.extra_shots > 0 and not has_espresso:
            raise ValueError(
                f"Extra espresso shots not supported for {product['name']}"
            )

        validated_items.append(item.model_dump())

    return {
        "status": "ready",
        "items": validated_items
    }
