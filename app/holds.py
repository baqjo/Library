"""Title holds ("booking"): waiting -> ready (a copy is set aside, status "held") -> fulfilled | expired | cancelled.

Invariant: a title never has both a waiting hold and an available copy - fill_holds() runs whenever a copy may
have become available (return, status change, new copies, expiry, cancellation).
"""
from datetime import timedelta
from . import db, circ
from .circ import CircError
from .models import Hold, Copy, Loan, Policy, Enrollment

ACTIVE = ("waiting", "ready")


def active_holds(student_id):
    return Hold.query.filter(Hold.student_id == student_id, Hold.status.in_(ACTIVE)).order_by(Hold.id).all()


def hold_for_copy(copy):
    return Hold.query.filter_by(copy_id=copy.id, status="ready").first()


def queue_position(hold):
    """1-based place among the waiting holds of the same title (0 when not waiting)."""
    if hold.status != "waiting":
        return 0
    return Hold.query.filter(Hold.title_id == hold.title_id, Hold.status == "waiting", Hold.id <= hold.id).count()


def _pickup_deadline(policy):
    return circ.next_open_day(circ.today() + timedelta(days=policy.hold_pickup_days), policy)


def fill_holds(title_id, commit=True):
    """Set aside available copies for the oldest waiting holds. Returns the holds that became ready."""
    policy = Policy.get()
    made = []
    while True:
        hold = Hold.query.filter_by(title_id=title_id, status="waiting").order_by(Hold.id).first()
        if not hold:
            break
        if not circ.student_is_active(hold.student):  # graduated / left: drop instead of setting a copy aside
            hold.status, hold.closed_at = "cancelled", circ.now_local()
            continue
        copy = Copy.query.filter_by(title_id=title_id, status="available").order_by(Copy.id).first()
        if not copy:
            break
        copy.status = "held"
        hold.status, hold.copy_id = "ready", copy.id
        hold.ready_at, hold.expires_at = circ.now_local(), _pickup_deadline(policy)
        made.append(hold)
        db.session.flush()
    if commit:
        db.session.commit()
    return made


def expire_holds():
    """Ready holds whose pickup date has passed release their copy (last pickup day = expires_at)."""
    rows = Hold.query.filter(Hold.status == "ready", Hold.expires_at < circ.today()).all()
    if not rows:
        return 0
    titles = set()
    for h in rows:
        h.status, h.closed_at = "expired", circ.now_local()
        if h.copy and h.copy.status == "held":
            h.copy.status = "available"
        titles.add(h.title_id)
    db.session.flush()
    for tid in titles:
        fill_holds(tid, commit=False)
    db.session.commit()
    return len(rows)


def release_copy(copy):
    """The held copy can no longer be set aside: its hold goes back to the queue with its original place."""
    h = hold_for_copy(copy)
    if h:
        h.status, h.copy_id, h.ready_at, h.expires_at = "waiting", None, None, None
    return h


def place_hold(student, title):
    policy = Policy.get()
    if not circ.student_is_active(student):
        raise CircError("not_active")
    mine = active_holds(student.id)
    if any(h.title_id == title.id for h in mine):
        raise CircError("hold_exists")
    if len(mine) >= policy.max_holds:
        raise CircError("max_holds", n=policy.max_holds)
    if (Loan.query.join(Copy, Copy.id == Loan.copy_id)
            .filter(Loan.student_id == student.id, Loan.returned_at.is_(None), Copy.title_id == title.id).first()):
        raise CircError("already_borrowed")
    if not any(c.status not in ("lost", "withdrawn") for c in title.copies):
        raise CircError("no_copies")
    if policy.block_fine_amount and circ.unpaid_total(student.id) >= policy.block_fine_amount:
        raise CircError("fines_block")
    y = circ.current_year()
    en = Enrollment.query.filter_by(student_id=student.id, year_id=y.id).first() if y else None
    h = Hold(title_id=title.id, student_id=student.id, year_id=y.id if y else None,
             section_id=en.section_id if en else None)
    db.session.add(h)
    db.session.flush()
    fill_holds(title.id, commit=False)
    db.session.commit()
    return h


def cancel_hold(hold):
    if hold.status not in ACTIVE:
        raise CircError("hold_closed")
    copy = hold.copy if hold.status == "ready" else None
    hold.status, hold.closed_at = "cancelled", circ.now_local()
    if copy and copy.status == "held":
        copy.status = "available"
    db.session.flush()
    fill_holds(hold.title_id, commit=False)
    db.session.commit()


def close_on_checkout(student_id, copy, claimed):
    """Student took a copy of a title: their hold on it is fulfilled; a spare copy set aside for them is freed."""
    mine = Hold.query.filter(Hold.student_id == student_id, Hold.title_id == copy.title_id,
                             Hold.status.in_(ACTIVE)).all()
    for h in mine:
        if h.status == "ready" and h.copy and h.copy.id != copy.id and h.copy.status == "held":
            h.copy.status = "available"
        h.status, h.closed_at = "fulfilled", circ.now_local()
