import csv
import io
from openpyxl import load_workbook

COLS = ["student_no", "name_ar", "name_en", "email", "stage", "class", "section"]


def read_rows(file_storage):
    """Return list of dicts with normalized lowercase headers from CSV or XLSX."""
    name = (file_storage.filename or "").lower()
    data = file_storage.read()
    rows = []
    if name.endswith(".xlsx"):
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        header = [str(h or "").strip().lower() for h in next(it, [])]
        for r in it:
            if r is None or all(c in (None, "") for c in r):
                continue
            rows.append({header[i]: ("" if c is None else str(c).strip()) for i, c in enumerate(r) if i < len(header)})
    else:
        text = data.decode("utf-8-sig", errors="replace")
        rdr = csv.DictReader(io.StringIO(text))
        for r in rdr:
            rows.append({(k or "").strip().lower(): (v or "").strip() for k, v in r.items()})
    return rows


def template_csv():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(COLS)
    w.writerow(["1001", "أحمد محمد", "Ahmed Mohammed", "ahmed@school.edu", "الابتدائية", "الصف الأول", "أ"])
    return "\ufeff" + out.getvalue()
