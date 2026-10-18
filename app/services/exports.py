"""CSV and Excel export helpers."""
import csv
import io
from datetime import date
from decimal import Decimal

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe(value):
    """Stop spreadsheet apps from running user text as a formula (CSV/Excel injection)."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def to_csv(headers, rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for row in rows:
        w.writerow(["" if v is None else _safe(v) for v in row])
    return buf.getvalue().encode("utf-8-sig")  # BOM so Excel reads UTF-8 (₹) correctly


def to_xlsx(headers, rows, sheet_name: str = "Data") -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]
    ws.append(list(headers))
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append([_safe(v) for v in row])
    for col in ws.columns:
        for cell in col[1:]:
            if isinstance(cell.value, Decimal):
                cell.number_format = "#,##0.00"
            elif isinstance(cell.value, date):
                cell.number_format = "yyyy-mm-dd"
        width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 50)
    ws.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
