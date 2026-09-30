from datetime import date
from flask import session, flash, current_app
from .i18n import tr


def L():
    return session.get("lang", current_app.config["DEFAULT_LANG"])


def msg(key, cat="ok", **kw):
    flash(tr(key, L(), **kw), cat)


def parse_date(s):
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None
