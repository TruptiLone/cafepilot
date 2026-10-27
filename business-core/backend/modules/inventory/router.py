from fastapi import APIRouter

router = APIRouter(tags=["Inventory"])


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "module": "inventory"}
