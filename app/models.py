import json
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from . import db


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="admin")  # admin | librarian (students never get a User row)
    failed_count = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class School(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name_ar = db.Column(db.String(200), default="")
    name_en = db.Column(db.String(200), default="")
    pin_login_enabled = db.Column(db.Boolean, default=True)

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
    patron_type = db.Column(db.String(10), default="student")  # student | staff (teachers, employees)
    active = db.Column(db.Boolean, default=True)  # used for staff; students are active while enrolled
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


# ================= Phase 2: catalog, barcodes, circulation =================
class Policy(db.Model):
    """Singleton circulation policy (kept in its own table so existing DBs upgrade cleanly)."""
    id = db.Column(db.Integer, primary_key=True)
    loan_days = db.Column(db.Integer, default=14)
    max_loans = db.Column(db.Integer, default=3)
    max_renewals = db.Column(db.Integer, default=2)
    fine_per_day = db.Column(db.Float, default=0.0)
    grace_days = db.Column(db.Integer, default=0)
    closed_weekdays = db.Column(db.String(20), default="")  # "4,5" (Mon=0 .. Sun=6)
    block_if_overdue = db.Column(db.Boolean, default=True)
    block_fine_amount = db.Column(db.Float, default=0.0)  # 0 = disabled
    allow_renew_overdue = db.Column(db.Boolean, default=False)
    hold_pickup_days = db.Column(db.Integer, default=3)  # how long a reserved copy waits for pickup
    max_holds = db.Column(db.Integer, default=3)         # active holds (waiting + ready) per student

    @classmethod
    def get(cls):
        p = cls.query.first()
        if not p:
            p = cls()
            db.session.add(p)
            db.session.commit()
        return p

    def closed_set(self):
        return {int(x) for x in (self.closed_weekdays or "").split(",") if x.strip().isdigit()}


class Holiday(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, unique=True, nullable=False)
    note = db.Column(db.String(200), default="")


class Title(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    isbn = db.Column(db.String(20), unique=True)
    title = db.Column(db.String(300), nullable=False)
    title_alt = db.Column(db.String(300), default="")
    author = db.Column(db.String(200), default="")
    publisher = db.Column(db.String(200), default="")
    pub_year = db.Column(db.String(10), default="")
    category = db.Column(db.String(100), default="")
    language = db.Column(db.String(10), default="ar")
    call_number = db.Column(db.String(60), default="")
    search_text = db.Column(db.Text, default="")  # normalized text used by the enhanced search
    copies = db.relationship("Copy", backref="title", cascade="all, delete-orphan", order_by="Copy.barcode")


class Copy(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title_id = db.Column(db.Integer, db.ForeignKey("title.id"), nullable=False)
    barcode = db.Column(db.String(50), unique=True, nullable=False)
    shelf = db.Column(db.String(60), default="")
    location = db.Column(db.String(100), default="")
    price = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default="available")  # available|out|lost|damaged|withdrawn
    added_at = db.Column(db.DateTime, default=datetime.utcnow)


class BarcodeRange(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False)  # copy | student
    prefix = db.Column(db.String(20), default="")
    start = db.Column(db.Integer, nullable=False)
    end = db.Column(db.Integer, nullable=False)
    padding = db.Column(db.Integer, default=0)
    note = db.Column(db.String(200), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def code(self, n):
        return f"{self.prefix}{str(n).zfill(self.padding)}".upper()

    def codes(self):
        return [self.code(n) for n in range(self.start, self.end + 1)]


class Loan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    copy_id = db.Column(db.Integer, db.ForeignKey("copy.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    year_id = db.Column(db.Integer, db.ForeignKey("academic_year.id"))
    semester_id = db.Column(db.Integer, db.ForeignKey("semester.id"))
    section_id = db.Column(db.Integer, db.ForeignKey("section.id"))  # class snapshot at loan time (for statistics)
    out_at = db.Column(db.DateTime, default=datetime.utcnow)
    due_date = db.Column(db.Date, nullable=False)
    returned_at = db.Column(db.DateTime)
    renewals = db.Column(db.Integer, default=0)
    note = db.Column(db.String(100), default="")
    copy = db.relationship("Copy")
    student = db.relationship("Student")
    section = db.relationship("Section")


class Fine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loan.id"))
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    reason = db.Column(db.String(20), default="overdue")  # overdue | lost
    status = db.Column(db.String(20), default="unpaid")  # unpaid | paid | waived
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    settled_at = db.Column(db.DateTime)
    student = db.relationship("Student")
    loan = db.relationship("Loan")


# ================= Phase 3: student portal & holds =================
class StudentCredential(db.Model):
    """PIN login for the student portal. Only a hash of the PIN is stored; staff see the PIN once, when generated."""
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), unique=True, nullable=False)
    pin_hash = db.Column(db.String(255), nullable=False)
    failed_count = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    student = db.relationship("Student")


class Hold(db.Model):
    """Reservation of a title by a student: waiting -> ready (a copy is set aside) -> fulfilled | expired | cancelled."""
    id = db.Column(db.Integer, primary_key=True)
    title_id = db.Column(db.Integer, db.ForeignKey("title.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    status = db.Column(db.String(20), default="waiting")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    ready_at = db.Column(db.DateTime)
    expires_at = db.Column(db.Date)
    copy_id = db.Column(db.Integer, db.ForeignKey("copy.id"))
    closed_at = db.Column(db.DateTime)
    year_id = db.Column(db.Integer, db.ForeignKey("academic_year.id"))
    section_id = db.Column(db.Integer, db.ForeignKey("section.id"))  # class snapshot for statistics
    title = db.relationship("Title")
    student = db.relationship("Student")
    copy = db.relationship("Copy")


# ================= Phase 4: notifications =================
class EmailLog(db.Model):
    """Outbox + history of e-mails sent through Microsoft Graph. dedupe_key makes every notification one-shot."""
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    kind = db.Column(db.String(20))  # hold_ready | due_soon | overdue | test
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"))
    to_addr = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(300), default="")
    body = db.Column(db.Text, default="")
    dedupe_key = db.Column(db.String(120), unique=True)
    status = db.Column(db.String(10), default="pending")  # pending | sent | failed
    attempts = db.Column(db.Integer, default=0)
    error = db.Column(db.String(500), default="")
    sent_at = db.Column(db.DateTime)
    student = db.relationship("Student")


class ImportJob(db.Model):
    """Rows of a previewed import, kept on the server so a 1,000+ row file is never round-tripped through the browser."""
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False)
    payload = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
