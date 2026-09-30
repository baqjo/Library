"""Student portal: sign-in (Microsoft Entra ID and/or student number + PIN), search, holds, my account.
Also hosts the staff-side PIN management."""
import os
import secrets
import time
from datetime import datetime, timedelta
from flask import (make_response, Blueprint, render_template, request, redirect, url_for, session, g, abort)
from flask_login import logout_user
from sqlalchemy import func, or_, and_
from werkzeug.security import generate_password_hash, check_password_hash
from . import db, circ, holds
from .auth import staff, student_required
from .catalog import search_titles, distinct_values
from .circ import CircError
from .i18n import tr
from .models import (School, Student, StudentCredential, Title, Copy, Loan, Fine, Hold, Enrollment, Section)
from .util import L, msg

bp = Blueprint("portal", __name__)
PER_PAGE = 12
MAX_FAILS, LOCK_MINUTES = 5, 10
PIN_METHOD = "pbkdf2:sha256:30000"  # PINs are short and rate-limited by lockout; keeps bulk generation fast
DUMMY_HASH = generate_password_hash("000000", method=PIN_METHOD)


# ---------------------------------------------------------------- sign-in
def ms_configured():
    return all(os.environ.get(k) for k in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"))


def ms_app():
    import msal
    return msal.ConfidentialClientApplication(
        os.environ["AZURE_CLIENT_ID"], client_credential=os.environ["AZURE_CLIENT_SECRET"],
        authority="https://login.microsoftonline.com/" + os.environ["AZURE_TENANT_ID"])


def _safe_next(target):
    return target if target and target.startswith("/portal") and not target.startswith("//") else url_for("portal.search")


def _start_student_session(st):
    logout_user()  # a browser is either staff or student, never both
    session.clear()
    session["student_id"] = st.id
    session["student_ts"] = time.time()


def _pin_ok(student_no, pin):
    """Returns the Student on success. Unknown student, wrong PIN and locked account are indistinguishable."""
    st = Student.query.filter(func.upper(Student.student_no) == student_no.strip().upper()).first() if student_no else None
    cred = StudentCredential.query.filter_by(student_id=st.id).first() if st else None
    now = datetime.utcnow()
    if not cred:
        check_password_hash(DUMMY_HASH, pin or "")  # similar timing
        return None
    if cred.locked_until and cred.locked_until > now:
        return None
    if check_password_hash(cred.pin_hash, pin or ""):
        cred.failed_count, cred.locked_until, cred.last_login = 0, None, now
        db.session.commit()
        return st
    cred.failed_count = (cred.failed_count or 0) + 1
    if cred.failed_count >= MAX_FAILS:
        cred.failed_count, cred.locked_until = 0, now + timedelta(minutes=LOCK_MINUTES)
    db.session.commit()
    return None


@bp.route("/student/login", methods=["GET", "POST"])
def login():
    school = School.get()
    pin_on, ms_on = bool(school.pin_login_enabled), ms_configured()
    if request.method == "POST":
        if not pin_on:
            abort(403)
        st = _pin_ok(request.form.get("student_no", ""), request.form.get("pin", ""))
        if not st:
            msg("bad_login", "err")
        elif not circ.student_is_active(st):
            msg("not_active", "err")
        else:
            _start_student_session(st)
            return redirect(_safe_next(request.args.get("next")))
    return render_template("portal_login.html", pin_on=pin_on, ms_on=ms_on)


@bp.route("/student/microsoft")
def ms_start():
    if not ms_configured():
        abort(404)
    redirect_uri = os.environ.get("AZURE_REDIRECT_URI") or url_for("portal.ms_callback", _external=True)
    flow = ms_app().initiate_auth_code_flow(["User.Read"], redirect_uri=redirect_uri)
    session["ms_flow"] = flow
    return redirect(flow["auth_uri"])


@bp.route("/auth/callback")
def ms_callback():
    flow = session.pop("ms_flow", None)
    if not ms_configured() or not flow:
        msg("ms_failed", "err")
        return redirect(url_for("portal.login"))
    try:
        result = ms_app().acquire_token_by_auth_code_flow(flow, request.args.to_dict())
    except ValueError:  # state mismatch / tampering
        result = {}
    claims = result.get("id_token_claims") or {}
    if "error" in result or not claims or claims.get("tid") != os.environ["AZURE_TENANT_ID"]:
        msg("ms_failed", "err")
        return redirect(url_for("portal.login"))
    email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    matches = Student.query.filter(func.lower(Student.email) == email).all() if email else []
    if len(matches) != 1:  # none, or ambiguous (two students share the address)
        msg("ms_no_match", "err")
        return redirect(url_for("portal.login"))
    if not circ.student_is_active(matches[0]):
        msg("not_active", "err")
        return redirect(url_for("portal.login"))
    _start_student_session(matches[0])
    return redirect(url_for("portal.search"))


@bp.route("/student/logout")
def logout():
    for k in ("student_id", "student_ts"):
        session.pop(k, None)
    return redirect(url_for("portal.login"))


# ---------------------------------------------------------------- search
def _availability(title_ids):
    """Per title: totals, where the available copies are (shelf/location groups), and the earliest due date."""
    info = {tid: dict(total=0, available=0, where={}, back=None) for tid in title_ids}
    for c in Copy.query.filter(Copy.title_id.in_(title_ids)):
        d = info[c.title_id]
        if c.status in ("lost", "withdrawn"):
            continue
        d["total"] += 1
        if c.status == "available":
            d["available"] += 1
            d["where"][(c.shelf, c.location)] = d["where"].get((c.shelf, c.location), 0) + 1
    for tid, due in (db.session.query(Copy.title_id, func.min(Loan.due_date))
                     .join(Loan, Loan.copy_id == Copy.id)
                     .filter(Copy.title_id.in_(title_ids), Loan.returned_at.is_(None)).group_by(Copy.title_id)):
        info[tid]["back"] = due
    return info


def _my_state(student_id, title_ids):
    holds_by_title = {h.title_id: h for h in Hold.query.filter(
        Hold.student_id == student_id, Hold.status.in_(holds.ACTIVE), Hold.title_id.in_(title_ids))}
    borrowed = {tid for (tid,) in db.session.query(Copy.title_id).join(Loan, Loan.copy_id == Copy.id)
                .filter(Loan.student_id == student_id, Loan.returned_at.is_(None), Copy.title_id.in_(title_ids))}
    return holds_by_title, borrowed


@bp.route("/portal/")
@student_required
def search():
    q = request.args.get("q", "").strip()
    category, language = request.args.get("category", ""), request.args.get("language", "")
    location, avail = request.args.get("location", ""), bool(request.args.get("available"))
    page = request.args.get("page", 1, type=int)
    pag = search_titles(q, category, language, location, avail).paginate(page=page, per_page=PER_PAGE, error_out=False)
    ids = [t.id for t in pag.items]
    my_holds, borrowed = _my_state(g.student.id, ids)
    return render_template("portal_search.html", pag=pag, q=q, category=category, language=language, location=location,
                           avail=avail, info=_availability(ids), my_holds=my_holds, borrowed=borrowed,
                           queue=holds.queue_position, categories=distinct_values(Title.category),
                           locations=distinct_values(Copy.location), searched=bool(q or category or language or location or avail))


@bp.route("/portal/title/<int:tid>")
@student_required
def title(tid):
    t = db.session.get(Title, tid) or abort(404)
    my_holds, borrowed = _my_state(g.student.id, [tid])
    return render_template("portal_title.html", ti=t, info=_availability([tid])[tid], hold=my_holds.get(tid),
                           borrowed=tid in borrowed, queue=holds.queue_position,
                           copies=[c for c in t.copies if c.status not in ("withdrawn",)])


def _where(copy):
    return " · ".join(x for x in (copy.shelf and f"{tr('shelf', L())} {copy.shelf}", copy.location) if x) or "—"


@bp.route("/portal/hold/<int:tid>", methods=["POST"])
@student_required
def hold_place(tid):
    t = db.session.get(Title, tid) or abort(404)
    try:
        h = holds.place_hold(g.student, t)
    except CircError as e:
        msg(e.code, "err", **e.ctx)
    else:
        if h.status == "ready":
            msg("hold_placed_ready", date=h.expires_at.isoformat(), where=_where(h.copy))
        else:
            msg("hold_placed_wait", n=holds.queue_position(h))
    return redirect(request.form.get("back") if (request.form.get("back") or "").startswith("/portal") else url_for("portal.title", tid=tid))


@bp.route("/portal/hold/<int:hid>/cancel", methods=["POST"])
@student_required
def hold_cancel(hid):
    h = db.session.get(Hold, hid)
    if not h or h.student_id != g.student.id:  # students can only touch their own holds
        abort(404)
    try:
        holds.cancel_hold(h)
        msg("hold_cancelled")
    except CircError as e:
        msg(e.code, "err")
    return redirect(url_for("portal.account"))


@bp.route("/portal/account")
@student_required
def account():
    s = g.student
    t = circ.today()
    loans = circ.open_loans(s.id)
    my_holds = holds.active_holds(s.id)
    history = (Hold.query.filter(Hold.student_id == s.id, Hold.status.notin_(holds.ACTIVE))
               .order_by(Hold.id.desc()).limit(10).all())
    fines = Fine.query.filter_by(student_id=s.id, status="unpaid").order_by(Fine.id.desc()).all()
    return render_template("portal_account.html", s=s, loans=loans, today=t, my_holds=my_holds, history=history,
                           fines=fines, total=sum(f.amount for f in fines), queue=holds.queue_position, where=_where)


# ---------------------------------------------------------------- staff: PIN management
def _new_pin():
    return f"{secrets.randbelow(10 ** 6):06d}"


def _set_pin(student):
    pin = _new_pin()
    cred = StudentCredential.query.filter_by(student_id=student.id).first() or StudentCredential(student_id=student.id)
    cred.pin_hash = generate_password_hash(pin, method=PIN_METHOD)
    cred.failed_count, cred.locked_until = 0, None
    db.session.add(cred)
    return pin


def _no_store(html):
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-store"  # PINs are shown once; keep them out of browser caches
    return resp


@bp.route("/students/<int:sid>/pin", methods=["POST"])
@staff
def pin_one(sid):
    st = db.session.get(Student, sid) or abort(404)
    pin = _set_pin(st)
    db.session.commit()
    return _no_store(render_template("pin_result.html", rows=[(st, pin)]))


@bp.route("/pins", methods=["GET", "POST"])
@staff
def pins():
    y = circ.current_year()
    sections = Section.query.filter_by(year_id=y.id).all() if y else []
    if request.method == "POST":
        sec = (request.form.get("section_id") or "").strip()          # "" = everyone, "staff", or a section id
        regen = bool(request.form.get("regenerate"))
        q = (db.session.query(Student)
             .outerjoin(Enrollment, (Enrollment.student_id == Student.id) & (Enrollment.year_id == (y.id if y else 0))))
        staff_ok = and_(Student.patron_type == "staff", Student.active.isnot(False))
        if sec == "staff":
            q = q.filter(staff_ok)
        elif sec.isdigit():
            q = q.filter(Enrollment.status == "active", Enrollment.section_id == int(sec))
        else:
            q = q.filter(or_(Enrollment.status == "active", staff_ok))
        rows = []
        have = {sid for (sid,) in db.session.query(StudentCredential.student_id)}
        for st in q.order_by(Student.student_no).all():
            if st.id in have and not regen:
                continue
            rows.append((st, _set_pin(st)))
        db.session.commit()
        return _no_store(render_template("pin_result.html", rows=rows))
    return render_template("pins.html", sections=sections)
