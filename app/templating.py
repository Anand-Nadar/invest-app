from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.formatting import format_inr, format_pct

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.filters["inr"] = format_inr
templates.env.filters["pct"] = format_pct
templates.env.filters["month_label"] = lambda d: d.strftime("%b %Y")
