"""Statistics and comparisons.

Rows are the values of one dimension (year, semester, stage, class, section, category, language, month);
columns are loans, distinct borrowers, enrolled students, participation, holds and fines. Every loan / hold keeps
the year, semester and the student's section at the time, so history stays correct after students are promoted.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request
from sqlalchemy import func
from . import db, circ
from .auth import staff
from .models import AcademicYear, Semester, Stage, SchoolClass, Section, Enrollment, Loan, Hold, Fine
from .util import L, parse_date

bp = Blueprint("stats", __name__)
DIMS = ["year", "semester", "stage", "class", "section", "category", "language", "month"]
CHRONO = {"year", "semester", "month"}       # rows are ordered in time -> show change vs previous row
WITH_DENOM = {"year", "semester", "stage", "class", "section"}  # rows for which the enrolled headcount is known
LANGS = {"ar": "lang_ar", "en": "lang_en", "other": "lang_other"}


def parse_filters(args):
    dim = args.get("dim") if args.get("dim") in DIMS else "class"
    raw = (args.get("year_id") or "").strip()
    cur = circ.current_year()
    if raw == "all":
        year_id = None
    elif raw.isdigit():
        year_id = int(raw)
    else:  # default: current year, except when comparing years
        year_id = None if dim == "year" else (cur.id if cur else None)
    to_int = lambda k: int(args[k]) if (args.get(k) or "").isdigit() else None
    return dict(dim=dim, year_id=year_id, year_sel="all" if year_id is None else str(year_id),
                semester_id=to_int("semester_id"), stage_id=to_int("stage_id"), class_id=to_int("class_id"),
                start=parse_date(args.get("from")), end=parse_date(args.get("to")))


class _Ctx:
    def __init__(self, lang):
        from .i18n import tr
        self.lang = lang
        self.tr = lambda k: tr(k, lang)
        self.years = {y.id: y for y in AcademicYear.query.all()}
        self.sems = {s.id: s for s in Semester.query.all()}


def _facts(f, ctx):
    """Loans, holds and fines as uniform dicts."""
    facts = []
    q = Loan.query
    if f["year_id"]:
        q = q.filter(Loan.year_id == f["year_id"])
    for l in q.all():
        facts.append(dict(kind="loan", year_id=l.year_id, semester_id=l.semester_id, section=l.section,
                          title=l.copy.title, student_id=l.student_id, when=l.out_at.date()))
    q = Hold.query
    if f["year_id"]:
        q = q.filter(Hold.year_id == f["year_id"])
    for h in q.all():
        when = h.created_at.date()
        sem = circ.semester_for(when, ctx.years.get(h.year_id))
        facts.append(dict(kind="hold", year_id=h.year_id, semester_id=sem.id if sem else None,
                          section=db.session.get(Section, h.section_id) if h.section_id else None,
                          title=h.title, student_id=h.student_id, when=when))
    for fn in Fine.query.filter(Fine.loan_id.isnot(None)).all():
        l = fn.loan
        if f["year_id"] and l.year_id != f["year_id"]:
            continue
        facts.append(dict(kind="fine", year_id=l.year_id, semester_id=l.semester_id, section=l.section,
                          title=l.copy.title, student_id=fn.student_id, when=fn.created_at.date(),
                          amount=fn.amount, unpaid=fn.status == "unpaid"))
    return facts


def _keep(fact, f):
    sec = fact["section"]
    if f["semester_id"] and fact["semester_id"] != f["semester_id"]:
        return False
    if f["stage_id"] and not (sec and sec.school_class.stage_id == f["stage_id"]):
        return False
    if f["class_id"] and not (sec and sec.class_id == f["class_id"]):
        return False
    if f["start"] and fact["when"] < f["start"]:
        return False
    if f["end"] and fact["when"] > f["end"]:
        return False
    return True


def _key(dim, fact, ctx):
    """(sort key, label) of the row a fact belongs to."""
    sec = fact.get("section")
    none = ((9, ""), "—")
    if dim == "year":
        y = ctx.years.get(fact.get("year_id"))
        return ((0, y.name), y.name) if y else none
    if dim == "semester":
        s = ctx.sems.get(fact.get("semester_id"))
        if not s:
            return ((9, ""), ctx.tr("outside_semester"))
        y = ctx.years.get(s.year_id)
        return ((0, y.name if y else "", s.start_date.isoformat() if s.start_date else "", s.id),
                f"{y.name if y else ''} — {s.name(ctx.lang)}")
    if dim in ("stage", "class", "section"):
        if not sec:
            return none
        c, st = sec.school_class, sec.school_class.stage
        if dim == "stage":
            return ((0, st.order, st.id), st.name(ctx.lang))
        if dim == "class":
            return ((0, st.order, c.order, c.id), f"{st.name(ctx.lang)} / {c.name(ctx.lang)}")
        return ((0, st.order, c.order, c.id, sec.name), f"{st.name(ctx.lang)} / {c.name(ctx.lang)} / {sec.name}")
    if dim == "category":
        v = (fact["title"].category or "").strip()
        return ((0, v), v) if v else none
    if dim == "language":
        v = fact["title"].language or "other"
        return ((0, v), ctx.tr(LANGS.get(v, "lang_other")))
    if dim == "month":
        return ((0, fact["when"].strftime("%Y-%m")), fact["when"].strftime("%Y-%m"))
    raise ValueError(dim)


def _headcounts(f, ctx):
    """Enrolled students per row (only for dimensions where that is meaningful)."""
    dim = f["dim"]
    if dim not in WITH_DENOM:
        return {}
    q = Enrollment.query.filter(Enrollment.section_id.isnot(None))
    if f["year_id"]:
        q = q.filter(Enrollment.year_id == f["year_id"])
    seen = defaultdict(set)
    for e in q.all():
        sec = e.section
        if f["stage_id"] and sec.school_class.stage_id != f["stage_id"]:
            continue
        if f["class_id"] and sec.class_id != f["class_id"]:
            continue
        if dim == "semester":
            for s in ctx.sems.values():
                if s.year_id == e.year_id and (not f["semester_id"] or s.id == f["semester_id"]):
                    seen[_key("semester", dict(semester_id=s.id), ctx)].add(e.student_id)
        else:
            seen[_key(dim, dict(year_id=e.year_id, section=sec, semester_id=None), ctx)].add(e.student_id)
    return {k: len(v) for k, v in seen.items()}


def compute(args):
    f = parse_filters(args)
    ctx = _Ctx(L())
    groups = defaultdict(lambda: dict(loans=0, students=set(), holds=0, fines=0.0, unpaid=0.0))
    titles, all_borrowers, total_loans = Counter(), set(), 0
    for fact in _facts(f, ctx):
        if not _keep(fact, f):
            continue
        g = groups[_key(f["dim"], fact, ctx)]
        if fact["kind"] == "loan":
            g["loans"] += 1
            g["students"].add(fact["student_id"])
            all_borrowers.add(fact["student_id"])
            titles[fact["title"].id] += 1
            total_loans += 1
        elif fact["kind"] == "hold":
            g["holds"] += 1
        else:
            g["fines"] += fact["amount"]
            g["unpaid"] += fact["amount"] if fact["unpaid"] else 0.0
    heads = _headcounts(f, ctx)
    for k in heads:                       # show rows that have students but no activity too
        groups[k]
    rows = []
    for (sk, label), g in sorted(groups.items(), key=lambda kv: kv[0][0]):
        n = heads.get((sk, label))
        rows.append(dict(label=label, students=n, loans=g["loans"], borrowers=len(g["students"]),
                         per_student=round(g["loans"] / n, 2) if n else None,
                         participation=round(100 * len(g["students"]) / n, 1) if n else None,
                         holds=g["holds"], fines=round(g["fines"], 2), unpaid=round(g["unpaid"], 2),
                         share=round(100 * g["loans"] / total_loans, 1) if total_loans else 0.0))
    top = max([r["loans"] for r in rows], default=0)
    prev = None
    for r in rows:
        r["bar"] = round(100 * r["loans"] / top, 1) if top else 0
        r["delta"] = (round(100 * (r["loans"] - prev) / prev, 1) if (f["dim"] in CHRONO and prev) else None)
        prev = r["loans"]
    from .models import Title
    top_titles = [(db.session.get(Title, tid), n) for tid, n in titles.most_common(10)]
    total_students = None
    if f["year_id"] and f["dim"] in WITH_DENOM and heads:
        total_students = sum(heads.values()) if f["dim"] != "semester" else max(heads.values())
    totals = dict(loans=total_loans, borrowers=len(all_borrowers),
                  holds=sum(r["holds"] for r in rows), fines=round(sum(r["fines"] for r in rows), 2),
                  unpaid=round(sum(r["unpaid"] for r in rows), 2), students=total_students,
                  participation=round(100 * len(all_borrowers) / total_students, 1) if total_students else None)
    return dict(f=f, rows=rows, totals=totals, top_titles=top_titles)


def kpis():
    t = circ.today()
    return dict(
        out=Loan.query.filter(Loan.returned_at.is_(None)).count(),
        overdue=Loan.query.filter(Loan.returned_at.is_(None), Loan.due_date < t).count(),
        ready=Hold.query.filter_by(status="ready").count(),
        waiting=Hold.query.filter_by(status="waiting").count(),
        unpaid=round(db.session.query(func.coalesce(func.sum(Fine.amount), 0.0)).filter_by(status="unpaid").scalar() or 0, 2))


STAT_COLS = [("students_n", "students"), ("loans_n", "loans"), ("borrowers", "borrowers"),
             ("participation", "participation"), ("per_student", "per_student"), ("share", "share"),
             ("change", "delta"), ("holds_n", "holds"), ("fines_amount", "fines"), ("fines_unpaid", "unpaid")]


@bp.route("/stats")
@staff
def index():
    res = compute(request.args)
    ctx_years = AcademicYear.query.order_by(AcademicYear.name.desc()).all()
    return render_template("stats.html", res=res, f=res["f"], dims=DIMS, cols=STAT_COLS, kpi=kpis(),
                           years=ctx_years, semesters=Semester.query.order_by(Semester.start_date).all(),
                           stages=Stage.query.order_by(Stage.order).all(),
                           classes=SchoolClass.query.order_by(SchoolClass.order).all(),
                           args=request.args, printing=bool(request.args.get("print")))
