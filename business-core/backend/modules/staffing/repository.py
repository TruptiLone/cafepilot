from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from backend.models.cafe import Cafe
from backend.modules.staffing.models import Employee, Shift


class StaffingRepository:
    def __init__(self, database_session: Session) -> None:
        self._database_session = database_session

    def get_cafe(self, cafe_id: str) -> Cafe | None:
        return self._database_session.get(Cafe, cafe_id)

    def create_employee(self, employee: Employee) -> None:
        self._database_session.add(employee)
        self._database_session.flush()

    def get_employee(self, cafe_id: str, employee_id: str) -> Employee | None:
        return self._database_session.scalar(
            select(Employee).where(
                Employee.cafe_id == cafe_id,
                Employee.employee_id == employee_id,
            )
        )

    def list_employees(self, cafe_id: str) -> list[Employee]:
        statement = (
            select(Employee)
            .where(Employee.cafe_id == cafe_id)
            .order_by(Employee.name, Employee.employee_id)
        )
        return list(self._database_session.scalars(statement))

    def create_shift(self, shift: Shift) -> None:
        self._database_session.add(shift)
        self._database_session.flush()

    def get_shift(
        self,
        cafe_id: str,
        shift_id: str,
        for_update: bool = False,
    ) -> tuple[Shift, Employee] | None:
        statement = (
            select(Shift, Employee)
            .join(
                Employee,
                and_(
                    Shift.employee_id == Employee.employee_id,
                    Shift.cafe_id == Employee.cafe_id,
                ),
            )
            .where(Shift.cafe_id == cafe_id, Shift.shift_id == shift_id)
        )
        if for_update:
            statement = statement.with_for_update(of=Shift)
        return self._database_session.execute(statement).one_or_none()

    def list_shifts(
        self,
        cafe_id: str,
        start_at: datetime,
        end_at: datetime,
        scheduled_only: bool = False,
    ) -> list[tuple[Shift, Employee]]:
        statement = (
            select(Shift, Employee)
            .join(
                Employee,
                and_(
                    Shift.employee_id == Employee.employee_id,
                    Shift.cafe_id == Employee.cafe_id,
                ),
            )
            .where(
                Shift.cafe_id == cafe_id,
                Shift.start_at < end_at,
                Shift.end_at > start_at,
            )
        )
        if scheduled_only:
            statement = statement.where(Shift.status == "scheduled")
        statement = statement.order_by(Shift.start_at, Shift.employee_id, Shift.shift_id)
        return list(self._database_session.execute(statement))
