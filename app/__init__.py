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

    from .models import User, School

    @login_manager.user_loader
    def load_user(uid):
        return db.session.get(User, int(uid))

    @app.context_processor
    def inject():
        lang = session.get("lang", app.config["DEFAULT_LANG"])
        school = School.query.first() if _tables_ready() else None
        return dict(
            lang=lang,
            dir="rtl" if lang == "ar" else "ltr",
            t=lambda k, **kw: tr(k, lang, **kw),
            school_name=((school.name_ar if lang == "ar" else school.name_en) or (school.name_ar or school.name_en)) if school else "",
        )

    from .routes import bp
    app.register_blueprint(bp)

    @app.cli.command("create-admin")
    @click.argument("username")
    @click.argument("password")
    def create_admin(username, password):
        db.create_all()
        u = User.query.filter_by(username=username).first() or User(username=username)
        u.set_password(password)
        u.role = "admin"
        db.session.add(u)
        db.session.commit()
        print("Admin ready:", username)

    with app.app_context():
        db.create_all()
    return app


def _tables_ready():
    try:
        from sqlalchemy import inspect
        return inspect(db.engine).has_table("school")
    except Exception:
        return False
