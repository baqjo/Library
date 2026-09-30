from flask import Blueprint, render_template, request, redirect, url_for
from . import db
from .auth import staff, admin_only
from .models import EmailLog
from .util import msg
from . import mailer

bp = Blueprint("notifications", __name__)


@bp.route("/notifications")
@staff
def index():
    counts = {s: EmailLog.query.filter_by(status=s).count() for s in ("pending", "sent", "failed")}
    rows = EmailLog.query.order_by(EmailLog.id.desc()).limit(100).all()
    return render_template("notifications.html", on=mailer.configured(), counts=counts, rows=rows)


@bp.route("/notifications/run", methods=["POST"])
@staff
def run_now():
    if not mailer.configured():
        msg("mail_not_configured", "err")
    else:
        made, sent, failed = mailer.run()
        msg("notif_result", made=made, sent=sent, failed=failed)
    return redirect(url_for("notifications.index"))


@bp.route("/notifications/test", methods=["POST"])
@admin_only
def test():
    to = request.form.get("to", "").strip()
    if not mailer.configured():
        msg("mail_not_configured", "err")
    elif "@" not in to:
        msg("required", "err")
    else:
        try:
            mailer.send_graph(to, "Test | \u0631\u0633\u0627\u0644\u0629 \u062a\u062c\u0631\u064a\u0628\u064a\u0629", "<p>School Library — Microsoft Graph e-mail works.</p>")
            msg("mail_test_ok")
        except Exception as e:
            db.session.rollback()
            msg("mail_test_fail", "err", error=str(e)[:200])
    return redirect(url_for("notifications.index"))


@bp.route("/notifications/retry", methods=["POST"])
@staff
def retry_failed():
    n = EmailLog.query.filter_by(status="failed").update({"status": "pending", "attempts": 0})
    db.session.commit()
    msg("saved")
    return redirect(url_for("notifications.index"))
