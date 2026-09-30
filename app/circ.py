"""Circulation business rules (kept free of Flask request code so it can be tested directly)."""
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import func
from . import db
from .barcode import normalize_scan
from .models import (Policy, Holiday, Copy, Student, Loan, Fine, Enrollment, AcademicYear, Semester)


class CircError(Exception):
    def __init__(self, code, **ctx):
        super().__init__(code)
        self.code, self.ctx = code, ctx


def now_local():
    tz = ZoneInfo(os.environ.get("SCHOOL_TZ", "UTC"))
    return datetime.now(tz).replace(tzinfo=None)


def today():
    return now_local().date()


def is_closed(d, policy, holidays):
    return d.weekday() in policy.closed_set() or d in holidays


def next_open_day(d, policy=None):
    policy = policy or Policy.get()
    holidays = {h.day for h in Holiday.query.all()}
    for _ in range(60):
        if not is_closed(d, policy, holidays):
            return d
        d += timedelta(days=1)
    return d


def compute_due(policy=None):
    policy = policy or Policy.get()
    return next_open_day(today() + timedelta(days=policy.loan_days), policy)


def overdue_open_days(due, on, policy):
    """Number of open (non-closed) days after `due` up to and including `on`."""
    if on <= due:
        return 0
    holidays = {h.day for h in Holiday.query.all()}
    n, d = 0, due + timedelta(days=1)
    while d <= on:
        if not is_closed(d, policy, holidays):
            n += 1
        d += timedelta(days=1)
    return n


# ---------- lookups ----------
def find_student(code):
    code = normalize_scan(code)
    if not code:
        return None
    return (Student.query.filter(func.upper(Student.barcode) == code).first()
            or Student.query.filter(func.upper(Student.student_no) == code).first())


def find_copy(code):
    code = normalize_scan(code)
    return Copy.query.filter(func.upper(Copy.barcode) == code).first() if code else None


def open_loans(student_id):
    return (Loan.query.filter_by(student_id=student_id, returned_at=None)
            .order_by(Loan.due_date).all())


def unpaid_total(student_id):
    return db.session.query(func.coalesce(func.sum(Fine.amount), 0.0)).filter_by(
        student_id=student_id, status="unpaid").scalar() or 0.0


def current_year():
    return AcademicYear.query.filter_by(is_current=True).first()


def semester_for(day, year):
    if not year:
        return None
    for s in year.semesters:
        if s.start_date and s.end_date and s.start_date <= day <= s.end_date:
            return s
    return None


# ---------- operations ----------
def checkout(student, copy):
    policy = Policy.get()
    if copy.status != "available":
        raise CircError("copy_" + copy.status if copy.status in ("out", "lost", "damaged", "withdrawn") else "copy_unavailable")
    loans = open_loans(student.id)
    if len(loans) >= policy.max_loans:
        raise CircError("max_loans", n=policy.max_loans)
    t = today()
    if policy.block_if_overdue and any(l.due_date < t for l in loans):
        raise CircError("has_overdue")
    if policy.block_fine_amount and unpaid_total(student.id) >= policy.block_fine_amount:
        raise CircError("fines_block")
    y = current_year()
    en = Enrollment.query.filter_by(student_id=student.id, year_id=y.id).first() if y else None
    sem = semester_for(t, y)
    loan = Loan(copy_id=copy.id, student_id=student.id, year_id=y.id if y else None,
                semester_id=sem.id if sem else None, section_id=en.section_id if en else None,
                out_at=now_local(), due_date=compute_due(policy))
    copy.status = "out"
    db.session.add(loan)
    db.session.commit()
    return loan


def checkin(copy):
    """Returns (loan or None, fine or None)."""
    policy = Policy.get()
    loan = Loan.query.filter_by(copy_id=copy.id, returned_at=None).first()
    if not loan:
        if copy.status == "lost":  # a lost copy has been found
            copy.status = "available"
            db.session.commit()
            return None, None
        raise CircError("not_checked_out")
    loan.returned_at = now_local()
    days = overdue_open_days(loan.due_date, today(), policy) - policy.grace_days
    fine = None
    if days > 0 and policy.fine_per_day > 0:
        fine = Fine(loan_id=loan.id, student_id=loan.student_id, amount=round(days * policy.fine_per_day, 2),
                    reason="overdue")
        db.session.add(fine)
    if copy.status == "out":
        copy.status = "available"
    db.session.commit()
    return loan, fine


def renew(loan):
    policy = Policy.get()
    if loan.returned_at:
        raise CircError("not_checked_out")
    if loan.renewals >= policy.max_renewals:
        raise CircError("max_renewals", n=policy.max_renewals)
    if loan.due_date < today() and not policy.allow_renew_overdue:
        raise CircError("renew_overdue")
    loan.due_date = compute_due(policy)
    loan.renewals += 1
    db.session.commit()
    return loan


def mark_copy(copy, status):
    """Set copy status; marking a checked-out copy lost closes the loan and bills its price."""
    fine = None
    if status == "lost":
        loan = Loan.query.filter_by(copy_id=copy.id, returned_at=None).first()
        if loan:
            loan.returned_at = now_local()
            loan.note = "lost"
            if copy.price and copy.price > 0:
                fine = Fine(loan_id=loan.id, student_id=loan.student_id, amount=copy.price, reason="lost")
                db.session.add(fine)
    elif status != "out" and copy.status == "out":
        raise CircError("copy_out")
    copy.status = status
    db.session.commit()
    return fine
