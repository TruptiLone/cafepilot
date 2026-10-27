from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.db.session import get_db
from backend.modules.inventory.schemas import InventoryItemResponse
from backend.modules.inventory.schemas import (
    StockCountCreate,
    StockCountResponse,
    StockCountSummaryResponse,
    StockCountVerify,
)
from backend.modules.inventory.service import (
    DEFAULT_CAFE_ID,
    CafeNotFoundError,
    InventoryItemNotFoundError,
    InventoryService,
    StockCountConflictError,
    StockCountNotFoundError,
    StockCountValidationError,
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


@router.post(
    "/counts",
    response_model=StockCountResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_stock_count(
    request: StockCountCreate,
    database_session: Session = Depends(get_db),
) -> StockCountResponse:
    """Create an internal/demo-only physical observation; authentication is pending."""
    try:
        return InventoryService(database_session).create_count(request)
    except CafeNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cafe not found") from error
    except InventoryItemNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventory item not found for cafe",
        ) from error


@router.get("/counts", response_model=list[StockCountSummaryResponse])
def list_stock_counts(
    cafe_id: str = Query(DEFAULT_CAFE_ID),
    status_filter: str | None = Query(default=None, alias="status"),
    database_session: Session = Depends(get_db),
) -> list[StockCountSummaryResponse]:
    try:
        return InventoryService(database_session).list_counts(cafe_id, status_filter)
    except CafeNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cafe not found") from error


@router.post("/counts/{count_id}/verify", response_model=StockCountResponse)
def verify_stock_count(
    count_id: str,
    request: StockCountVerify,
    cafe_id: str = Query(DEFAULT_CAFE_ID),
    database_session: Session = Depends(get_db),
) -> StockCountResponse:
    """Verify a draft count; count-line editing and voiding are not available."""
    try:
        return InventoryService(database_session).verify_count(count_id, request, cafe_id)
    except CafeNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cafe not found") from error
    except StockCountNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stock count not found") from error
    except StockCountConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except StockCountValidationError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


@router.get("/counts/{count_id}", response_model=StockCountResponse)
def get_stock_count(
    count_id: str,
    cafe_id: str = Query(DEFAULT_CAFE_ID),
    database_session: Session = Depends(get_db),
) -> StockCountResponse:
    try:
        return InventoryService(database_session).get_count(count_id, cafe_id)
    except CafeNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cafe not found") from error
    except StockCountNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stock count not found") from error
