from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import SessionLocal, engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.staffing.models import Employee, Shift
from backend.modules.staffing.repository import StaffingRepository
from backend.modules.staffing.schemas import ShiftCreate
from backend.modules.staffing.service import ShiftOverlapError, StaffingService

TEST_CAFE_ID = "TEST_STAFFING_API_CAFE"
OTHER_CAFE_ID = "TEST_STAFFING_API_OTHER_CAFE"
TEST_EMPLOYEE_ID = "TEST_STAFFING_EMPLOYEE"
SECOND_EMPLOYEE_ID = "TEST_STAFFING_EMPLOYEE_2"
INACTIVE_EMPLOYEE_ID = "TEST_STAFFING_INACTIVE"
OTHER_EMPLOYEE_ID = "TEST_STAFFING_OTHER"


@pytest.fixture
def database_session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(database_session: Session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: database_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def staffing_catalog(database_session: Session) -> None:
    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    database_session.add_all(
        [
            Cafe(
                cafe_id=TEST_CAFE_ID,
                name="Staffing API Test",
                timezone="America/Los_Angeles",
            ),
            Cafe(cafe_id=OTHER_CAFE_ID, name="Other Staffing Test", timezone="UTC"),
            Employee(
                employee_id=TEST_EMPLOYEE_ID,
                cafe_id=TEST_CAFE_ID,
                name="Alex Barista",
                role="barista",
                active=True,
                created_at=created_at,
            ),
            Employee(
                employee_id=SECOND_EMPLOYEE_ID,
                cafe_id=TEST_CAFE_ID,
                name="Blair Lead",
                role="shift_lead",
                active=True,
                created_at=created_at,
            ),
            Employee(
                employee_id=INACTIVE_EMPLOYEE_ID,
                cafe_id=TEST_CAFE_ID,
                name="Inactive Employee",
                role="barista",
                active=False,
                created_at=created_at,
            ),
            Employee(
                employee_id=OTHER_EMPLOYEE_ID,
                cafe_id=OTHER_CAFE_ID,
                name="Other Employee",
                role="barista",
                active=True,
                created_at=created_at,
            ),
        ]
    )
    database_session.flush()


def _shift_payload(**overrides) -> dict:
    payload = {
        "cafe_id": TEST_CAFE_ID,
        "employee_id": TEST_EMPLOYEE_ID,
        "start_at": "2025-01-02T08:00:00-08:00",
        "end_at": "2025-01-02T12:00:00-08:00",
    }
    payload.update(overrides)
    return payload


@pytest.mark.database
def test_employee_create_list_detail_and_cafe_isolation(
    client: TestClient,
    staffing_catalog: None,
) -> None:
    response = client.post(
        "/api/v1/staffing/employees",
        json={"cafe_id": TEST_CAFE_ID, "name": "Casey Cook", "role": "cook"},
    )

    assert response.status_code == 201
    created = response.json()
    assert created["employee_id"].startswith("employee_")
    assert created["active"] is True
    detail = client.get(
        f"/api/v1/staffing/employees/{created['employee_id']}",
        params={"cafe_id": TEST_CAFE_ID},
    )
    assert detail.status_code == 200
    assert detail.json() == created

    employees = client.get(
        "/api/v1/staffing/employees",
        params={"cafe_id": TEST_CAFE_ID},
    )
    assert employees.status_code == 200
    assert [employee["name"] for employee in employees.json()] == [
        "Alex Barista",
        "Blair Lead",
        "Casey Cook",
        "Inactive Employee",
    ]
    assert client.get(
        f"/api/v1/staffing/employees/{created['employee_id']}",
        params={"cafe_id": OTHER_CAFE_ID},
    ).status_code == 404
    assert client.get(
        "/api/v1/staffing/employees/unknown",
        params={"cafe_id": TEST_CAFE_ID},
    ).status_code == 404
    assert client.post(
        "/api/v1/staffing/employees",
        json={"cafe_id": "UNKNOWN_CAFE", "name": "Test", "role": "barista"},
    ).status_code == 404


@pytest.mark.database
@pytest.mark.parametrize(
    "payload",
    [
        {"cafe_id": TEST_CAFE_ID, "name": " ", "role": "barista"},
        {"cafe_id": TEST_CAFE_ID, "name": "Test", "role": " "},
        {"cafe_id": TEST_CAFE_ID, "name": "Test", "role": "barista", "active": False},
    ],
)
def test_invalid_employee_requests_are_rejected(
    client: TestClient,
    staffing_catalog: None,
    payload: dict,
) -> None:
    assert client.post("/api/v1/staffing/employees", json=payload).status_code == 422


@pytest.mark.database
def test_shift_scheduling_overlap_boundaries_and_atomic_rollback(
    client: TestClient,
    staffing_catalog: None,
    database_session: Session,
) -> None:
    first = client.post("/api/v1/staffing/shifts", json=_shift_payload())
    overlap = client.post(
        "/api/v1/staffing/shifts",
        json=_shift_payload(
            start_at="2025-01-02T10:00:00-08:00",
            end_at="2025-01-02T13:00:00-08:00",
        ),
    )
    adjacent = client.post(
        "/api/v1/staffing/shifts",
        json=_shift_payload(
            start_at="2025-01-02T12:00:00-08:00",
            end_at="2025-01-02T13:00:00-08:00",
        ),
    )
    other_employee = client.post(
        "/api/v1/staffing/shifts",
        json=_shift_payload(
            employee_id=SECOND_EMPLOYEE_ID,
            start_at="2025-01-02T10:00:00-08:00",
            end_at="2025-01-02T13:00:00-08:00",
        ),
    )

    assert first.status_code == 201
    assert first.json()["start_at"] == "2025-01-02T16:00:00Z"
    assert overlap.status_code == 409
    assert adjacent.status_code == 201
    assert other_employee.status_code == 201
    assert database_session.scalar(
        select(func.count()).select_from(Shift).where(Shift.cafe_id == TEST_CAFE_ID)
    ) == 3


@pytest.mark.database
@pytest.mark.parametrize(
    "payload",
    [
        _shift_payload(start_at="2025-01-02T08:00:00", end_at="2025-01-02T09:00:00-08:00"),
        _shift_payload(start_at="2025-01-02T09:00:00-08:00", end_at="2025-01-02T09:00:00-08:00"),
        _shift_payload(start_at="2025-01-02T10:00:00-08:00", end_at="2025-01-02T09:00:00-08:00"),
    ],
)
def test_shift_requires_valid_timezone_aware_range(
    client: TestClient,
    staffing_catalog: None,
    payload: dict,
) -> None:
    assert client.post("/api/v1/staffing/shifts", json=payload).status_code == 422


@pytest.mark.database
def test_shift_validates_employee_cafe_and_active_status(
    client: TestClient,
    staffing_catalog: None,
    database_session: Session,
) -> None:
    assert client.post(
        "/api/v1/staffing/shifts",
        json=_shift_payload(employee_id="UNKNOWN_EMPLOYEE"),
    ).status_code == 404
    assert client.post(
        "/api/v1/staffing/shifts",
        json=_shift_payload(employee_id=OTHER_EMPLOYEE_ID),
    ).status_code == 404
    assert client.post(
        "/api/v1/staffing/shifts",
        json=_shift_payload(employee_id=INACTIVE_EMPLOYEE_ID),
    ).status_code == 409
    assert client.post(
        "/api/v1/staffing/shifts",
        json=_shift_payload(cafe_id="UNKNOWN_CAFE"),
    ).status_code == 404
    assert database_session.scalar(
        select(func.count()).select_from(Shift).where(Shift.cafe_id == TEST_CAFE_ID)
    ) == 0


@pytest.mark.database
def test_shift_list_is_range_filtered_deterministic_and_cafe_scoped(
    client: TestClient,
    staffing_catalog: None,
) -> None:
    later = client.post(
        "/api/v1/staffing/shifts",
        json=_shift_payload(
            employee_id=SECOND_EMPLOYEE_ID,
            start_at="2025-01-02T12:00:00-08:00",
            end_at="2025-01-02T16:00:00-08:00",
        ),
    ).json()
    earlier = client.post("/api/v1/staffing/shifts", json=_shift_payload()).json()

    response = client.get(
        "/api/v1/staffing/shifts",
        params={
            "cafe_id": TEST_CAFE_ID,
            "start_at": "2025-01-02T09:00:00-08:00",
            "end_at": "2025-01-02T13:00:00-08:00",
        },
    )

    assert response.status_code == 200
    assert [shift["shift_id"] for shift in response.json()] == [
        earlier["shift_id"],
        later["shift_id"],
    ]
    assert client.get(
        "/api/v1/staffing/shifts",
        params={
            "cafe_id": OTHER_CAFE_ID,
            "start_at": "2025-01-02T00:00:00+00:00",
            "end_at": "2025-01-03T00:00:00+00:00",
        },
    ).json() == []


@pytest.mark.database
def test_cancel_is_idempotent_preserves_history_and_releases_time(
    client: TestClient,
    staffing_catalog: None,
) -> None:
    shift = client.post("/api/v1/staffing/shifts", json=_shift_payload()).json()
    first_cancel = client.post(
        f"/api/v1/staffing/shifts/{shift['shift_id']}/cancel",
        params={"cafe_id": TEST_CAFE_ID},
    )
    second_cancel = client.post(
        f"/api/v1/staffing/shifts/{shift['shift_id']}/cancel",
        params={"cafe_id": TEST_CAFE_ID},
    )
    replacement = client.post("/api/v1/staffing/shifts", json=_shift_payload())

    assert first_cancel.status_code == 200
    assert first_cancel.json()["status"] == "cancelled"
    assert second_cancel.status_code == 200
    assert replacement.status_code == 201
    history = client.get(
        "/api/v1/staffing/shifts",
        params={
            "cafe_id": TEST_CAFE_ID,
            "start_at": "2025-01-02T08:00:00-08:00",
            "end_at": "2025-01-02T12:00:00-08:00",
        },
    ).json()
    assert {entry["status"] for entry in history} == {"scheduled", "cancelled"}
    assert client.post(
        "/api/v1/staffing/shifts/unknown/cancel",
        params={"cafe_id": TEST_CAFE_ID},
    ).status_code == 404
    assert client.post(
        f"/api/v1/staffing/shifts/{shift['shift_id']}/cancel",
        params={"cafe_id": OTHER_CAFE_ID},
    ).status_code == 404


@pytest.mark.database
def test_coverage_counts_unique_scheduled_employees_and_ignores_cancelled(
    client: TestClient,
    staffing_catalog: None,
) -> None:
    client.post(
        "/api/v1/staffing/shifts",
        json=_shift_payload(
            start_at="2025-01-02T08:00:00-08:00",
            end_at="2025-01-02T10:00:00-08:00",
        ),
    )
    second = client.post(
        "/api/v1/staffing/shifts",
        json=_shift_payload(
            employee_id=SECOND_EMPLOYEE_ID,
            start_at="2025-01-02T08:30:00-08:00",
            end_at="2025-01-02T09:30:00-08:00",
        ),
    ).json()
    client.post(
        f"/api/v1/staffing/shifts/{second['shift_id']}/cancel",
        params={"cafe_id": TEST_CAFE_ID},
    )

    response = client.get(
        "/api/v1/staffing/reports/coverage",
        params={
            "cafe_id": TEST_CAFE_ID,
            "start_at": "2025-01-02T08:00:00-08:00",
            "end_at": "2025-01-02T11:00:00-08:00",
            "required_staff": 2,
        },
    )

    assert response.status_code == 200
    assert response.json()["hours"] == [
        {
            "hour_start": "2025-01-02T08:00:00-08:00",
            "scheduled_employees": 1,
            "required_staff": 2,
            "coverage_status": "understaffed",
        },
        {
            "hour_start": "2025-01-02T09:00:00-08:00",
            "scheduled_employees": 1,
            "required_staff": 2,
            "coverage_status": "understaffed",
        },
        {
            "hour_start": "2025-01-02T10:00:00-08:00",
            "scheduled_employees": 0,
            "required_staff": 2,
            "coverage_status": "understaffed",
        },
    ]


@pytest.mark.database
def test_coverage_handles_overnight_shift(
    client: TestClient,
    staffing_catalog: None,
) -> None:
    client.post(
        "/api/v1/staffing/shifts",
        json=_shift_payload(
            start_at="2025-01-02T22:00:00-08:00",
            end_at="2025-01-03T02:00:00-08:00",
        ),
    )
    response = client.get(
        "/api/v1/staffing/reports/coverage",
        params={
            "cafe_id": TEST_CAFE_ID,
            "start_at": "2025-01-02T22:00:00-08:00",
            "end_at": "2025-01-03T02:00:00-08:00",
            "required_staff": 1,
        },
    )

    assert response.status_code == 200
    assert [hour["scheduled_employees"] for hour in response.json()["hours"]] == [
        1,
        1,
        1,
        1,
    ]
    assert [hour["coverage_status"] for hour in response.json()["hours"]] == [
        "adequate",
        "adequate",
        "adequate",
        "adequate",
    ]


@pytest.mark.database
@pytest.mark.parametrize(
    ("start_at", "end_at", "expected_hours"),
    [
        (
            "2025-03-09T00:00:00-08:00",
            "2025-03-09T04:00:00-07:00",
            [
                "2025-03-09T00:00:00-08:00",
                "2025-03-09T01:00:00-08:00",
                "2025-03-09T03:00:00-07:00",
            ],
        ),
        (
            "2025-11-02T00:00:00-07:00",
            "2025-11-02T03:00:00-08:00",
            [
                "2025-11-02T00:00:00-07:00",
                "2025-11-02T01:00:00-07:00",
                "2025-11-02T01:00:00-08:00",
                "2025-11-02T02:00:00-08:00",
            ],
        ),
    ],
)
def test_coverage_handles_daylight_saving_transitions(
    client: TestClient,
    staffing_catalog: None,
    start_at: str,
    end_at: str,
    expected_hours: list[str],
) -> None:
    response = client.get(
        "/api/v1/staffing/reports/coverage",
        params={
            "cafe_id": TEST_CAFE_ID,
            "start_at": start_at,
            "end_at": end_at,
        },
    )

    assert response.status_code == 200
    assert [hour["hour_start"] for hour in response.json()["hours"]] == expected_hours


@pytest.mark.database
def test_concurrent_overlapping_shifts_use_independent_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cafe_id = f"TEST_STAFFING_CONCURRENT_{uuid4().hex}"
    employee_id = f"TEST_STAFFING_EMPLOYEE_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(
            Cafe.__table__.insert(),
            {"cafe_id": cafe_id, "name": "Concurrent Staffing", "timezone": "UTC"},
        )
        connection.execute(
            Employee.__table__.insert(),
            {
                "employee_id": employee_id,
                "cafe_id": cafe_id,
                "name": "Concurrent Employee",
                "role": "barista",
                "active": True,
                "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            },
        )

    request = ShiftCreate(
        cafe_id=cafe_id,
        employee_id=employee_id,
        start_at=datetime(2025, 1, 2, 8, tzinfo=timezone.utc),
        end_at=datetime(2025, 1, 2, 12, tzinfo=timezone.utc),
    )
    barrier = Barrier(2)
    original_create = StaffingRepository.create_shift

    def synchronized_create(self, shift):
        if shift.employee_id == employee_id:
            barrier.wait(timeout=5)
        return original_create(self, shift)

    def submit() -> str:
        with SessionLocal() as session:
            try:
                StaffingService(session).schedule_shift(request)
                return "created"
            except ShiftOverlapError:
                return "overlap"

    monkeypatch.setattr(StaffingRepository, "create_shift", synchronized_create)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: submit(), range(2)))

        assert sorted(results) == ["created", "overlap"]
        with SessionLocal() as session:
            assert session.scalar(
                select(func.count()).select_from(Shift).where(
                    Shift.employee_id == employee_id
                )
            ) == 1
    finally:
        with engine.begin() as connection:
            connection.execute(delete(Shift).where(Shift.employee_id == employee_id))
            connection.execute(delete(Employee).where(Employee.employee_id == employee_id))
            connection.execute(delete(Cafe).where(Cafe.cafe_id == cafe_id))
