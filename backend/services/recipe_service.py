
import json
from collections import defaultdict
from pathlib import Path

# Find project data regardless of terminal working directory
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def load_recipes():
    """Read recipe definitions from the JSON file."""
    with open(DATA_DIR / "recipes.json", encoding="utf-8") as file:
        return json.load(file)


def calculate_ingredients(order_items):
    """
    Calculate total ingredients for a catering request.

    Each item has:
      product_id, size, quantity, milk, extra_shots
    """
    data = load_recipes()

    # Make it easy to look up a recipe by product ID
    recipes = {
        recipe["product_id"]: recipe
        for recipe in data["recipes"]
    }

    rules = data["recipe_rules"]
    milk_options = rules["milk_substitutions"]["choices"]
    shot_rule = rules["extra_espresso_shot"]

    # Accumulate quantities for each ingredient
    totals = defaultdict(float)
    units = {}

    for item in order_items:
        product_id = item["product_id"]
        size = item.get("size", "regular")
        quantity = item["quantity"]
        milk = item.get("milk", "whole")
        extra_shots = item.get("extra_shots", 0)

        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise ValueError("Quantity must be a positive integer")

        if not isinstance(extra_shots, int) or isinstance(extra_shots, bool) or extra_shots < 0:
            raise ValueError("Extra shots must be a non-negative integer")

        if product_id not in recipes:
            raise ValueError(f"Unknown product: {product_id}")

        recipe = recipes[product_id]

        if size not in recipe["portions"]:
            raise ValueError(f"Invalid size '{size}' for {product_id}")

        if milk not in milk_options:
            raise ValueError(f"Unsupported milk option: {milk}")

        # A milk choice may only apply to a milk-based recipe.
        # Espresso extras may only apply to drinks containing espresso.
        ingredients = recipe["portions"][size]
        has_milk = any(x["ingredient_id"] == "WHOLE_MILK" for x in ingredients)
        has_espresso = any(x["ingredient_id"] == "COFFEE_BEANS" for x in ingredients)

        if milk != "whole" and not has_milk:
            raise ValueError(f"Milk substitution not supported for {product_id}")

        if extra_shots and not has_espresso:
            raise ValueError(f"Extra shots not supported for {product_id}")

        for ingredient in ingredients:
            ingredient_id = ingredient["ingredient_id"]
            ingredient_qty = ingredient["quantity"]
            unit = ingredient["unit"]

            # Replace whole milk with the selected milk type.
            if ingredient_id == "WHOLE_MILK":
                ingredient_id = milk_options[milk]

            if ingredient_id in units and units[ingredient_id] != unit:
                raise ValueError(f"Conflicting units for {ingredient_id}")

            units[ingredient_id] = unit
            totals[ingredient_id] += ingredient_qty * quantity

        # Add optional espresso shots.
        if extra_shots:
            bean_id = shot_rule["ingredient_id"]
            shot_unit = shot_rule["unit"]

            if bean_id in units and units[bean_id] != shot_unit:
                raise ValueError(f"Conflicting units for {bean_id}")

            units[bean_id] = shot_unit

            totals[bean_id] += (
                shot_rule["quantity_per_shot"]
                * extra_shots
                * quantity
            )

    return {
        ingredient_id: {
            "required_quantity": required,
            "unit": units[ingredient_id]
        }
        for ingredient_id, required in totals.items()
    }
