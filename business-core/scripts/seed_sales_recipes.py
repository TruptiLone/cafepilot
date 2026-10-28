"""Seed deterministic Sales V2 recipes and modifier inventory effects."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.session import SessionLocal
from backend.models.cafe import Cafe
from backend.modules.inventory.models import InventoryItem
from backend.modules.sales.models import (
    Modifier,
    ModifierRecipeEffect,
    ProductVariation,
    RecipeLine,
)
from scripts.seed_sales_catalog import DEMO_CAFE_ID, catalog_identifier

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = REPOSITORY_ROOT / "data" / "recipes.json"
EFFECTIVE_FROM = datetime(2025, 1, 1, tzinfo=timezone.utc)
VALID_UNITS = frozenset({"g", "mL", "each"})
MAX_QUANTITY = Decimal("999999999999999.999")
PORTION_CODES = {"regular": "REG", "large": "LRG", "each": "REG"}


class SalesRecipeSeedError(ValueError):
    """Raised when recipe source data or existing records are inconsistent."""


@dataclass(frozen=True)
class RecipeSourceLine:
    product_id: str
    portion: str
    ingredient_id: str
    quantity: Decimal
    unit: str


@dataclass(frozen=True)
class RecipeSourceCatalog:
    cafe_id: str
    lines: tuple[RecipeSourceLine, ...]


@dataclass(frozen=True)
class SalesRecipePlan:
    recipe_lines: tuple[dict[str, object], ...]
    modifier_effects: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class SalesRecipeSeedResult:
    recipe_lines_created: int
    modifier_effects_created: int


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SalesRecipeSeedError(f"{field_name} must be a nonempty string")
    return value


def _quantity(value: object, label: str) -> Decimal:
    if isinstance(value, bool):
        raise SalesRecipeSeedError(f"{label} must be a positive Decimal quantity")
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise SalesRecipeSeedError(
            f"{label} must be a positive Decimal quantity"
        ) from error
    if (
        not quantity.is_finite()
        or quantity <= 0
        or quantity > MAX_QUANTITY
        or quantity.as_tuple().exponent < -3
    ):
        raise SalesRecipeSeedError(
            f"{label} must fit NUMERIC(18,3) and be positive"
        )
    return quantity


def load_recipe_catalog(source_path: Path = SOURCE_PATH) -> RecipeSourceCatalog:
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SalesRecipeSeedError(f"Recipe source not found: {source_path}") from error
    except json.JSONDecodeError as error:
        raise SalesRecipeSeedError(f"Invalid recipe JSON: {source_path}") from error

    if not isinstance(source, dict):
        raise SalesRecipeSeedError("Recipe source must contain an object")
    cafe_id = _required_string(source.get("cafe_id"), "cafe_id")
    recipes = source.get("recipes")
    if not isinstance(recipes, list):
        raise SalesRecipeSeedError("recipes must be a list")

    lines: list[RecipeSourceLine] = []
    identities: set[tuple[str, str, str]] = set()
    for recipe_index, recipe in enumerate(recipes):
        if not isinstance(recipe, dict):
            raise SalesRecipeSeedError(f"recipes[{recipe_index}] must be an object")
        product_id = _required_string(recipe.get("product_id"), "product_id")
        portions = recipe.get("portions")
        if not isinstance(portions, dict) or not portions:
            raise SalesRecipeSeedError(f"Recipe portions are missing: {product_id}")
        for portion, source_lines in portions.items():
            if portion not in PORTION_CODES:
                raise SalesRecipeSeedError(
                    f"Unsupported recipe portion for {product_id}: {portion}"
                )
            if not isinstance(source_lines, list) or not source_lines:
                raise SalesRecipeSeedError(
                    f"Recipe portion must contain ingredients: {product_id} {portion}"
                )
            for line_index, source_line in enumerate(source_lines):
                if not isinstance(source_line, dict):
                    raise SalesRecipeSeedError(
                        f"Invalid recipe line: {product_id} {portion} {line_index}"
                    )
                ingredient_id = _required_string(
                    source_line.get("ingredient_id"),
                    "ingredient_id",
                )
                identity = (product_id, portion, ingredient_id)
                if identity in identities:
                    raise SalesRecipeSeedError(
                        f"Duplicate recipe ingredient: {product_id} {portion} {ingredient_id}"
                    )
                unit = _required_string(source_line.get("unit"), "unit")
                if unit not in VALID_UNITS:
                    raise SalesRecipeSeedError(
                        f"Unsupported recipe unit for {ingredient_id}: {unit}"
                    )
                identities.add(identity)
                lines.append(
                    RecipeSourceLine(
                        product_id=product_id,
                        portion=portion,
                        ingredient_id=ingredient_id,
                        quantity=_quantity(
                            source_line.get("quantity"),
                            f"quantity for {product_id} {portion} {ingredient_id}",
                        ),
                        unit=unit,
                    )
                )

    if cafe_id != DEMO_CAFE_ID:
        raise SalesRecipeSeedError(f"Unexpected recipe source café: {cafe_id}")
    recipe_rules = source.get("recipe_rules")
    if not isinstance(recipe_rules, dict):
        raise SalesRecipeSeedError("recipe_rules must be an object")
    milk_rule = recipe_rules.get("milk_substitutions")
    expected_milk_choices = {
        "whole": "WHOLE_MILK",
        "skim": "SKIM_MILK",
        "oat": "OAT_MILK",
        "almond": "ALMOND_MILK",
    }
    if (
        not isinstance(milk_rule, dict)
        or milk_rule.get("operation") != "replace"
        or milk_rule.get("default_ingredient_id") != "WHOLE_MILK"
        or milk_rule.get("choices") != expected_milk_choices
    ):
        raise SalesRecipeSeedError("milk substitution rules do not match M3")
    shot_rule = recipe_rules.get("extra_espresso_shot")
    if (
        not isinstance(shot_rule, dict)
        or shot_rule.get("operation") != "add"
        or shot_rule.get("ingredient_id") != "COFFEE_BEANS"
        or _quantity(shot_rule.get("quantity_per_shot"), "extra shot quantity")
        != Decimal("9")
        or shot_rule.get("unit") != "g"
    ):
        raise SalesRecipeSeedError("extra espresso shot rule does not match M3")
    return RecipeSourceCatalog(cafe_id=cafe_id, lines=tuple(lines))


def inventory_identifier(cafe_id: str, ingredient_id: str) -> str:
    return catalog_identifier(cafe_id, ingredient_id)


def build_recipe_plan(
    cafe_id: str,
    source: RecipeSourceCatalog,
) -> SalesRecipePlan:
    recipe_lines: list[dict[str, object]] = []
    variation_ids: set[str] = set()
    for line in source.lines:
        code = PORTION_CODES[line.portion]
        variation_id = catalog_identifier(cafe_id, f"{line.product_id}_{code}")
        ingredient_id = inventory_identifier(cafe_id, line.ingredient_id)
        variation_ids.add(variation_id)
        recipe_lines.append(
            {
                "recipe_line_id": catalog_identifier(
                    cafe_id,
                    f"RECIPE_{line.product_id}_{code}_{line.ingredient_id}_20250101",
                ),
                "cafe_id": cafe_id,
                "variation_id": variation_id,
                "ingredient_id": ingredient_id,
                "quantity": line.quantity,
                "unit": line.unit,
                "effective_from": EFFECTIVE_FROM,
            }
        )
    if len(variation_ids) != 30:
        raise SalesRecipeSeedError(
            f"Recipe source must map exactly 30 variations, found {len(variation_ids)}"
        )

    effect_definitions = (
        ("MILK_SKIM", "replace", "WHOLE_MILK", "SKIM_MILK", None, None),
        ("MILK_OAT", "replace", "WHOLE_MILK", "OAT_MILK", None, None),
        ("MILK_ALMOND", "replace", "WHOLE_MILK", "ALMOND_MILK", None, None),
        ("EXTRA_SHOT", "add", None, "COFFEE_BEANS", Decimal("9"), "g"),
        (
            "EXTRA_VANILLA",
            "add",
            None,
            "VANILLA_SYRUP",
            Decimal("15"),
            "mL",
        ),
        (
            "EXTRA_CARAMEL",
            "add",
            None,
            "CARAMEL_SYRUP",
            Decimal("15"),
            "mL",
        ),
    )
    modifier_effects = tuple(
        {
            "modifier_recipe_effect_id": catalog_identifier(
                cafe_id,
                f"RECIPE_EFFECT_{modifier_local_id}",
            ),
            "cafe_id": cafe_id,
            "modifier_id": catalog_identifier(cafe_id, modifier_local_id),
            "effect": effect,
            "target_ingredient_id": (
                inventory_identifier(cafe_id, target_ingredient_id)
                if target_ingredient_id is not None
                else None
            ),
            "ingredient_id": inventory_identifier(cafe_id, ingredient_id),
            "quantity": quantity,
            "unit": unit,
        }
        for (
            modifier_local_id,
            effect,
            target_ingredient_id,
            ingredient_id,
            quantity,
            unit,
        ) in effect_definitions
    )
    return SalesRecipePlan(
        recipe_lines=tuple(recipe_lines),
        modifier_effects=modifier_effects,
    )


def _assert_prerequisites(
    session: Session,
    cafe_id: str,
    plan: SalesRecipePlan,
) -> None:
    if session.get(Cafe, cafe_id) is None:
        raise SalesRecipeSeedError(f"Café does not exist: {cafe_id}")
    inventory_units: dict[str, str] = {}
    for expected in plan.recipe_lines:
        variation = session.get(ProductVariation, expected["variation_id"])
        if variation is None or variation.cafe_id != cafe_id:
            raise SalesRecipeSeedError(
                f"Variation does not exist: {expected['variation_id']}"
            )
        item = session.get(InventoryItem, expected["ingredient_id"])
        if item is None or item.cafe_id != cafe_id:
            raise SalesRecipeSeedError(
                f"Inventory item does not exist: {expected['ingredient_id']}"
            )
        if item.base_unit != expected["unit"]:
            raise SalesRecipeSeedError(
                f"Recipe unit conflicts with inventory item: {item.ingredient_id}"
            )
        inventory_units[item.ingredient_id] = item.base_unit

    for expected in plan.modifier_effects:
        modifier = session.get(Modifier, expected["modifier_id"])
        if modifier is None or modifier.cafe_id != cafe_id:
            raise SalesRecipeSeedError(
                f"Modifier does not exist: {expected['modifier_id']}"
            )
        ingredient = session.get(InventoryItem, expected["ingredient_id"])
        if ingredient is None or ingredient.cafe_id != cafe_id:
            raise SalesRecipeSeedError(
                f"Effect ingredient does not exist: {expected['ingredient_id']}"
            )
        if expected["effect"] == "add" and ingredient.base_unit != expected["unit"]:
            raise SalesRecipeSeedError(
                f"Effect unit conflicts with inventory item: {ingredient.ingredient_id}"
            )
        target_id = expected["target_ingredient_id"]
        if target_id is not None:
            target = session.get(InventoryItem, target_id)
            if target is None or target.cafe_id != cafe_id:
                raise SalesRecipeSeedError(
                    f"Effect target does not exist: {target_id}"
                )
            if target.base_unit != ingredient.base_unit:
                raise SalesRecipeSeedError(
                    f"Replacement units do not match: {target_id}"
                )


def _assert_matches(record: object, expected: dict[str, object], label: str) -> None:
    if any(getattr(record, field) != value for field, value in expected.items()):
        raise SalesRecipeSeedError(f"Conflicting existing {label}")


def _seed_record(
    session: Session,
    model,
    primary_key: str,
    identity_fields: tuple[str, ...],
    expected: dict[str, object],
    label: str,
) -> int:
    existing_by_id = session.get(model, expected[primary_key])
    existing_by_identity = session.scalar(
        select(model).where(
            *(getattr(model, field) == expected[field] for field in identity_fields)
        )
    )
    if existing_by_id is None and existing_by_identity is None:
        session.add(model(**expected))
        return 1
    if (
        existing_by_id is None
        or existing_by_identity is None
        or existing_by_id is not existing_by_identity
    ):
        raise SalesRecipeSeedError(f"Conflicting existing {label} identity")
    _assert_matches(existing_by_id, expected, label)
    return 0


def seed_sales_recipes(
    session: Session,
    cafe_id: str = DEMO_CAFE_ID,
    source: RecipeSourceCatalog | None = None,
) -> SalesRecipeSeedResult:
    plan = build_recipe_plan(cafe_id, source or load_recipe_catalog())
    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    with transaction:
        _assert_prerequisites(session, cafe_id, plan)
        recipe_lines_created = sum(
            _seed_record(
                session,
                RecipeLine,
                "recipe_line_id",
                ("cafe_id", "variation_id", "effective_from", "ingredient_id"),
                expected,
                f"recipe line: {expected['recipe_line_id']}",
            )
            for expected in plan.recipe_lines
        )
        modifier_effects_created = sum(
            _seed_record(
                session,
                ModifierRecipeEffect,
                "modifier_recipe_effect_id",
                (
                    "cafe_id",
                    "modifier_id",
                    "effect",
                    "target_ingredient_id",
                    "ingredient_id",
                ),
                expected,
                f"modifier recipe effect: {expected['modifier_recipe_effect_id']}",
            )
            for expected in plan.modifier_effects
        )
        session.flush()
    return SalesRecipeSeedResult(
        recipe_lines_created=recipe_lines_created,
        modifier_effects_created=modifier_effects_created,
    )


def main() -> None:
    with SessionLocal() as session:
        result = seed_sales_recipes(session)
    print(
        "Sales recipes seed completed: "
        f"{result.recipe_lines_created} recipe lines and "
        f"{result.modifier_effects_created} modifier effects created."
    )


if __name__ == "__main__":
    main()
