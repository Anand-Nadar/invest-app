from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.formatting import format_inr, format_inr_signed, format_pct, format_pct_signed, format_price, format_qty

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.filters["inr"] = format_inr
templates.env.filters["pct"] = format_pct
templates.env.filters["inr_signed"] = format_inr_signed
templates.env.filters["pct_signed"] = format_pct_signed
templates.env.filters["qty"] = format_qty
templates.env.filters["price"] = format_price
templates.env.filters["month_label"] = lambda d: d.strftime("%b %Y")
