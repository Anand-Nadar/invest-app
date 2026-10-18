from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.roadmap import BY_SLUG, NAV_PHASES, PHASES
from datetime import date

from app.services import finance as finance_svc
from app.services.periods import month_bounds
from app.services.system import get_system_status
from app.templating import templates

router = APIRouter(include_in_schema=False)


def _ctx(active: str, **extra) -> dict:
    return {"settings": get_settings(), "nav": NAV_PHASES, "phases": PHASES, "active": active, **extra}


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    system = get_system_status(db)
    nw = month = None
    if system["database_ok"]:
        try:
            nw = finance_svc.net_worth(db)
            month = finance_svc.period_summary(db, *month_bounds(date.today()))
        except SQLAlchemyError:  # finance tables missing until `alembic upgrade head`
            db.rollback()
            nw = month = None
    return templates.TemplateResponse(
        request, "dashboard.html",
        _ctx("dashboard", system=system, nw=nw, month=month, has_accounts=bool(nw and nw.account_rows)),
    )


@router.get("/{slug}")
def section(request: Request, slug: str):
    phase = BY_SLUG.get(slug)
    if phase is None or phase.nav_label is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "placeholder.html", _ctx(slug, phase=phase))
