from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.db.session import get_db
from backend.modules.staffing.schemas import (
    CoverageReportResponse,
    EmployeeCreate,
    EmployeeResponse,
    ShiftCreate,
    ShiftResponse,
)
from backend.modules.staffing.service import (
    DEFAULT_CAFE_ID,
    CafeNotFoundError,
    EmployeeInactiveError,
    EmployeeNotFoundError,
    ShiftNotFoundError,
    ShiftOverlapError,
    StaffingService,
    StaffingValidationError,
)

router = APIRouter(tags=["Staffing"])


@router.post(
    "/employees",
    response_model=EmployeeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_employee(
    request: EmployeeCreate,
    database_session: Session = Depends(get_db),
) -> EmployeeResponse:
    """Create an internal/demo-only employee; authentication is pending."""
    try:
        return StaffingService(database_session).create_employee(request)
    except CafeNotFoundError as error:
        raise HTTPException(status_code=404, detail="Cafe not found") from error


@router.get("/employees", response_model=list[EmployeeResponse])
def list_employees(
    cafe_id: str = Query(DEFAULT_CAFE_ID),
    database_session: Session = Depends(get_db),
) -> list[EmployeeResponse]:
    try:
        return StaffingService(database_session).list_employees(cafe_id)
    except CafeNotFoundError as error:
        raise HTTPException(status_code=404, detail="Cafe not found") from error


@router.get("/employees/{employee_id}", response_model=EmployeeResponse)
def get_employee(
    employee_id: str,
    cafe_id: str = Query(DEFAULT_CAFE_ID),
    database_session: Session = Depends(get_db),
) -> EmployeeResponse:
    try:
        return StaffingService(database_session).get_employee(employee_id, cafe_id)
    except CafeNotFoundError as error:
        raise HTTPException(status_code=404, detail="Cafe not found") from error
    except EmployeeNotFoundError as error:
        raise HTTPException(status_code=404, detail="Employee not found") from error


@router.post(
    "/shifts",
    response_model=ShiftResponse,
    status_code=status.HTTP_201_CREATED,
)
def schedule_shift(
    request: ShiftCreate,
    database_session: Session = Depends(get_db),
) -> ShiftResponse:
    """Schedule an internal/demo-only shift; authentication is pending."""
    try:
        return StaffingService(database_session).schedule_shift(request)
    except CafeNotFoundError as error:
        raise HTTPException(status_code=404, detail="Cafe not found") from error
    except EmployeeNotFoundError as error:
        raise HTTPException(status_code=404, detail="Employee not found") from error
    except EmployeeInactiveError as error:
        raise HTTPException(status_code=409, detail="Employee is inactive") from error
    except ShiftOverlapError as error:
        raise HTTPException(status_code=409, detail="Shift overlaps an existing shift") from error


@router.get("/shifts", response_model=list[ShiftResponse])
def list_shifts(
    start_at: datetime = Query(),
    end_at: datetime = Query(),
    cafe_id: str = Query(DEFAULT_CAFE_ID),
    database_session: Session = Depends(get_db),
) -> list[ShiftResponse]:
    try:
        return StaffingService(database_session).list_shifts(cafe_id, start_at, end_at)
    except CafeNotFoundError as error:
        raise HTTPException(status_code=404, detail="Cafe not found") from error
    except StaffingValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/shifts/{shift_id}/cancel", response_model=ShiftResponse)
def cancel_shift(
    shift_id: str,
    cafe_id: str = Query(DEFAULT_CAFE_ID),
    database_session: Session = Depends(get_db),
) -> ShiftResponse:
    try:
        return StaffingService(database_session).cancel_shift(shift_id, cafe_id)
    except CafeNotFoundError as error:
        raise HTTPException(status_code=404, detail="Cafe not found") from error
    except ShiftNotFoundError as error:
        raise HTTPException(status_code=404, detail="Shift not found") from error


@router.get("/reports/coverage", response_model=CoverageReportResponse)
def coverage_report(
    start_at: datetime = Query(),
    end_at: datetime = Query(),
    cafe_id: str = Query(DEFAULT_CAFE_ID),
    required_staff: int = Query(2, ge=1),
    database_session: Session = Depends(get_db),
) -> CoverageReportResponse:
    try:
        return StaffingService(database_session).coverage_report(
            cafe_id,
            start_at,
            end_at,
            required_staff,
        )
    except CafeNotFoundError as error:
        raise HTTPException(status_code=404, detail="Cafe not found") from error
    except StaffingValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
