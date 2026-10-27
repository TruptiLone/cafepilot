from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.db.session import get_db
from backend.modules.inventory.schemas import InventoryItemResponse
from backend.modules.inventory.service import (
    DEFAULT_CAFE_ID,
    CafeNotFoundError,
    InventoryItemNotFoundError,
    InventoryService,
)

router = APIRouter(tags=["Inventory"])


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "module": "inventory"}


@router.get("/items", response_model=list[InventoryItemResponse])
def list_inventory_items(
    cafe_id: str = Query(DEFAULT_CAFE_ID),
    category: str | None = Query(default=None),
    include_inactive: bool = Query(default=False),
    database_session: Session = Depends(get_db),
) -> list[InventoryItemResponse]:
    try:
        return InventoryService(database_session).list_items(
            cafe_id=cafe_id,
            category=category,
            include_inactive=include_inactive,
        )
    except CafeNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cafe not found",
        ) from error


@router.get("/items/{ingredient_id}", response_model=InventoryItemResponse)
def get_inventory_item(
    ingredient_id: str,
    cafe_id: str = Query(DEFAULT_CAFE_ID),
    database_session: Session = Depends(get_db),
) -> InventoryItemResponse:
    try:
        return InventoryService(database_session).get_item(
            ingredient_id=ingredient_id,
            cafe_id=cafe_id,
        )
    except CafeNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cafe not found",
        ) from error
    except InventoryItemNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventory item not found",
        ) from error
