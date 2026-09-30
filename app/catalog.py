import re
from flask import Blueprint, render_template, request, redirect, url_for, Response
from sqlalchemy import or_, and_, func
from . import db
from .models import Title, Copy, Loan
from .auth import staff, admin_only
from .util import L, msg
from .i18n import tr
from .barcode import normalize_scan, is_valid
from .textnorm import tokens, build_search_text, clean_isbn, isbn_valid, isbn_variants
from .models import Hold
from . import holds
from .barcodes import free_codes, barcode_in_use
from .circ import mark_copy, CircError
from .importer import read_rows

bp = Blueprint("catalog", __name__, url_prefix="/catalog")
PER_PAGE = 20
STATUSES = ["available", "out", "held", "lost", "damaged", "withdrawn"]
SYSTEM_STATUSES = ("out", "held")  # set by circulation / holds, never picked by hand


# ---------- ISBN / search ----------
def refresh_search(t):
    t.search_text = build_search_text(t)


def search_titles(q="", category="", language="", location="", only_available=False):
    """Shared by the staff catalog and the student portal.
    Every word must appear (Arabic letter variants / diacritics / digits normalised); ISBN-10 and ISBN-13 are
    interchangeable; a copy barcode finds its title."""
    query = Title.query
    q = (q or "").strip()
    if q:
        parts = []
        toks = tokens(q)
        if toks:
            parts.append(and_(*[Title.search_text.contains(t, autoescape=True) for t in toks]))
        variants = isbn_variants(q)
        if variants:
            parts.append(Title.isbn.in_(variants))
        digits = clean_isbn(q)
        if len(digits) >= 6 and re.fullmatch(r"[0-9Xx\- ]+", q):
            parts.append(Title.isbn.contains(digits, autoescape=True))
        parts.append(Title.id.in_(db.session.query(Copy.title_id).filter(func.upper(Copy.barcode) == normalize_scan(q))))
        query = query.filter(or_(*parts))
    if category:
        query = query.filter(Title.category == category)
    if language:
        query = query.filter(Title.language == language)
    if location or only_available:
        sub = db.session.query(Copy.title_id)
        if location:
            sub = sub.filter(Copy.location == location)
        if only_available:
            sub = sub.filter(Copy.status == "available")
        query = query.filter(Title.id.in_(sub))
    return query.order_by(Title.title)


def copy_counts(title_ids):
    rows = (db.session.query(Copy.title_id, func.count(Copy.id),
                             func.sum(db.case((Copy.status == "available", 1), else_=0)))
            .filter(Copy.title_id.in_(title_ids)).group_by(Copy.title_id).all())
    return {tid: (n, int(a or 0)) for tid, n, a in rows}


def distinct_values(col):
    return [v for (v,) in db.session.query(col).filter(col != "", col.isnot(None)).distinct().order_by(col).all()]


@bp.route("/")
@staff
def index():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "")
    language = request.args.get("language", "")
    location = request.args.get("location", "")
    avail = bool(request.args.get("available"))
    page = request.args.get("page", 1, type=int)
    pag = search_titles(q, category, language, location, avail).paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template("catalog.html", pag=pag, q=q, category=category, language=language, location=location,
                           avail=avail, counts=copy_counts([t.id for t in pag.items]),
                           categories=distinct_values(Title.category), locations=distinct_values(Copy.location))


# ---------- titles ----------
def _fill_title(t, f):
    t.title = f.get("title", "").strip()
    t.title_alt = f.get("title_alt", "").strip()
    t.author = f.get("author", "").strip()
    t.publisher = f.get("publisher", "").strip()
    t.pub_year = f.get("pub_year", "").strip()
    t.category = f.get("category", "").strip()
    t.language = f.get("language", "ar")
    t.call_number = f.get("call_number", "").strip()


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:tid>/edit", methods=["GET", "POST"])
@staff
def title_form(tid=None):
    t = db.session.get(Title, tid) if tid else None
    if request.method == "POST":
        f = request.form
        isbn = clean_isbn(f.get("isbn"))
        dup = Title.query.filter(Title.isbn == isbn, Title.id != (tid or 0)).first() if isbn else None
        if not f.get("title", "").strip():
            msg("required", "err")
        elif dup:
            msg("isbn_exists", "err")
            return redirect(url_for("catalog.detail", tid=dup.id))
        else:
            t = t or Title()
            _fill_title(t, f)
            t.isbn = isbn or None
            refresh_search(t)
            db.session.add(t)
            db.session.commit()
            msg("saved")
            if isbn and not isbn_valid(isbn):
                msg("isbn_checksum", "err")
            return redirect(url_for("catalog.detail", tid=t.id))
    return render_template("title_form.html", ti=t, categories=distinct_values(Title.category))


@bp.route("/<int:tid>")
@staff
def detail(tid):
    t = db.session.get(Title, tid)
    loans = {l.copy_id: l for l in Loan.query.filter(Loan.copy_id.in_([c.id for c in t.copies]), Loan.returned_at.is_(None)).all()} if t.copies else {}
    return render_template("title_detail.html", ti=t, loans=loans, statuses=STATUSES,
                           shelves=distinct_values(Copy.shelf), locations=distinct_values(Copy.location),
                           free=len(free_codes("copy", 500)))


@bp.route("/<int:tid>/delete", methods=["POST"])
@staff
def title_delete(tid):
    t = db.session.get(Title, tid)
    if Hold.query.filter_by(title_id=tid).first() or (
            Loan.query.filter(Loan.copy_id.in_([c.id for c in t.copies])).first() if t.copies else False):
        msg("in_use", "err")
        return redirect(url_for("catalog.detail", tid=tid))
    db.session.delete(t)
    db.session.commit()
    msg("deleted")
    return redirect(url_for("catalog.index"))


# ---------- copies ----------
def add_copies(title, n, shelf, location, price, manual_code=None):
    """Create n copies. Barcodes come from the copy range(s) unless one is typed manually. Returns error key or None."""
    if manual_code:
        code = normalize_scan(manual_code)
        if n != 1:
            return "manual_single"
        if not is_valid(code):
            return "bad_chars"
        if barcode_in_use(code):
            return "barcode_exists"
        codes = [code]
    else:
        codes = free_codes("copy", n)
        if len(codes) < n:
            return "range_exhausted_copy"
    for c in codes:
        db.session.add(Copy(title_id=title.id, barcode=c, shelf=shelf, location=location, price=price))
    return None


@bp.route("/<int:tid>/copies", methods=["POST"])
@staff
def copies_add(tid):
    t = db.session.get(Title, tid)
    f = request.form
    n = min(max(int(f.get("count") or 1), 1), 200)
    try:
        price = float(f.get("price") or 0)
    except ValueError:
        price = 0.0
    err = add_copies(t, n, f.get("shelf", "").strip(), f.get("location", "").strip(), price,
                     f.get("barcode", "").strip() or None)
    if err:
        db.session.rollback()
        msg(err, "err", need=n)
    else:
        db.session.commit()
        holds.fill_holds(t.id)
        msg("copies_added", n=n)
    return redirect(url_for("catalog.detail", tid=tid))


@bp.route("/copy/<int:cid>", methods=["POST"])
@staff
def copy_update(cid):
    c = db.session.get(Copy, cid)
    f = request.form
    c.shelf, c.location = f.get("shelf", "").strip(), f.get("location", "").strip()
    try:
        c.price = float(f.get("price") or 0)
    except ValueError:
        pass
    new_status = f.get("status", c.status)
    if new_status != c.status and new_status in STATUSES and new_status not in SYSTEM_STATUSES:
        try:
            mark_copy(c, new_status)
        except CircError as e:
            db.session.rollback()
            msg(e.code, "err")
            return redirect(url_for("catalog.detail", tid=c.title_id))
    db.session.commit()
    msg("saved")
    return redirect(url_for("catalog.detail", tid=c.title_id))


@bp.route("/copy/<int:cid>/delete", methods=["POST"])
@staff
def copy_delete(cid):
    c = db.session.get(Copy, cid)
    tid = c.title_id
    if Loan.query.filter_by(copy_id=cid).first() or Hold.query.filter_by(copy_id=cid).first():
        msg("in_use", "err")
    else:
        db.session.delete(c)
        db.session.commit()
        msg("deleted")
    return redirect(url_for("catalog.detail", tid=tid))


# ---------- import ----------
COLS = ["isbn", "title", "title_alt", "author", "publisher", "year", "category", "language",
        "call_number", "copies", "shelf", "location", "price", "barcode"]


@bp.route("/import/template")
@staff
def import_template():
    body = "\ufeff" + ",".join(COLS) + "\n9780140449136,الأمير الصغير,The Little Prince,Saint-Exupery,Dar,2015,أدب,ar,843,2,A-3,المكتبة الرئيسية,25,\n"
    return Response(body, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=catalog_template.csv"})


@bp.route("/import", methods=["GET", "POST"])
@staff
def import_catalog():
    if request.method == "GET":
        return render_template("catalog_import.html", errs=None)
    f = request.files.get("file")
    if not f or not f.filename:
        msg("required", "err")
        return redirect(url_for("catalog.import_catalog"))
    made_titles = made_copies = 0
    errs = []
    for i, r in enumerate(read_rows(f), start=2):
        try:
            title_txt = r.get("title", "")
            isbn = clean_isbn(r.get("isbn"))
            t = Title.query.filter_by(isbn=isbn).first() if isbn else None
            if not t:
                if not title_txt:
                    raise ValueError("title required")
                t = Title(isbn=isbn or None)
                _fill_title(t, {**r, "pub_year": r.get("year", ""), "language": r.get("language") or "ar"})
                refresh_search(t)
                db.session.add(t)
                db.session.flush()
                made_titles += 1
            n = int(float(r.get("copies") or 0))
            if n:
                try:
                    price = float(r.get("price") or 0)
                except ValueError:
                    price = 0.0
                err = add_copies(t, n, r.get("shelf", ""), r.get("location", ""), price, r.get("barcode") or None)
                if err:
                    raise ValueError(err)
                made_copies += n
            db.session.commit()
            holds.fill_holds(t.id)
        except Exception as e:  # noqa - report the row and continue
            db.session.rollback()
            errs.append((i, tr(str(e), L())))
    return render_template("catalog_import.html", errs=errs, made_titles=made_titles, made_copies=made_copies)
