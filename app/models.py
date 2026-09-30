import json
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from . import db


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="admin")  # admin | librarian | student

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class School(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name_ar = db.Column(db.String(200), default="")
    name_en = db.Column(db.String(200), default="")

    @classmethod
    def get(cls):
        s = cls.query.first()
        if not s:
            s = cls()
            db.session.add(s)
            db.session.commit()
        return s


class AcademicYear(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), unique=True, nullable=False)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    is_current = db.Column(db.Boolean, default=False)
    semesters = db.relationship("Semester", backref="year", cascade="all, delete-orphan",
                                order_by="Semester.start_date")


class Semester(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    year_id = db.Column(db.Integer, db.ForeignKey("academic_year.id"), nullable=False)
    name_ar = db.Column(db.String(100), nullable=False)
    name_en = db.Column(db.String(100), default="")
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)

    def name(self, lang):
        return (self.name_ar if lang == "ar" else (self.name_en or self.name_ar))


class Stage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name_ar = db.Column(db.String(100), nullable=False)
    name_en = db.Column(db.String(100), default="")
    order = db.Column(db.Integer, default=1)
    classes = db.relationship("SchoolClass", backref="stage", order_by="SchoolClass.order")

    def name(self, lang):
        return self.name_ar if lang == "ar" else (self.name_en or self.name_ar)


class SchoolClass(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    stage_id = db.Column(db.Integer, db.ForeignKey("stage.id"), nullable=False)
    name_ar = db.Column(db.String(100), nullable=False)
    name_en = db.Column(db.String(100), default="")
    order = db.Column(db.Integer, default=1)

    def name(self, lang):
        return self.name_ar if lang == "ar" else (self.name_en or self.name_ar)

    def next_class(self):
        """Next class in the same stage, else first class of the next stage, else None."""
        nxt = (SchoolClass.query.filter_by(stage_id=self.stage_id)
               .filter(SchoolClass.order > self.order).order_by(SchoolClass.order).first())
        if nxt:
            return nxt
        stages = Stage.query.filter(Stage.order > self.stage.order).order_by(Stage.order).all()
        for st in stages:
            first = SchoolClass.query.filter_by(stage_id=st.id).order_by(SchoolClass.order).first()
            if first:
                return first
        return None


class Section(db.Model):
    __table_args__ = (db.UniqueConstraint("year_id", "class_id", "name"),)
    id = db.Column(db.Integer, primary_key=True)
    year_id = db.Column(db.Integer, db.ForeignKey("academic_year.id"), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey("school_class.id"), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    year = db.relationship("AcademicYear")
    school_class = db.relationship("SchoolClass")

    def label(self, lang):
        c = self.school_class
        return f"{c.stage.name(lang)} / {c.name(lang)} / {self.name}"


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_no = db.Column(db.String(50), unique=True, nullable=False)
    name_ar = db.Column(db.String(200), default="")
    name_en = db.Column(db.String(200), default="")
    email = db.Column(db.String(200), default="")
    barcode = db.Column(db.String(50), unique=True)  # assigned in phase 2
    enrollments = db.relationship("Enrollment", backref="student", cascade="all, delete-orphan")

    def name(self, lang):
        return (self.name_ar or self.name_en) if lang == "ar" else (self.name_en or self.name_ar)


class Enrollment(db.Model):
    __table_args__ = (db.UniqueConstraint("student_id", "year_id"),)
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    year_id = db.Column(db.Integer, db.ForeignKey("academic_year.id"), nullable=False)
    section_id = db.Column(db.Integer, db.ForeignKey("section.id"))
    status = db.Column(db.String(20), default="active")  # active | graduated
    section = db.relationship("Section")


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    kind = db.Column(db.String(30))  # move | promote | import
    summary = db.Column(db.String(300))
    data = db.Column(db.Text, default="{}")
    undone = db.Column(db.Boolean, default=False)

    def payload(self):
        return json.loads(self.data or "{}")
