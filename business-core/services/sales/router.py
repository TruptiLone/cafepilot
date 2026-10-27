from fastapi import APIRouter

router = APIRouter(tags=["Sales"])


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "module": "sales"}
