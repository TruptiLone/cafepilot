"""Seed the deterministic Sales V2 variation and modifier catalog."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.session import SessionLocal
from backend.modules.sales.models import (
    MenuProduct,
    Modifier,
    ModifierGroup,
    ProductModifierGroup,
    ProductVariation,
)
from scripts.seed_sales import SalesSeedCatalog, SalesSeedProduct, load_sales_catalog

DEMO_CAFE_ID = "CAFE001"
LARGE_SURCHARGE_CENTS = 75
MAX_INTEGER = 2_147_483_647

DRINK_PRODUCT_IDS = frozenset(
    {
        "COF001",
        "COF002",
        "COF003",
        "COF004",
        "COF005",
        "COF006",
        "COF007",
        "COF008",
        "DRK001",
        "DRK002",
        "DRK003",
        "DRK004",
    }
)
BAKERY_PRODUCT_IDS = frozenset(
    {"BAK001", "BAK002", "BAK003", "BAK004", "BAK005", "BAK006"}
)
MILK_PRODUCT_IDS = frozenset(
    {"COF003", "COF004", "COF005", "COF006", "COF007", "DRK001", "DRK002", "DRK003"}
)
EXTRAS_PRODUCT_IDS = frozenset(
    {"COF003", "COF004", "COF005", "COF006", "COF007"}
)

GROUP_DEFINITIONS = (
    {
        "local_id": "MILK",
        "name": "Milk",
        "selection_type": "single",
        "min_select": 1,
        "max_select": 1,
    },
    {
        "local_id": "EXTRAS",
        "name": "Extras",
        "selection_type": "multiple",
        "min_select": 0,
        "max_select": 3,
    },
)

MODIFIER_DEFINITIONS = (
    ("MILK_WHOLE", "MILK", "Whole milk", 0, True),
    ("MILK_SKIM", "MILK", "Skim milk", 0, False),
    ("MILK_OAT", "MILK", "Oat milk", 75, False),
    ("MILK_ALMOND", "MILK", "Almond milk", 75, False),
    ("EXTRA_SHOT", "EXTRAS", "Extra shot", 100, False),
    ("EXTRA_VANILLA", "EXTRAS", "Vanilla", 50, False),
    ("EXTRA_CARAMEL", "EXTRAS", "Caramel", 50, False),
)


class SalesCatalogSeedError(ValueError):
    """Raised when prerequisites or existing catalog rows conflict."""


@dataclass(frozen=True)
class SalesCatalogPlan:
    variations: tuple[dict[str, object], ...]
    groups: tuple[dict[str, object], ...]
    modifiers: tuple[dict[str, object], ...]
    mappings: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class SalesCatalogSeedResult:
    variations_created: int
    groups_created: int
    modifiers_created: int
    mappings_created: int


def catalog_identifier(cafe_id: str, local_identifier: str) -> str:
    if cafe_id == DEMO_CAFE_ID:
        return local_identifier
    return f"{cafe_id}:{local_identifier}"


def catalog_for_cafe(
    source_catalog: SalesSeedCatalog,
    cafe_id: str,
) -> SalesSeedCatalog:
    if cafe_id == DEMO_CAFE_ID:
        return source_catalog
    return SalesSeedCatalog(
        cafe_id=cafe_id,
        products=tuple(
            SalesSeedProduct(
                product_id=catalog_identifier(cafe_id, product.product_id),
                name=product.name,
                category=product.category,
                price_cents=product.price_cents,
            )
            for product in source_catalog.products
        ),
    )


def _canonical_product_id(catalog: SalesSeedCatalog, product_id: str) -> str:
    if catalog.cafe_id == DEMO_CAFE_ID:
        return product_id
    prefix = f"{catalog.cafe_id}:"
    if not product_id.startswith(prefix):
        raise SalesCatalogSeedError(
            f"Product id must be namespaced for café {catalog.cafe_id}: {product_id}"
        )
    return product_id[len(prefix) :]


def build_sales_catalog_plan(catalog: SalesSeedCatalog) -> SalesCatalogPlan:
    expected_product_ids = DRINK_PRODUCT_IDS | BAKERY_PRODUCT_IDS
    products_by_canonical_id: dict[str, SalesSeedProduct] = {}
    for product in catalog.products:
        canonical_id = _canonical_product_id(catalog, product.product_id)
        if canonical_id in products_by_canonical_id:
            raise SalesCatalogSeedError(f"Duplicate catalog product: {canonical_id}")
        products_by_canonical_id[canonical_id] = product

    if set(products_by_canonical_id) != expected_product_ids:
        missing = sorted(expected_product_ids - products_by_canonical_id.keys())
        unexpected = sorted(products_by_canonical_id.keys() - expected_product_ids)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected {', '.join(unexpected)}")
        raise SalesCatalogSeedError(f"Sales catalog products do not match: {'; '.join(details)}")

    for canonical_id, product in products_by_canonical_id.items():
        expected_category = "bakery" if canonical_id in BAKERY_PRODUCT_IDS else (
            "coffee" if canonical_id.startswith("COF") else "other_drinks"
        )
        if product.category != expected_category:
            raise SalesCatalogSeedError(
                f"Unexpected category for {canonical_id}: {product.category}"
            )
        if product.price_cents > MAX_INTEGER - LARGE_SURCHARGE_CENTS:
            raise SalesCatalogSeedError(
                f"Large price exceeds supported range: {canonical_id}"
            )

    variations: list[dict[str, object]] = []
    for canonical_id, product in sorted(products_by_canonical_id.items()):
        is_bakery = canonical_id in BAKERY_PRODUCT_IDS
        variations.append(
            {
                "variation_id": catalog_identifier(
                    catalog.cafe_id,
                    f"{canonical_id}_REG",
                ),
                "cafe_id": catalog.cafe_id,
                "product_id": product.product_id,
                "code": "REG",
                "name": "Regular" if is_bakery else "Regular 12 oz",
                "size_oz": None if is_bakery else 12,
                "price_cents": product.price_cents,
                "active": True,
            }
        )
        if not is_bakery:
            variations.append(
                {
                    "variation_id": catalog_identifier(
                        catalog.cafe_id,
                        f"{canonical_id}_LRG",
                    ),
                    "cafe_id": catalog.cafe_id,
                    "product_id": product.product_id,
                    "code": "LRG",
                    "name": "Large 16 oz",
                    "size_oz": 16,
                    "price_cents": product.price_cents + LARGE_SURCHARGE_CENTS,
                    "active": True,
                }
            )

    groups = tuple(
        {
            "group_id": catalog_identifier(catalog.cafe_id, definition["local_id"]),
            "cafe_id": catalog.cafe_id,
            "name": definition["name"],
            "selection_type": definition["selection_type"],
            "min_select": definition["min_select"],
            "max_select": definition["max_select"],
            "active": True,
        }
        for definition in GROUP_DEFINITIONS
    )
    modifiers = tuple(
        {
            "modifier_id": catalog_identifier(catalog.cafe_id, local_id),
            "cafe_id": catalog.cafe_id,
            "group_id": catalog_identifier(catalog.cafe_id, group_local_id),
            "name": name,
            "price_cents": price_cents,
            "is_default": is_default,
            "active": True,
        }
        for local_id, group_local_id, name, price_cents, is_default in MODIFIER_DEFINITIONS
    )

    mappings: list[dict[str, object]] = []
    for group_local_id, product_ids in (
        ("MILK", MILK_PRODUCT_IDS),
        ("EXTRAS", EXTRAS_PRODUCT_IDS),
    ):
        group_id = catalog_identifier(catalog.cafe_id, group_local_id)
        for canonical_id in sorted(product_ids):
            product = products_by_canonical_id[canonical_id]
            mappings.append(
                {
                    "product_modifier_group_id": catalog_identifier(
                        catalog.cafe_id,
                        f"{canonical_id}_{group_local_id}",
                    ),
                    "cafe_id": catalog.cafe_id,
                    "product_id": product.product_id,
                    "group_id": group_id,
                    "active": True,
                }
            )

    return SalesCatalogPlan(
        variations=tuple(variations),
        groups=groups,
        modifiers=modifiers,
        mappings=tuple(mappings),
    )


def _assert_products_match(session: Session, catalog: SalesSeedCatalog) -> None:
    for expected in catalog.products:
        product = session.get(MenuProduct, expected.product_id)
        if product is None:
            raise SalesCatalogSeedError(f"Menu product does not exist: {expected.product_id}")
        if (
            product.cafe_id != catalog.cafe_id
            or product.name != expected.name
            or product.category != expected.category
            or product.price_cents != expected.price_cents
            or product.active is not True
        ):
            raise SalesCatalogSeedError(f"Conflicting menu product: {expected.product_id}")


def _assert_matches(record: object, expected: dict[str, object], label: str) -> None:
    if any(getattr(record, field) != value for field, value in expected.items()):
        raise SalesCatalogSeedError(f"Conflicting existing {label}")


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
        raise SalesCatalogSeedError(f"Conflicting existing {label} identity")
    _assert_matches(existing_by_id, expected, label)
    return 0


def seed_sales_catalog(
    session: Session,
    catalog: SalesSeedCatalog,
) -> SalesCatalogSeedResult:
    plan = build_sales_catalog_plan(catalog)
    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    with transaction:
        _assert_products_match(session, catalog)
        variations_created = sum(
            _seed_record(
                session,
                ProductVariation,
                "variation_id",
                ("cafe_id", "product_id", "code"),
                expected,
                f"variation: {expected['variation_id']}",
            )
            for expected in plan.variations
        )
        groups_created = sum(
            _seed_record(
                session,
                ModifierGroup,
                "group_id",
                ("cafe_id", "name"),
                expected,
                f"modifier group: {expected['group_id']}",
            )
            for expected in plan.groups
        )
        modifiers_created = sum(
            _seed_record(
                session,
                Modifier,
                "modifier_id",
                ("cafe_id", "group_id", "name"),
                expected,
                f"modifier: {expected['modifier_id']}",
            )
            for expected in plan.modifiers
        )
        mappings_created = sum(
            _seed_record(
                session,
                ProductModifierGroup,
                "product_modifier_group_id",
                ("cafe_id", "product_id", "group_id"),
                expected,
                f"product modifier group: {expected['product_modifier_group_id']}",
            )
            for expected in plan.mappings
        )
        session.flush()

    return SalesCatalogSeedResult(
        variations_created=variations_created,
        groups_created=groups_created,
        modifiers_created=modifiers_created,
        mappings_created=mappings_created,
    )


def main() -> None:
    catalog = load_sales_catalog()
    with SessionLocal() as session:
        result = seed_sales_catalog(session, catalog)
    print(
        "Sales V2 catalog seed completed: "
        f"{result.variations_created} variations, "
        f"{result.groups_created} groups, "
        f"{result.modifiers_created} modifiers, and "
        f"{result.mappings_created} product-group mappings created."
    )


if __name__ == "__main__":
    main()
