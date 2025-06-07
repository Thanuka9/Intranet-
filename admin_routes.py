from flask import (
    Blueprint, request, jsonify, render_template,
    redirect, url_for, session, flash, current_app
)
from flask_login import login_required, current_user
from models import (
    db,
    StudyMaterial, UserProgress, SubTopic,
    User, Designation, Exam, Question, UserScore,
    Category, Level, Area, UserLevelProgress,
    SpecialExamRecord, Client, LevelArea,
    Task, TaskDocument, FailedLogin, Event, Role, ExamAccessRequest, IncorrectAnswer, Department, user_departments, SupportAttachment, 
    SupportTicket, AuditLog
)
import logging
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from gridfs import GridFS
from pymongo import MongoClient
from functools import wraps
from sqlalchemy import func, or_, and_, case, cast, text, func
import io, csv, json
from bson import ObjectId
from flask import make_response
import os
from audit import log_event
from dotenv import load_dotenv
from pymongo import MongoClient
from gridfs import GridFS
from sqlalchemy.types import String
from sqlalchemy import desc
from IPython.display import HTML
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm  import joinedload
from collections import defaultdict



# Load .env variables (if running locally)
load_dotenv()

# --- MongoDB + GridFS setup ---
mongo_uri = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
mongo_db_name = os.getenv('MONGO_DB_NAME', 'collective_rcm')

mongo_client = MongoClient(mongo_uri)
mongo_db = mongo_client[mongo_db_name]
grid_fs = GridFS(mongo_db)


ALLOWED_EXTENSIONS = {'pdf', 'docx', 'ppt', 'xlsx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Blueprint & Logging ---
admin_routes = Blueprint('admin_routes', __name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# --- Helper to delete files from GridFS and model ---
def delete_files_from_gridfs(file_refs):
    """
    Given a list of 'file_id|filename' strings, delete each from GridFS.
    Returns list of IDs successfully deleted.
    """
    deleted = []
    for ref in file_refs or []:
        file_id, _ = ref.split("|")
        try:
            grid_fs.delete(file_id)
            deleted.append(file_id)
        except Exception as e:
            logging.error(f"Failed deleting file {file_id}: {e}")
    return deleted

# --- Admin Authentication Middleware ---
def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        # 1) Super-admins always pass
        if current_user.is_super_admin:
            return func(*args, **kwargs)

        # 2) “Plain” admins: role_id == 2
        if any(role.id == 2 for role in current_user.roles):
            return func(*args, **kwargs)

        # 3) Otherwise block & log
        flash("You don’t have permission to do that.", "access_denied")
        logging.warning(
            f"Unauthorized access attempt by user_id={current_user.id} "
            f"to {request.path} from {request.remote_addr}"
        )
        return redirect(request.referrer or url_for('admin_routes.admin_dashboard'))
    return wrapper

def super_admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_super_admin:
            flash("You don’t have permission to perform that action.", "access_denied")
            logging.warning(
                f"Unauthorized super-admin attempt by user_id={current_user.id} "
                f"to {request.path} from {request.remote_addr}"
            )
            return redirect(request.referrer or url_for('admin_routes.admin_dashboard'))
        return func(*args, **kwargs)
    return wrapper

# --- Sync PostgreSQL Sequence ---
def sync_sequence(model):
    """
    Reset the PostgreSQL sequence for the given model’s primary key.
    This assumes that:
      - The primary key column is named `id`.
      - The underlying sequence is named `<tablename>_id_seq`.
    After calling this, the next INSERT (without an explicit id) will use MAX(id)+1.
    """
    table_name = model.__tablename__            # e.g. "categories"
    seq_name = f"{table_name}_id_seq"          # e.g. "categories_id_seq"

    try:
        # 1) Find the current maximum id in the table
        result = db.session.execute(
            text(f"SELECT COALESCE(MAX(id), 0) FROM {table_name}")
        )
        max_id = result.scalar() or 0

        # 2) Compute the next value
        next_val = max_id + 1

        # 3) Reset the sequence to (max_id + 1)
        db.session.execute(
            text(f"ALTER SEQUENCE {seq_name} RESTART WITH :next_val"),
            {"next_val": next_val}
        )

        # 4) Commit so that ALTER SEQUENCE takes effect immediately
        db.session.commit()
        current_app.logger.info(f"Sequence {seq_name} synced to next value {next_val}")

    except Exception as e:
        # If something goes wrong (e.g. sequence does not exist), rollback and log
        db.session.rollback()
        current_app.logger.error(f"Failed to sync sequence {seq_name}: {e}")


# --- Admin Dashboard ---
@admin_routes.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    user_id = current_user.id
    q = request.args.get('q', '').strip()

    logging.info(f"user_id={user_id} viewed admin dashboard from {request.remote_addr} (search={q})")
    try:
        # 1) Basic Aggregates
        total_users            = User.query.count()
        total_designations     = Designation.query.count()
        total_clients          = Client.query.count()
        total_exams            = Exam.query.count()
        total_questions        = Question.query.count()
        total_study_materials  = StudyMaterial.query.count()
        special_exam_count     = SpecialExamRecord.query.count()

        # 2) Active Users
        active_user_ids = set(
            u for (u,) in db.session.query(UserScore.user_id).distinct()
        ) | set(
            u for (u,) in db.session.query(UserProgress.user_id).distinct()
        )
        active_users = len(active_user_ids)

        # 3) Performance Stats
        average_exam_score     = db.session.query(func.avg(UserScore.score)).scalar() or 0
        passed_exam_count      = UserScore.query.filter(UserScore.score >= 56).count()
        special_exam_passed_1  = SpecialExamRecord.query.filter_by(paper1_passed=True).count()
        special_exam_passed_2  = SpecialExamRecord.query.filter_by(paper2_passed=True).count()

        # 4) Course Progress / Restrictions
        course_completion_avg  = db.session.query(func.avg(UserProgress.progress_percentage)).scalar() or 0
        # Count materials that have a minimum_level > 1 (i.e. actually restricted)
        restricted_courses     = StudyMaterial.query.filter(StudyMaterial.minimum_level > 1).count()

        # 5) Recent Events
        recent_events = Event.query.order_by(Event.date.desc()).limit(5).all()

        # 6) Global Search
        search_results = None
        if q:
            user_hits   = User.query.filter(
                (User.first_name.ilike(f"%{q}%")) |
                (User.last_name.ilike (f"%{q}%")) |
                (User.employee_email.ilike(f"%{q}%"))
            ).all()
            course_hits = StudyMaterial.query.filter(StudyMaterial.title.ilike(f"%{q}%")).all()
            exam_hits   = Exam.query.filter(Exam.title.ilike(f"%{q}%")).all()
            search_results = {
                'users':   user_hits,
                'courses': course_hits,
                'exams':   exam_hits
            }

        return render_template(
            'admin_dashboard.html',
            # aggregates
            total_users=total_users,
            active_users=active_users,
            total_designations=total_designations,
            total_clients=total_clients,
            total_exams=total_exams,
            total_questions=total_questions,
            total_study_materials=total_study_materials,
            restricted_courses=restricted_courses,
            special_exam_count=special_exam_count,
            average_exam_score=round(average_exam_score, 2),
            passed_exam_count=passed_exam_count,
            special_exam_passed_1=special_exam_passed_1,
            special_exam_passed_2=special_exam_passed_2,
            course_completion_avg=round(course_completion_avg, 2),
            recent_events=recent_events,
            # search
            q=q,
            search_results=search_results,

            # legacy data (if still needed)
            courses=StudyMaterial.query.all(),
            users=User.query.all(),
            designations=Designation.query.all(),
            exams=Exam.query.all(),
            special_exam_records=SpecialExamRecord.query.all()
        )
    except Exception as e:
        logging.error(f"user_id={user_id} error loading admin dashboard: {e}")
        return render_template('500.html'), 500


# --- Delete Course ---
@admin_routes.route('/admin/delete_course/<int:course_id>', methods=['POST'])
@login_required
@super_admin_required
def delete_course(course_id):
    user_id = current_user.id
    ip      = request.remote_addr
    logging.info(f"user_id={user_id} deleting course_id={course_id} from {ip}")

    try:
        # 1) Fetch and cascade‐delete related rows
        course = StudyMaterial.query.get_or_404(course_id)
        SubTopic.query.filter_by(study_material_id=course_id).delete()
        UserProgress.query.filter_by(study_material_id=course_id).delete()

        # 2) Remove files from GridFS and from the model
        deleted_ids = delete_files_from_gridfs(course.files or [])
        course.files = [
            f for f in (course.files or [])
            if f.split("|", 1)[0] not in deleted_ids
        ]

        # 3) Delete the course itself
        db.session.delete(course)
        db.session.commit()

        flash("Course deleted successfully!", "success")
    except Exception as e:
        logging.error(f"user_id={user_id} error deleting course {course_id}: {e}", exc_info=True)
        db.session.rollback()
        flash("Failed to delete course. Please try again.", "error")

    # Redirect back to the Manage Courses page
    return redirect(url_for('admin_routes.view_courses'))

# --- Generate Reports ---
@admin_routes.route('/admin/reports')
@login_required
@admin_required
def generate_reports():
    user_id = current_user.id
    ip      = request.remote_addr
    logging.info(f"user_id={user_id} generating reports from {ip}")

    try:
        # --- 1) User search/filter ---
        search = request.args.get('search', '').strip()
        users_q = User.query
        if search:
            users_q = users_q.filter(or_(
                User.first_name.ilike(f'%{search}%'),
                User.last_name.ilike(f'%{search}%'),
                User.employee_email.ilike(f'%{search}%')
            ))
        users = users_q.order_by(User.join_date.desc()).all()

        # --- 2) Course Progress per User ---
        course_progress_data = []
        for u in users:
            total_courses = UserProgress.query.filter_by(user_id=u.id).count()
            avg_prog = db.session.query(
                func.avg(UserProgress.progress_percentage)
            ).filter_by(user_id=u.id).scalar() or 0
            course_progress_data.append({
                'user_id': u.id,
                'user_name': f"{u.first_name} {u.last_name}",
                'total_courses': total_courses,
                'avg_progress': round(avg_prog, 2)
            })

        # --- 3) Exam Performance per User ---
        exam_performance_data = []
        for u in users:
            total_attempts = UserScore.query.filter_by(user_id=u.id).count()
            avg_score = db.session.query(
                func.avg(UserScore.score)
            ).filter_by(user_id=u.id).scalar() or 0
            passed = UserScore.query.filter_by(user_id=u.id)\
                                   .filter(UserScore.score >= 56).count()
            exam_performance_data.append({
                'user_id': u.id,
                'user_name': f"{u.first_name} {u.last_name}",
                'total_attempts': total_attempts,
                'avg_score': round(avg_score, 2),
                'successful_attempts': passed
            })

        # --- 4) Special Exam Records for these Users ---
        user_ids = [u.id for u in users]
        special_exam_records = (
            SpecialExamRecord.query
            .filter(SpecialExamRecord.user_id.in_(user_ids))
            .all()
        )

        return render_template(
            'admin_reports.html',
            search=search,
            users=users,
            course_progress_data=course_progress_data,
            exam_performance_data=exam_performance_data,
            special_exam_records=special_exam_records
        )
    except Exception as e:
        logging.error(f"user_id={user_id} error generating reports: {e}")
        return render_template('500.html'), 500


# --- Set Restrictions ---
@admin_routes.route('/admin/set_restrictions', methods=['POST'])
@login_required
@super_admin_required
def set_restrictions():
    user_id = current_user.id
    ip      = request.remote_addr
    course_id = request.form.get('course_id')
    lvl       = request.form.get('restriction_level')
    logging.info(f"user_id={user_id} setting restriction on course_id={course_id} level={lvl} from {ip}")
    try:
        if not course_id or not lvl:
            return jsonify({'error':'Course ID and level required'}), 400
        lvl = int(lvl)
        if lvl<1 or lvl>12:
            return jsonify({'error':'Invalid level'}), 400

        course = StudyMaterial.query.get(course_id)
        if not course:
            return jsonify({'error':'Course not found'}), 404
        course.restriction_level = lvl
        db.session.commit()
        return jsonify({'success':f'Updated Course {course_id}'}), 200
    except Exception as e:
        logging.error(f"user_id={user_id} error setting restrictions: {e}")
        return jsonify({'error':'Failed to set restrictions'}), 500

@admin_routes.route('/admin/edit_course/<int:course_id>', methods=['POST'])
@login_required
@super_admin_required
def edit_course(course_id):
    user_id = current_user.id
    ip      = request.remote_addr
    logging.info(f"user_id={user_id} editing course_id={course_id} from {ip}")

    try:
        course = StudyMaterial.query.get_or_404(course_id)

        # 1) Update metadata
        course.title       = request.form['title']
        course.description = request.form['description']
        course.course_time = int(request.form['course_time'])
        course.max_time    = int(request.form['max_time'])

        # 2) Optional FKs
        def parse_int(name):
            val = request.form.get(name)
            return int(val) if val and val.isdigit() else None

        course.category_id = parse_int('category_id')
        course.level_id    = parse_int('level_id')

        # 3) Minimum Level comes from the designation dropdown (value = starting_level)
        try:
            course.minimum_level = int(request.form.get('minimum_level') or 1)
        except ValueError:
            course.minimum_level = 1

        # 4) Delete checked files
        for fid in request.form.getlist('delete_files'):
            grid_fs.delete(ObjectId(fid))
            course.files = [f for f in course.files if not f.startswith(fid + '|')]

        # 5) Replace files
        replace_ids   = request.form.getlist('replace_file_ids')
        replace_files = request.files.getlist('replace_files')
        for fid, new_file in zip(replace_ids, replace_files):
            if new_file and allowed_file(new_file.filename):
                grid_fs.delete(ObjectId(fid))
                course.files = [f for f in course.files if not f.startswith(fid + '|')]

                filename = secure_filename(new_file.filename)
                new_fid  = grid_fs.put(new_file, filename=filename,
                                       content_type=new_file.content_type)
                course.files.append(f"{new_fid}|{filename}")

        # 6) Add any brand-new uploads
        for extra in request.files.getlist('new_files'):
            if extra and allowed_file(extra.filename):
                fn  = secure_filename(extra.filename)
                fid = grid_fs.put(extra, filename=fn,
                                  content_type=extra.content_type)
                course.files.append(f"{fid}|{fn}")

        db.session.commit()
        flash("Course updated successfully.", "success")

    except Exception as e:
        logging.error(f"user_id={user_id} error editing course {course_id}: {e}", exc_info=True)
        db.session.rollback()
        flash("Failed to edit course. Please check your inputs.", "error")

    # Return to Manage Courses list
    return redirect(url_for('admin_routes.view_courses'))


# --- Delete a single question from an exam ---
@admin_routes.route('/delete_exam/<int:exam_id>', methods=['POST'])
@login_required
@super_admin_required
def delete_exam(exam_id):
    user_id = current_user.id
    ip      = request.remote_addr
    logging.info(f"user_id={user_id} deleting exam_id={exam_id} from {ip}")
    try:
        exam = Exam.query.get_or_404(exam_id)
        Question.query.filter_by(exam_id=exam_id).delete()
        UserScore.query.filter_by(exam_id=exam_id).delete()
        db.session.delete(exam)
        db.session.commit()
        flash("Exam deleted successfully!", "success")
    except Exception as e:
        logging.error(f"user_id={user_id} error deleting exam {exam_id}: {e}")
        flash("Failed to delete exam.", "error")
    return redirect(url_for('admin_routes.admin_dashboard'))


@admin_routes.route('/edit_exam/<int:exam_id>', methods=['GET', 'POST'])
@login_required
@super_admin_required
def edit_exam(exam_id):
    """
    GET  → Render a form to edit (title, duration, level, etc.) for that exam.
    POST → Read the form data and update the existing exam record.
    """
    exam = Exam.query.get_or_404(exam_id)

    if request.method == 'POST':
        # 1) Grab each field from request.form (names must match your form inputs)
        new_title    = request.form.get('title', '').strip()
        new_duration = request.form.get('duration', '').strip()
        new_level    = request.form.get('level', '').strip()  # this is a string ID

        # 2) Simple validation
        if not new_title:
            flash("Title cannot be blank.", "warning")
            return redirect(url_for('admin_routes.edit_exam', exam_id=exam_id))

        # 3) Assign back onto the `exam` object
        exam.title = new_title

        # Convert duration to int if valid, otherwise leave as-is
        if new_duration.isdigit():
            exam.duration = int(new_duration)

        # Convert level to int and assign to the foreign-key column
        if new_level.isdigit():
            exam.level_id = int(new_level)

        try:
            db.session.commit()
            flash("Exam updated successfully.", "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating exam {exam_id}: {e}")
            flash("Failed to update exam.", "error")

        return redirect(url_for('admin_routes.admin_dashboard'))

    # If GET → render a simple template with current exam data pre‐filled
    return render_template("admin_edit_exam.html", exam=exam)



@admin_routes.route('/delete_question/<int:question_id>', methods=['POST'])
@login_required
@super_admin_required
def delete_question(question_id):
    q = Question.query.get_or_404(question_id)
    exam_id = q.exam_id
    db.session.delete(q)
    db.session.commit()
    flash("Question deleted.", "success")
    # use the dot‐notation so Flask finds the edit_exam_page on this blueprint
    return redirect(url_for('.edit_exam_page', exam_id=exam_id))


# --- Show the edit‐exam page (list of questions + per-question form) ---
@admin_routes.route('/exam/<int:exam_id>/edit', methods=['GET'])
@login_required
@super_admin_required
def edit_exam_page(exam_id):
    exam      = Exam.query.get_or_404(exam_id)
    questions = Question.query.filter_by(exam_id=exam_id).all()

    # you need these for your select-lists on the form
    levels     = Level.query.order_by(Level.level_number).all()
    areas      = Area.query.order_by(Area.name).all()
    courses    = StudyMaterial.query.order_by(StudyMaterial.title).all()
    categories = Category.query.order_by(Category.name).all()

    return render_template(
        'edit_exam.html',    # adjust to match where your file actually lives
        exam=exam,
        questions=questions,
        levels=levels,
        areas=areas,
        courses=courses,
        categories=categories
    )


@admin_routes.route('/update_question/<int:question_id>', methods=['POST'])
@login_required
@super_admin_required
def update_question(question_id):
    q = Question.query.get_or_404(question_id)

    # store the text
    q.question_text = request.form['question_text'].strip()

    # rebuild the comma‑separated choices string
    options = [request.form.get(f'option_{i}', '').strip() for i in range(4)]
    q.choices = ','.join(options)

    # store the correct letter
    q.correct_ans = request.form['correct_ans'].strip()

    db.session.commit()
    flash("Question updated.", "success")
    return redirect(url_for('.edit_exam_page', exam_id=q.exam_id))


# --- View Special Exam Record ---
@admin_routes.route('/admin/view_special_exam_record/<int:record_id>')
@login_required
@admin_required
def view_special_exam_record(record_id):
    user_id = current_user.id
    ip      = request.remote_addr
    logging.info(f"user_id={user_id} viewing special record_id={record_id} from {ip}")
    try:
        record = SpecialExamRecord.query.get_or_404(record_id)
        return render_template('admin_view_special_exam.html', record=record)
    except Exception as e:
        logging.error(f"user_id={user_id} error viewing record {record_id}: {e}")
        flash("Failed to load record.", "error")
        return redirect(url_for('admin_routes.admin_dashboard'))

# --- Manage Users page ---
@admin_routes.route('/admin/users')
@login_required
@super_admin_required
def view_users():
    status = request.args.get('status')  # 'verified' or 'unverified'
    qry = User.query.order_by(User.join_date.desc())

    if status == 'verified':
        qry = qry.filter_by(is_verified=True)
    elif status == 'unverified':
        qry = qry.filter_by(is_verified=False)

    users           = qry.all()
    designations    = Designation.query.order_by(Designation.title).all()
    all_departments = Department.query.order_by(Department.name).all()

    return render_template(
        'admin_users.html',
        users=users,
        status=status,
        designations=designations,
        all_departments=all_departments
    )


@admin_routes.route('/admin/user/designation/<int:user_id>', methods=['POST'])
@login_required
@super_admin_required
def change_designation(user_id):
    user = User.query.get_or_404(user_id)
    new_desig = request.form.get('designation_id', type=int)
    desig = Designation.query.get(new_desig)
    if desig:
        user.designation_id = new_desig
        db.session.commit()
        flash(f"{user.first_name} {user.last_name} is now {desig.title}.", "success")
    else:
        flash("Invalid designation selected.", "error")
    return redirect(url_for('admin_routes.view_users', status=request.args.get('status', None)))


@admin_routes.route('/admin/user/departments/<int:user_id>', methods=['POST'])
@login_required
@super_admin_required
def change_user_departments(user_id):
    user = User.query.get_or_404(user_id)
    dept_ids = request.form.getlist('departments')  # list of strings
    try:
        dept_ids_int = [int(x) for x in dept_ids]
    except ValueError:
        dept_ids_int = []

    selected_departments = Department.query.filter(Department.id.in_(dept_ids_int)).all()
    user.departments = selected_departments
    db.session.commit()

    if selected_departments:
        names = ", ".join([d.name for d in selected_departments])
        flash(f"{user.first_name} {user.last_name} now belongs to: {names}", "success")
    else:
        flash(f"{user.first_name} {user.last_name} has no departments assigned.", "warning")

    return redirect(url_for('admin_routes.view_users', status=request.args.get('status', None)))


# --- Manage Courses page ---
@admin_routes.route('/admin/courses')
@login_required
@super_admin_required
def view_courses():
    # eager‐load only the remaining FK relationships
    courses = (
        StudyMaterial.query
        .options(
            db.joinedload(StudyMaterial.category),
            db.joinedload(StudyMaterial.level)
        )
        .order_by(StudyMaterial.title)
        .all()
    )

    # for filter dropdowns or edit forms
    categories   = Category.query.order_by(Category.name).all()
    levels       = Level.query.order_by(Level.level_number).all()
    # load all designations so the "Minimum Designation" dropdown can show names
    designations = Designation.query.order_by(Designation.starting_level).all()

    return render_template(
        'admin_courses.html',
        courses=courses,
        categories=categories,
        levels=levels,
        designations=designations
    )

# --- Manage Exams page ---
@admin_routes.route('/admin/exams')
@login_required
@super_admin_required
def view_exams():
    exams = (
        Exam.query
        .options(
            db.joinedload(Exam.level),
            db.joinedload(Exam.area),
            db.joinedload(Exam.course),
            db.joinedload(Exam.category),
            db.joinedload(Exam.created_by_user)
        )
        .order_by(Exam.title)
        .all()
    )

    # needed if you plan to offer filters or editing forms
    levels     = Level.query.order_by(Level.level_number).all()
    areas      = Area.query.order_by(Area.name).all()
    courses    = StudyMaterial.query.order_by(StudyMaterial.title).all()
    categories = Category.query.order_by(Category.name).all()
    users      = User.query.order_by(User.first_name, User.last_name).all()

    return render_template(
        'admin_exams.html',
        exams=exams,
        levels=levels,
        areas=areas,
        courses=courses,
        categories=categories,
        users=users
    )

# --- View Analytics ---
@admin_routes.route('/admin/analytics')
@login_required
@admin_required
def view_analytics():
    """
    Renders the Analytics Dashboard page.
    Supports custom start/end date, quick ranges (all/last 30/60/90),
    department filter, designation filter.
    """
    # ── 1) PERIOD + DATE RANGE LOGIC ───────────────────
    periods = ['all', 30, 60, 90]
    period_str     = request.args.get('period', '30').strip()
    start_date_str = request.args.get('start_date', '').strip()
    end_date_str   = request.args.get('end_date', '').strip()
    sel_dept       = request.args.get('department', '').strip()
    sel_desig      = request.args.get('designation', '').strip()
    today = datetime.utcnow().date()

    # Use datetime for inclusive range
    start_date, end_date = None, None
    period = None

    # If no custom dates, default to “Last 30 days”
    if not start_date_str and not end_date_str:
        period_val = 30
        start_date = today - timedelta(days=period_val)
        end_date   = today
        period     = period_val
    else:
        # If user chose “all” on quick range:
        if period_str == 'all':
            start_date, end_date = None, None
            period = 'all'
        else:
            # Parse period as an integer
            try:
                period_val = int(period_str)
                if period_val not in [30, 60, 90]:
                    period_val = 30
            except ValueError:
                period_val = 30

            # Default “Last N days”
            start_date = today - timedelta(days=period_val)
            end_date   = today
            period = period_val

            # Override with custom dates if provided
            if start_date_str and end_date_str:
                try:
                    sd = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                    ed = datetime.strptime(end_date_str,   "%Y-%m-%d").date()
                    start_date, end_date = sd, ed
                    period = (ed - sd).days
                except ValueError:
                    # Keep “Last N days” if parsing fails
                    pass

    # Always turn into datetime (full day for inclusivity)
    if start_date is not None and end_date is not None:
        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime   = datetime.combine(end_date,   datetime.max.time())
    else:
        start_datetime, end_datetime = None, None


    # ── 2) BUILD USER QUERY (apply dept & designation) ───
    user_query = User.query.filter(User.deleted_at.is_(None))

    if sel_dept:
        user_query = user_query.filter(User.departments.any(Department.name == sel_dept))
    if sel_desig:
        user_query = user_query.filter(User.designation.has(Designation.title == sel_desig))

    users_filtered = user_query.all()
    total_users    = len(users_filtered)
    active_users   = total_users  # Adjust if you have an “inactive” flag

    # If no users at all, return early with all “No data” responses
    if total_users == 0:
        return render_template(
            'admin_analytics.html',
            # ── FILTERS ──
            periods         = periods,
            period          = period,
            start_date      = (start_date.strftime('%Y-%m-%d') if start_date else ''),
            end_date        = (end_date.strftime('%Y-%m-%d')   if end_date   else ''),
            all_departments = Department.query.order_by(Department.name).all(),
            sel_dept        = sel_dept,
            designations    = Designation.query.order_by(Designation.title).all(),
            sel_desig       = sel_desig,

            # ── SUMMARY CARDS ── (all zeroes)
            total_users         = 0,
            active_users        = 0,
            avg_exam_score      = 0,
            avg_course_progress = 0,
            special_avg_score   = 0,

            # ── PASS/FAIL ──
            passed_count        = 0,
            failed_count        = 0,
            pass_pct            = 0,
            fail_pct            = 0,
            special_pass_count  = 0,
            special_fail_count  = 0,
            sp_pass_pct         = 0,
            sp_fail_pct         = 0,

            # ── TOP 5 USERS ──
            top_users = [],

            # ── DEPT & DESIGNATION DISTRIBUTIONS ──
            dept_labels  = [],
            dept_values  = [],
            desig_labels = [],
            desig_values = [],

            # ── CATEGORY PERFORMANCE ──
            cat_names      = [],
            cat_avg_scores = [],
            cat_counts     = [],

            # ── LEVEL PROGRESS FUNNEL ──
            funnel_levels    = [],
            funnel_total     = [],
            funnel_completed = [],

            # ── TIME‐SPENT HEATMAP ──
            heatmap_labels = [],
            heatmap_depts  = [],
            heatmap_data   = [],

            # ── TOP MISSED QUESTIONS ──
            missed_labels = [],
            missed_values = [],

            # ── TASKS ASSIGNED vs COMPLETED by Dept ──
            task_depts     = [],
            task_assigned  = [],
            task_completed = [],

            # ── EXAM & COURSE BAR DATA (use IDs) ──
            exam_ids            = [],
            exam_avg_scores     = [],
            course_ids          = [],
            course_avg_progress = [],

            # ── SCORE TREND ──
            ts_labels           = [],
            ts_avg_scores       = [],
            spec_ts_labels      = [],
            spec_ts_avg_scores  = [],

            # ── 3D SCATTER METRICS (empty) ──
            metrics      = {
                "avg_exam_score":        [],
                "total_time_spent":      [],
                "completion_rate":       [],
                "exams_taken":           [],
                "avg_attempts_per_exam": [],
                "score_improvement":     [],
                "last_activity_days_ago":[],
                "avg_special_score":     [],
                "user_labels":           [],
                "department":            [],
            },
            axis_options = [
                {"key": "avg_exam_score",          "label": "Avg Exam Score"},
                {"key": "total_time_spent",        "label": "Total Time Spent"},
                {"key": "completion_rate",         "label": "Completion Rate"},
                {"key": "exams_taken",             "label": "Exams Taken"},
                {"key": "avg_attempts_per_exam",   "label": "Avg Attempts per Exam"},
                {"key": "score_improvement",       "label": "Score Improvement"},
                {"key": "last_activity_days_ago",  "label": "Last Activity (days ago)"},
                {"key": "avg_special_score",       "label": "Avg Special Exam Score"},
            ],
            default_x = "avg_exam_score",
            default_y = "total_time_spent",
            default_z = "completion_rate"
        )

    # ── 3) HELPER: wrap any query in a date‐filter ─────────
    def date_filter(query, model_field):
        if start_date is not None and end_date is not None:
            return query.filter(
                func.date(model_field) >= start_date,
                func.date(model_field) <= end_date
            )
        return query

    # ── 4) SUMMARY CARDS ────────────────────────────────────
    avg_exam_score = date_filter(
        db.session.query(func.avg(UserScore.score))
                  .filter(UserScore.user_id.in_([u.id for u in users_filtered])),
        UserScore.created_at
    ).scalar() or 0

    avg_course_progress = date_filter(
        db.session.query(func.avg(UserProgress.progress_percentage))
                  .filter(UserProgress.user_id.in_([u.id for u in users_filtered])),
        UserProgress.completion_date
    ).scalar() or 0

    special_scores = []
    recs = SpecialExamRecord.query.filter(
        SpecialExamRecord.user_id.in_([u.id for u in users_filtered])
    ).all()

    for rec in recs:
        if rec.paper1_completed_at is not None and rec.paper1_score is not None:
            special_scores.append(rec.paper1_score)
        if rec.paper2_completed_at is not None and rec.paper2_score is not None:
            special_scores.append(rec.paper2_score)

    if special_scores:
        special_avg_score = round(sum(special_scores) / len(special_scores), 2)
    else:
        special_avg_score = 0

    # ── 5) PASS / FAIL ─────────────────────────────────────
    passed_count = date_filter(
        UserScore.query.filter(
            UserScore.user_id.in_([u.id for u in users_filtered]),
            UserScore.score >= 56
        ),
        UserScore.created_at
    ).count()

    failed_count = date_filter(
        UserScore.query.filter(
            UserScore.user_id.in_([u.id for u in users_filtered]),
            UserScore.score < 56
        ),
        UserScore.created_at
    ).count()

    pf_total = passed_count + failed_count
    if pf_total == 0:
        pass_pct = fail_pct = 0
    else:
        pass_pct = round(passed_count / pf_total * 100, 1)
        fail_pct = round(failed_count / pf_total * 100, 1)

    special_pass = sum(
        1 for rec in recs
        if (rec.paper1_completed_at and rec.paper1_passed) or (rec.paper2_completed_at and rec.paper2_passed)
    )
    special_fail = sum(
        1 for rec in recs
        if (
            (rec.paper1_completed_at or rec.paper2_completed_at) and
            not ((rec.paper1_completed_at and rec.paper1_passed) or (rec.paper2_completed_at and rec.paper2_passed))
        )
    )
    sp_total = special_pass + special_fail
    if sp_total == 0:
        sp_pass_pct = sp_fail_pct = 0
    else:
        sp_pass_pct = round(special_pass / sp_total * 100, 1)
        sp_fail_pct = round(special_fail / sp_total * 100, 1)

    # ── 6) TOP 5 USERS BY AVG SCORE ───────────────────────
    top_users_q = (
        db.session.query(User, func.avg(UserScore.score).label('avg_score'))
          .join(UserScore, UserScore.user_id == User.id)
          .filter(User.deleted_at.is_(None))
    )
    if start_date is not None and end_date is not None:
        top_users_q = top_users_q.filter(
            UserScore.created_at >= start_date,
            UserScore.created_at <= end_date
        )
    if sel_dept:
        top_users_q = top_users_q.filter(User.departments.any(Department.name == sel_dept))
    if sel_desig:
        top_users_q = top_users_q.filter(User.designation.has(Designation.title == sel_desig))

    top_users = (
        top_users_q
          .group_by(User.id)
          .order_by(func.avg(UserScore.score).desc())
          .limit(5)
          .all()
    )

    # ── 7) USERS BY DEPARTMENT (Pie Chart) ─────────────────
    dept_q = (
        db.session.query(Department.name, func.count(User.id))
          .join(user_departments, Department.id == user_departments.c.department_id)
          .join(User, User.id == user_departments.c.user_id)
          .filter(User.deleted_at.is_(None))
    )
    if sel_dept:
        dept_q = dept_q.filter(User.departments.any(Department.name == sel_dept))
    if sel_desig:
        dept_q = dept_q.filter(User.designation.has(Designation.title == sel_desig))

    dept_counts = (
        dept_q.group_by(Department.name)
             .order_by(Department.name)
             .all()
    )
    dept_labels = [r[0] for r in dept_counts]
    dept_values = [r[1] for r in dept_counts]

    # ── 8) USERS BY DESIGNATION (Bar Chart) ───────────────
    desig_q = (
        db.session.query(Designation.title, func.count(User.id))
          .join(User, User.designation_id == Designation.id)
          .filter(User.deleted_at.is_(None))
    )
    if sel_dept:
        desig_q = desig_q.filter(User.departments.any(Department.name == sel_dept))
    if sel_desig:
        desig_q = desig_q.filter(User.designation.has(Designation.title == sel_desig))

    desig_counts = (
        desig_q.group_by(Designation.title)
               .order_by(Designation.title)
               .all()
    )
    desig_labels = [r[0] for r in desig_counts]
    desig_values = [r[1] for r in desig_counts]

    # ── 9) CATEGORY PERFORMANCE (horizontal bar) ──────────
    cat_avg_data = (
        db.session.query(
            Category.name.label('cat_name'),
            func.avg(UserScore.score).label('avg_score'),
            func.count(UserScore.id).label('count_scores')
        )
        .join(UserScore, UserScore.category_id == Category.id)
        .filter(
            UserScore.user_id.in_([u.id for u in users_filtered]),
            UserScore.created_at >= start_date if start_date is not None else True,
            UserScore.created_at <= end_date   if end_date   is not None else True
        )
        .group_by(Category.name)
        .order_by(Category.name)
        .all()
    )
    cat_names      = [r.cat_name     for r in cat_avg_data]
    cat_avg_scores = [round(r.avg_score, 2) for r in cat_avg_data]
    cat_counts     = [r.count_scores for r in cat_avg_data]

    # ── 10) LEVEL PROGRESS FUNNEL (stacked bar) ───────────
    level_stats = []
    levels_data = Level.query.order_by(Level.level_number).all()
    for lvl in levels_data:
        total_assigned = (
            db.session.query(func.count(UserLevelProgress.user_id.distinct()))
              .filter(UserLevelProgress.level_id == lvl.id)
              .scalar()
        ) or 0

        completed_count = (
            db.session.query(func.count(UserLevelProgress.user_id.distinct()))
              .filter(
                  UserLevelProgress.level_id == lvl.id,
                  UserLevelProgress.status == 'completed'
              )
              .scalar()
        ) or 0

        level_stats.append({
            'level_name': f"Level {lvl.level_number}",
            'total_assigned': total_assigned,
            'completed': completed_count
        })

    funnel_levels    = [l['level_name']    for l in level_stats]
    funnel_total     = [l['total_assigned'] for l in level_stats]
    funnel_completed = [l['completed']      for l in level_stats]

    # ── 11) TIME SPENT HEATMAP (Plotly) ───────────────────
    all_departments = Department.query.order_by(Department.name).all()
    all_courses     = StudyMaterial.query.all()
    heatmap_labels  = [str(c.id) for c in all_courses]
    heatmap_depts   = [d.name  for d in all_departments]
    heatmap_data    = []
    for dept in all_departments:
        row = []
        for course in all_courses:
            avg_time = (
                db.session.query(func.avg(UserProgress.time_spent))
                  .join(User, User.id == UserProgress.user_id)
                  .join(user_departments, user_departments.c.user_id == User.id)
                  .filter(
                      user_departments.c.department_id == dept.id,
                      UserProgress.study_material_id == course.id
                  )
                  .scalar()
            ) or 0
            # convert seconds → half‐hours (1800 seconds = 0.5 hours)
            row.append(round(avg_time / 1800, 2))
        heatmap_data.append(row)

    # ── 12) TOP 5 MOST MISSED QUESTIONS ───────────────────
    top_missed = (
        db.session.query(
            IncorrectAnswer.question_id,
            func.count(IncorrectAnswer.id).label('miss_count')
        )
        .group_by(IncorrectAnswer.question_id)
        .order_by(func.count(IncorrectAnswer.id).desc())
        .limit(5)
        .all()
    )
    missed_labels = []
    missed_values = []
    for qid, cnt in top_missed:
        # Fetch one sample IncorrectAnswer record for this question_id
        sample_rec = (
            IncorrectAnswer.query
            .filter_by(question_id=qid)
            .order_by(IncorrectAnswer.id.asc())
            .first()
        )

        if sample_rec:
            # Determine whether this is a special‐paper or regular exam
            if sample_rec.special_paper:
                sp = sample_rec.special_paper.lower()
                if sp == 'paper1':
                    prefix = f"SP1-{sample_rec.exam_id or ''}"
                elif sp == 'paper2':
                    prefix = f"SP2-{sample_rec.exam_id or ''}"
                else:
                    prefix = f"SP-{sample_rec.exam_id or ''}"
            else:
                # regular exam uses exam_id column
                prefix = f"R-{sample_rec.exam_id or ''}"

            # Now fetch the Question to get its question_number (fallback to id)
            question = Question.query.get(qid)
            if question:
                question_num = getattr(question, 'question_number', None) or question.id
            else:
                question_num = qid

            label = f"{prefix}-{question_num}"
        else:
            label = f"{qid}-N/A"

        missed_labels.append(label)
        missed_values.append(cnt)

    # ── 13) TASKS ASSIGNED vs COMPLETED (Grouped Bar) ─────
    task_stats = []
    for dept in all_departments:
        total_tasks = (
            db.session.query(func.count(Task.id))
              .join(User, User.id == Task.assigned_by)
              .join(user_departments, user_departments.c.user_id == User.id)
              .filter(user_departments.c.department_id == dept.id)
              .scalar() or 0
        )
        completed_tasks = (
            db.session.query(func.count(Task.id))
              .join(User, User.id == Task.assigned_by)
              .join(user_departments, user_departments.c.user_id == User.id)
              .filter(
                  user_departments.c.department_id == dept.id,
                  Task.status.ilike('%Complete%')
              )
              .scalar() or 0
        )
        task_stats.append({
            'dept': dept.name,
            'assigned': total_tasks,
            'completed': completed_tasks
        })

    task_depts     = [ts['dept']      for ts in task_stats]
    task_assigned  = [ts['assigned']  for ts in task_stats]
    task_completed = [ts['completed'] for ts in task_stats]


    # ── 14) SCORE TREND (dual‐line) ────────────────────────
    ts = (
        date_filter(
            db.session.query(
                func.date(UserScore.created_at).label('date'),
                func.avg(UserScore.score).label('avg_score')
            ).filter(UserScore.user_id.in_([u.id for u in users_filtered])),
            UserScore.created_at
        )
        .group_by(func.date(UserScore.created_at))
        .order_by(func.date(UserScore.created_at))
        .all()
    )
    ts_labels     = [r.date.strftime('%Y-%m-%d') for r in ts]
    ts_avg_scores = [round(r.avg_score, 2) for r in ts]

    spec_ts = (
        db.session.query(
            func.date(SpecialExamRecord.created_at).label('spec_date'),
            func.avg(
                case(
                    (SpecialExamRecord.paper2_completed_at.is_(None), SpecialExamRecord.paper1_score),
                    (SpecialExamRecord.paper1_completed_at.is_(None), SpecialExamRecord.paper2_score),
                    else_=((SpecialExamRecord.paper1_score + SpecialExamRecord.paper2_score)/2)
                )
            ).label('spec_avg_score')
        )
        .filter(
            SpecialExamRecord.user_id.in_([u.id for u in users_filtered]),
            SpecialExamRecord.created_at >= start_date if start_date is not None else True,
            SpecialExamRecord.created_at <= end_date   if end_date   is not None else True
        )
        .group_by(func.date(SpecialExamRecord.created_at))
        .order_by(func.date(SpecialExamRecord.created_at))
        .all()
    )
    spec_ts_labels     = [r.spec_date.strftime('%Y-%m-%d') for r in spec_ts]
    spec_ts_avg_scores = [round(r.spec_avg_score, 2) for r in spec_ts]

    # ── 15) 3D SCATTER METRICS ──────────────────────────────
    metrics = {
        "avg_exam_score":        [],
        "total_time_spent":      [],
        "completion_rate":       [],
        "exams_taken":           [],
        "avg_attempts_per_exam": [],
        "score_improvement":     [],
        "last_activity_days_ago":[],
        "avg_special_score":     [],
        "user_labels":           [],
        "department":            [],
    }
    for user in users_filtered:
        scores     = sorted(user.scores, key=lambda s: s.created_at)
        progresses = user.study_progress

        avg_score = round(sum(s.score for s in scores) / len(scores), 2) if scores else 0
        metrics["avg_exam_score"].append(avg_score)

        total_spent = sum(getattr(sp, "time_spent", 0) for sp in progresses)
        metrics["total_time_spent"].append(round(total_spent, 2))

        assigned_count  = len(progresses)
        completed_count = sum(1 for sp in progresses if getattr(sp, "progress_percentage", 0) >= 100)
        comp_rate       = round((completed_count / assigned_count * 100), 2) if assigned_count else 0
        metrics["completion_rate"].append(comp_rate)

        metrics["exams_taken"].append(len(scores))

        exam_ids = set()
        attempts = 0
        for s in scores:
            exam_ids.add(s.exam_id)
            attempts += getattr(s, "attempt", 1)
        avg_att = round((attempts / len(exam_ids)), 2) if exam_ids else 0
        metrics["avg_attempts_per_exam"].append(avg_att)

        if len(scores) >= 2:
            improv = scores[-1].score - scores[0].score
        else:
            improv = 0
        metrics["score_improvement"].append(improv)

        dates = [s.created_at for s in scores] + [sp.completion_date for sp in progresses if sp.completion_date]
        if dates:
            last_dt = max(dates)
            days_ago = (today - last_dt.date()).days
        else:
            days_ago = None
        metrics["last_activity_days_ago"].append(days_ago if days_ago is not None else "")

        rec = getattr(user, "special_exam_record", None)
        if rec:
            took1 = rec.paper1_completed_at is not None
            took2 = rec.paper2_completed_at is not None
            scores_list = []
            if took1 and rec.paper1_score is not None:
                scores_list.append(rec.paper1_score)
            if took2 and rec.paper2_score is not None:
                scores_list.append(rec.paper2_score)
            if scores_list:
                avg_special = round(sum(scores_list) / len(scores_list), 2)
            else:
                avg_special = 0
        else:
            avg_special = 0
        metrics["avg_special_score"].append(avg_special)

        metrics["user_labels"].append(f"{user.first_name} {user.last_name}")
        metrics["department"].append(
            ", ".join([d.name for d in user.departments]) if user.departments else "N/A"
        )

    axis_options = [
        {"key": "avg_exam_score",          "label": "Avg Exam Score"},
        {"key": "total_time_spent",        "label": "Total Time Spent"},
        {"key": "completion_rate",         "label": "Completion Rate"},
        {"key": "exams_taken",             "label": "Exams Taken"},
        {"key": "avg_attempts_per_exam",   "label": "Avg Attempts per Exam"},
        {"key": "score_improvement",       "label": "Score Improvement"},
        {"key": "last_activity_days_ago",  "label": "Last Activity (days ago)"},
        {"key": "avg_special_score",       "label": "Avg Special Exam Score"},
    ]
    default_x = "avg_exam_score"
    default_y = "total_time_spent"
    default_z = "completion_rate"

    # ── 16) EXAM‐LEVEL + COURSE‐LEVEL DATA (use IDs) ──────
    exam_data = (
        date_filter(
            db.session.query(
                Exam.id.label("exam_id"),
                func.avg(UserScore.score).label("avg_score")
            )
            .join(UserScore, UserScore.exam_id == Exam.id)
            .filter(UserScore.user_id.in_([u.id for u in users_filtered])),
            UserScore.created_at
        )
        .group_by(Exam.id)
        .order_by(Exam.id)
        .all()
    )
    exam_ids        = [str(r.exam_id)           for r in exam_data]
    exam_avg_scores = [round(r.avg_score, 2) for r in exam_data]

    cp_data = (
        date_filter(
            db.session.query(
                StudyMaterial.id.label("material_id"),
                func.avg(UserProgress.progress_percentage).label("avg_prog")
            )
            .join(UserProgress, UserProgress.study_material_id == StudyMaterial.id)
            .filter(UserProgress.user_id.in_([u.id for u in users_filtered])),
            UserProgress.completion_date
        )
        .group_by(StudyMaterial.id)
        .order_by(StudyMaterial.id)
        .all()
    )
    course_ids         = [str(r.material_id)         for r in cp_data]
    course_avg_progress = [round(r.avg_prog, 2) for r in cp_data]

    # ── 17) RENDER TEMPLATE ───────────────────────────────
    return render_template(
        'admin_analytics.html',

        # ── FILTERS ──
        periods         = periods,
        period          = period,
        start_date      = (start_date.strftime('%Y-%m-%d') if start_date else ''),
        end_date        = (end_date.strftime('%Y-%m-%d')   if end_date   else ''),
        all_departments = Department.query.order_by(Department.name).all(),
        sel_dept        = sel_dept,
        designations    = Designation.query.order_by(Designation.title).all(),
        sel_desig       = sel_desig,

        # ── SUMMARY CARDS ──
        total_users         = total_users,
        active_users        = active_users,
        avg_exam_score      = round(avg_exam_score, 2),
        avg_course_progress = round(avg_course_progress, 2),
        special_avg_score   = round(special_avg_score, 2),

        # ── PASS / FAIL ──
        passed_count        = passed_count,
        failed_count        = failed_count,
        pass_pct            = pass_pct,
        fail_pct            = fail_pct,
        special_pass_count  = special_pass,
        special_fail_count  = special_fail,
        sp_pass_pct         = sp_pass_pct,
        sp_fail_pct         = sp_fail_pct,

        # ── TOP 5 USERS ──
        top_users           = top_users,

        # ── DEPT & DESIGNATION DISTRIBUTIONS ──
        dept_labels         = dept_labels,
        dept_values         = dept_values,
        desig_labels        = desig_labels,
        desig_values        = desig_values,

        # ── CATEGORY PERFORMANCE ──
        cat_names           = cat_names,
        cat_avg_scores      = cat_avg_scores,
        cat_counts          = cat_counts,

        # ── LEVEL PROGRESS FUNNEL ──
        funnel_levels       = funnel_levels,
        funnel_total        = funnel_total,
        funnel_completed    = funnel_completed,

        # ── TIME‐SPENT HEATMAP ──
        heatmap_labels      = heatmap_labels,
        heatmap_depts       = heatmap_depts,
        heatmap_data        = heatmap_data,

        # ── TOP MISSED QUESTIONS ──
        missed_labels       = missed_labels,
        missed_values       = missed_values,

        # ── TASKS ASSIGNED vs COMPLETED by Dept ──
        task_depts          = task_depts,
        task_assigned       = task_assigned,
        task_completed      = task_completed,

        # ── EXAM & COURSE BAR DATA ──
        exam_ids            = exam_ids,
        exam_avg_scores     = exam_avg_scores,
        course_ids          = course_ids,
        course_avg_progress = course_avg_progress,

        # ── SCORE TREND ──
        ts_labels           = ts_labels,
        ts_avg_scores       = ts_avg_scores,
        spec_ts_labels      = spec_ts_labels,
        spec_ts_avg_scores  = spec_ts_avg_scores,

        # ── 3D SCATTER METRICS ──
        metrics             = metrics,
        axis_options        = axis_options,
        default_x           = default_x,
        default_y           = default_y,
        default_z           = default_z
    )

@admin_routes.route('/admin/analytics/users')
@login_required
@admin_required
def analytics_user_list():
    # 1) Get search and filter parameters
    q = request.args.get('q', '').strip()
    dept_id = request.args.get('dept', '').strip()
    sort = request.args.get('sort', '').strip()

    # 2) Base query
    users_query = User.query

    # 3) Apply text search if provided
    if q:
        users_query = users_query.filter(
            (User.first_name.ilike(f'%{q}%')) |
            (User.last_name.ilike(f'%{q}%')) |
            (User.employee_email.ilike(f'%{q}%'))
        )

    # 4) Apply many-to-many department filter
    if dept_id:
        try:
            dept_int = int(dept_id)
            users_query = users_query.filter(
                User.departments.any(Department.id == dept_int)
            )
        except ValueError:
            # ignore invalid dept_id
            pass

    # 5) Execute query (ordered by last then first name)
    users = users_query.order_by(User.last_name, User.first_name).all()

    # 6) Gather per-user statistics
    user_stats = []
    for user in users:
        exams_taken = len(user.scores)
        avg_score = (
            round(sum(s.score for s in user.scores) / exams_taken, 2)
            if exams_taken else 0
        )

        courses_taken = len(user.study_progress)
        avg_progress = (
            round(sum(sp.progress_percentage for sp in user.study_progress) / courses_taken, 2)
            if courses_taken else 0
        )

        user_stats.append({
            'user': user,
            'exams_taken': exams_taken,
            'avg_score': avg_score,
            'courses_taken': courses_taken,
            'avg_progress': avg_progress
        })

    # 7) Sort the list of dicts in Python if requested
    if sort == 'score_desc':
        user_stats.sort(key=lambda x: x['avg_score'], reverse=True)
    elif sort == 'score_asc':
        user_stats.sort(key=lambda x: x['avg_score'])
    elif sort == 'progress_desc':
        user_stats.sort(key=lambda x: x['avg_progress'], reverse=True)
    elif sort == 'progress_asc':
        user_stats.sort(key=lambda x: x['avg_progress'])

    # 8) Fetch all departments for the dropdown
    departments = Department.query.order_by(Department.name).all()

    # 9) Render template
    return render_template(
        'admin_analytics_users.html',
        user_stats=user_stats,
        departments=departments,
        search_query=q,
        dept_id=dept_id,
        sort=sort
    )



@admin_routes.route('/admin/analytics/user/<int:user_id>')
@login_required
@admin_required
def analytics_user_detail(user_id):
    user = User.query.get_or_404(user_id)

    # 1) Fetch all “regular” exam attempts (title, score, date)
    exam_scores_query = (
        db.session.query(Exam.title, UserScore.score, UserScore.created_at)
        .join(UserScore, UserScore.exam_id == Exam.id)
        .filter(UserScore.user_id == user_id)
        .order_by(UserScore.created_at)
        .all()
    )
    exam_titles       = [e[0] for e in exam_scores_query]
    exam_scores_list  = [e[1] for e in exam_scores_query]
    exam_dates        = [e[2].strftime('%Y-%m-%d') if e[2] else '' for e in exam_scores_query]

    # 2) Fetch course progress (title, percent, date)
    course_progress_query = (
        db.session.query(
            StudyMaterial.title,
            UserProgress.progress_percentage,
            UserProgress.completion_date
        )
        .join(UserProgress, UserProgress.study_material_id == StudyMaterial.id)
        .filter(UserProgress.user_id == user_id)
        .order_by(UserProgress.completion_date)
        .all()
    )
    course_titles   = [c[0] for c in course_progress_query]
    course_percents = [c[1] for c in course_progress_query]

    # 3) Fetch special exam record for this user (if any)
    #    Assume there is a one-to-one relationship: user.special_exam_record
    special_rec = getattr(user, 'special_exam_record', None)
    if special_rec:
        # If either paper1 or paper2 exists, extract them; else treat as None
        special_paper1_score = special_rec.paper1_score if special_rec.paper1_completed_at else None
        special_paper2_score = special_rec.paper2_score if special_rec.paper2_completed_at else None
        special_exam_date    = special_rec.created_at.strftime('%Y-%m-%d') if special_rec.created_at else None
    else:
        special_paper1_score = None
        special_paper2_score = None
        special_exam_date    = None

    # 4) Build timeline items and sort by date
    #    Each item is (date_obj, title_string, detail_string)
    timeline = []

    # Add each regular exam attempt
    for title, score, dt in exam_scores_query:
        timeline.append((dt, f"Exam: {title}", f"{score}%"))

    # Add each course progress entry (if date exists)
    for title, percent, comp_date in course_progress_query:
        if comp_date:
            timeline.append((comp_date, f"Course: {title}", f"{percent}%"))

    # Add special exam event (if any date)
    if special_rec and special_rec.created_at:
        # If both papers exist, show “Special Exam Completed” with average
        sp_scores = []
        if special_paper1_score is not None:
            sp_scores.append(special_paper1_score)
        if special_paper2_score is not None:
            sp_scores.append(special_paper2_score)
        if sp_scores:
            avg_sp = round(sum(sp_scores) / len(sp_scores), 1)
            timeline.append(
                (special_rec.created_at, "Special Exam", f"{avg_sp}% (avg of paper scores)")
            )
        else:
            # If somehow record exists but no scores, still note attempt
            timeline.append(
                (special_rec.created_at, "Special Exam", "Attempted (no score)")
            )

    # Sort timeline by date (oldest first). If date is None, put at the beginning.
    timeline = sorted(timeline, key=lambda item: item[0] or datetime.min)

    return render_template(
        'admin_analytics_user_detail.html',
        user=user,
        # Regular exam data
        exam_titles=exam_titles,
        exam_scores=exam_scores_list,
        exam_dates=exam_dates,
        # Special exam data
        special_paper1_score=special_paper1_score,
        special_paper2_score=special_paper2_score,
        special_exam_date=special_exam_date,
        # Course progress data
        course_titles=course_titles,
        course_percents=course_percents,
        # Timeline
        timeline=timeline
    )

# Deactivate User
@admin_routes.route('/admin/user/deactivate/<int:user_id>', methods=['POST'])
@login_required
@super_admin_required
def deactivate_user(user_id):
    user = User.query.get_or_404(user_id)
    try:
        user.is_verified = False
        db.session.commit()
        flash(f"User {user.first_name} {user.last_name} has been deactivated.", "success")
    except Exception:
        db.session.rollback()
        flash("Failed to deactivate user.", "error")
    return redirect(url_for('admin_routes.view_users'))

# Activate User
@admin_routes.route('/admin/user/activate/<int:user_id>', methods=['POST'])
@login_required
@super_admin_required
def activate_user(user_id):
    user = User.query.get_or_404(user_id)
    try:
        user.is_verified = True
        db.session.commit()
        flash(f"User {user.first_name} {user.last_name} has been activated.", "success")
    except Exception:
        db.session.rollback()
        flash("Failed to activate user.", "error")
    return redirect(url_for('admin_routes.view_users'))

@admin_routes.route('/admin/user/delete/<int:user_id>', methods=['POST'])
@login_required
@super_admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    try:
        db.session.delete(user)
        db.session.commit()
        flash(f"User {user.first_name} {user.last_name} has been deleted.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Cascade-delete failed for user {user.id}: {e}")
        flash("Failed to delete user. Please try again.", "error")
    return redirect(url_for('admin_routes.view_analytics'))

# --- Admin Reports Dashboard ---
@admin_routes.route('/admin/reports', methods=['GET'])
@login_required
def reports_landing():
    # Example: Custom data for your landing table (replace with your own query/data)
    custom_table = [
        {"name": "Total Users", "value": User.query.filter(User.deleted_at.is_(None)).count()},
        {"name": "Total Departments", "value": Department.query.count()},
        {"name": "Total Courses", "value": StudyMaterial.query.count()},
        {"name": "Total Exams", "value": Exam.query.count()},
    ]
    search = request.args.get('search', '').strip()
    return render_template("admin_reports_landing.html", custom_table=custom_table, search=search)


@admin_routes.route('/admin/reports/download', methods=['GET', 'POST'])
@login_required
def download_report():
    """
    Download CSV for type in [
        'course_progress','exam_performance','special_exams','audit_logs','users',
        'departments','courses','exams','leaderboard','inactive_users','custom',
        'incorrect_answers','incorrect_top10'
    ]
    Optional ?search= filter. For custom: Accepts ?type=custom&report_model=users&fields=id&fields=first_name, etc.
    """
    rpt_type     = request.args.get('type')
    search       = request.args.get('search', '').strip()
    report_model = request.args.get('report_model')
    fields       = request.args.getlist('fields')

    # audit-specific filters
    start      = request.args.get('start')
    end        = request.args.get('end')
    event_type = request.args.get('event_type', '').strip()
    user_id    = request.args.get('user_id', '').strip()

    si = io.StringIO()
    cw = csv.writer(si)

    # 1. Extended Preset Reports

    if rpt_type == 'course_progress':
        cw.writerow(['User ID','Name','Total Courses','Avg Progress (%)'])
        users = User.query.order_by(User.join_date.desc()).all()
        for u in users:
            total = UserProgress.query.filter_by(user_id=u.id).count()
            avg   = db.session.query(func.avg(UserProgress.progress_percentage)).filter_by(user_id=u.id).scalar() or 0
            cw.writerow([u.id, f"{u.first_name} {u.last_name}", total, round(avg,2)])
        filename = 'course_progress.csv'

    elif rpt_type == 'exam_performance':
        cw.writerow(['User ID','Name','Total Attempts','Avg Score','Successful Attempts'])
        users = User.query.order_by(User.join_date.desc()).all()
        for u in users:
            total  = UserScore.query.filter_by(user_id=u.id).count()
            avg    = db.session.query(func.avg(UserScore.score)).filter_by(user_id=u.id).scalar() or 0
            passed = UserScore.query.filter_by(user_id=u.id).filter(UserScore.score >= 56).count()
            cw.writerow([u.id, f"{u.first_name} {u.last_name}", total, round(avg,2), passed])
        filename = 'exam_performance.csv'

    elif rpt_type == 'special_exams':
        cw.writerow([
            'User ID','Name',
            'Paper1 Score','Paper1 Passed',
            'Paper2 Score','Paper2 Passed'
        ])
        records = SpecialExamRecord.query
        if search:
            records = records.join(User).filter(or_(
                User.first_name.ilike(f'%{search}%'),
                User.last_name.ilike(f'%{search}%'),
                User.employee_email.ilike(f'%{search}%')
            ))
        for r in records.all():
            cw.writerow([
                r.user_id,
                f"{r.user.first_name} {r.user.last_name}",
                r.paper1_score,
                'Yes' if r.paper1_passed else 'No',
                r.paper2_score,
                'Yes' if r.paper2_passed else 'No'
            ])
        filename = 'special_exams.csv'

    if rpt_type == 'audit_logs':
        # 1) Write header
        cw.writerow([
            'Timestamp','Event Type','User ID','Email',
            'IP Address','Target','Details'
        ])

        # 2) Build unified AuditLog query
        ql = AuditLog.query
        if start:
            ql = ql.filter(AuditLog.created_at >= start)
        if end:
            ql = ql.filter(AuditLog.created_at <= end)
        if event_type:
            ql = ql.filter(AuditLog.event_type == event_type)
        if user_id:
            ql = ql.filter(AuditLog.actor_user_id == int(user_id))

        if search:
            ql = ql.filter(or_(
                AuditLog.event_type.ilike(f'%{search}%'),
                AuditLog.ip_address.ilike(f'%{search}%'),
                cast(AuditLog.description, String).ilike(f'%{search}%')
            ))

        # 3) Stream each log as a CSV row
        for log in ql.order_by(AuditLog.created_at.desc()).all():
            cw.writerow([
                log.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                log.event_type,
                log.actor_user_id or '',
                log.actor_user.employee_email if log.actor_user else '',
                log.ip_address or '',
                f"{log.target_table}#{log.target_id}" if log.target_table else '',
                '' if log.description is None else json.dumps(log.description)
            ])

        filename = 'audit_logs.csv'

    elif rpt_type == 'users':
        cw.writerow(['User ID','First Name','Last Name','Email','Department','Join Date'])
        q = User.query
        if search:
            q = q.filter(or_(
                User.first_name.ilike(f'%{search}%'),
                User.last_name.ilike(f'%{search}%'),
                User.employee_email.ilike(f'%{search}%')
            ))
        users = q.order_by(User.join_date.desc()).all()
        for u in users:
            cw.writerow([
                u.id,
                u.first_name,
                u.last_name,
                u.employee_email,
                ", ".join([dept.name for dept in u.departments]) if u.departments else '',
                u.join_date.strftime('%Y-%m-%d') if u.join_date else '',
            ])
        filename = 'users.csv'

    elif rpt_type == 'departments':
        cw.writerow(['Department','User Count','Avg Exam Score','Avg Progress','Completion Rate'])
        departments = Department.query.all()
        for d in departments:
            d_users = User.query.filter(User.departments.any(Department.id == d.id)).all()
            ids = [u.id for u in d_users]
            if not ids:
                continue
            avg_score = db.session.query(func.avg(UserScore.score)).filter(UserScore.user_id.in_(ids)).scalar() or 0
            avg_progress = db.session.query(func.avg(UserProgress.progress_percentage)).filter(UserProgress.user_id.in_(ids)).scalar() or 0
            total_assigned = UserProgress.query.filter(UserProgress.user_id.in_(ids)).count()
            total_completed = UserProgress.query.filter(UserProgress.user_id.in_(ids), UserProgress.progress_percentage >= 100).count()
            completion_rate = (total_completed / total_assigned * 100) if total_assigned else 0
            cw.writerow([
                d.name,
                len(ids),
                round(avg_score,2),
                round(avg_progress,2),
                round(completion_rate,2)
            ])
        filename = 'departments.csv'

    elif rpt_type == 'courses':
        cw.writerow(['Course','Assigned','Completed','Avg Progress','Avg Exam Score'])
        courses = StudyMaterial.query.all()
        for c in courses:
            progress = UserProgress.query.filter_by(study_material_id=c.id)
            assigned = progress.count()
            completed = progress.filter(UserProgress.progress_percentage >= 100).count()
            avg_progress = progress.with_entities(func.avg(UserProgress.progress_percentage)).scalar() or 0
            user_ids = [p.user_id for p in progress.all()]
            avg_score = db.session.query(func.avg(UserScore.score)).filter(UserScore.user_id.in_(user_ids)).scalar() or 0
            cw.writerow([
                c.title,
                assigned,
                completed,
                round(avg_progress,2),
                round(avg_score,2)
            ])
        filename = 'courses.csv'

    elif rpt_type == 'exams':
        cw.writerow(['Exam','Attempts','Avg Score','Pass Rate (%)','Top Performer'])
        exams = Exam.query.all()
        for ex in exams:
            scores = UserScore.query.filter_by(exam_id=ex.id)
            attempts = scores.count()
            avg_score = scores.with_entities(func.avg(UserScore.score)).scalar() or 0
            pass_count = scores.filter(UserScore.score >= 56).count()
            pass_rate = (pass_count / attempts * 100) if attempts else 0
            top_score = scores.order_by(UserScore.score.desc()).first()
            top_user = f"{top_score.user.first_name} {top_score.user.last_name}" if top_score and top_score.user else ""
            cw.writerow([
                ex.title,
                attempts,
                round(avg_score,2),
                round(pass_rate,2),
                top_user
            ])
        filename = 'exams.csv'

    elif rpt_type == 'leaderboard':
        cw.writerow(['User','Email','Department','Courses Completed','Avg Score','Time Spent (min)'])
        users = User.query.filter(User.deleted_at.is_(None)).all()
        for u in users:
            completed = UserProgress.query.filter_by(user_id=u.id).filter(UserProgress.progress_percentage >= 100).count()
            avg_score = db.session.query(func.avg(UserScore.score)).filter_by(user_id=u.id).scalar() or 0
            time_spent = db.session.query(func.sum(UserProgress.time_spent)).filter_by(user_id=u.id).scalar() or 0
            cw.writerow([
                f"{u.first_name} {u.last_name}",
                u.employee_email,
                ", ".join([dept.name for dept in u.departments]) if u.departments else '',
                completed,
                round(avg_score,2),
                int(time_spent) if time_spent else 0
            ])
        filename = 'leaderboard.csv'

    elif rpt_type == 'inactive_users':
        cw.writerow(['User','Email','Department'])
        users = User.query.filter(User.deleted_at.is_(None)).all()
        inactive_days = 30
        today = datetime.utcnow().date()
        for u in users:
            progress_dates = [p.completion_date for p in getattr(u, 'study_progress', []) if p.completion_date]
            score_dates = [s.created_at for s in getattr(u, 'scores', []) if s.created_at]
            activity_dates = []
            if hasattr(u, 'last_login') and u.last_login:
                activity_dates.append(u.last_login)
            activity_dates.extend(progress_dates)
            activity_dates.extend(score_dates)
            last_activity = max(activity_dates) if activity_dates else None
            if not last_activity or (today - last_activity.date()).days > inactive_days:
                cw.writerow([
                    f"{u.first_name} {u.last_name}",
                    u.employee_email,
                    ", ".join([dept.name for dept in u.departments]) if u.departments else '',
                ])
        filename = 'inactive_users.csv'

    # 2. Incorrect Answers → all details
    elif rpt_type == 'incorrect_answers':
        cw.writerow([
            'User ID',
            'User Name',
            'Exam ID',
            'Question ID',
            'User Answer',
            'Correct Answer',
            'Date'
        ])
        # Join with User to allow optional filtering by name/email
        records_q = IncorrectAnswer.query.join(User, IncorrectAnswer.user_id == User.id)
        if search:
            records_q = records_q.filter(or_(
                User.first_name.ilike(f'%{search}%'),
                User.last_name.ilike(f'%{search}%'),
                User.employee_email.ilike(f'%{search}%')
            ))
        # Order by answered_at descending (newest first)
        for rec in records_q.order_by(IncorrectAnswer.answered_at.desc()).all():
            user = User.query.get(rec.user_id)
            cw.writerow([
                rec.user_id,
                f"{user.first_name} {user.last_name}" if user else 'Unknown User',
                rec.exam_id,
                rec.question_id,
                rec.user_answer,
                rec.correct_answer,
                rec.answered_at.strftime('%Y-%m-%d') if rec.answered_at else ''
            ])
        filename = 'incorrect_answers.csv'

    # 3. Top 10 missed questions across all users
    elif rpt_type == 'incorrect_top10':
        cw.writerow(['Exam ID', 'Question ID', 'Times Missed'])
        top10 = (
            db.session.query(
                IncorrectAnswer.exam_id,
                IncorrectAnswer.question_id,
                func.count(IncorrectAnswer.id).label('miss_count')
            )
            .group_by(IncorrectAnswer.exam_id, IncorrectAnswer.question_id)
            .order_by(func.count(IncorrectAnswer.id).desc())
            .limit(10)
            .all()
        )
        for exam_id, question_id, miss_count in top10:
            cw.writerow([exam_id, question_id, miss_count])
        filename = 'top10_missed_questions.csv'

    # 4. Fully Custom Report
    elif rpt_type == 'custom' and report_model and fields:
        REPORT_MODELS = {
            "users": {
                "model": User,
                "fields": {
                    "id": lambda u: u.id,
                    "first_name": lambda u: u.first_name,
                    "last_name": lambda u: u.last_name,
                    "employee_email": lambda u: u.employee_email,
                    "department": lambda u: ", ".join([dept.name for dept in u.departments]) if u.departments else '',
                    "join_date": lambda u: u.join_date.strftime('%Y-%m-%d') if u.join_date else '',
                }
            },
            "course_progress": {
                "model": UserProgress,
                "fields": {
                    "user_id": lambda up: up.user_id,
                    "user": lambda up: f"{up.user.first_name} {up.user.last_name}" if up.user else '',
                    "course": lambda up: up.study_material.title if up.study_material else '',
                    "progress_percentage": lambda up: up.progress_percentage,
                    "completion_date": lambda up: up.completion_date.strftime('%Y-%m-%d') if up.completion_date else '',
                }
            },
            "exam_scores": {
                "model": UserScore,
                "fields": {
                    "user_id": lambda us: us.user_id,
                    "user": lambda us: f"{us.user.first_name} {us.user.last_name}" if us.user else '',
                    "exam": lambda us: us.exam.title if us.exam else '',
                    "score": lambda us: us.score,
                    "created_at": lambda us: us.created_at.strftime('%Y-%m-%d') if us.created_at else '',
                }
            },
            "departments": {
                "model": Department,
                "fields": {
                    "id": lambda d: d.id,
                    "name": lambda d: d.name,
                }
            },
            # Add more as needed
        }
        if report_model not in REPORT_MODELS:
            flash("Invalid report model.", "error")
            return redirect(url_for('admin_routes.custom_report'))

        allowed_fields = REPORT_MODELS[report_model]["fields"]
        selected_fields = [f for f in fields if f in allowed_fields]
        if not selected_fields:
            flash("No valid fields selected.", "error")
            return redirect(url_for('admin_routes.custom_report'))

        # Header
        cw.writerow([f.replace("_", " ").title() for f in selected_fields])

        # Query & write
        Model = REPORT_MODELS[report_model]["model"]
        q = Model.query
        if report_model == "users" and search:
            q = q.filter(or_(
                User.first_name.ilike(f'%{search}%'),
                User.last_name.ilike(f'%{search}%'),
                User.employee_email.ilike(f'%{search}%')
            ))
        objs = q.all()
        for obj in objs:
            cw.writerow([allowed_fields[f](obj) for f in selected_fields])
        filename = f"{report_model}_custom.csv"

    else:
        flash("Invalid report type.", "error")
        return redirect(url_for('admin_routes.reports_dashboard'))

    # send CSV
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    output.headers["Content-type"] = "text/csv"
    return output


@admin_routes.route('/admin/reports/custom')
@login_required
def custom_report():
    """
    UI to let admins build a custom report (choose model, columns, filters)
    """
    REPORT_MODELS = {
        "users": [
            ("id", "User ID"),
            ("first_name", "First Name"),
            ("last_name", "Last Name"),
            ("employee_email", "Email"),
            ("department", "Department"),
            ("join_date", "Join Date"),
        ],
        "course_progress": [
            ("user_id", "User ID"),
            ("user", "User"),
            ("course", "Course"),
            ("progress_percentage", "Progress (%)"),
            ("completion_date", "Completed On"),
        ],
        "exam_scores": [
            ("user_id", "User ID"),
            ("user", "User"),
            ("exam", "Exam"),
            ("score", "Score"),
            ("created_at", "Completed At"),
        ],
        "departments": [
            ("id", "Department ID"),
            ("name", "Department Name"),
        ],
        "courses": [
            ("id", "Course ID"),
            ("title", "Course Title"),
        ],
        "exams": [
            ("id", "Exam ID"),
            ("title", "Exam Title"),
        ],
    }
    return render_template("admin_custom_report.html", report_models=REPORT_MODELS)

# ──────────────── 1) VIEW AND ASSIGN ROLES ─────────────────
@admin_routes.route('/admin/roles')
@login_required
@super_admin_required
def view_roles():
    """
    Show all roles, and for each role list its users.
    """
    roles = Role.query.order_by(Role.name).all()
    users = User.query.order_by(User.first_name, User.last_name).all()
    return render_template('admin_roles.html', roles=roles, users=users)

@admin_routes.route('/admin/roles/assign', methods=['POST'])
@login_required
@super_admin_required
def assign_role():
    """
    Assign or remove a role from a user.
    Form fields: user_id, role_id, action = 'add'|'remove'
    """
    user_id = request.form.get('user_id', type=int)
    role_id = request.form.get('role_id', type=int)
    action  = request.form.get('action')
    user = User.query.get_or_404(user_id)
    role = Role.query.get_or_404(role_id)

    if action == 'add':
        if role not in user.roles:
            user.roles.append(role)
            db.session.commit()
            flash(f"Added role {role.name} to {user.first_name}.", "success")
    elif action == 'remove':
        if role in user.roles:
            user.roles.remove(role)
            db.session.commit()
            flash(f"Removed role {role.name} from {user.first_name}.", "warning")
    else:
        flash("Unknown action.", "error")

    return redirect(url_for('admin_routes.view_roles'))


# ──────────────── 2) VIEW AUDIT LOGS ─────────────────

@admin_routes.route('/admin/audit-logs')
@login_required
@super_admin_required
def view_audit_logs():
    # --- Read optional filter params ----
    start      = request.args.get('start')       # e.g. '2025-05-01'
    end        = request.args.get('end')         # e.g. '2025-05-07'
    q          = request.args.get('q', '').strip()
    event_type = request.args.get('type', '').strip()
    user_id    = request.args.get('user_id', '').strip()

    # --- Build AuditLog query ---
    ql = AuditLog.query
    if start:
        ql = ql.filter(AuditLog.created_at >= start)
    if end:
        ql = ql.filter(AuditLog.created_at <= end)
    if event_type:
        ql = ql.filter(AuditLog.event_type == event_type)
    if user_id:
        ql = ql.filter(AuditLog.actor_user_id == int(user_id))

    # free‐text search over event_type, ip_address, description JSONB
    if q:
        ql = ql.filter(or_(
            AuditLog.event_type.ilike(f'%{q}%'),
            AuditLog.ip_address.ilike(f'%{q}%'),
            cast(AuditLog.description, String).ilike(f'%{q}%')
        ))

    # --- Pagination & ordering ---
    page     = int(request.args.get('page', 1))
    per_page = 50
    logs = ql.order_by(AuditLog.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template(
        'admin_audit_logs.html',
        audit_logs=logs.items,
        pagination=logs,
        filters={
            'start': start,
            'end': end,
            'q': q,
            'type': event_type,
            'user_id': user_id
        }
    )

@admin_routes.route('/admin/users/bulk-action', methods=['POST'])
@login_required
@super_admin_required
def bulk_user_action():
    action   = request.form.get('action')
    user_ids = request.form.getlist('user_ids', type=int)

    if not user_ids:
        flash("No users selected.", "warning")
        return redirect(url_for('admin_routes.view_users'))

    users = User.query.filter(User.id.in_(user_ids)).all()
    if action == 'delete':
        for u in users:
            db.session.delete(u)
        flash(f"Deleted {len(users)} users.", "success")
    elif action == 'deactivate':
        for u in users:
            u.is_verified = False
        flash(f"Deactivated {len(users)} users.", "warning")
    else:
        flash("Unknown bulk action.", "error")
        return redirect(url_for('admin_routes.view_users'))

    db.session.commit()
    return redirect(url_for('admin_routes.view_users'))

@admin_routes.route('/admin/exam_requests', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_exam_requests():
    from_date_str = request.args.get('from_date', '')
    to_date_str   = request.args.get('to_date', '')

    # --- Handle Approve/Reject POST ---
    if request.method == 'POST':
        req_id = request.form.get('request_id')
        action = request.form.get('action')  # approve or reject
        req = ExamAccessRequest.query.get_or_404(req_id)
        if req.status != 'pending':
            flash("Request already processed.", "warning")
        else:
            req.status = 'approved' if action == 'approve' else 'rejected'
            req.reviewed_at = datetime.utcnow()
            db.session.commit()
            flash(f"Request {action}ed successfully.", "success")
        return redirect(url_for('admin_routes.manage_exam_requests'))

    # --- Build query with optional filters ---
    query = ExamAccessRequest.query.options(
        db.joinedload(ExamAccessRequest.user)
    ).order_by(ExamAccessRequest.requested_at.desc())

    try:
        if from_date_str:
            from_date = datetime.strptime(from_date_str, "%Y-%m-%d")
            query = query.filter(ExamAccessRequest.requested_at >= from_date)
        if to_date_str:
            to_date = datetime.strptime(to_date_str, "%Y-%m-%d")
            query = query.filter(ExamAccessRequest.requested_at <= to_date)
    except ValueError:
        flash("Invalid date format. Use YYYY-MM-DD.", "danger")

    all_requests = query.all()

    # --- Attach readable exam titles ---
    special_titles = {
        9991: "Special Exam Paper 1",
        9992: "Special Exam Paper 2"
    }

    for r in all_requests:
        if r.exam_id in special_titles:
            r.exam_title = special_titles[r.exam_id]
        else:
            exam = Exam.query.get(r.exam_id)
            r.exam_title = exam.title if exam else "Unknown Exam"

    return render_template(
        'admin_exam_requests.html',
        requests=all_requests,
        from_date=from_date_str,
        to_date=to_date_str
    )

# ------------------------------------------------------------
# Special Exam Questions Mapping
# ------------------------------------------------------------
SPECIAL_EXAM_QUESTIONS = {
    "paper1": {
        0: "What does BPO stand for?",
        1: "Which of the following is an advantage of BPO?",
        2: "What does RCM stand for in the healthcare industry?",
        3: "Which of the following is NOT a part of the Revenue Cycle Management (RCM) process?",
        4: "Which step in the RCM process involves confirming the patient's eligibility for insurance coverage?",
        5: "Who is referred to as a 'patient' in the healthcare system?",
        6: "Choose a responsibility of a subscriber in the RCM process.",
        7: "In RCM, who is referred to as a 'provider'?",
        8: "What is another name for Medicare Part C?",
        9: "Which government health insurance program is primarily designed for individuals with low income?",
        10: "Which of the following health services is not covered by Medicare Part A?",
        11: "What does the Date of Service (DOS) refer to?",
        12: "How does co-pay differ from co-insurance?",
        13: "Which of the following statements is true about medical billing and coding?",
        14: "Which coding system is commonly used for diagnosis codes?",
        15: "Which of the following is a key function of a modifier?",
        16: "Which of the following factors can cause changes in the fee schedule rate?",
        17: "What is a Policy Number?",
        18: "A claim that has been pending for 75 days from the Date of Service (DOS) would be placed in which bucket?",
        19: "If a claim is billed to UHC for $250, and $125 is marked as W/O (contractual obligation), how much of the claim is potentially payable by the insurance?",
        20: "What is the formula for calculating the allowable amount?",
        21: "Which of the following formulas correctly represents the contractual write-off?",
        22: "What does a fee schedule in medical billing specify?",
        23: "What types of expenses count toward the out-of-pocket maximum?",
        24: "When does the deductible amount reset?",
        25: "Which of the following is a key feature of CPT codes?",
        26: "If a patient has an 80/20 co-insurance plan, how much will the insurance company pay for a $100 medical bill after the deductible is met?",
        27: "What does NPI stand for in healthcare?",
        28: "What does CMS stand for?",
        29: "Which of the following is a government-sponsored health insurance program in the United States?",
        30: "What is a deductible in health insurance?",
        31: "What is Authorization?",
        32: "What is Provider Credentialing?",
        33: "What are patient responsibilities in RCM?",
        34: "Which of the following is NOT a method of performing VOB?",
        35: "What is Secondary or Tertiary Insurance?",
        36: "Can individuals under 65 qualify for Medicare?",
        37: "What does HIPAA require from healthcare providers?",
        38: "What does auto insurance cover?",
        39: "The doctor submitted a claim for DOS on 02/01/2025. The patient had an in-network deductible of $1250 and an out-of-pocket balance of $3000. There is a separate co-pay for office visits, which is $100. The patient had surgery at the outpatient hospital. The insurance allowable rate for this procedure is $1750. And the coinsurance percentage for all outpatient procedures is 20%. Please calculate the following: The deductible and final insurance payment amount"
    },
    "paper2": {
        0: "Which of the following is an example of BPO?",
        1: "Which of the following is an example of a commercial payor in the United States?",
        2: "Which of the following is NOT a part of the RCM process?",
        3: "Who is responsible for purchasing workers' compensation insurance?",
        4: "Which is the first step in RCM process?",
        5: "Which of the following would be considered a responsibility of a 'patient'?",
        6: "If a child is covered under their parent's health insurance plan, the subscriber is:",
        7: "Claims aged up to 30 days from the Date of Service (DOS) are placed in which bucket?",
        8: "Which status indicates that the insurance company has refused to make a payment for the claim?",
        9: "Which of the following factors can cause changes in fee schedule rates?",
        10: "Which of the following is an example of a CARC code?",
        11: "Which of the following statements is true regarding patient responsibility?",
        12: "How is the total payment amount calculated?",
        13: "How is the allowable amount determined?",
        14: "Which of the following statements about manual payment posting is true?",
        15: "Which of the following best describes the process of posting adjustments in medical billing?",
        16: "Where can claims get rejected during the billing process?",
        17: "Which of the following best describes the NPI?",
        18: "What does Medicare Part B cover?",
        19: "What does the Effective Date of an insurance policy indicate?",
        20: "What does the abbreviation 'DX' refer to in medical billing?",
        21: "If a patient has a $6,000 out-of-pocket maximum and has already paid $6,000 in medical expenses, how much will they owe for additional medical services that year?",
        22: "A participating provider is also known as:",
        23: "In below, who is not referred to as a 'provider' in RCM?",
        24: "Which of the following is NOT covered by Medicare Part B?",
        25: "What is a diagnosis code?",
        26: "Which of the following is a government health insurance program designed for elderly citizens and certain disabled individuals?",
        27: "If a spouse is covered under their partner’s insurance policy, the spouse is referred to as the:",
        28: "Which of the following statements is true about an out-of-pocket maximum?",
        29: "If a patient has a $2000 yearly deductible and each doctor’s visit costs $100, how many visits must the patient pay for out-of-pocket before insurance starts covering the cost?",
        30: "What is Practice Management Software in RCM?",
        31: "What does Medicare Part A cover?",
        32: "Why is it important to verify benefits before providing services?",
        33: "What is a deductible in health insurance?",
        34: "Which of the following is NOT a method of performing VOB?",
        35: "What is an Out-of-Pocket Maximum?",
        36: "Why is Provider Enrollment important in healthcare?",
        37: "What is a referral in healthcare?",
        38: "What does HIPAA require from healthcare providers?",
        39: "A claim was billed to BCBS with a billed amount of $1500. As per the patient's insurance plan, the allowable is $1200. The patient has a Copay of $100 and the patient has 20% coinsurance. Calculate the following: Insurance paid amount and Total patient responsibility amount"
    }
}

# ------------------------------------------------------------
# 1. Incorrect Summary: List users & count of wrong answers
# ------------------------------------------------------------
@admin_routes.route('/incorrect_summary')
@login_required
@admin_required
def incorrect_summary():
    summary = (
        db.session.query(
            User.id,
            User.first_name,
            User.last_name,
            User.employee_email,
            func.count(IncorrectAnswer.id).label('wrong_count')
        )
        .join(IncorrectAnswer, IncorrectAnswer.user_id == User.id)
        .group_by(User.id)
        .order_by(desc('wrong_count'))
        .all()
    )
    return render_template('incorrect_summary.html', data=summary)

# ------------------------------------------------------------
# 2. Incorrect Answers: Details for one user, patching special questions
# ------------------------------------------------------------
@admin_routes.route('/incorrect_answers')
@login_required
@admin_required
def view_incorrect_answers():
    user_id  = request.args.get('user_id', type=int)
    page     = request.args.get('page', 1, type=int)
    per_page = 40

    if not user_id:
        flash("Please select a user first.", "warning")
        return redirect(url_for('admin_routes.incorrect_summary'))
    user = User.query.get_or_404(user_id)

    # Subquery for latest answered_at for each question
    last_wrong_sq = (
        db.session.query(
            IncorrectAnswer.question_id,
            func.max(IncorrectAnswer.answered_at).label('last_wrong')
        )
        .filter(IncorrectAnswer.user_id == user_id)
        .group_by(IncorrectAnswer.question_id)
        .subquery()
    )

    # Main query for incorrect answers
    detailed_q = (
        db.session.query(
            last_wrong_sq.c.last_wrong.label('answered_at'),
            case(
                (IncorrectAnswer.special_paper.isnot(None),
                 db.func.concat('Special Exam ', IncorrectAnswer.special_paper)),
                else_=Exam.title
            ).label('exam_title'),
            IncorrectAnswer.special_paper,
            IncorrectAnswer.question_id.label('question_id_val'),
            Question.question_text,
            IncorrectAnswer.user_answer,
            IncorrectAnswer.correct_answer
        )
        .join(
            IncorrectAnswer,
            and_(
                IncorrectAnswer.question_id == last_wrong_sq.c.question_id,
                IncorrectAnswer.answered_at == last_wrong_sq.c.last_wrong
            )
        )
        .outerjoin(Question, IncorrectAnswer.question_id == Question.id)
        .outerjoin(Exam, IncorrectAnswer.exam_id == Exam.id)
        .order_by(desc(last_wrong_sq.c.last_wrong))
    )

    pagination = detailed_q.paginate(page=page, per_page=per_page, error_out=False)
    rows = pagination.items

    # Patch special exam questions if needed (dict for mutability)
    patched_records = []
    for row in rows:
        row_dict = dict(row._mapping) if hasattr(row, '_mapping') else dict(row)
        if row_dict['special_paper'] and row_dict['question_id_val'] is not None:
            paper = row_dict['special_paper'].lower()
            question_map = SPECIAL_EXAM_QUESTIONS.get(paper, {})
            if not row_dict['question_text']:
                fallback = question_map.get(row_dict['question_id_val'])
                if fallback:
                    row_dict['question_text'] = fallback
                else:
                    row_dict['question_text'] = "[Special Exam Q not found]"
                    logging.warning(
                        f"Missing SPECIAL_EXAM_QUESTIONS entry for paper '{paper}' question_id {row_dict['question_id_val']}"
                    )
        patched_records.append(row_dict)

    # Group patched_records by exam_title
    grouped_by_exam = defaultdict(list)
    for rec in patched_records:
        grouped_by_exam[rec['exam_title']].append(rec)

    return render_template(
        'incorrect_details.html',
        user       = user,
        grouped    = grouped_by_exam,
        pagination = pagination
    )

# ------------------------------------------------------------
# 3. Clear all incorrect answers for a user
# ------------------------------------------------------------
@admin_routes.route('/incorrect_answers/clear', methods=['POST'])
@login_required
@admin_required
def clear_incorrect_answers():
    user_id = request.form.get('user_id', type=int)
    if not user_id:
        flash("No user specified to clear.", "warning")
        return redirect(url_for('admin_routes.incorrect_summary'))

    user = User.query.get_or_404(user_id)

    deleted_count = (
        IncorrectAnswer.query
        .filter_by(user_id=user_id)
        .delete(synchronize_session=False)
    )
    db.session.commit()

    flash(f"Cleared {deleted_count} incorrect answers for {user.first_name} {user.last_name}.", "success")
    return redirect(url_for('admin_routes.view_incorrect_answers', user_id=user_id))

# --- Manage Level-Area Gating Rules ---
@admin_routes.route('/level_areas')
@login_required
@admin_required
def manage_level_areas():
    q = (
        db.session.query(LevelArea)
          .join(Level,    Level.id    == LevelArea.level_id)
          .join(Category, Category.id == LevelArea.category_id)
          .join(Area,     Area.id     == LevelArea.area_id)
          .outerjoin(Exam, Exam.id     == LevelArea.required_exam_id)
          .options(
              joinedload(LevelArea.level),
              joinedload(LevelArea.category),
              joinedload(LevelArea.area),
              joinedload(LevelArea.required_exam),  # ← use the real relationship name
          )
          .order_by(
              Level.level_number,
              Category.name,
              Area.name
          )
    )
    level_areas = q.all()

    levels     = Level.query.order_by(Level.level_number).all()
    categories = Category.query.order_by(Category.name).all()
    areas      = Area.query.order_by(Area.name).all()
    exams      = Exam.query.order_by(Exam.title).all()

    return render_template(
        'admin_level_areas.html',
        level_areas=level_areas,
        levels=levels,
        categories=categories,
        areas=areas,
        exams=exams
    )

# Create
@admin_routes.route('/level_areas/create', methods=['POST'])
@login_required
@admin_required
def create_level_area():
    la = LevelArea(
        level_id         = request.form.get('level_id', type=int),
        category_id      = request.form.get('category_id', type=int),
        area_id          = request.form.get('area_id', type=int),
        required_exam_id = request.form.get('required_exam_id', type=int)
    )
    db.session.add(la)
    db.session.commit()
    flash("Level-area rule added.", "success")
    return redirect(url_for('admin_routes.manage_level_areas'))

# Edit
@admin_routes.route('/level_areas/<int:id>/edit', methods=['POST'])
@login_required
@admin_required
def edit_level_area(id):
    la = LevelArea.query.get_or_404(id)
    la.level_id         = request.form.get('level_id', type=int)
    la.category_id      = request.form.get('category_id', type=int)
    la.area_id          = request.form.get('area_id', type=int)
    la.required_exam_id = request.form.get('required_exam_id', type=int)
    db.session.commit()
    flash("Level-area rule updated.", "success")
    return redirect(url_for('admin_routes.manage_level_areas'))

# Delete
@admin_routes.route('/level_areas/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_level_area(id):
    la = LevelArea.query.get_or_404(id)
    db.session.delete(la)
    db.session.commit()
    flash("Level-area rule removed.", "warning")
    return redirect(url_for('admin_routes.manage_level_areas'))


# — List all associations, plus data for the add-form dropdowns
@admin_routes.route('/admin/user_clients')
@login_required
@admin_required
def manage_user_clients():
    users   = User.query.options(joinedload(User.clients)) \
                       .order_by(User.first_name, User.last_name).all()
    clients = Client.query.order_by(Client.name).all()
    return render_template(
        'admin_user_clients.html',
        users=users,
        clients=clients
    )

# — Add a new link
@admin_routes.route('/admin/user_clients/add', methods=['POST'])
@login_required
@admin_required
def add_user_client():
    user_id   = request.form.get('user_id', type=int)
    client_id = request.form.get('client_id', type=int)
    user   = User.query.get_or_404(user_id)
    client = Client.query.get_or_404(client_id)

    if client not in user.clients:
        user.clients.append(client)
        db.session.commit()
        flash('Client added to user.', 'success')
    else:
        flash('That user already has this client.', 'warning')

    return redirect(url_for('admin_routes.manage_user_clients'))

# — Edit (swap) an existing link
@admin_routes.route('/admin/user_clients/edit', methods=['POST'])
@login_required
@admin_required
def edit_user_client():
    user_id        = request.form.get('user_id', type=int)
    old_client_id  = request.form.get('old_client_id', type=int)
    new_client_id  = request.form.get('new_client_id', type=int)

    user       = User.query.get_or_404(user_id)
    old_client = Client.query.get_or_404(old_client_id)
    new_client = Client.query.get_or_404(new_client_id)

    if old_client in user.clients:
        user.clients.remove(old_client)
    if new_client not in user.clients:
        user.clients.append(new_client)

    db.session.commit()
    flash('User’s client updated.', 'success')
    return redirect(url_for('admin_routes.manage_user_clients'))

# — Delete an existing link
@admin_routes.route('/admin/user_clients/delete', methods=['POST'])
@login_required
@admin_required
def delete_user_client():
    user_id   = request.form.get('user_id', type=int)
    client_id = request.form.get('client_id', type=int)

    user   = User.query.get_or_404(user_id)
    client = Client.query.get_or_404(client_id)

    if client in user.clients:
        user.clients.remove(client)
        db.session.commit()
        flash('Client removed from user.', 'warning')
    else:
        flash('Association not found.', 'danger')

    return redirect(url_for('admin_routes.manage_user_clients'))



@admin_routes.route('/admin/seeds', methods=['GET'])
@login_required
@super_admin_required
def manage_seeds():
    all_roles = Role.query.order_by(Role.name).all()
    all_designations = Designation.query.order_by(Designation.title).all()
    all_departments = Department.query.order_by(Department.name).all()
    all_clients = Client.query.order_by(Client.name).all()
    all_levels = Level.query.order_by(Level.level_number).all()
    all_areas = Area.query.order_by(Area.name).all()
    all_categories = Category.query.order_by(Category.name).all()
    return render_template('admin_seeds.html',
        roles=all_roles,
        designations=all_designations,
        departments=all_departments,
        clients=all_clients,
        levels=all_levels,
        areas=all_areas,
        categories=all_categories
    )

def sync_sequence(model, sequence_name=None):
    # Only for PostgreSQL and only if using autoincrement IDs
    table = model.__tablename__
    pk_col = model.__mapper__.primary_key[0].name
    if not sequence_name:
        sequence_name = f"{table}_{pk_col}_seq"
    max_id = db.session.execute(text(f"SELECT MAX({pk_col}) FROM {table}")).scalar() or 0
    db.session.execute(text(f"SELECT setval('{sequence_name}', {max_id})"))
    db.session.commit()

# ----- ROLE CRUD -----
@admin_routes.route('/admin/seeds/roles/add', methods=['POST'])
@login_required
@super_admin_required
def add_role():
    name = request.form.get('role_name', '').strip()
    if not name:
        flash("Role name cannot be empty.", "error")
    else:
        if Role.query.filter_by(name=name).first():
            flash(f"Role '{name}' already exists.", "warning")
        else:
            db.session.add(Role(name=name))
            try:
                db.session.commit()
                sync_sequence(Role)
                flash(f"Added Role '{name}'.", "success")
            except IntegrityError:
                db.session.rollback()
                sync_sequence(Role)
                flash("Failed to add role (possible ID conflict). Sequence has been resynced, please try again.", "error")
    return redirect(url_for('admin_routes.manage_seeds'))

@admin_routes.route('/admin/seeds/roles/edit/<int:id>', methods=['POST'])
@login_required
@super_admin_required
def edit_role(id):
    role = Role.query.get_or_404(id)
    new_name = request.form.get('role_name', '').strip()
    if not new_name:
        flash("Role name cannot be empty.", "error")
    else:
        conflict = Role.query.filter(Role.name == new_name, Role.id != id).first()
        if conflict:
            flash(f"Another role with name '{new_name}' already exists.", "warning")
        else:
            role.name = new_name
            try:
                db.session.commit()
                flash(f"Role updated to '{new_name}'.", "success")
            except IntegrityError:
                db.session.rollback()
                flash("Failed to update role due to a database error.", "error")
    return redirect(url_for('admin_routes.manage_seeds'))

@admin_routes.route('/admin/seeds/roles/delete/<int:id>', methods=['POST'])
@login_required
@super_admin_required
def delete_role(id):
    role = Role.query.get_or_404(id)
    try:
        db.session.delete(role)
        db.session.commit()
        sync_sequence(Role)
        flash(f"Deleted Role '{role.name}'.", "success")
    except IntegrityError:
        db.session.rollback()
        flash(
            f"Failed to delete role '{role.name}'. It may be referenced by other records.",
            "error"
        )
    return redirect(url_for('admin_routes.manage_seeds'))

# ----- DESIGNATION CRUD -----
@admin_routes.route('/admin/seeds/designations/add', methods=['POST'])
@login_required
@super_admin_required
def add_designation():
    title = request.form.get('desig_title', '').strip()
    starting_level = request.form.get('desig_starting_level', '').strip()
    if not title or not starting_level.isdigit():
        flash("Title and numeric starting level are required.", "error")
    else:
        lvl = int(starting_level)
        conflict = Designation.query.filter_by(title=title).first()
        if conflict:
            flash(f"Designation '{title}' already exists.", "warning")
        else:
            new_desig = Designation(title=title, starting_level=lvl)
            db.session.add(new_desig)
            try:
                db.session.commit()
                sync_sequence(Designation)
                flash(f"Added Designation '{title}'.", "success")
            except IntegrityError:
                db.session.rollback()
                sync_sequence(Designation)
                flash("Failed to add designation (possible ID conflict). Sequence has been resynced, please try again.", "error")
    return redirect(url_for('admin_routes.manage_seeds'))

@admin_routes.route('/admin/seeds/designations/edit/<int:id>', methods=['POST'])
@login_required
@super_admin_required
def edit_designation(id):
    desig = Designation.query.get_or_404(id)
    new_title = request.form.get('desig_title', '').strip()
    new_level = request.form.get('desig_starting_level', '').strip()
    if not new_title or not new_level.isdigit():
        flash("Designation title and numeric level are required.", "error")
    else:
        lvl = int(new_level)
        conflict = Designation.query.filter(
            Designation.title == new_title, Designation.id != id
        ).first()
        if conflict:
            flash(f"Another designation named '{new_title}' already exists.", "warning")
        else:
            desig.title = new_title
            desig.starting_level = lvl
            try:
                db.session.commit()
                flash(f"Updated Designation to '{new_title}'.", "success")
            except IntegrityError:
                db.session.rollback()
                flash("Failed to update designation due to a database error.", "error")
    return redirect(url_for('admin_routes.manage_seeds'))

@admin_routes.route('/admin/seeds/designations/delete/<int:id>', methods=['POST'])
@login_required
@super_admin_required
def delete_designation(id):
    desig = Designation.query.get_or_404(id)
    try:
        db.session.delete(desig)
        db.session.commit()
        sync_sequence(Designation)
        flash(f"Deleted Designation '{desig.title}'.", "success")
    except IntegrityError:
        db.session.rollback()
        flash(
            f"Failed to delete designation '{desig.title}'. It may be referenced by other records.",
            "error"
        )
    return redirect(url_for('admin_routes.manage_seeds'))

# ----- DEPARTMENT CRUD -----
@admin_routes.route('/admin/seeds/departments/add', methods=['POST'])
@login_required
@super_admin_required
def add_department():
    name = request.form.get('dept_name', '').strip()
    if not name:
        flash("Department name cannot be empty.", "error")
    else:
        if Department.query.filter_by(name=name).first():
            flash(f"Department '{name}' already exists.", "warning")
        else:
            db.session.add(Department(name=name))
            try:
                db.session.commit()
                sync_sequence(Department)
                flash(f"Added Department '{name}'.", "success")
            except IntegrityError:
                db.session.rollback()
                sync_sequence(Department)
                flash("Failed to add department (possible ID conflict). Sequence has been resynced, please try again.", "error")
    return redirect(url_for('admin_routes.manage_seeds'))

@admin_routes.route('/admin/seeds/departments/edit/<int:id>', methods=['POST'])
@login_required
@super_admin_required
def edit_department(id):
    dept = Department.query.get_or_404(id)
    new_name = request.form.get('dept_name', '').strip()
    if not new_name:
        flash("Department name cannot be empty.", "error")
    else:
        conflict = Department.query.filter(Department.name == new_name, Department.id != id).first()
        if conflict:
            flash(f"Another department named '{new_name}' already exists.", "warning")
        else:
            dept.name = new_name
            try:
                db.session.commit()
                flash(f"Department updated to '{new_name}'.", "success")
            except IntegrityError:
                db.session.rollback()
                flash("Failed to update department due to a database error.", "error")
    return redirect(url_for('admin_routes.manage_seeds'))

@admin_routes.route('/admin/seeds/departments/delete/<int:id>', methods=['POST'])
@login_required
@super_admin_required
def delete_department(id):
    dept = Department.query.get_or_404(id)
    try:
        db.session.delete(dept)
        db.session.commit()
        sync_sequence(Department)
        flash(f"Deleted Department '{dept.name}'.", "success")
    except IntegrityError:
        db.session.rollback()
        flash(
            f"Failed to delete department '{dept.name}'. It may be referenced by other records.",
            "error"
        )
    return redirect(url_for('admin_routes.manage_seeds'))

# ----- CLIENT CRUD -----
@admin_routes.route('/admin/seeds/clients/add', methods=['POST'])
@login_required
@super_admin_required
def add_client():
    name = request.form.get('client_name', '').strip()
    if not name:
        flash("Client name cannot be empty.", "error")
    else:
        if Client.query.filter_by(name=name).first():
            flash(f"Client '{name}' already exists.", "warning")
        else:
            db.session.add(Client(name=name))
            try:
                db.session.commit()
                sync_sequence(Client)
                flash(f"Added Client '{name}'.", "success")
            except IntegrityError:
                db.session.rollback()
                sync_sequence(Client)
                flash("Failed to add client (possible ID conflict). Sequence has been resynced, please try again.", "error")
    return redirect(url_for('admin_routes.manage_seeds'))

@admin_routes.route('/admin/seeds/clients/edit/<int:id>', methods=['POST'])
@login_required
@super_admin_required
def edit_client(id):
    client = Client.query.get_or_404(id)
    new_name = request.form.get('client_name', '').strip()
    if not new_name:
        flash("Client name cannot be empty.", "error")
    else:
        conflict = Client.query.filter(Client.name == new_name, Client.id != id).first()
        if conflict:
            flash(f"Another client named '{new_name}' already exists.", "warning")
        else:
            client.name = new_name
            try:
                db.session.commit()
                flash(f"Client updated to '{new_name}'.", "success")
            except IntegrityError:
                db.session.rollback()
                flash("Failed to update client due to a database error.", "error")
    return redirect(url_for('admin_routes.manage_seeds'))

@admin_routes.route('/admin/seeds/clients/delete/<int:id>', methods=['POST'])
@login_required
@super_admin_required
def delete_client(id):
    client = Client.query.get_or_404(id)
    try:
        db.session.delete(client)
        db.session.commit()
        sync_sequence(Client)
        flash(f"Deleted Client '{client.name}'.", "success")
    except IntegrityError:
        db.session.rollback()
        flash(
            f"Failed to delete client '{client.name}'. It may be referenced by other records.",
            "error"
        )
    return redirect(url_for('admin_routes.manage_seeds'))

# ----- LEVEL CRUD -----
@admin_routes.route('/admin/seeds/levels/add', methods=['POST'])
@login_required
@super_admin_required
def add_level():
    lvl_num = request.form.get('level_number', '').strip()
    title = request.form.get('level_title', '').strip()
    if not lvl_num.isdigit() or not title:
        flash("A numeric level and title are required.", "error")
    else:
        num = int(lvl_num)
        if Level.query.filter_by(level_number=num).first():
            flash(f"Level #{num} already exists.", "warning")
        else:
            db.session.add(Level(level_number=num, title=title))
            try:
                db.session.commit()
                sync_sequence(Level)
                flash(f"Added Level {num} – '{title}'.", "success")
            except IntegrityError:
                db.session.rollback()
                sync_sequence(Level)
                flash("Failed to add level (possible ID conflict). Sequence has been resynced, please try again.", "error")
    return redirect(url_for('admin_routes.manage_seeds'))

@admin_routes.route('/admin/seeds/levels/edit/<int:id>', methods=['POST'])
@login_required
@super_admin_required
def edit_level(id):
    lvl = Level.query.get_or_404(id)
    new_num = request.form.get('level_number', '').strip()
    new_title = request.form.get('level_title', '').strip()
    if not new_num.isdigit() or not new_title:
        flash("A numeric level and title are required.", "error")
    else:
        num = int(new_num)
        conflict = Level.query.filter(Level.level_number == num, Level.id != id).first()
        if conflict:
            flash(f"Another level with # {num} already exists.", "warning")
        else:
            lvl.level_number = num
            lvl.title = new_title
            try:
                db.session.commit()
                flash(f"Updated Level to #{num} – '{new_title}'.", "success")
            except IntegrityError:
                db.session.rollback()
                flash("Failed to update level due to a database error.", "error")
    return redirect(url_for('admin_routes.manage_seeds'))

@admin_routes.route('/admin/seeds/levels/delete/<int:id>', methods=['POST'])
@login_required
@super_admin_required
def delete_level(id):
    lvl = Level.query.get_or_404(id)
    try:
        db.session.delete(lvl)
        db.session.commit()
        sync_sequence(Level)
        flash(f"Deleted Level #{lvl.level_number}.", "success")
    except IntegrityError:
        db.session.rollback()
        flash(
            f"Failed to delete level #{lvl.level_number}. It may be referenced by other records.",
            "error"
        )
    return redirect(url_for('admin_routes.manage_seeds'))

# ----- AREA CRUD -----
@admin_routes.route('/admin/seeds/areas/add', methods=['POST'])
@login_required
@super_admin_required
def add_area():
    name = request.form.get('area_name', '').strip()
    if not name:
        flash("Area name cannot be empty.", "error")
    else:
        if Area.query.filter_by(name=name).first():
            flash(f"Area '{name}' already exists.", "warning")
        else:
            db.session.add(Area(name=name))
            try:
                db.session.commit()
                sync_sequence(Area)
                flash(f"Added Area '{name}'.", "success")
            except IntegrityError:
                db.session.rollback()
                sync_sequence(Area)
                flash("Failed to add area (possible ID conflict). Sequence has been resynced, please try again.", "error")
    return redirect(url_for('admin_routes.manage_seeds'))

@admin_routes.route('/admin/seeds/areas/edit/<int:id>', methods=['POST'])
@login_required
@super_admin_required
def edit_area(id):
    area = Area.query.get_or_404(id)
    new_name = request.form.get('area_name', '').strip()
    if not new_name:
        flash("Area name cannot be empty.", "error")
    else:
        conflict = Area.query.filter(Area.name == new_name, Area.id != id).first()
        if conflict:
            flash(f"Another area named '{new_name}' already exists.", "warning")
        else:
            area.name = new_name
            try:
                db.session.commit()
                flash(f"Area updated to '{new_name}'.", "success")
            except IntegrityError:
                db.session.rollback()
                flash("Failed to update area due to a database error.", "error")
    return redirect(url_for('admin_routes.manage_seeds'))

@admin_routes.route('/admin/seeds/areas/delete/<int:id>', methods=['POST'])
@login_required
@super_admin_required
def delete_area(id):
    area = Area.query.get_or_404(id)
    try:
        db.session.delete(area)
        db.session.commit()
        sync_sequence(Area)
        flash(f"Deleted Area '{area.name}'.", "success")
    except IntegrityError:
        db.session.rollback()
        flash(
            f"Failed to delete area '{area.name}'. It may be referenced by other records.",
            "error"
        )
    return redirect(url_for('admin_routes.manage_seeds'))

# ----- CATEGORY CRUD -----
@admin_routes.route('/admin/seeds/categories/add', methods=['POST'])
@login_required
@super_admin_required
def add_category():
    name = request.form.get('category_name', '').strip()
    if not name:
        flash("Category name cannot be empty.", "error")
        return redirect(url_for('admin_routes.manage_seeds'))

    # 1) Check for duplicate name upfront
    if Category.query.filter_by(name=name).first():
        flash(f"Category '{name}' already exists.", "warning")
        return redirect(url_for('admin_routes.manage_seeds'))

    # 2) Attempt to insert a new category
    new_cat = Category(name=name)
    db.session.add(new_cat)
    try:
        db.session.commit()
        # 3) After a successful INSERT, re-sync the sequence so it remains correct
        sync_sequence(Category)

        flash(f"Added Category '{name}'.", "success")
    except IntegrityError:
        # 4) If insert fails (most likely due to sequence conflict), rollback
        db.session.rollback()

        # 5) Attempt to fix the sequence and then ask the user to retry
        sync_sequence(Category)
        flash(
            "Failed to add category (possible ID conflict). "
            "Sequence has been resynced—please try again.",
            "error"
        )

    return redirect(url_for('admin_routes.manage_seeds'))


@admin_routes.route('/admin/seeds/categories/edit/<int:id>', methods=['POST'])
@login_required
@super_admin_required
def edit_category(id):
    cat = Category.query.get_or_404(id)
    new_name = request.form.get('category_name', '').strip()

    if not new_name:
        flash("Category name cannot be empty.", "error")
        return redirect(url_for('admin_routes.manage_seeds'))

    # 1) No change → early exit
    if cat.name == new_name:
        flash("No changes detected for category.", "info")
        return redirect(url_for('admin_routes.manage_seeds'))

    # 2) Check that no other category already has `new_name`
    conflict = (
        Category.query
        .filter(Category.name == new_name, Category.id != id)
        .first()
    )
    if conflict:
        flash(f"Another category named '{new_name}' already exists.", "warning")
        return redirect(url_for('admin_routes.manage_seeds'))

    # 3) Attempt to update the name
    cat.name = new_name
    try:
        db.session.commit()
        flash(f"Category updated to '{new_name}'.", "success")
    except IntegrityError:
        db.session.rollback()
        flash(
            "Failed to update category (possible constraint or sequence error). "
            "Please verify your database state and try again.",
            "error"
        )

    return redirect(url_for('admin_routes.manage_seeds'))


@admin_routes.route('/admin/seeds/categories/delete/<int:id>', methods=['POST'])
@login_required
@super_admin_required
def delete_category(id):
    cat = Category.query.get_or_404(id)
    category_name = cat.name

    try:
        db.session.delete(cat)
        db.session.commit()
        # Once the row is removed, re-sync the sequence so future INSERTs continue smoothly
        sync_sequence(Category)

        flash(f"Deleted Category '{category_name}'.", "success")
    except IntegrityError:
        db.session.rollback()
        flash(
            f"Failed to delete category '{category_name}'. It may be referenced by other records.",
            "error"
        )

    return redirect(url_for('admin_routes.manage_seeds'))


# ────────────────────────────────────────────────────────────────────────────
# 1) LIST ALL SUPPORT TICKETS (super‐admins only)
# ────────────────────────────────────────────────────────────────────────────
@admin_routes.route('/admin/support_tickets')
@login_required
@super_admin_required
def admin_list_tickets():
    """
    Show all support tickets. Only super‐admins may view/assign.
    """
    tickets = (
        SupportTicket.query
        .options(
            db.joinedload(SupportTicket.user),      # load the submitter
            db.joinedload(SupportTicket.assignee)   # load the current assignee
        )
        .order_by(SupportTicket.created_at.desc())
        .all()
    )
    return render_template('admin_support_index.html', tickets=tickets)


# ────────────────────────────────────────────────────────────────────────────
# 2) VIEW & RESPOND TO A SINGLE TICKET (super‐admins only)
# ────────────────────────────────────────────────────────────────────────────
@admin_routes.route('/admin/support_tickets/<int:ticket_id>', methods=['GET', 'POST'])
@login_required
@super_admin_required
def admin_view_ticket(ticket_id):
    """
    GET  → Display ticket details + super‐admin response form.
    POST → Save status, assigned_to, admin_response, and set resolved_at if needed.
    """
    ticket = SupportTicket.query.get_or_404(ticket_id)

    # 1) Gather all IT‐department users
    it_dept = Department.query.filter_by(name="IT Department").first()
    it_users = it_dept.users if it_dept else []

    # 2) Gather all super‐admins
    super_admins = User.query.filter_by(is_super_admin=True).all()

    # 3) Combine into one “assignable” list
    #    (If a super‐admin is also in IT Dept, they’ll appear twice here; 
    #     you can dedupe if needed, but typically they’re distinct.)
    assignable_users = it_users + super_admins

    if request.method == 'POST':
        # 4a) Read the form data
        new_status   = request.form.get('status', ticket.status)
        assigned_id  = request.form.get('assigned_to', type=int)
        admin_resp   = request.form.get('admin_response', '').strip()

        # 4b) Validate the selected assignee
        if assigned_id:
            chosen = User.query.get(assigned_id)
            if not chosen:
                flash("Selected user does not exist.", "danger")
                return redirect(
                    url_for('admin_routes.admin_view_ticket', ticket_id=ticket.id)
                )
            if chosen not in assignable_users:
                flash("You may only assign to an IT Dept user or a super‐admin.", "danger")
                return redirect(
                    url_for('admin_routes.admin_view_ticket', ticket_id=ticket.id)
                )
            ticket.assigned_to = assigned_id
        else:
            ticket.assigned_to = None

        # 4c) Update status & possibly resolved_at
        ticket.status = new_status
        if new_status == "Resolved" and not ticket.resolved_at:
            ticket.resolved_at = datetime.utcnow()
        elif new_status != "Resolved":
            ticket.resolved_at = None

        # 4d) Save the super‐admin’s response text
        ticket.admin_response = admin_resp or None

        # 4e) Commit to the database
        try:
            db.session.commit()
            flash("Ticket updated successfully.", "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating ticket #{ticket.id}: {e}")
            flash("Failed to update ticket. Please try again.", "danger")

        return redirect(
            url_for('admin_routes.admin_view_ticket', ticket_id=ticket.id)
        )

    # On GET: render the detail page, passing the combined assignable_users
    return render_template(
        'admin_support_detail.html',
        ticket=ticket,
        assignable_users=assignable_users
    )