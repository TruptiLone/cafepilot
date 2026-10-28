from sqlalchemy import Boolean, DateTime, Text, UniqueConstraint, inspect
from sqlalchemy.dialects.postgresql import ExcludeConstraint

from backend.db.base import Base
from backend.models.cafe import Cafe
from backend.modules.inventory.models import InventoryItem, InventoryMovement, StockCount
from backend.modules.sales.models import MenuProduct, Sale
from backend.modules.staffing.models import Employee, Shift


def _constraint_columns(table, constraint_type):
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, constraint_type)
    }


def _check_constraint_sql(table) -> set[str]:
    return {
        str(constraint.sqltext)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }


def test_staffing_tables_are_registered_in_shared_metadata() -> None:
    assert {"cafes", "employees", "shifts"}.issubset(Base.metadata.tables)


def test_staffing_columns_constraints_and_indexes_are_defined() -> None:
    employees = Base.metadata.tables["employees"]
    shifts = Base.metadata.tables["shifts"]

    assert isinstance(employees.c.employee_id.type, Text)
    assert isinstance(employees.c.active.type, Boolean)
    assert isinstance(employees.c.created_at.type, DateTime)
    assert employees.c.created_at.type.timezone
    assert shifts.c.start_at.type.timezone
    assert shifts.c.end_at.type.timezone
    assert shifts.c.created_at.type.timezone
    assert employees.primary_key.columns.keys() == ["employee_id"]
    assert shifts.primary_key.columns.keys() == ["shift_id"]
    assert ("employee_id", "cafe_id") in _constraint_columns(
        employees,
        UniqueConstraint,
    )
    assert "start_at < end_at" in _check_constraint_sql(shifts)
    assert "status IN ('scheduled', 'cancelled')" in _check_constraint_sql(shifts)
    assert "fk_shifts_employee_cafe" in {
        constraint.name for constraint in shifts.foreign_key_constraints
    }
    exclusion = next(
        constraint
        for constraint in shifts.constraints
        if isinstance(constraint, ExcludeConstraint)
    )
    assert exclusion.name == "excl_shifts_employee_scheduled_overlap"
    assert "ix_employees_cafe_id_active" in {
        index.name for index in employees.indexes
    }
    assert "ix_shifts_cafe_id_start_at" in {index.name for index in shifts.indexes}


def test_staffing_relationships_configure_without_overlap_warnings() -> None:
    assert inspect(Employee).relationships["cafe"].mapper.class_ is Cafe
    assert inspect(Employee).relationships["shifts"].mapper.class_ is Shift
    assert inspect(Shift).relationships["cafe"].mapper.class_ is Cafe
    assert inspect(Shift).relationships["employee"].mapper.class_ is Employee
