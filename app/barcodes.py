from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, Response
from flask_login import login_required
from sqlalchemy import func
from . import db
from .barcode import svg, is_valid
from .models import BarcodeRange, Copy, Student, Section, Enrollment, School
from .util import L, msg
from .circ import current_year

bp = Blueprint("barcodes", __name__, url_prefix="/barcodes")
MAX_RANGE = 5000


def used_codes():
    return ({c for (c,) in db.session.query(func.upper(Copy.barcode)).all()} |
            {c for (c,) in db.session.query(func.upper(Student.barcode)).filter(Student.barcode.isnot(None)).all()})


def barcode_in_use(code):
    code = code.upper()
    return bool(Copy.query.filter(func.upper(Copy.barcode) == code).first()
                or Student.query.filter(func.upper(Student.barcode) == code).first())


def free_codes(kind, limit=None):
    """Unused codes from ranges of `kind`, in range order."""
    used = used_codes()
    out = []
    for r in BarcodeRange.query.filter_by(kind=kind).order_by(BarcodeRange.id).all():
        for n in range(r.start, r.end + 1):
            c = r.code(n)
            if c not in used:
                out.append(c)
                if limit and len(out) >= limit:
                    return out
    return out


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        kind = request.form.get("kind")
        prefix = request.form.get("prefix", "").strip().upper()
        try:
            start, end = int(request.form["start"]), int(request.form["end"])
            padding = int(request.form.get("padding") or 0)
        except (KeyError, ValueError):
            msg("required", "err")
            return redirect(url_for("barcodes.index"))
        if kind not in ("copy", "student") or start < 0 or end < start:
            msg("bad_range", "err")
        elif end - start + 1 > MAX_RANGE:
            msg("range_too_big", "err", n=MAX_RANGE)
        elif not is_valid(prefix + str(end).zfill(padding)):
            msg("bad_chars", "err")
        elif any(r.prefix == prefix and r.padding == padding and not (end < r.start or start > r.end)
                 for r in BarcodeRange.query.all()):
            msg("range_overlap", "err")
        else:
            db.session.add(BarcodeRange(kind=kind, prefix=prefix, start=start, end=end, padding=padding,
                                        note=request.form.get("note", "").strip()))
            db.session.commit()
            msg("saved")
        return redirect(url_for("barcodes.index"))
    used = used_codes()
    rows = []
    for r in BarcodeRange.query.order_by(BarcodeRange.id).all():
        n_used = sum(1 for n in range(r.start, r.end + 1) if r.code(n) in used)
        rows.append((r, n_used, r.end - r.start + 1))
    return render_template("barcodes.html", rows=rows)


@bp.route("/<int:rid>/delete", methods=["POST"])
@login_required
def delete(rid):
    r = db.session.get(BarcodeRange, rid)
    used = used_codes()
    if any(r.code(n) in used for n in range(r.start, r.end + 1)):
        msg("in_use", "err")
    else:
        db.session.delete(r)
        db.session.commit()
        msg("deleted")
    return redirect(url_for("barcodes.index"))


@bp.route("/<int:rid>/labels")
@login_required
def labels(rid):
    r = db.session.get(BarcodeRange, rid)
    lo = request.args.get("from", type=int) or r.start
    hi = request.args.get("to", type=int) or r.end
    lo, hi = max(lo, r.start), min(hi, r.end)
    cols = min(max(request.args.get("cols", 3, type=int), 1), 6)
    skip = max(request.args.get("skip", 0, type=int), 0)  # already-used cells on a partly printed sheet
    items = [(r.code(n), "") for n in range(lo, hi + 1)]
    return render_template("labels.html", items=items, skip=skip, cols=cols,
                           title=f"{r.prefix}{str(lo).zfill(r.padding)} – {r.prefix}{str(hi).zfill(r.padding)}",
                           svg=svg)


@bp.route("/student-cards")
@login_required
def student_cards():
    """Printable patron labels for the students of a section (or all) in the current year."""
    y = current_year()
    sid = request.args.get("section_id", type=int)
    q = (db.session.query(Student).join(Enrollment, Enrollment.student_id == Student.id)
         .filter(Enrollment.year_id == (y.id if y else 0), Student.barcode.isnot(None)))
    if sid:
        q = q.filter(Enrollment.section_id == sid)
    studs = q.order_by(Student.student_no).all()
    cols = min(max(request.args.get("cols", 3, type=int), 1), 6)
    items = [(s.barcode, s.name(L())) for s in studs]
    sections = Section.query.filter_by(year_id=y.id).all() if y else []
    return render_template("labels.html", items=items, cols=cols, skip=0, title="", svg=svg,
                           sections=sections, section_id=sid, cards=True)


@bp.route("/<int:rid>/assign", methods=["POST"])
@login_required
def assign_students(rid):
    """Give every student of the current year who has no barcode the next free code from this range."""
    r = db.session.get(BarcodeRange, rid)
    if r.kind != "student":
        return redirect(url_for("barcodes.index"))
    y = current_year()
    studs = (db.session.query(Student).join(Enrollment, Enrollment.student_id == Student.id)
             .filter(Enrollment.year_id == (y.id if y else 0), Enrollment.status == "active",
                     (Student.barcode.is_(None)) | (Student.barcode == ""))
             .order_by(Student.student_no).all())
    used = used_codes()
    free = [r.code(n) for n in range(r.start, r.end + 1) if r.code(n) not in used]
    if len(free) < len(studs):
        msg("range_exhausted", "err", need=len(studs), have=len(free))
        return redirect(url_for("barcodes.index"))
    for s, code in zip(studs, free):
        s.barcode = code
    db.session.commit()
    msg("assigned_n", n=len(studs))
    return redirect(url_for("barcodes.index"))


@bp.route("/img/<code>.svg")
def image(code):
    try:
        return Response(svg(code), mimetype="image/svg+xml")
    except ValueError:
        return Response("bad", status=400)
