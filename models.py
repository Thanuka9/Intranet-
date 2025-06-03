import uuid
import random
from datetime import datetime, timedelta
from extensions import db
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import Column, Integer, String, LargeBinary, Date, DateTime, Boolean, ForeignKey, Text, Table, JSON
from sqlalchemy.orm import relationship
from flask_login import UserMixin
from sqlalchemy import Float
from flask import request
from sqlalchemy.dialects.postgresql import ARRAY 

# -------------------------------
# Association Table for User and Tasks
# -------------------------------
user_task_association = Table(
    'user_task_association', db.Model.metadata,
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    Column('task_id', Integer, ForeignKey('tasks.id', ondelete='CASCADE'), primary_key=True),
)

# -------------------------------
# Association Table for Departments
# -------------------------------
user_departments = Table(
    'user_departments', db.Model.metadata,
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    Column('department_id', Integer, ForeignKey('departments.id', ondelete='CASCADE'), primary_key=True),
)

# -------------------------------------
# Association Table for Roles
# -------------------------------------
user_roles = Table(
    'user_roles', db.Model.metadata,
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    Column('role_id', Integer, ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
)

# -------------------------------------
# Association Table for user_clients
# -------------------------------------
user_clients = Table(
  'user_clients', db.Model.metadata,
  Column('user_id',   Integer, ForeignKey('users.id', ondelete='CASCADE'),   primary_key=True),
  Column('client_id', Integer, ForeignKey('clients.id', ondelete='CASCADE'), primary_key=True),
)

# -------------------------------------
# Role Model
# -------------------------------------
class Role(db.Model):
    __tablename__ = 'roles'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)

    # Relationship: Users assigned to this role
    users = relationship("User", secondary=user_roles, back_populates="roles")

    def __repr__(self):
        return f"<Role(id={self.id}, name='{self.name}')>"
    
# -------------------------------------
# Designation Model (Enhanced)
# -------------------------------------
class Designation(db.Model):
    __tablename__ = 'designations'

    id = Column(Integer, primary_key=True)
    title = Column(String(50), unique=True, nullable=False)
    starting_level = Column(Integer, default=0)  # Minimum level required for this designation
    
    # Relationships
    users = relationship("User", back_populates="designation")
    # Relationship to study materials where this designation is used as minimum requirement.
    study_materials = relationship("StudyMaterial", back_populates="minimum_designation", cascade="all, delete-orphan")

    def can_skip_level(self, target_level):
        """Check if the user can skip a level based on their designation."""
        return self.starting_level <= target_level

    def __repr__(self):
        return f"<Designation(id={self.id}, title='{self.title}', starting_level={self.starting_level})>"

# -------------------------------------
# Category Model (Integrated)
# -------------------------------------
class Category(db.Model):
    __tablename__ = 'categories'

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)  # Billing, Posting, VOB, etc.

    # Relationships
    level_areas = relationship("LevelArea", back_populates="category")
    exams = relationship("Exam", back_populates="category", cascade="all, delete-orphan")
    user_scores = relationship("UserScore", back_populates="category", cascade="all, delete-orphan")
    questions = relationship("Question", back_populates="category", cascade="all, delete-orphan")
    study_materials = relationship("StudyMaterial", back_populates="category", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Category(id={self.id}, name='{self.name}')>"

# -------------------------------
# Client Model
# -------------------------------

class Client(db.Model):
    __tablename__ = 'clients'

    id   = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)

    # Many-to-many relationship to User
    users = relationship(
        "User",
        secondary=user_clients,
        back_populates="clients"
    )

    # One-to-many relationship to Task
    tasks = relationship(
        "Task",
        back_populates="client",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Client(id={self.id}, name='{self.name}')>"


# -------------------------------------
# Updated StudyMaterial Model
# -------------------------------------
class StudyMaterial(db.Model):
    __tablename__ = 'study_materials'

    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    course_time = Column(Integer, nullable=False)
    max_time = Column(Integer, nullable=False)
    total_pages = Column(Integer, nullable=True, default=0)
    # Updated to store file IDs as a list (e.g. ["<mongo_id>|filename", ...])
    files = Column(ARRAY(String), default=[])  
    restriction_level = Column(Integer, nullable=True, default=0)

    # Foreign Keys
    category_id = Column(Integer, ForeignKey('categories.id'), nullable=True)
    level_id = Column(Integer, ForeignKey('levels.id'), nullable=True)
    # 'minimum_level' references the designation that defines the minimum required level.
    minimum_level = Column(Integer, ForeignKey('designations.id'), nullable=False, default=1)

    # Relationships using back_populates for two‑way linkage
    category = relationship("Category", back_populates="study_materials")
    level = relationship("Level", back_populates="study_materials")
    minimum_designation = relationship("Designation", foreign_keys=[minimum_level], back_populates="study_materials")
    subtopics = relationship("SubTopic", back_populates="study_material", cascade="all, delete-orphan")
    user_progress = relationship("UserProgress", back_populates="study_material", cascade="all, delete-orphan")
    exams = relationship("Exam", back_populates="course", cascade="all, delete-orphan")

    def is_accessible(self, user):
        """Check if the user can access this study material."""
        user_level = user.get_current_level() or 1
        required_level = self.minimum_designation.starting_level if self.minimum_designation else 1
        return user_level >= required_level

    def __repr__(self):
        return f"<StudyMaterial(id={self.id}, title='{self.title}', category_id={self.category_id})>"


# -------------------------------------
# Updated SubTopic Model
# -------------------------------------
class SubTopic(db.Model):
    __tablename__ = 'subtopics'

    id = Column(Integer, primary_key=True)
    study_material_id = Column(Integer, ForeignKey('study_materials.id'), nullable=False)
    title = Column(String(255), nullable=False)
    # Allow NULL if a subtopic has no file
    file_id = Column(String(255), nullable=True)
    page_count = Column(Integer, nullable=True, default=0)

    # Two‑way relationship with StudyMaterial
    study_material = relationship("StudyMaterial", back_populates="subtopics")

    def __repr__(self):
        return f"<SubTopic(id={self.id}, title='{self.title}', page_count={self.page_count})>"
# -------------------------------------
# UserProgress Model
# -------------------------------------
class UserProgress(db.Model):
    __tablename__ = 'user_progress'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    study_material_id = db.Column(db.Integer, db.ForeignKey('study_materials.id'), nullable=False, index=True)
    pages_visited = db.Column(db.Integer, default=0)  # Pages visited by the user
    progress_percentage = db.Column(db.Integer, default=0)  # Progress percentage
    time_spent = db.Column(db.Integer, default=0)  # Total time spent (in seconds)
    start_date = db.Column(db.DateTime, default=datetime.utcnow)  # Automatic start date
    completion_date = db.Column(db.DateTime, nullable=True)
    completed = db.Column(db.Boolean, default=False)  # New field for completion status

    # New Addition: Link Progress to Levels
    level_id = db.Column(db.Integer, db.ForeignKey('levels.id'), nullable=True, index=True)

    # Relationships
    user = db.relationship("User", back_populates="study_progress", passive_deletes=True)
    study_material = db.relationship("StudyMaterial", back_populates="user_progress")
    level = db.relationship("Level", back_populates="user_progress")  # New relationship

    def calculate_progress(self, total_pages):
        """Calculate and update progress percentage."""
        self.progress_percentage = int((self.pages_visited / total_pages) * 100)
        self.completed = (self.progress_percentage >= 100)  # Mark as completed if 100%
        db.session.commit()

    def update_time_spent(self, additional_time):
        """Update the total time spent on the material."""
        self.time_spent = (self.time_spent or 0) + additional_time
        db.session.commit()

    def __repr__(self):
        return (f"<UserProgress(id={self.id}, user_id={self.user_id}, "
                f"progress_percentage={self.progress_percentage}, time_spent={self.time_spent}, "
                f"completed={self.completed})>")

# -------------------------------------
# Level Model
# -------------------------------------
class Level(db.Model):
    __tablename__ = 'levels'

    id = db.Column(db.Integer, primary_key=True)
    level_number = db.Column(db.Integer, nullable=False, unique=True)
    title = db.Column(db.String(255), nullable=False)

    # Relationships
    level_areas = db.relationship("LevelArea", back_populates="level", cascade="all, delete-orphan")
    study_materials = db.relationship("StudyMaterial", back_populates="level", cascade="all, delete-orphan")
    user_level_progress = db.relationship("UserLevelProgress", back_populates="level", cascade="all, delete-orphan")
    user_progress = db.relationship("UserProgress", back_populates="level", cascade="all, delete-orphan")
    exams = relationship("Exam", back_populates="level", cascade="all, delete-orphan")
    user_scores = relationship("UserScore", back_populates="level", cascade="all, delete-orphan") 

    def __repr__(self):
        return f"<Level(id={self.id}, level_number={self.level_number}, title='{self.title}')>"

# -------------------------------------
# UserLevelProgress Model
# -------------------------------------
class UserLevelProgress(db.Model):
    __tablename__ = 'user_level_progress'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    level_id = db.Column(db.Integer, db.ForeignKey('levels.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    area_id = db.Column(db.Integer, ForeignKey('areas.id'), nullable=False)
    status = db.Column(db.String(20), default='pending') 
    attempts = db.Column(db.Integer, default=0)
    best_score = db.Column(db.Float)

    # Relationships
    user = db.relationship("User", back_populates="level_progress", passive_deletes=True)
    level = db.relationship("Level", back_populates="user_level_progress")
    category = db.relationship("Category")
    area = db.relationship("Area", back_populates="user_level_progress")
    

    def __repr__(self):
        return (f"<UserLevelProgress(user_id={self.user_id}, "
                f"level_id={self.level_id}, category_id={self.category_id}, "
                f"status={self.status})>")

# -------------------------------------
#User Model
# -------------------------------------
class User(db.Model, UserMixin):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    first_name = Column(String(50), nullable=False)
    last_name = Column(String(50), nullable=False)
    phone_number = Column(String(15), nullable=True)
    employee_email = Column(String(120), unique=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    employee_id = Column(String(20), unique=True, nullable=False)
    join_date = Column(Date, nullable=False)
    profile_picture = Column(LargeBinary, nullable=True)
    deleted_at        = Column(DateTime, nullable=True)
    last_login = db.Column(db.DateTime, nullable=True)

    # Using a relationship to link to the Department model
    departments = relationship(
        "Department",
        secondary=user_departments,
        back_populates="users"
    )

    is_super_admin = Column(Boolean, default=False)  # Super admin privileges
    current_level = Column(Integer, default=0)  # Tracks the user's current active level

    # ---------------------------------
    # Foreign Key Relationships
    # ---------------------------------
    designation_id = Column(Integer, ForeignKey('designations.id'), nullable=True)
    designation = relationship("Designation", back_populates="users")

    clients = relationship(
    "Client",
    secondary=user_clients,
    back_populates="users",
    passive_deletes=True
    )

    # ---------------------------------
    # Progress Tracking Relationships
    # ---------------------------------
    level_progress = db.relationship(
        "UserLevelProgress",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    study_progress = db.relationship(
        "UserProgress",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    scores = db.relationship(
        "UserScore",
        back_populates="user",
        cascade="all, delete-orphan"
    )

    # ---------------------------------
    # Exam and Task Management
    # ---------------------------------
    created_exams = relationship(
        "Exam",
        back_populates="created_by_user",
        cascade="all, delete-orphan",
        passive_deletes=True
    )
    tasks_assigned = relationship(
        "Task",
        foreign_keys='Task.assigned_by',
        back_populates="assigned_by_user",
        cascade="all, delete-orphan",
        passive_deletes=True
    )
    tasks_received = relationship(
        "Task",
        secondary="user_task_association",
        back_populates="assignees",
        passive_deletes=True
    )
    # ---------------------------------
    # password_reset_requests
    # ---------------------------------
    password_reset_requests = db.relationship(
        'PasswordResetRequest',
        back_populates='user',
        cascade='all, delete-orphan'
    )
    # ---------------------------------
    # Event Management
    # ---------------------------------
    events = relationship(
        "Event",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True
    )

    # ---------------------------------
    # Email Verification and 2FA
    # ---------------------------------
    is_verified = Column(Boolean, default=False)
    verification_token    = Column(Text, nullable=True)
    two_fa_code = Column(String(6), nullable=True)
    two_fa_expiration = Column(DateTime, nullable=True)

    # ---------------------------------
    # Password Reset Fields
    # ---------------------------------
    password_reset_token  = Column(Text, nullable=True)
    password_reset_expiration = Column(DateTime, nullable=True)

    # ---------------------------------
    # SpecialExamRecord Relationship (One-to-One)
    # ---------------------------------
    special_exam_record = db.relationship(
        "SpecialExamRecord",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan"
    )
    # ---------------------------------
    # Incorrect Answers Tracking
    # ---------------------------------
    incorrect_answers = relationship('IncorrectAnswer', back_populates='user', cascade='all, delete-orphan')

    # ---------------------------------
    # Role-based Access Control (RBAC)
    # ---------------------------------
    roles = db.relationship("Role", secondary=user_roles, back_populates="users", passive_deletes=True)

    # ---------------------------------
    # Curent Level
    # ---------------------------------
    def get_current_level(self):
        """
        Returns the user's current active level.
        Defaults to 1 if current_level is not set.
        """
        return self.current_level if self.current_level else 1

    @property
    def role(self):
        """
        Returns the user's default role.
        If roles are assigned, returns the first role's name; otherwise defaults to "member".
        """
        if self.roles and len(self.roles) > 0:
            return self.roles[0].name
        return "member"

    # ---------------------------------
    # Designation-Based Logic
    # ---------------------------------
    def can_skip_level(self, target_level: int) -> bool:
        """
        Check if the user can skip a level based on their designation.
        :param target_level: Target level to be skipped
        :return: Boolean indicating if skipping is allowed
        """
        if not self.designation:
            return False
        return self.designation.starting_level <= target_level

    def can_skip_exam(self, exam) -> bool:
        """
        Check if the user can skip a specific exam based on their designation.
        :param exam: Exam object to check
        :return: Boolean indicating if skipping the exam is allowed
        """
        return self.can_skip_level(exam.level.level_number)

    # ---------------------------------
    # Two-Factor Authentication
    # ---------------------------------
    def generate_2fa_code(self) -> None:
        """
        Generate and set a 6-digit 2FA code valid for 5 minutes.
        """
        self.two_fa_code = str(random.randint(100000, 999999))
        self.two_fa_expiration = datetime.utcnow() + timedelta(minutes=5)
        db.session.commit()

    # ---------------------------------
    # Lockout on Failed Logins
    # ---------------------------------
    failed_login_count = Column(Integer, default=0, nullable=False)
    is_locked          = Column(Boolean, default=False, nullable=False)
    locked_at          = Column(DateTime, nullable=True)

    def lock(self):
        """Freeze the account."""
        self.is_locked = True
        self.locked_at = datetime.utcnow()
        

    def reset_lock(self):
        """Clear failed‐login counter and unlock."""
        self.failed_login_count = 0
        self.is_locked          = False
        self.locked_at          = None
        db.session.commit()
    # ---------------------------------
    # Password Management
    # ---------------------------------
    def set_password(self, password: str) -> None:
        """
        Hash and set the user's password.
        :param password: Plain text password
        """
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """
        Verify the user's password.
        :param password: Plain text password
        :return: Boolean indicating if the password is correct
        """
        return check_password_hash(self.password_hash, password)

    # ---------------------------------
    # String Representation for Debugging
    # ---------------------------------
    def __repr__(self):
        full_name = f"{self.first_name} {self.last_name}"
        return f"<User(id={self.id}, name='{full_name}', level={self.current_level})>"

# -------------------------------
# Exam Model
# -------------------------------
class Exam(db.Model):
    __tablename__ = 'exams'

    id = Column(Integer, primary_key=True)
    title = Column(String(100), nullable=False)
    duration = Column(Integer, nullable=False)
    level_id = Column(Integer, ForeignKey('levels.id'), nullable=False)
    area_id = Column(Integer, ForeignKey('areas.id'), nullable=False)
    course_id = Column(Integer, ForeignKey('study_materials.id'), nullable=False)
    created_by = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    category_id = Column(Integer, ForeignKey('categories.id'), nullable=False)

    # New Additions
    minimum_level = Column(Integer, nullable=True)             # Minimum level required for this exam
    minimum_designation_level = Column(Integer, nullable=True) # Designation level required for skipping

    # Relationships
    level = relationship("Level", back_populates="exams")
    area = relationship("Area", back_populates="exams")
    created_by_user = relationship("User", back_populates="created_exams", passive_deletes=True)
    course = relationship("StudyMaterial", back_populates="exams")
    category = relationship("Category", back_populates="exams")
    questions = relationship("Question", back_populates="exam", cascade="all, delete-orphan")
    scores = relationship("UserScore", back_populates="exam", cascade="all, delete-orphan")
    level_areas = relationship(
        "LevelArea",
        back_populates="required_exam",
        foreign_keys="[LevelArea.required_exam_id]",
        cascade="all, delete-orphan"
    )


    def __repr__(self):
        level_num = self.level.level_number if self.level else 'N/A'
        area_name = self.area.name if self.area else 'N/A'
        return f"<Exam(id={self.id}, title='{self.title}', level='{level_num}', area='{area_name}')>"

    def is_accessible(self, user):
        """
        Check if the user meets the minimum level requirement.
        """
        return user.get_current_level() >= (int(self.minimum_level) if self.minimum_level is not None else 1)

    def is_skippable(self, user):
        """
        Check if the user can skip this exam based on their designation level.
        """
        if not self.minimum_designation_level:
            return False
        if user.designation:
            return user.designation.starting_level >= self.minimum_designation_level
        return False

    incorrect_answers = relationship('IncorrectAnswer', back_populates='exam', cascade='all, delete-orphan')
# -------------------------------
# Question Model
# -------------------------------
class Question(db.Model):
    __tablename__ = 'questions'

    id = Column(Integer, primary_key=True)
    exam_id = Column(Integer, ForeignKey('exams.id', ondelete='CASCADE'), nullable=False)
    question_text = Column(Text, nullable=False)
    choices = Column(Text, nullable=False)  # Stores comma-separated choices
    correct_answer = Column(String(255), nullable=False)
    category_id = Column(Integer, ForeignKey('categories.id', ondelete='CASCADE'), nullable=False)

    # Relationships
    exam = relationship("Exam", back_populates="questions", passive_deletes=True)
    category = relationship("Category", back_populates="questions", passive_deletes=True)

    def __repr__(self):
        return f"<Question(id={self.id}, text='{self.question_text[:30]}...', category='{self.category.name}')>"

    def get_choices(self):
        """Return the list of choices as a list"""
        return self.choices.split(',')
    
    def set_choices(self, choices_list):
        """Set the choices as a comma-separated string"""
        self.choices = ','.join(choices_list)

# -------------------------------
# UserScore Model
# -------------------------------
class UserScore(db.Model):
    __tablename__ = 'user_scores'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    exam_id = Column(Integer, ForeignKey('exams.id'), nullable=False)
    area_id = Column(Integer, ForeignKey('areas.id'), nullable=False)
    level_id = Column(Integer, ForeignKey('levels.id'), nullable=False)  # Tracks Level
    category_id = Column(Integer, ForeignKey('categories.id'), nullable=False)
    score = Column(Float, nullable=False)  # Changed from Integer to Float
    attempts = Column(Integer, default=1)  # Tracks attempts for better analytics
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="scores", passive_deletes=True)
    exam = relationship("Exam", back_populates="scores")
    area = relationship("Area", back_populates="user_scores")  # Linked to Area
    level = relationship("Level", back_populates="user_scores")  # Linked to Level
    category = relationship("Category", back_populates="user_scores")  # Linked to Category

    def __repr__(self):
        # Handle None values gracefully in repr
        level_num = self.level.level_number if self.level else 'N/A'
        area_name = self.area.name if self.area else 'N/A'
        return (f"<UserScore(id={self.id}, user_id={self.user_id}, "
                f"level={level_num}, area='{area_name}', score={self.score})>")

# -------------------------------
# Exam Access Request Model
# -------------------------------
class ExamAccessRequest(db.Model):
    __tablename__ = 'exam_access_requests'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False
    )
    exam_id = db.Column(db.Integer, nullable=False)  # Supports both regular & special exams
    status = db.Column(db.String(20), default='pending')  # pending | approved | rejected
    requested_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    used        = db.Column(db.Boolean, nullable=False, default=False)

    user = db.relationship(
        "User",
        backref=db.backref("exam_requests", passive_deletes=True),
        passive_deletes=True
    )

    @property
    def is_special_exam(self):
        return self.exam_id in (9991, 9992)

# --------------------------------    
# Task Model
# -------------------------------
class Task(db.Model):
    __tablename__ = 'tasks'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    due_date = db.Column(db.Date, nullable=False)
    priority = db.Column(db.String(20), nullable=False, default="Medium")
    status = db.Column(db.String(50), nullable=False, default="Getting Things Started...")
    progress = db.Column(db.Integer, nullable=False, default=0)

    # Foreign Keys
    assigned_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False
    )
    completed_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id', ondelete='CASCADE'), nullable=True)

    # Relationships
    assigned_by_user = db.relationship(
        "User",
        foreign_keys=[assigned_by],
        back_populates="tasks_assigned",
        passive_deletes=True
    )
    completed_by_user = db.relationship(
        "User",
        foreign_keys=[completed_by],
        passive_deletes=True
    )
    client = db.relationship("Client", back_populates="tasks")

    assignees = db.relationship(
        "User",
        secondary="user_task_association",
        back_populates="tasks_received"
    )

    documents = db.relationship(
        "TaskDocument",
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True
    )

    def calculate_progress(self):
        """Calculate progress based on the task's status."""
        status_progress_mapping = {
            "Getting Things Started...": 0,
            "Setting Up the Path...": 20,
            "Halfway There! Keep Going!": 50,
            "Almost Done! Just a Little More!": 80,
            "Wrapping Things Up...": 90,
            "Final Touches in Progress...": 95,
            "Complete! Ready to Go!": 100
        }
        self.progress = status_progress_mapping.get(self.status, 0)
        db.session.commit()

    def __repr__(self):
        return f"<Task(id={self.id}, title='{self.title}', status='{self.status}', progress={self.progress})>"

# -------------------------------
# TaskDocument Model
# -------------------------------
class TaskDocument(db.Model):
    __tablename__ = 'task_documents'

    id = Column(Integer, primary_key=True)
    filename = Column(String(255), nullable=False)
    filetype = Column(String(255), nullable=False)
    data = Column(LargeBinary, nullable=False)
    upload_date = Column(DateTime, default=datetime.utcnow)

    # 1) add ondelete="CASCADE" here:
    task_id = Column(
        Integer,
        ForeignKey('tasks.id', ondelete='CASCADE'),
        nullable=False
    )

    # 2) enable passive_deletes=True so SQLAlchemy trusts the DB to cascade
    task = relationship(
        "Task",
        back_populates="documents",
        passive_deletes=True
    )

    def __repr__(self):
        return f"<TaskDocument(id={self.id}, filename='{self.filename}', task_id={self.task_id})>"

# -------------------------------
# FailedLogin Model
# -------------------------------
class FailedLogin(db.Model):
    __tablename__ = 'failed_logins'

    id          = Column(Integer, primary_key=True)
    email       = Column(String(120), nullable=False)
    ip_address  = Column(String(45), nullable=True)
    user_agent  = Column(String(256), nullable=True)
    timestamp   = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return (
            f"<FailedLogin(id={self.id}, email='{self.email}', "
            f"ip='{self.ip_address}', ts={self.timestamp})>"
        )

def log_failed_login_attempt(email):
    """Log a failed login attempt, capturing IP and User-Agent."""
    try:
        fl = FailedLogin(
            email=email,
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent'),
            timestamp=datetime.utcnow()
        )
        db.session.add(fl)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        # Consider using logging.error(...) instead of print in production
        print(f"Error logging failed login attempt: {e}")

# -------------------------------
# Event Model
# -------------------------------
class Event(db.Model):
    __tablename__ = 'events'

    id = Column(Integer, primary_key=True)
    title = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    date = Column(Date, nullable=False)

    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    user = db.relationship("User", back_populates="events", passive_deletes=True)

    def __repr__(self):
        return f"<Event(id={self.id}, title='{self.title}', date={self.date}, user_id={self.user_id})>"

# -------------------------------
# Area Model
# -------------------------------
class Area(db.Model):
    __tablename__ = 'areas'

    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)  # Billing, Posting, VOB, etc.t

    # Relationships
    level_areas = relationship("LevelArea", back_populates="area", cascade="all, delete-orphan")
    exams = relationship("Exam", back_populates="area", cascade="all, delete-orphan")  # Linked to Exam
    user_level_progress = relationship("UserLevelProgress", back_populates="area", cascade="all, delete-orphan")
    user_scores = relationship("UserScore", back_populates="area", cascade="all, delete-orphan")  # Linked to UserScore

    def __repr__(self):
        return f"<Area(id={self.id}, name='{self.name}')>"

# -------------------------------
# LevelArea Model
# -------------------------------
class LevelArea(db.Model):
    __tablename__ = 'level_areas'

    id               = Column(Integer, primary_key=True)
    level_id         = Column(Integer, ForeignKey('levels.id'), nullable=False)
    category_id      = Column(Integer, ForeignKey('categories.id'), nullable=False)
    area_id          = Column(Integer, ForeignKey('areas.id'), nullable=False)
    required_exam_id = Column(Integer, ForeignKey('exams.id'), nullable=True)

    # Relationships
    level         = relationship("Level",    back_populates="level_areas")
    category      = relationship("Category", back_populates="level_areas")
    area          = relationship("Area",     back_populates="level_areas")
    required_exam = relationship(
        "Exam",
        foreign_keys=[required_exam_id],
        back_populates="level_areas",
        lazy='joined'
    )

    @property
    def exam(self):
        """Alias for backward compatibility with existing code."""
        return self.required_exam

    def __repr__(self):
        return (
            f"<LevelArea(id={self.id}, level_id={self.level_id}, "
            f"category_id={self.category_id}, area_id={self.area_id}, "
            f"required_exam_id={self.required_exam_id})>"
        )

# -------------------------------
# SpecialExamRecord Model
# -------------------------------
class SpecialExamRecord(db.Model):
    __tablename__ = 'special_exam_records'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)

    # Paper 1 fields
    paper1_score = db.Column(db.Float, default=0.0)
    paper1_passed = db.Column(db.Boolean, default=False)
    paper1_time_spent = db.Column(db.Integer, default=0)  # in seconds
    paper1_completed_at = db.Column(db.DateTime, nullable=True)
    paper1_attempts = db.Column(db.Integer, default=0)

    # Paper 2 fields
    paper2_score = db.Column(db.Float, default=0.0)
    paper2_passed = db.Column(db.Boolean, default=False)
    paper2_time_spent = db.Column(db.Integer, default=0)  # in seconds
    paper2_completed_at = db.Column(db.DateTime, nullable=True)
    paper2_attempts = db.Column(db.Integer, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    # Use back_populates on both sides of the relationship.
    user = db.relationship("User", back_populates="special_exam_record", uselist=False, passive_deletes=True)

    def __repr__(self):
        return f"<SpecialExamRecord(id={self.id}, user_id={self.user_id})>"

# -------------------------------------
# Department Model
# -------------------------------------
class Department(db.Model):
    __tablename__ = 'departments'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    
    # Relationship: Users assigned to this department
    users = relationship(
        "User",
        secondary=user_departments,
        back_populates="departments"
    )
    
    def __repr__(self):
        return f"<Department(id={self.id}, name='{self.name}')>"
    
# -------------------------------------
# IncorrectAnswer Model
# -------------------------------------
class IncorrectAnswer(db.Model):
    __tablename__ = 'incorrect_answers'

    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    exam_id        = db.Column(db.Integer, db.ForeignKey('exams.id', ondelete='CASCADE'), nullable=True, index=True)
    special_paper  = db.Column(db.String(10), nullable=True, index=True)
    question_id    = db.Column(db.Integer, nullable=False)
    user_answer    = db.Column(Text, nullable=False)      # <-- now free-text
    correct_answer = db.Column(Text, nullable=False)      # <-- now free-text
    answered_at    = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index('ix_user_exam', 'user_id', 'exam_id'),
    )

    user = db.relationship(
        'User',
        back_populates='incorrect_answers',
        passive_deletes=True
    )
    exam = db.relationship(
        'Exam',
        back_populates='incorrect_answers',
        passive_deletes=True
    )

# -------------------------------------
# PasswordResetRequest Model
# -------------------------------------
class PasswordResetRequest(db.Model):
    __tablename__ = 'password_reset_request'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False
    )
    token = db.Column(db.String(128), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    timestamp = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    # back-reference to User
    user = db.relationship(
        'User',
        back_populates='password_reset_requests',
        passive_deletes=True
    )

# -------------------------------------
# SupportTicket Model
# -------------------------------------
class SupportTicket(db.Model):
    __tablename__ = 'support_tickets'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False
    )
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)

    # ──────────────── New Column ─────────────────
    # Stores the administrator’s response text
    admin_response = db.Column(db.Text, nullable=True)
    # ───────────────────────────────────────────────

    status = db.Column(db.String(50), default="Open")  # Open, In Progress, Resolved
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)
    assigned_to = db.Column(
        db.Integer,
        db.ForeignKey('users.id'),
        nullable=True
    )

    # Relationships
    user = db.relationship(
        "User",
        foreign_keys=[user_id],
        backref="support_tickets",
        passive_deletes=True
    )
    assignee = db.relationship(
        "User",
        foreign_keys=[assigned_to],
        lazy='joined'
    )
    attachments = db.relationship(
        "SupportAttachment",
        back_populates="ticket",
        cascade="all, delete-orphan"
    )

    def time_taken_minutes(self):
        if self.resolved_at:
            delta = self.resolved_at - self.created_at
            return int(delta.total_seconds() // 60)
        return None

    def __repr__(self):
        return f"<SupportTicket(id={self.id}, user_id={self.user_id}, status={self.status})>"


# -------------------------------------
# SupportAttachment Model
# -------------------------------------
class SupportAttachment(db.Model):
    __tablename__ = 'support_attachments'

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    mimetype = db.Column(db.String(100), nullable=False)
    data = db.Column(db.LargeBinary, nullable=False)
    upload_time = db.Column(db.DateTime, default=datetime.utcnow)
    ticket_id = db.Column(
        db.Integer,
        db.ForeignKey('support_tickets.id', ondelete='CASCADE'),
        nullable=False
    )

    # Relationship
    ticket = db.relationship("SupportTicket", back_populates="attachments")

    def __repr__(self):
        return f"<SupportAttachment(id={self.id}, filename='{self.filename}', ticket_id={self.ticket_id})>"