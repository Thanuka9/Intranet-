import uuid
import random
from datetime import datetime, timedelta
from extensions import db
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import Column, Integer, String, LargeBinary, Date, DateTime, Boolean, ForeignKey, Text, Table, JSON
from sqlalchemy.orm import relationship
from flask_login import UserMixin
from sqlalchemy import Float
from sqlalchemy.dialects.postgresql import ARRAY 

# -------------------------------
# Association Table
# -------------------------------
user_task_association = Table(
    'user_task_association', db.Model.metadata,
    Column('user_id', Integer, ForeignKey('users.id'), primary_key=True),
    Column('task_id', Integer, ForeignKey('tasks.id'), primary_key=True)
)

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
    # Added relationship so you can access StudyMaterials from Category.
    study_materials = relationship("StudyMaterial", back_populates="category", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Category(id={self.id}, name='{self.name}')>"

# -------------------------------
# Client Model
# -------------------------------
class Client(db.Model):
    __tablename__ = 'clients'

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    users = relationship("User", back_populates="client", cascade="all, delete-orphan")

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
    restriction_level = Column(Integer, nullable=True)

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
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
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
    user = db.relationship("User", back_populates="study_progress")
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
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    level_id = db.Column(db.Integer, db.ForeignKey('levels.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    area_id = db.Column(db.Integer, ForeignKey('areas.id'), nullable=False)
    status = db.Column(db.String(20), default='pending') 
    attempts = db.Column(db.Integer, default=0)
    best_score = db.Column(db.Float)

    # Relationships
    user = db.relationship("User", back_populates="level_progress")
    level = db.relationship("Level", back_populates="user_level_progress")
    category = db.relationship("Category")
    area = db.relationship("Area", back_populates="user_level_progress")
    

    def __repr__(self):
        return (f"<UserLevelProgress(user_id={self.user_id}, "
                f"level_id={self.level_id}, category_id={self.category_id}, "
                f"status={self.status})>")

# -------------------------------------
# Updated User Model
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
    role = Column(String(50), default="member")  # e.g., member, admin
    departments = Column(Text, nullable=True)
    is_super_admin = Column(Boolean, default=False)  # Super admin privileges
    current_level = Column(Integer, default=0)  # Tracks user's active level

    # Foreign Key Relationships
    designation_id = Column(Integer, ForeignKey('designations.id'), nullable=True)
    designation = relationship("Designation", back_populates="users")

    client_id = Column(Integer, ForeignKey('clients.id'), nullable=True)
    client = relationship("Client", back_populates="users")

    # Progress Tracking Relationships
    level_progress = db.relationship("UserLevelProgress", back_populates="user", cascade="all, delete-orphan")
    study_progress = db.relationship("UserProgress", back_populates="user", cascade="all, delete-orphan")

    # `scores` relationship defined correctly as `user_scores`
    scores = db.relationship("UserScore", back_populates="user", cascade="all, delete-orphan")

    # Exam and Question Relationships
    created_exams = relationship("Exam", back_populates="created_by_user", cascade="all, delete-orphan")

    # Task Management Relationships
    tasks_assigned = relationship(
        "Task",
        foreign_keys='Task.assigned_by',
        back_populates="assigned_by_user",
        cascade="all, delete-orphan"
    )
    tasks_received = relationship(
        "Task",
        secondary="user_task_association",
        back_populates="assignees"
    )

    # Event Relationships
    events = relationship("Event", back_populates="user", cascade="all, delete-orphan")

    # Email Verification and 2FA
    is_verified = Column(Boolean, default=False)
    verification_token = Column(String(36), unique=True, nullable=True)
    two_fa_code = Column(String(6), nullable=True)
    two_fa_expiration = Column(DateTime, nullable=True)

    # Password Reset Fields
    password_reset_token = Column(String(36), unique=True, nullable=True)
    password_reset_expiration = Column(DateTime, nullable=True)

    # SpecialExamRecord Relationship (one-to-one)
    special_exam_record = db.relationship(
        "SpecialExamRecord",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan"
    )

    # Designation-Based Level and Exam Skipping Logic
    def can_skip_level(self, target_level):
        """
        Check if the user can skip a level based on their designation.
        """
        if not self.designation:
            return False  # No designation assigned, no skipping allowed
        return self.designation.starting_level <= target_level

    def can_skip_exam(self, exam):
        """
        Check if the user can skip a specific exam based on designation.
        """
        return self.can_skip_level(exam.level.level_number)

    # 2FA Code Generation
    def generate_2fa_code(self):
        self.two_fa_code = str(random.randint(100000, 999999))
        self.two_fa_expiration = datetime.utcnow() + timedelta(minutes=5)
        db.session.commit()

    # Password Management
    def set_password(self, password):
        """Hash and set the user's password."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Verify the user's password."""
        return check_password_hash(self.password_hash, password)

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
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    category_id = Column(Integer, ForeignKey('categories.id'), nullable=False)

    # New Additions
    minimum_level = Column(Integer, nullable=True)             # Minimum level required for this exam
    minimum_designation_level = Column(Integer, nullable=True) # Designation level required for skipping

    # Relationships
    level = relationship("Level", back_populates="exams")
    area = relationship("Area", back_populates="exams")
    created_by_user = relationship("User", back_populates="created_exams")
    course = relationship("StudyMaterial", back_populates="exams")
    category = relationship("Category", back_populates="exams")
    questions = relationship("Question", back_populates="exam", cascade="all, delete-orphan")
    scores = relationship("UserScore", back_populates="exam", cascade="all, delete-orphan")
    level_areas = relationship("LevelArea", back_populates="exam", cascade="all, delete-orphan")

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

# -------------------------------
# Question Model
# -------------------------------
class Question(db.Model):
    __tablename__ = 'questions'

    id = Column(Integer, primary_key=True)
    exam_id = Column(Integer, ForeignKey('exams.id'), nullable=False)
    question_text = Column(Text, nullable=False)
    choices = Column(Text, nullable=False)  # Stores comma-separated choices
    correct_answer = Column(String(255), nullable=False)
    category_id = Column(Integer, ForeignKey('categories.id'), nullable=False)

    # Relationships
    exam = relationship("Exam", back_populates="questions")
    category = relationship("Category", back_populates="questions")

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
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    exam_id = Column(Integer, ForeignKey('exams.id'), nullable=False)
    area_id = Column(Integer, ForeignKey('areas.id'), nullable=False)
    level_id = Column(Integer, ForeignKey('levels.id'), nullable=False)  # Tracks Level
    category_id = Column(Integer, ForeignKey('categories.id'), nullable=False)
    score = Column(Float, nullable=False)  # Changed from Integer to Float
    attempts = Column(Integer, default=1)  # Tracks attempts for better analytics
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="scores")
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

#--------------------------------    
# Task Model
# -------------------------------
class Task(db.Model):
    __tablename__ = 'tasks'

    id = Column(Integer, primary_key=True)
    title = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    due_date = Column(Date, nullable=False)
    priority = Column(String(20), default="Medium")
    status = Column(String(50), default="Getting Things Started...")
    progress = Column(Integer, default=0)

    assigned_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    completed_by = Column(Integer, ForeignKey('users.id'), nullable=True)

    assigned_by_user = relationship("User", foreign_keys=[assigned_by], back_populates="tasks_assigned")
    completed_by_user = relationship("User", foreign_keys=[completed_by])

    client_id = Column(Integer, ForeignKey('clients.id'), nullable=True)
    client = relationship("Client")

    assignees = relationship("User", secondary=user_task_association, back_populates="tasks_received")
    documents = relationship("TaskDocument", back_populates="task", cascade="all, delete-orphan")

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
    filetype = Column(String(50), nullable=False)
    data = Column(LargeBinary, nullable=False)
    upload_date = Column(DateTime, default=datetime.utcnow)

    task_id = Column(Integer, ForeignKey('tasks.id'), nullable=False)
    task = relationship("Task", back_populates="documents")

    def __repr__(self):
        return f"<TaskDocument(id={self.id}, filename='{self.filename}', task_id={self.task_id})>"

# -------------------------------
# FailedLogin Model
# -------------------------------
class FailedLogin(db.Model):
    __tablename__ = 'failed_logins'
    id = Column(Integer, primary_key=True)
    email = Column(String(120), nullable=False)
    attempt_time = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<FailedLogin(id={self.id}, email='{self.email}', attempt_time={self.attempt_time})>"

def log_failed_login_attempt(email):
    """Log a failed login attempt."""
    try:
        failed_login = FailedLogin(email=email)
        db.session.add(failed_login)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
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

    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    user = relationship("User", back_populates="events")

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

    id = Column(Integer, primary_key=True)
    level_id = Column(Integer, ForeignKey('levels.id'), nullable=False)
    category_id = Column(Integer, ForeignKey('categories.id'), nullable=False)
    area_id = Column(Integer, ForeignKey('areas.id'), nullable=False)  
    required_exam_id = Column(Integer, ForeignKey('exams.id'), nullable=True)

    # Relationships
    level = relationship("Level", back_populates="level_areas")
    category = relationship("Category", back_populates="level_areas")
    area = relationship("Area", back_populates="level_areas")  
    exam = relationship("Exam", back_populates="level_areas")

    def __repr__(self):
        return (f"<LevelArea(id={self.id}, level_id={self.level_id}, "
                f"category_id={self.category_id}, required_exam_id={self.required_exam_id})>")

# -------------------------------
# SpecialExamRecord Model
# -------------------------------
class SpecialExamRecord(db.Model):
    __tablename__ = 'special_exam_records'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    # Paper 1 fields
    paper1_score = db.Column(db.Float, default=0.0)
    paper1_passed = db.Column(db.Boolean, default=False)
    paper1_time_spent = db.Column(db.Integer, default=0)  # in seconds
    paper1_completed_at = db.Column(db.DateTime, nullable=True)

    # Paper 2 fields
    paper2_score = db.Column(db.Float, default=0.0)
    paper2_passed = db.Column(db.Boolean, default=False)
    paper2_time_spent = db.Column(db.Integer, default=0)  # in seconds
    paper2_completed_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    # Use back_populates on both sides of the relationship.
    user = db.relationship("User", back_populates="special_exam_record", uselist=False)

    def __repr__(self):
        return f"<SpecialExamRecord(id={self.id}, user_id={self.user_id})>"


