import time
from functools import wraps
from flask import abort, session, redirect, url_for, g, request
from flask_login import login_required, current_user

STUDENT_IDLE_SECONDS = 30 * 60


def roles_required(*roles):
    def deco(f):
        @wraps(f)
        @login_required
        def wrapper(*a, **k):
            if current_user.role not in roles:
                abort(403)
            return f(*a, **k)
        return wrapper
    return deco


staff = roles_required("admin", "librarian")
admin_only = roles_required("admin")


def student_required(f):
    """Student portal guard. Student sessions are separate from staff (Flask-Login) sessions:
    a student never has a User row, so staff pages are unreachable by construction."""
    @wraps(f)
    def wrapper(*a, **k):
        from . import db
        from .models import Student
        from .circ import student_is_active
        sid = session.get("student_id")
        now = time.time()
        if not sid or now - session.get("student_ts", 0) > STUDENT_IDLE_SECONDS:
            for key in ("student_id", "student_ts"):
                session.pop(key, None)
            return redirect(url_for("portal.login", next=request.path))
        st = db.session.get(Student, sid)
        if not st or not student_is_active(st):  # graduated / removed while logged in
            for key in ("student_id", "student_ts"):
                session.pop(key, None)
            return redirect(url_for("portal.login"))
        session["student_ts"] = now  # sliding idle timeout
        g.student = st
        return f(*a, **k)
    return wrapper
