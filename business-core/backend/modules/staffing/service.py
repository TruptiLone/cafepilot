from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models.cafe import Cafe
from backend.modules.staffing.models import Employee, Shift
from backend.modules.staffing.repository import StaffingRepository
from backend.modules.staffing.schemas import EmployeeCreate, ShiftCreate

DEFAULT_CAFE_ID = "CAFE001"


class CafeNotFoundError(LookupError):
    pass


class EmployeeNotFoundError(LookupError):
    pass


class EmployeeInactiveError(RuntimeError):
    pass


class ShiftNotFoundError(LookupError):
    pass


class ShiftOverlapError(RuntimeError):
    pass


class StaffingValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ShiftResult:
    shift_id: str
    cafe_id: str
    employee_id: str
    employee_name: str
    start_at: datetime
    end_at: datetime
    status: str
    created_at: datetime


@dataclass(frozen=True)
class CoverageHourResult:
    hour_start: datetime
    scheduled_employees: int
    required_staff: int
    coverage_status: str


@dataclass(frozen=True)
class CoverageReportResult:
    cafe_id: str
    start_at: datetime
    end_at: datetime
    required_staff: int
    hours: list[CoverageHourResult]


class StaffingService:
    def __init__(self, database_session: Session) -> None:
        self._database_session = database_session
        self._repository = StaffingRepository(database_session)

    def create_employee(self, request: EmployeeCreate) -> Employee:
        with self._transaction():
            self._require_cafe(request.cafe_id)
            employee = Employee(
                employee_id=f"employee_{uuid4().hex}",
                cafe_id=request.cafe_id,
                name=request.name,
                role=request.role,
                active=True,
                created_at=datetime.now(timezone.utc),
            )
            self._repository.create_employee(employee)
        return employee

    def list_employees(self, cafe_id: str = DEFAULT_CAFE_ID) -> list[Employee]:
        self._require_cafe(cafe_id)
        return self._repository.list_employees(cafe_id)

    def get_employee(
        self,
        employee_id: str,
        cafe_id: str = DEFAULT_CAFE_ID,
    ) -> Employee:
        self._require_cafe(cafe_id)
        employee = self._repository.get_employee(cafe_id, employee_id)
        if employee is None:
            raise EmployeeNotFoundError(employee_id)
        return employee

    def schedule_shift(self, request: ShiftCreate, shift_id: str | None = None) -> ShiftResult:
        """``shift_id`` lets a caller retry safely: the same id can only be created once."""
        shift_id = shift_id or f"shift_{uuid4().hex}"
        with self._transaction():
            self._require_cafe(request.cafe_id)
            employee = self._repository.get_employee(
                request.cafe_id,
                request.employee_id,
            )
            if employee is None:
                raise EmployeeNotFoundError(request.employee_id)
            if not employee.active:
                raise EmployeeInactiveError(request.employee_id)

            shift = Shift(
                shift_id=shift_id,
                cafe_id=request.cafe_id,
                employee_id=request.employee_id,
                start_at=request.start_at.astimezone(timezone.utc),
                end_at=request.end_at.astimezone(timezone.utc),
                status="scheduled",
                created_at=datetime.now(timezone.utc),
            )
            try:
                with self._database_session.begin_nested():
                    self._repository.create_shift(shift)
            except IntegrityError as error:
                if self._is_overlap_violation(error):
                    raise ShiftOverlapError(request.employee_id) from error
                raise

        return self.get_shift(shift_id, request.cafe_id)

    def list_shifts(
        self,
        cafe_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> list[ShiftResult]:
        self._require_cafe(cafe_id)
        self._validate_range(start_at, end_at)
        return [
            self._shift_result(shift, employee)
            for shift, employee in self._repository.list_shifts(
                cafe_id,
                start_at,
                end_at,
            )
        ]

    def get_shift(
        self,
        shift_id: str,
        cafe_id: str = DEFAULT_CAFE_ID,
    ) -> ShiftResult:
        self._require_cafe(cafe_id)
        row = self._repository.get_shift(cafe_id, shift_id)
        if row is None:
            raise ShiftNotFoundError(shift_id)
        return self._shift_result(*row)

    def cancel_shift(
        self,
        shift_id: str,
        cafe_id: str = DEFAULT_CAFE_ID,
    ) -> ShiftResult:
        with self._transaction():
            self._require_cafe(cafe_id)
            row = self._repository.get_shift(cafe_id, shift_id, for_update=True)
            if row is None:
                raise ShiftNotFoundError(shift_id)
            shift, employee = row
            shift.status = "cancelled"
            self._database_session.flush()
            result = self._shift_result(shift, employee)
        return result

    def coverage_report(
        self,
        cafe_id: str,
        start_at: datetime,
        end_at: datetime,
        required_staff: int,
    ) -> CoverageReportResult:
        cafe = self._require_cafe(cafe_id)
        self._validate_range(start_at, end_at)
        if required_staff < 1:
            raise StaffingValidationError("required_staff must be positive")
        try:
            cafe_timezone = ZoneInfo(cafe.timezone)
        except ZoneInfoNotFoundError as error:
            raise StaffingValidationError("cafe timezone is invalid") from error

        start_utc = start_at.astimezone(timezone.utc)
        end_utc = end_at.astimezone(timezone.utc)
        rows = self._repository.list_shifts(
            cafe_id,
            start_utc,
            end_utc,
            scheduled_only=True,
        )
        hours: list[CoverageHourResult] = []
        hour_start = start_utc
        while hour_start < end_utc:
            hour_end = min(hour_start + timedelta(hours=1), end_utc)
            employee_ids = {
                shift.employee_id
                for shift, _ in rows
                if shift.start_at < hour_end and shift.end_at > hour_start
            }
            scheduled_employees = len(employee_ids)
            hours.append(
                CoverageHourResult(
                    hour_start=hour_start.astimezone(cafe_timezone),
                    scheduled_employees=scheduled_employees,
                    required_staff=required_staff,
                    coverage_status=(
                        "adequate"
                        if scheduled_employees >= required_staff
                        else "understaffed"
                    ),
                )
            )
            hour_start += timedelta(hours=1)

        return CoverageReportResult(
            cafe_id=cafe_id,
            start_at=start_utc.astimezone(cafe_timezone),
            end_at=end_utc.astimezone(cafe_timezone),
            required_staff=required_staff,
            hours=hours,
        )

    def _require_cafe(self, cafe_id: str) -> Cafe:
        cafe = self._repository.get_cafe(cafe_id)
        if cafe is None:
            raise CafeNotFoundError(cafe_id)
        return cafe

    @staticmethod
    def _validate_range(start_at: datetime, end_at: datetime) -> None:
        for value in (start_at, end_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise StaffingValidationError("timestamps must include a timezone")
        if start_at >= end_at:
            raise StaffingValidationError("start_at must be before end_at")

    @staticmethod
    def _is_overlap_violation(error: IntegrityError) -> bool:
        original_error = error.orig
        return getattr(original_error, "sqlstate", None) == "23P01"

    @staticmethod
    def _shift_result(shift: Shift, employee: Employee) -> ShiftResult:
        return ShiftResult(
            shift_id=shift.shift_id,
            cafe_id=shift.cafe_id,
            employee_id=shift.employee_id,
            employee_name=employee.name,
            start_at=shift.start_at,
            end_at=shift.end_at,
            status=shift.status,
            created_at=shift.created_at,
        )

    def _transaction(self) -> AbstractContextManager:
        if self._database_session.in_transaction():
            return self._database_session.begin_nested()
        return self._database_session.begin()
