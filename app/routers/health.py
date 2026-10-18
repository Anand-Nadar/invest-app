from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.services.system import get_system_status

router = APIRouter(tags=["system"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    s = get_settings()
    st = get_system_status(db)
    return {
        "status": "ok" if st["database_ok"] else "degraded",
        "app": s.app_name,
        "version": s.app_version,
        "environment": s.environment,
        "database": "ok" if st["database_ok"] else "unavailable",
        "migrations_current": st["migrations_current"],
    }
