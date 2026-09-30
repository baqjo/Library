from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from . import db
from .i18n import tr
from .models import Policy, Holiday, Loan, Fine, Student, Copy, Title, Enrollment, Hold
from .auth import staff, admin_only
from .util import L, msg, parse_date
from . import circ, holds
from .circ import CircError

bp = Blueprint("circ", __name__)


def _err(e):
    return jsonify(ok=False, code=e.code, message=tr(e.code, L(), **e.ctx)), 200


def _patron_payload(s):
    y = circ.current_year()
    en = Enrollment.query.filter_by(student_id=s.id, year_id=y.id).first() if y else None
    t = circ.today()
    loans = []
    for l in circ.open_loans(s.id):
        loans.append(dict(id=l.id, title=l.copy.title.title, barcode=l.copy.barcode, due=l.due_date.isoformat(),
                          overdue=l.due_date < t, renewals=l.renewals))
    ready = [dict(title=h.title.title, barcode=h.copy.barcode if h.copy else "",
                  where=" · ".join(x for x in ((h.copy.shelf if h.copy else ""), (h.copy.location if h.copy else "")) if x))
             for h in holds.active_holds(s.id) if h.status == "ready"]
    return dict(id=s.id, name=s.name(L()), student_no=s.student_no, barcode=s.barcode or "", ready_holds=ready,
                klass=(tr("staff", L()) if s.patron_type == "staff" else (en.section.label(L()) if en and en.section else "")), loans=loans,
                fines=round(circ.unpaid_total(s.id), 2))


@bp.route("/circulation")
@staff
def desk():
    return render_template("circ.html", policy=Policy.get())


@bp.route("/api/circ/patron", methods=["POST"])
@staff
def api_patron():
    s = circ.find_student(request.json.get("code", ""))
    if not s:
        return jsonify(ok=False, code="patron_not_found", message=tr("patron_not_found", L()))
    return jsonify(ok=True, patron=_patron_payload(s))


@bp.route("/api/circ/checkout", methods=["POST"])
@staff
def api_checkout():
    d = request.json
    s = db.session.get(Student, d.get("patron_id"))
    c = circ.find_copy(d.get("code", ""))
    if not s:
        return jsonify(ok=False, code="patron_not_found", message=tr("patron_not_found", L()))
    if not c:
        return jsonify(ok=False, code="copy_not_found", message=tr("copy_not_found", L()))
    try:
        loan = circ.checkout(s, c)
    except CircError as e:
        return _err(e)
    return jsonify(ok=True, message=tr("checked_out", L(), title=c.title.title, due=loan.due_date.isoformat()),
                   patron=_patron_payload(s))


@bp.route("/api/circ/checkin", methods=["POST"])
@staff
def api_checkin():
    c = circ.find_copy(request.json.get("code", ""))
    if not c:
        return jsonify(ok=False, code="copy_not_found", message=tr("copy_not_found", L()))
    try:
        loan, fine = circ.checkin(c)
    except CircError as e:
        return _err(e)
    m = tr("checked_in", L(), title=c.title.title)
    if loan:
        m += " — " + loan.student.name(L())
    if fine:
        m += " — " + tr("fine_assessed", L(), amount=f"{fine.amount:g}")
    h = holds.hold_for_copy(c) if c.status == "held" else None
    if h:
        m += " — " + tr("set_aside", L(), name=h.student.name(L()))
    return jsonify(ok=True, message=m, fine=bool(fine), hold=bool(h))


@bp.route("/api/circ/renew", methods=["POST"])
@staff
def api_renew():
    loan = db.session.get(Loan, request.json.get("loan_id"))
    if not loan:
        return jsonify(ok=False, code="not_checked_out", message=tr("not_checked_out", L()))
    try:
        circ.renew(loan)
    except CircError as e:
        return _err(e)
    return jsonify(ok=True, message=tr("renewed", L(), due=loan.due_date.isoformat()),
                   patron=_patron_payload(loan.student))


# ---------- holds (staff view) ----------
@bp.route("/holds")
@staff
def holds_page():
    status = request.args.get("status", "ready")
    q = Hold.query
    if status == "closed":
        q = q.filter(Hold.status.notin_(holds.ACTIVE))
    else:
        q = q.filter_by(status=status if status in holds.ACTIVE else "ready")
    rows = q.order_by(Hold.id.desc() if status == "closed" else Hold.id).limit(300).all()
    return render_template("holds.html", rows=rows, status=status, queue=holds.queue_position)


@bp.route("/holds/<int:hid>/cancel", methods=["POST"])
@staff
def hold_cancel(hid):
    try:
        holds.cancel_hold(db.session.get(Hold, hid))
        msg("hold_cancelled")
    except CircError as e:
        msg(e.code, "err")
    return redirect(request.referrer or url_for("circ.holds_page"))


# ---------- fines ----------
@bp.route("/fines")
@staff
def fines():
    status = request.args.get("status", "unpaid")
    q = Fine.query
    if status in ("unpaid", "paid", "waived"):
        q = q.filter_by(status=status)
    rows = q.order_by(Fine.id.desc()).limit(300).all()
    return render_template("fines.html", rows=rows, status=status,
                           total=sum(f.amount for f in rows))


@bp.route("/fines/<int:fid>/<action>", methods=["POST"])
@staff
def fine_action(fid, action):
    f = db.session.get(Fine, fid)
    if action in ("paid", "waived") and f.status == "unpaid":
        f.status, f.settled_at = action, circ.now_local()
        db.session.commit()
        msg("saved")
    return redirect(request.referrer or url_for("circ.fines"))


# ---------- policy & holidays ----------
@bp.route("/policy", methods=["GET", "POST"])
@admin_only
def policy():
    p = Policy.get()
    if request.method == "POST":
        f = request.form
        def num(k, cast, lo=0):
            try:
                return max(cast(f.get(k) or 0), lo)
            except ValueError:
                return lo
        p.loan_days = num("loan_days", int, 1)
        p.max_loans = num("max_loans", int, 1)
        p.max_renewals = num("max_renewals", int)
        p.grace_days = num("grace_days", int)
        p.fine_per_day = num("fine_per_day", float)
        p.block_fine_amount = num("block_fine_amount", float)
        p.hold_pickup_days = num("hold_pickup_days", int, 1)
        p.max_holds = num("max_holds", int, 1)
        p.block_if_overdue = bool(f.get("block_if_overdue"))
        p.allow_renew_overdue = bool(f.get("allow_renew_overdue"))
        days = sorted({int(x) for x in f.getlist("closed") if x.isdigit() and 0 <= int(x) <= 6})
        p.closed_weekdays = ",".join(map(str, days))
        db.session.commit()
        msg("saved")
        return redirect(url_for("circ.policy"))
    return render_template("policy.html", p=p, holidays=Holiday.query.order_by(Holiday.day).all())


@bp.route("/policy/holiday", methods=["POST"])
@admin_only
def holiday_add():
    d = parse_date(request.form.get("day"))
    if not d:
        msg("required", "err")
    elif Holiday.query.filter_by(day=d).first():
        msg("duplicate", "err")
    else:
        db.session.add(Holiday(day=d, note=request.form.get("note", "").strip()))
        db.session.commit()
        msg("saved")
    return redirect(url_for("circ.policy"))


@bp.route("/policy/holiday/<int:hid>/delete", methods=["POST"])
@admin_only
def holiday_delete(hid):
    db.session.delete(db.session.get(Holiday, hid))
    db.session.commit()
    msg("deleted")
    return redirect(url_for("circ.policy"))
