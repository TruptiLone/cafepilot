"""Seed the Sales menu catalog without creating sales."""

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from backend.db.session import SessionLocal
from backend.models.cafe import Cafe
from backend.modules.inventory import models as inventory_models
from backend.modules.sales.models import MenuProduct

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = REPOSITORY_ROOT / "data" / "menu.json"
MAX_INTEGER = 2_147_483_647


class SeedValidationError(ValueError):
    """Raised when the source catalog cannot be safely imported."""


class SeedConflictError(ValueError):
    """Raised when an existing record differs from the source catalog."""


@dataclass(frozen=True)
class SalesSeedProduct:
    product_id: str
    name: str
    category: str
    price_cents: int


@dataclass(frozen=True)
class SalesSeedCatalog:
    cafe_id: str
    products: tuple[SalesSeedProduct, ...]


@dataclass(frozen=True)
class SeedResult:
    products_created: int


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SeedValidationError(f"{field_name} must be a nonempty string")
    return value


def load_sales_catalog(source_path: Path = SOURCE_PATH) -> SalesSeedCatalog:
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SeedValidationError(f"Menu source not found: {source_path}") from error
    except json.JSONDecodeError as error:
        raise SeedValidationError(f"Invalid menu JSON: {source_path}") from error

    if not isinstance(source, dict):
        raise SeedValidationError("Menu source must contain an object")

    cafe_id = _required_string(source.get("cafe_id"), "cafe_id")
    source_products = source.get("products")
    if not isinstance(source_products, list):
        raise SeedValidationError("products must be a list")

    products: list[SalesSeedProduct] = []
    product_ids: set[str] = set()
    for index, source_product in enumerate(source_products):
        if not isinstance(source_product, dict):
            raise SeedValidationError(f"products[{index}] must be an object")

        product_id = _required_string(source_product.get("id"), "id")
        if product_id in product_ids:
            raise SeedValidationError(f"Duplicate product id: {product_id}")
        price_cents = source_product.get("price_cents")
        if (
            isinstance(price_cents, bool)
            or not isinstance(price_cents, int)
            or price_cents < 0
            or price_cents > MAX_INTEGER
        ):
            raise SeedValidationError(
                f"price_cents for {product_id} must be a supported nonnegative integer"
            )

        product_ids.add(product_id)
        products.append(
            SalesSeedProduct(
                product_id=product_id,
                name=_required_string(source_product.get("name"), "name"),
                category=_required_string(source_product.get("category"), "category"),
                price_cents=price_cents,
            )
        )

    if not products:
        raise SeedValidationError("products must not be empty")
    return SalesSeedCatalog(cafe_id=cafe_id, products=tuple(products))


def _matches_product(
    product: MenuProduct,
    catalog_product: SalesSeedProduct,
    cafe_id: str,
) -> bool:
    return (
        product.cafe_id == cafe_id
        and product.name == catalog_product.name
        and product.category == catalog_product.category
        and product.price_cents == catalog_product.price_cents
        and product.active is True
    )


def seed_sales(session: Session, catalog: SalesSeedCatalog) -> SeedResult:
    products_created = 0

    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    with transaction:
        if session.get(Cafe, catalog.cafe_id) is None:
            raise SeedConflictError(f"Café does not exist: {catalog.cafe_id}")

        for catalog_product in catalog.products:
            existing_product = session.get(MenuProduct, catalog_product.product_id)
            if existing_product is None:
                session.add(
                    MenuProduct(
                        product_id=catalog_product.product_id,
                        cafe_id=catalog.cafe_id,
                        name=catalog_product.name,
                        category=catalog_product.category,
                        price_cents=catalog_product.price_cents,
                    )
                )
                products_created += 1
            elif not _matches_product(existing_product, catalog_product, catalog.cafe_id):
                raise SeedConflictError(
                    f"Conflicting menu product: {catalog_product.product_id}"
                )

    return SeedResult(products_created=products_created)


def main() -> None:
    catalog = load_sales_catalog()
    with SessionLocal() as session:
        result = seed_sales(session, catalog)

    print(f"Sales seed completed: {result.products_created} product(s) created.")


if __name__ == "__main__":
    main()
