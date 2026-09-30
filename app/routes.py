import json
from datetime import date
from flask import (current_app, send_from_directory, Blueprint, render_template, request, redirect, url_for, session,
                   flash, Response, jsonify)
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import or_
from . import db
from .i18n import tr
from .util import L, msg, parse_date
from .models import (User, School, AcademicYear, Semester, Stage, SchoolClass, Section,
                     Student, Enrollment, AuditLog)
from .importer import read_rows, template_csv, COLS

bp = Blueprint("main", __name__)


def current_year():
    return AcademicYear.query.filter_by(is_current=True).first() or AcademicYear.query.order_by(AcademicYear.id.desc()).first()


# ---------- auth / language ----------
@bp.route("/lang/<code>")
def set_lang(code):
    session["lang"] = "en" if code == "en" else "ar"
    return redirect(request.referrer or url_for("main.index"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = User.query.filter_by(username=request.form.get("username", "").strip()).first()
        if u and u.check_password(request.form.get("password", "")):
            login_user(u)
            return redirect(url_for("main.index"))
        msg("bad_login", "err")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("main.login"))


@bp.route("/")
@login_required
def index():
    y = current_year()
    total = Student.query.count()
    by_class = []
    if y:
        rows = (db.session.query(SchoolClass, db.func.count(Enrollment.id))
                .join(Section, Section.class_id == SchoolClass.id)
                .join(Enrollment, Enrollment.section_id == Section.id)
                .filter(Enrollment.year_id == y.id, Enrollment.status == "active")
                .group_by(SchoolClass.id).all())
        by_class = [(c.stage.name(L()) + " / " + c.name(L()), n) for c, n in rows]
    return render_template("index.html", year=y, total=total, by_class=by_class)


# ---------- school settings ----------
@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    s = School.get()
    if request.method == "POST":
        s.name_ar = request.form.get("name_ar", "").strip()
        s.name_en = request.form.get("name_en", "").strip()
        db.session.commit()
        msg("saved")
        return redirect(url_for("main.settings"))
    return render_template("settings.html", s=s)


# ---------- years & semesters ----------
@bp.route("/years", methods=["GET", "POST"])
@login_required
def years():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            msg("required", "err")
        elif AcademicYear.query.filter_by(name=name).first():
            msg("duplicate", "err")
        else:
            y = AcademicYear(name=name, start_date=parse_date(request.form.get("start_date")),
                             end_date=parse_date(request.form.get("end_date")),
                             is_current=AcademicYear.query.count() == 0)
            db.session.add(y)
            db.session.commit()
            msg("saved")
        return redirect(url_for("main.years"))
    return render_template("years.html", years=AcademicYear.query.order_by(AcademicYear.name.desc()).all())


@bp.route("/years/<int:yid>/current", methods=["POST"])
@login_required
def year_current(yid):
    AcademicYear.query.update({AcademicYear.is_current: False})
    db.session.get(AcademicYear, yid).is_current = True
    db.session.commit()
    msg("saved")
    return redirect(url_for("main.years"))


@bp.route("/years/<int:yid>/delete", methods=["POST"])
@login_required
def year_delete(yid):
    y = db.session.get(AcademicYear, yid)
    if Enrollment.query.filter_by(year_id=yid).first() or Section.query.filter_by(year_id=yid).first():
        msg("in_use", "err")
    else:
        db.session.delete(y)
        db.session.commit()
        msg("deleted")
    return redirect(url_for("main.years"))


@bp.route("/years/<int:yid>/semesters", methods=["POST"])
@login_required
def semester_add(yid):
    n_ar = request.form.get("name_ar", "").strip()
    if not n_ar:
        msg("required", "err")
    else:
        db.session.add(Semester(year_id=yid, name_ar=n_ar, name_en=request.form.get("name_en", "").strip(),
                                start_date=parse_date(request.form.get("start_date")),
                                end_date=parse_date(request.form.get("end_date"))))
        db.session.commit()
        msg("saved")
    return redirect(url_for("main.years"))


@bp.route("/semesters/<int:sid>/delete", methods=["POST"])
@login_required
def semester_delete(sid):
    db.session.delete(db.session.get(Semester, sid))
    db.session.commit()
    msg("deleted")
    return redirect(url_for("main.years"))


# ---------- structure ----------
@bp.route("/structure")
@login_required
def structure():
    return render_template("structure.html", stages=Stage.query.order_by(Stage.order).all(),
                           years=AcademicYear.query.order_by(AcademicYear.name.desc()).all(),
                           year=current_year(),
                           sections=Section.query.order_by(Section.name).all())


@bp.route("/structure/stage", methods=["POST"])
@login_required
def stage_add():
    if not request.form.get("name_ar", "").strip():
        msg("required", "err")
    else:
        db.session.add(Stage(name_ar=request.form["name_ar"].strip(), name_en=request.form.get("name_en", "").strip(),
                             order=int(request.form.get("order") or 1)))
        db.session.commit()
        msg("saved")
    return redirect(url_for("main.structure"))


@bp.route("/structure/class", methods=["POST"])
@login_required
def class_add():
    if not request.form.get("name_ar", "").strip():
        msg("required", "err")
    else:
        db.session.add(SchoolClass(stage_id=int(request.form["stage_id"]), name_ar=request.form["name_ar"].strip(),
                                   name_en=request.form.get("name_en", "").strip(),
                                   order=int(request.form.get("order") or 1)))
        db.session.commit()
        msg("saved")
    return redirect(url_for("main.structure"))


@bp.route("/structure/section", methods=["POST"])
@login_required
def section_add():
    name = request.form.get("name", "").strip()
    yid, cid = int(request.form["year_id"]), int(request.form["class_id"])
    if not name:
        msg("required", "err")
    elif Section.query.filter_by(year_id=yid, class_id=cid, name=name).first():
        msg("duplicate", "err")
    else:
        db.session.add(Section(year_id=yid, class_id=cid, name=name))
        db.session.commit()
        msg("saved")
    return redirect(url_for("main.structure"))


@bp.route("/structure/<kind>/<int:oid>/delete", methods=["POST"])
@login_required
def structure_delete(kind, oid):
    model = {"stage": Stage, "class": SchoolClass, "section": Section}[kind]
    obj = db.session.get(model, oid)
    used = ((kind == "stage" and obj.classes) or
            (kind == "class" and Section.query.filter_by(class_id=oid).first()) or
            (kind == "section" and Enrollment.query.filter_by(section_id=oid).first()))
    if used:
        msg("in_use", "err")
    else:
        db.session.delete(obj)
        db.session.commit()
        msg("deleted")
    return redirect(url_for("main.structure"))


# ---------- students ----------
PER_PAGE = 25


@bp.route("/students")
@login_required
def students():
    years = AcademicYear.query.order_by(AcademicYear.name.desc()).all()
    yid = request.args.get("year_id", type=int) or (current_year().id if current_year() else None)
    stage_id = request.args.get("stage_id", type=int)
    class_id = request.args.get("class_id", type=int)
    section_id = request.args.get("section_id", type=int)
    q = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)

    query = (db.session.query(Student, Enrollment, Section)
             .outerjoin(Enrollment, (Enrollment.student_id == Student.id) & (Enrollment.year_id == yid))
             .outerjoin(Section, Section.id == Enrollment.section_id)
             .outerjoin(SchoolClass, SchoolClass.id == Section.class_id))
    if stage_id:
        query = query.filter(SchoolClass.stage_id == stage_id)
    if class_id:
        query = query.filter(Section.class_id == class_id)
    if section_id:
        query = query.filter(Section.id == section_id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Student.student_no.like(like), Student.name_ar.like(like),
                                 Student.name_en.like(like), Student.email.like(like)))
    pag = query.order_by(Student.student_no).paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template("students.html", pag=pag, years=years, yid=yid, stage_id=stage_id, class_id=class_id,
                           section_id=section_id, q=q, stages=Stage.query.order_by(Stage.order).all(),
                           classes=SchoolClass.query.order_by(SchoolClass.order).all(),
                           year_sections=Section.query.filter_by(year_id=yid).all() if yid else [])


@bp.route("/students/new", methods=["GET", "POST"])
@bp.route("/students/<int:sid>", methods=["GET", "POST"])
@login_required
def student_form(sid=None):
    st = db.session.get(Student, sid) if sid else None
    y = current_year()
    en = Enrollment.query.filter_by(student_id=sid, year_id=y.id).first() if (st and y) else None
    if request.method == "POST":
        no = request.form.get("student_no", "").strip()
        dup = Student.query.filter(Student.student_no == no, Student.id != (sid or 0)).first()
        if not no:
            msg("required", "err")
        elif dup:
            msg("duplicate", "err")
        else:
            st = st or Student()
            st.student_no = no
            st.name_ar = request.form.get("name_ar", "").strip()
            st.name_en = request.form.get("name_en", "").strip()
            st.email = request.form.get("email", "").strip()
            db.session.add(st)
            db.session.flush()
            sec = request.form.get("section_id", type=int)
            if y and sec:
                e = Enrollment.query.filter_by(student_id=st.id, year_id=y.id).first() or Enrollment(student_id=st.id, year_id=y.id)
                e.section_id, e.status = sec, "active"
                db.session.add(e)
            db.session.commit()
            msg("saved")
            return redirect(url_for("main.students"))
    secs = Section.query.filter_by(year_id=y.id).all() if y else []
    return render_template("student_form.html", st=st, en=en, secs=secs)


@bp.route("/students/<int:sid>/delete", methods=["POST"])
@login_required
def student_delete(sid):
    db.session.delete(db.session.get(Student, sid))
    db.session.commit()
    msg("deleted")
    return redirect(url_for("main.students"))


def log(kind, summary, data):
    db.session.add(AuditLog(kind=kind, summary=summary, data=json.dumps(data)))


@bp.route("/students/move", methods=["POST"])
@login_required
def students_move():
    ids = request.form.getlist("ids", type=int)
    yid = request.form.get("year_id", type=int)
    target = request.form.get("target_section", type=int)
    if not ids or not target:
        msg("nothing_selected", "err")
        return redirect(request.referrer or url_for("main.students"))
    before = {}
    for sid in ids:
        e = Enrollment.query.filter_by(student_id=sid, year_id=yid).first()
        if e:
            before[str(e.id)] = e.section_id
            e.section_id, e.status = target, "active"
        else:
            e = Enrollment(student_id=sid, year_id=yid, section_id=target)
            db.session.add(e)
            db.session.flush()
            before[str(e.id)] = None
    sec = db.session.get(Section, target)
    log("move", f"{len(ids)} → {sec.label(L())}", {"before": before})
    db.session.commit()
    msg("moved_n", n=len(ids))
    return redirect(url_for("main.students", year_id=yid))


# ---------- import ----------
def _resolve_section(row, year, auto):
    """Return (section, error)."""
    st_n, cl_n, se_n = row.get("stage", ""), row.get("class", ""), row.get("section", "")
    if not (st_n and cl_n and se_n):
        return None, "stage/class/section required"
    stage = Stage.query.filter(or_(Stage.name_ar == st_n, Stage.name_en == st_n)).first()
    if not stage:
        if not auto:
            return None, f"unknown stage: {st_n}"
        stage = Stage(name_ar=st_n, order=(db.session.query(db.func.max(Stage.order)).scalar() or 0) + 1)
        db.session.add(stage)
        db.session.flush()
    cls = SchoolClass.query.filter(SchoolClass.stage_id == stage.id,
                                   or_(SchoolClass.name_ar == cl_n, SchoolClass.name_en == cl_n)).first()
    if not cls:
        if not auto:
            return None, f"unknown class: {cl_n}"
        cls = SchoolClass(stage_id=stage.id, name_ar=cl_n,
                          order=(db.session.query(db.func.max(SchoolClass.order)).filter_by(stage_id=stage.id).scalar() or 0) + 1)
        db.session.add(cls)
        db.session.flush()
    sec = Section.query.filter_by(year_id=year.id, class_id=cls.id, name=se_n).first()
    if not sec:
        if not auto:
            return None, f"unknown section: {se_n}"
        sec = Section(year_id=year.id, class_id=cls.id, name=se_n)
        db.session.add(sec)
        db.session.flush()
    return sec, None


@bp.route("/import/template")
@login_required
def import_template():
    return Response(template_csv(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=students_template.csv"})


@bp.route("/import", methods=["GET", "POST"])
@login_required
def import_students():
    year = current_year()
    if request.method == "GET":
        return render_template("import.html", year=year)
    if not year:
        msg("required", "err")
        return redirect(url_for("main.years"))
    auto = bool(request.form.get("auto"))
    confirm = request.form.get("confirm") == "1"
    if confirm:
        rows = json.loads(request.form["rows"])
    else:
        f = request.files.get("file")
        if not f or not f.filename:
            msg("required", "err")
            return redirect(url_for("main.import_students"))
        rows = read_rows(f)
    ok, errs, seen = [], [], set()
    for i, r in enumerate(rows, start=2):
        no = r.get("student_no", "")
        if not no:
            errs.append((i, "student_no required"))
            continue
        if no in seen:
            errs.append((i, "duplicate in file"))
            continue
        seen.add(no)
        sec, err = _resolve_section(r, year, auto)
        if err:
            errs.append((i, err))
        else:
            ok.append(r)
    if not confirm:
        db.session.rollback()  # preview only: discard auto-created rows
        return render_template("import_preview.html", ok=ok, errs=errs, auto=auto, rows_json=json.dumps(ok))
    created = 0
    for r in ok:
        sec, _ = _resolve_section(r, year, auto)
        st = Student.query.filter_by(student_no=r["student_no"]).first() or Student(student_no=r["student_no"])
        st.name_ar = r.get("name_ar", st.name_ar or "")
        st.name_en = r.get("name_en", st.name_en or "")
        st.email = r.get("email", st.email or "")
        db.session.add(st)
        db.session.flush()
        e = Enrollment.query.filter_by(student_id=st.id, year_id=year.id).first() or Enrollment(student_id=st.id, year_id=year.id)
        e.section_id, e.status = sec.id, "active"
        db.session.add(e)
        created += 1
    log("import", f"{created} students", {})
    db.session.commit()
    msg("imported_n", n=created)
    return redirect(url_for("main.students"))


# ---------- promotion ----------
def _plan(src_year, tgt_year, class_id):
    q = (db.session.query(Student, Enrollment, Section)
         .join(Enrollment, Enrollment.student_id == Student.id)
         .join(Section, Section.id == Enrollment.section_id)
         .filter(Enrollment.year_id == src_year, Enrollment.status == "active"))
    if class_id:
        q = q.filter(Section.class_id == class_id)
    plan = []
    for st, en, sec in q.order_by(Student.student_no).all():
        nxt = sec.school_class.next_class()
        plan.append(dict(student=st, enrollment=en, src=sec, next_class=nxt))
    return plan


@bp.route("/promote", methods=["GET", "POST"])
@login_required
def promote():
    years = AcademicYear.query.order_by(AcademicYear.name.desc()).all()
    classes = SchoolClass.query.order_by(SchoolClass.order).all()
    if request.method == "GET":
        return render_template("promote.html", years=years, classes=classes, plan=None)
    src = request.form.get("src_year", type=int)
    tgt = request.form.get("tgt_year", type=int)
    cid = request.form.get("class_id", type=int)
    if not src or not tgt or src == tgt:
        msg("required", "err")
        return redirect(url_for("main.promote"))
    if request.form.get("action") == "preview":
        return render_template("promote.html", years=years, classes=classes, plan=_plan(src, tgt, cid),
                               src=src, tgt=tgt, cid=cid)
    ids = set(request.form.getlist("ids", type=int))
    created, n = [], 0
    for p in _plan(src, tgt, cid):
        if p["student"].id not in ids:
            continue
        existing = Enrollment.query.filter_by(student_id=p["student"].id, year_id=tgt).first()
        if existing:
            continue
        if p["next_class"] is None:
            e = Enrollment(student_id=p["student"].id, year_id=tgt, section_id=None, status="graduated")
        else:
            sec = (Section.query.filter_by(year_id=tgt, class_id=p["next_class"].id, name=p["src"].name).first()
                   or Section(year_id=tgt, class_id=p["next_class"].id, name=p["src"].name))
            db.session.add(sec)
            db.session.flush()
            e = Enrollment(student_id=p["student"].id, year_id=tgt, section_id=sec.id)
        db.session.add(e)
        db.session.flush()
        created.append(e.id)
        n += 1
    log("promote", f"{n} students", {"created": created})
    db.session.commit()
    msg("promoted_n", n=n)
    return redirect(url_for("main.history"))


# ---------- history / undo ----------
@bp.route("/history")
@login_required
def history():
    return render_template("history.html", logs=AuditLog.query.order_by(AuditLog.id.desc()).limit(100).all())


@bp.route("/history/<int:lid>/undo", methods=["POST"])
@login_required
def undo(lid):
    lg = db.session.get(AuditLog, lid)
    if lg.undone:
        return redirect(url_for("main.history"))
    d = lg.payload()
    if lg.kind == "move":
        for eid, old in d["before"].items():
            e = db.session.get(Enrollment, int(eid))
            if e:
                if old is None:
                    db.session.delete(e)
                else:
                    e.section_id = old
    elif lg.kind == "promote":
        for eid in d["created"]:
            e = db.session.get(Enrollment, int(eid))
            if e:
                db.session.delete(e)
    else:
        return redirect(url_for("main.history"))
    lg.undone = True
    db.session.commit()
    msg("undone")
    return redirect(url_for("main.history"))


@bp.route("/sw.js")
def service_worker():
    resp = send_from_directory(current_app.static_folder, "sw.js", mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp
