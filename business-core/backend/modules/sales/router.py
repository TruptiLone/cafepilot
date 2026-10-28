from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from backend.db.session import get_db
from backend.modules.sales.schemas import (
    IngredientUsageResponse,
    MenuProductResponse,
    SaleCreate,
    SaleResponse,
    SalesReportResponse,
    SaleSummaryResponse,
)
from backend.modules.sales.service import (
    DEFAULT_CAFE_ID,
    CafeNotFoundError,
    ProductNotFoundError,
    SaleConflictError,
    SaleNotFoundError,
    SalesService,
    SalesValidationError,
)

router = APIRouter(tags=["Sales"])


@router.get("/products", response_model=list[MenuProductResponse])
def list_products(
    cafe_id: str = Query(DEFAULT_CAFE_ID),
    database_session: Session = Depends(get_db),
) -> list[MenuProductResponse]:
    try:
        return SalesService(database_session).list_products(cafe_id)
    except CafeNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cafe not found") from error


@router.get("/reports/summary", response_model=SalesReportResponse)
def sales_summary(
    cafe_id: str = Query(DEFAULT_CAFE_ID),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    database_session: Session = Depends(get_db),
) -> SalesReportResponse:
    try:
        return SalesService(database_session).report(cafe_id, start_at, end_at)
    except CafeNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cafe not found") from error
    except SalesValidationError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


@router.post("", response_model=SaleResponse, status_code=status.HTTP_201_CREATED)
def create_sale(
    request: SaleCreate,
    response: Response,
    database_session: Session = Depends(get_db),
) -> SaleResponse:
    """Record an internal/demo-only completed sale; authentication is pending."""
    try:
        sale, created = SalesService(database_session).create_sale(request)
        if not created:
            response.status_code = status.HTTP_200_OK
        return sale
    except CafeNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cafe not found") from error
    except ProductNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found for cafe") from error
    except SaleConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except SalesValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


@router.get("", response_model=list[SaleSummaryResponse])
def list_sales(
    cafe_id: str = Query(DEFAULT_CAFE_ID),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    database_session: Session = Depends(get_db),
) -> list[SaleSummaryResponse]:
    try:
        return SalesService(database_session).list_sales(cafe_id, start_at, end_at)
    except CafeNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cafe not found") from error
    except SalesValidationError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


@router.get("/{sale_id}", response_model=SaleResponse)
def get_sale(
    sale_id: str,
    cafe_id: str = Query(DEFAULT_CAFE_ID),
    database_session: Session = Depends(get_db),
) -> SaleResponse:
    try:
        return SalesService(database_session).get_sale(sale_id, cafe_id)
    except CafeNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cafe not found") from error
    except SaleNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sale not found") from error


@router.get(
    "/{sale_id}/ingredient-usage",
    response_model=IngredientUsageResponse,
)
def get_ingredient_usage(
    sale_id: str,
    cafe_id: str = Query(DEFAULT_CAFE_ID),
    database_session: Session = Depends(get_db),
) -> IngredientUsageResponse:
    try:
        return SalesService(database_session).get_ingredient_usage(sale_id, cafe_id)
    except CafeNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cafe not found",
        ) from error
    except SaleNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sale not found",
        ) from error
    except SalesValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
