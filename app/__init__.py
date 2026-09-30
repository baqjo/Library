import os
import click
from flask import Flask, session, request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from config import Config
from .i18n import tr

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
login_manager.login_view = "main.login"


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    from .models import User, School, Student

    @login_manager.user_loader
    def load_user(uid):
        return db.session.get(User, int(uid))

    @app.context_processor
    def inject():
        lang = session.get("lang", app.config["DEFAULT_LANG"])
        school = School.query.first() if _tables_ready() else None
        sid = session.get("student_id")
        portal_student = db.session.get(Student, sid) if sid else None
        from flask_login import current_user
        return dict(
            portal_student=portal_student,
            is_admin=bool(current_user.is_authenticated and current_user.role == "admin"),
            lang=lang,
            dir="rtl" if lang == "ar" else "ltr",
            t=lambda k, **kw: tr(k, lang, **kw),
            school_name=((school.name_ar if lang == "ar" else school.name_en) or (school.name_ar or school.name_en)) if school else "",
        )

    from .routes import bp
    from .catalog import bp as catalog_bp
    from .barcodes import bp as barcodes_bp
    from .circulation import bp as circ_bp
    from .portal import bp as portal_bp
    for b in (bp, catalog_bp, barcodes_bp, circ_bp, portal_bp):
        app.register_blueprint(b)

    if os.environ.get("TRUST_PROXY") == "1":  # behind Render / a reverse proxy: honour X-Forwarded-*
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    @app.errorhandler(403)
    def forbidden(_e):
        from flask import render_template
        return render_template("403.html"), 403

    @app.before_request
    def expire_holds_lazily():
        """No background scheduler: ready holds past their pickup date are released on the next request."""
        if request.endpoint in (None, "static"):
            return
        try:
            from .holds import expire_holds
            expire_holds()
        except Exception:  # never break a page because of housekeeping
            db.session.rollback()

    @app.cli.command("create-admin")
    @click.argument("username")
    @click.argument("password")
    @click.option("--role", default="admin", type=click.Choice(["admin", "librarian"]))
    def create_admin(username, password, role):
        db.create_all()
        u = User.query.filter_by(username=username).first() or User(username=username)
        u.set_password(password)
        u.role = role
        db.session.add(u)
        db.session.commit()
        print("Admin ready:", username)

    with app.app_context():
        db.create_all()
        upgrade_schema()
        backfill_search_text()
    return app


def upgrade_schema():
    """create_all() never alters existing tables: add missing plain columns and fill their scalar defaults."""
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)
    for table in db.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have or col.primary_key or col.foreign_keys or col.unique:
                continue
            ddl = col.type.compile(dialect=db.engine.dialect)
            default = col.default.arg if (col.default is not None and col.default.is_scalar) else None
            with db.engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'))
                if default is not None:
                    conn.execute(text(f'UPDATE "{table.name}" SET "{col.name}" = :d WHERE "{col.name}" IS NULL'), {"d": default})


def backfill_search_text():
    from .models import Title
    from .textnorm import build_search_text
    rows = Title.query.filter((Title.search_text.is_(None)) | (Title.search_text == "")).all()
    for t in rows:
        t.search_text = build_search_text(t)
    if rows:
        db.session.commit()


def _tables_ready():
    try:
        from sqlalchemy import inspect
        return inspect(db.engine).has_table("school")
    except Exception:
        return False
