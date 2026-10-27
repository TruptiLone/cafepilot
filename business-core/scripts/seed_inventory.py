"""Seed the synthetic café inventory catalog without creating stock history.

Perishability and active flags retain model defaults because the source catalog
does not verify them.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from backend.db.session import SessionLocal
from backend.models.cafe import Cafe
from backend.modules.inventory.models import InventoryItem

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = REPOSITORY_ROOT / "data" / "inventory.json"
CAFE_NAME = "CafePilot Demo Cafe"
CAFE_TIMEZONE = "America/Los_Angeles"
VALID_UNITS = frozenset({"g", "mL", "each"})


class SeedValidationError(ValueError):
    """Raised when the source catalog cannot be safely imported."""


class SeedConflictError(ValueError):
    """Raised when an existing record differs from the source catalog."""


@dataclass(frozen=True)
class InventorySeedItem:
    ingredient_id: str
    name: str
    category: str
    base_unit: str


@dataclass(frozen=True)
class InventorySeedCatalog:
    cafe_id: str
    items: tuple[InventorySeedItem, ...]


@dataclass(frozen=True)
class SeedResult:
    cafes_created: int
    items_created: int


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SeedValidationError(f"{field_name} must be a nonempty string")
    return value


def load_inventory_catalog(source_path: Path = SOURCE_PATH) -> InventorySeedCatalog:
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SeedValidationError(f"Inventory source not found: {source_path}") from error
    except json.JSONDecodeError as error:
        raise SeedValidationError(f"Invalid inventory JSON: {source_path}") from error

    if not isinstance(source, dict):
        raise SeedValidationError("Inventory source must contain an object")

    cafe_id = _required_string(source.get("cafe_id"), "cafe_id")
    inventory = source.get("inventory")
    if not isinstance(inventory, list):
        raise SeedValidationError("inventory must be a list")

    items: list[InventorySeedItem] = []
    ingredient_ids: set[str] = set()
    for index, item in enumerate(inventory):
        if not isinstance(item, dict):
            raise SeedValidationError(f"inventory[{index}] must be an object")

        ingredient_id = _required_string(item.get("ingredient_id"), "ingredient_id")
        if ingredient_id in ingredient_ids:
            raise SeedValidationError(f"Duplicate ingredient_id: {ingredient_id}")

        base_unit = _required_string(item.get("unit"), "unit")
        if base_unit not in VALID_UNITS:
            raise SeedValidationError(f"Unsupported unit for {ingredient_id}: {base_unit}")

        ingredient_ids.add(ingredient_id)
        items.append(
            InventorySeedItem(
                ingredient_id=ingredient_id,
                name=_required_string(item.get("name"), "name"),
                category=_required_string(item.get("category"), "category"),
                base_unit=base_unit,
            )
        )

    return InventorySeedCatalog(cafe_id=cafe_id, items=tuple(items))


def _matches_cafe(cafe: Cafe) -> bool:
    return cafe.name == CAFE_NAME and cafe.timezone == CAFE_TIMEZONE


def _matches_item(item: InventoryItem, catalog_item: InventorySeedItem, cafe_id: str) -> bool:
    return (
        item.cafe_id == cafe_id
        and item.name == catalog_item.name
        and item.category == catalog_item.category
        and item.base_unit == catalog_item.base_unit
        and item.reorder_point is None
        and item.par_level is None
        and item.is_perishable is False
        and item.active is True
    )


def seed_inventory(session: Session, catalog: InventorySeedCatalog) -> SeedResult:
    cafes_created = 0
    items_created = 0

    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    with transaction:
        cafe = session.get(Cafe, catalog.cafe_id)
        if cafe is None:
            session.add(
                Cafe(
                    cafe_id=catalog.cafe_id,
                    name=CAFE_NAME,
                    timezone=CAFE_TIMEZONE,
                )
            )
            cafes_created = 1
        elif not _matches_cafe(cafe):
            raise SeedConflictError(f"Conflicting café record: {catalog.cafe_id}")

        for catalog_item in catalog.items:
            existing_item = session.get(InventoryItem, catalog_item.ingredient_id)
            if existing_item is None:
                session.add(
                    InventoryItem(
                        ingredient_id=catalog_item.ingredient_id,
                        cafe_id=catalog.cafe_id,
                        name=catalog_item.name,
                        category=catalog_item.category,
                        base_unit=catalog_item.base_unit,
                        reorder_point=None,
                        par_level=None,
                    )
                )
                items_created += 1
            elif not _matches_item(existing_item, catalog_item, catalog.cafe_id):
                raise SeedConflictError(
                    f"Conflicting inventory item: {catalog_item.ingredient_id}"
                )

    return SeedResult(cafes_created=cafes_created, items_created=items_created)


def main() -> None:
    catalog = load_inventory_catalog()
    with SessionLocal() as session:
        result = seed_inventory(session, catalog)

    print(
        f"Seed completed: {result.cafes_created} café(s) and "
        f"{result.items_created} inventory item(s) created."
    )


if __name__ == "__main__":
    main()
