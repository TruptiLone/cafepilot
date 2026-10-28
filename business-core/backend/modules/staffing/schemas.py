from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def validate_aware_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value


def validate_nonblank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


class EmployeeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cafe_id: str = "CAFE001"
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)

    @field_validator("name", "role")
    @classmethod
    def validate_text_fields(cls, value: str, info) -> str:
        return validate_nonblank(value, info.field_name)


class EmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    employee_id: str
    cafe_id: str
    name: str
    role: str
    active: bool
    created_at: datetime


class ShiftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cafe_id: str = "CAFE001"
    employee_id: str = Field(min_length=1)
    start_at: datetime
    end_at: datetime

    @field_validator("employee_id")
    @classmethod
    def validate_employee_id(cls, value: str) -> str:
        return validate_nonblank(value, "employee_id")

    @field_validator("start_at", "end_at")
    @classmethod
    def validate_timestamps(cls, value: datetime) -> datetime:
        return validate_aware_timestamp(value)

    @model_validator(mode="after")
    def validate_time_range(self) -> "ShiftCreate":
        if self.start_at >= self.end_at:
            raise ValueError("start_at must be before end_at")
        return self


class ShiftResponse(BaseModel):
    shift_id: str
    cafe_id: str
    employee_id: str
    employee_name: str
    start_at: datetime
    end_at: datetime
    status: str
    created_at: datetime


class CoverageHourResponse(BaseModel):
    hour_start: datetime
    scheduled_employees: int
    required_staff: int
    coverage_status: str


class CoverageReportResponse(BaseModel):
    cafe_id: str
    start_at: datetime
    end_at: datetime
    required_staff: int
    hours: list[CoverageHourResponse]
