"""E-mail notifications through Microsoft Graph (application permission Mail.Send, sent as MAIL_SENDER).

There is no background scheduler: notifications are collected into an outbox (EmailLog) and sent by
`flask --app wsgi send-notifications` (run it from a cron job every 15-30 minutes) or by the "Run now" button.
Every notification has a unique dedupe_key, so running it repeatedly never sends the same e-mail twice.
"""
import html
import os
from datetime import timedelta
from urllib.parse import quote
import requests
from . import db, circ, holds
from .i18n import tr
from .models import EmailLog, Hold, Loan, Student

GRAPH = "https://graph.microsoft.com/v1.0"
DUE_SOON_DAYS = 2
MAX_ATTEMPTS = 3


def configured():
    return all(os.environ.get(k) for k in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "MAIL_SENDER"))


def _token():
    import msal
    app = msal.ConfidentialClientApplication(
        os.environ["AZURE_CLIENT_ID"], client_credential=os.environ["AZURE_CLIENT_SECRET"],
        authority="https://login.microsoftonline.com/" + os.environ["AZURE_TENANT_ID"])
    res = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in res:
        raise RuntimeError(res.get("error_description") or res.get("error") or "token error")
    return res["access_token"]


def send_graph(to, subject, html_body):
    """Raises on any failure."""
    r = requests.post(
        f"{GRAPH}/users/{quote(os.environ['MAIL_SENDER'])}/sendMail",
        headers={"Authorization": "Bearer " + _token(), "Content-Type": "application/json"},
        json={"message": {"subject": subject, "body": {"contentType": "HTML", "content": html_body},
                          "toRecipients": [{"emailAddress": {"address": to}}]}, "saveToSentItems": False},
        timeout=20)
    if r.status_code != 202:
        raise RuntimeError(f"Graph {r.status_code}: {r.text[:300]}")


# ---------------------------------------------------------------- composing (Arabic first, then English)
def _wrap(lines_ar, lines_en, school):
    e = html.escape
    def block(lines, d):
        return f'<div dir="{d}" style="font-family:Arial,sans-serif;font-size:14px;line-height:1.7">' + "".join(
            f"<p>{e(x)}</p>" for x in lines) + "</div>"
    return (block(lines_ar, "rtl") + '<hr style="border:0;border-top:1px solid #ccc">' + block(lines_en, "ltr")
            + f'<p style="color:#666;font-size:12px">{e(school)}</p>')


def _school():
    from .models import School
    s = School.get()
    return " / ".join(x for x in (s.name_ar, s.name_en) if x) or "School Library"


def compose(kind, student, **c):
    """Returns (subject, html). Every value that came from the database is escaped in _wrap()."""
    n_ar, n_en = student.name_ar or student.name_en, student.name_en or student.name_ar
    if kind == "hold_ready":
        ar = [f"عزيزي/عزيزتي {n_ar}،", f"نسختك من الكتاب «{c['title']}» جاهزة للاستلام من المكتبة ({c['where']}).",
              f"يرجى الاستلام حتى تاريخ {c['until']}."]
        en = [f"Dear {n_en},", f"Your copy of “{c['title']}” is ready for pickup at the library ({c['where']}).",
              f"Please collect it by {c['until']}."]
        return "حجزك جاهز للاستلام | Your hold is ready", _wrap(ar, en, _school())
    if kind == "due_soon":
        ar = [f"عزيزي/عزيزتي {n_ar}،", f"نذكّرك بأن موعد إرجاع الكتاب «{c['title']}» هو {c['due']}."]
        en = [f"Dear {n_en},", f"A reminder that “{c['title']}” is due on {c['due']}."]
        return "تذكير بموعد إرجاع كتاب | Book due soon", _wrap(ar, en, _school())
    if kind == "overdue":
        ar = [f"عزيزي/عزيزتي {n_ar}،", "لديك كتب تجاوزت موعد إرجاعها، يرجى إعادتها إلى المكتبة في أقرب وقت:"] + [
            f"• {t} (الاستحقاق {d})" for t, d in c["items"]]
        en = [f"Dear {n_en},", "The following items are overdue; please return them to the library soon:"] + [
            f"• {t} (due {d})" for t, d in c["items"]]
        return "كتب متأخرة | Overdue books", _wrap(ar, en, _school())
    raise ValueError(kind)


# ---------------------------------------------------------------- outbox
def enqueue(kind, student, key, **ctx):
    """Add one notification unless the student has no e-mail or the same one was already queued/sent."""
    if not (student.email or "").strip() or EmailLog.query.filter_by(dedupe_key=key).first():
        return None
    subject, body = compose(kind, student, **ctx)
    row = EmailLog(kind=kind, student_id=student.id, to_addr=student.email.strip(), subject=subject, body=body,
                   dedupe_key=key)
    db.session.add(row)
    db.session.commit()
    return row


def collect():
    """Queue everything that is currently due. Returns the number of new messages."""
    today, made = circ.today(), 0
    for h in Hold.query.filter_by(status="ready").all():
        c = h.copy
        where = " · ".join(x for x in (c.shelf, c.location) if x) if c else ""
        made += bool(enqueue("hold_ready", h.student, f"hold:{h.id}", title=h.title.title, where=where or "—",
                             until=h.expires_at.isoformat()))
    soon = today + timedelta(days=DUE_SOON_DAYS)
    for l in Loan.query.filter(Loan.returned_at.is_(None), Loan.due_date >= today, Loan.due_date <= soon).all():
        made += bool(enqueue("due_soon", l.student, f"due:{l.id}", title=l.copy.title.title, due=l.due_date.isoformat()))
    week = today.isocalendar()
    by_student = {}
    for l in Loan.query.filter(Loan.returned_at.is_(None), Loan.due_date < today).order_by(Loan.due_date).all():
        by_student.setdefault(l.student_id, []).append((l.copy.title.title, l.due_date.isoformat()))
    for sid, items in by_student.items():          # at most one overdue reminder per student per week
        st = db.session.get(Student, sid)
        if circ.student_is_active(st):
            made += bool(enqueue("overdue", st, f"overdue:{sid}:{week[0]}-{week[1]}", items=items))
    return made


def flush(limit=100):
    """Send pending messages. Returns (sent, failed)."""
    sent = failed = 0
    if not configured():
        return 0, 0
    for row in EmailLog.query.filter_by(status="pending").order_by(EmailLog.id).limit(limit).all():
        row.attempts = (row.attempts or 0) + 1
        try:
            send_graph(row.to_addr, row.subject, row.body)
            row.status, row.sent_at, row.error = "sent", circ.now_local(), ""
            sent += 1
        except Exception as e:  # network, permission, throttling...
            row.error = str(e)[:500]
            if row.attempts >= MAX_ATTEMPTS:
                row.status = "failed"
                failed += 1
        db.session.commit()
    return sent, failed


def run():
    made = collect()
    sent, failed = flush()
    return made, sent, failed
