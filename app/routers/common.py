"""Helpers shared by the finance and portfolio routers."""
from datetime import date

from fastapi import HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def form_data(request: Request) -> dict[str, str]:
    form = await request.form()
    return {k: v for k, v in form.items() if isinstance(v, str)}


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def get_or_404(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404)
    return obj


def safe_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def safe_int(value: str) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def attachment(data: bytes, filename: str, media_type: str) -> Response:
    return Response(data, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
