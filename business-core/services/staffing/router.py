from fastapi import APIRouter

router = APIRouter(tags=["Staffing"])


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "module": "staffing"}
