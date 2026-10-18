from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.roadmap import BY_SLUG, NAV_PHASES, PHASES
from app.services.system import get_system_status
from app.templating import templates

router = APIRouter(include_in_schema=False)


def _ctx(active: str, **extra) -> dict:
    return {"settings": get_settings(), "nav": NAV_PHASES, "phases": PHASES, "active": active, **extra}


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "dashboard.html", _ctx("dashboard", system=get_system_status(db))
    )


@router.get("/{slug}")
def section(request: Request, slug: str):
    phase = BY_SLUG.get(slug)
    if phase is None or phase.nav_label is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "placeholder.html", _ctx(slug, phase=phase))
