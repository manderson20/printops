from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz", tags=["health"])
def healthz():
    return {"status": "ok", "service": "printops-api"}
