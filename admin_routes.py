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
    Task, TaskDocument, FailedLogin, Event, Role, ExamAccessRequest, IncorrectAnswer, Department
)
import logging
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from gridfs import GridFS
from pymongo import MongoClient
from functools import wraps
from sqlalchemy import func, or_, and_
import io, csv
from bson import ObjectId
from flask import make_response
import os
from dotenv import load_dotenv
from pymongo import MongoClient
from gridfs import GridFS
from sqlalchemy import desc
from IPython.display import HTML
from sqlalchemy.orm  import joinedload


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

        # 4) Course Progress
        course_completion_avg  = db.session.query(func.avg(UserProgress.progress_percentage)).scalar() or 0
        restricted_courses     = StudyMaterial.query.filter(StudyMaterial.restriction_level.isnot(None)).count()

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
@admin_routes.route('/delete_course/<int:course_id>', methods=['POST'])
@login_required
@super_admin_required
def delete_course(course_id):
    user_id = current_user.id
    ip      = request.remote_addr
    logging.info(f"user_id={user_id} deleting course_id={course_id} from {ip}")
    try:
        course = StudyMaterial.query.get_or_404(course_id)
        SubTopic.query.filter_by(study_material_id=course_id).delete()
        UserProgress.query.filter_by(study_material_id=course_id).delete()

        # delete attached files
        deleted_ids = delete_files_from_gridfs(course.files)
        # remove from model
        course.files = [f for f in course.files if f.split("|")[0] not in deleted_ids]

        db.session.delete(course)
        db.session.commit()
        flash("Course deleted successfully!", "success")
    except Exception as e:
        logging.error(f"user_id={user_id} error deleting course {course_id}: {e}")
        flash("Failed to delete course. Please try again.", "error")
    return redirect(url_for('admin_routes.admin_dashboard'))

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

# --- Edit Course ---
@admin_routes.route('/admin/edit_course/<int:course_id>', methods=['POST'])
@login_required
@super_admin_required
def edit_course(course_id):
    user_id = current_user.id
    ip      = request.remote_addr
    logging.info(f"user_id={user_id} editing course_id={course_id} from {ip}")

    try:
        course = StudyMaterial.query.get_or_404(course_id)

        # 1) Update metadata fields
        course.title       = request.form['title']
        course.description = request.form['description']
        course.course_time = int(request.form['course_time'])
        course.max_time    = int(request.form['max_time'])
        course.category_id = request.form.get('category_id') or None
        course.level_id    = request.form.get('level_id')    or None
        course.minimum_designation_id = (
            request.form.get('minimum_designation_id') or None
        )

        # 2) Delete any attachments the user checked
        delete_ids = request.form.getlist('delete_files')
        for fid in delete_ids:
            grid_fs.delete(ObjectId(fid))                           # remove from GridFS
            course.files = [f for f in course.files if not f.startswith(fid + '|')]

        # 3) Replace attachments: for each old‐file you want to swap out
        replace_ids   = request.form.getlist('replace_file_ids')
        replace_files = request.files.getlist('replace_files')
        for fid, new_file in zip(replace_ids, replace_files):
            if new_file and allowed_file(new_file.filename):
                # delete old in GridFS & metadata
                grid_fs.delete(ObjectId(fid))
                course.files = [f for f in course.files if not f.startswith(fid + '|')]

                # save the new one
                filename = secure_filename(new_file.filename)
                new_fid  = grid_fs.put(new_file, filename=filename,
                                       content_type=new_file.content_type)
                course.files.append(f"{new_fid}|{filename}")

        # 4) Add any additional new uploads
        for extra in request.files.getlist('new_files'):
            if extra and allowed_file(extra.filename):
                fn  = secure_filename(extra.filename)
                fid = grid_fs.put(extra, filename=fn, content_type=extra.content_type)
                course.files.append(f"{fid}|{fn}")

        db.session.commit()
        flash("Course updated successfully.", "success")

    except Exception as e:
        logging.error(f"user_id={user_id} error editing course {course_id}: {e}")
        flash("Failed to edit course. Please check your inputs.", "error")

    return redirect(url_for('admin_routes.admin_dashboard'))

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
        flash("Failed to delete exam.", "error")  # <-- fixed here
    return redirect(url_for('admin_routes.admin_dashboard'))

@admin_routes.route('/edit_exam/<int:exam_id>', methods=['POST'])
@login_required
@super_admin_required
def edit_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    # update your exam.title, exam.duration, exam.level, etc.
    db.session.commit()
    flash("Exam updated successfully.", "success")
    return redirect(url_for('admin_routes.admin_dashboard'))


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

# --- List / Filter Users ---
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

    users        = qry.all()
    designations = Designation.query.order_by(Designation.title).all()

    return render_template(
        'admin_users.html',
        users=users,
        status=status,
        designations=designations
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
    return redirect(url_for('admin_routes.view_users'))


# --- Manage Courses page ---
@admin_routes.route('/admin/courses')
@login_required
@super_admin_required
def view_courses():
    # eager‐load the FK relationships for display
    courses = (
        StudyMaterial.query
        .options(
            db.joinedload(StudyMaterial.category),
            db.joinedload(StudyMaterial.level),
            db.joinedload(StudyMaterial.minimum_designation)
        )
        .order_by(StudyMaterial.title)
        .all()
    )

    # for filter dropdowns or edit forms
    categories   = Category.query.order_by(Category.name).all()
    levels       = Level.query.order_by(Level.level_number).all()
    designations = Designation.query.order_by(Designation.title).all()

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
# --- Admin Analytics ---
@admin_routes.route('/admin/analytics')
@login_required
@admin_required
def view_analytics():
    # Add 'all' as an option for all time
    periods = ['all', 30, 60, 90]
    period_str = request.args.get('period', '')
    start_date_str = request.args.get('start_date', '').strip()
    end_date_str = request.args.get('end_date', '').strip()
    q = request.args.get('q', '').strip()

    today = datetime.utcnow().date()

    # All time analytics
    if period_str == 'all':
        start_date = None
        end_date = None
        period = 'all'
    elif period_str:
        try:
            period = int(period_str)
        except ValueError:
            period = 30
        if period not in [30, 60, 90]:
            period = 30
        start_date = today - timedelta(days=period)
        end_date = today
    elif start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            period = (end_date - start_date).days
        except ValueError:
            period = 30
            start_date = today - timedelta(days=period)
            end_date = today
    else:
        period = 30
        start_date = today - timedelta(days=period)
        end_date = today

    # --- User queries ---
    user_query = User.query.filter(User.deleted_at.is_(None))
    # Total users (not deleted)
    total_users = user_query.count()
    active_users = total_users

    # --- Analytics Queries ---
    def date_filter(query, model_field):
        """Apply date filter if start_date/end_date are set (not all time)"""
        if start_date and end_date:
            return query.filter(model_field >= start_date, model_field <= end_date)
        return query

    # Average exam score, course progress, exam stats filtered by date if not all time
    avg_exam_score = date_filter(db.session.query(func.avg(UserScore.score)), UserScore.created_at).scalar() or 0
    avg_course_progress = date_filter(db.session.query(func.avg(UserProgress.progress_percentage)), UserProgress.completion_date).scalar() or 0

    exam_data = (
        date_filter(
            db.session.query(
                Exam.title,
                func.avg(UserScore.score).label('avg_score')
            )
            .join(UserScore, UserScore.exam_id == Exam.id),
            UserScore.created_at
        )
        .group_by(Exam.title)
        .order_by(Exam.title)
        .all()
    )
    exam_labels = [row.title for row in exam_data]
    exam_avg_scores = [round(row.avg_score, 2) for row in exam_data]

    passed_count = date_filter(UserScore.query.filter(UserScore.score >= 56), UserScore.created_at).count()
    failed_count = date_filter(UserScore.query.filter(UserScore.score < 56), UserScore.created_at).count()
    pf_total = passed_count + failed_count or 1
    pass_pct = round(passed_count / pf_total * 100, 1)
    fail_pct = round(failed_count / pf_total * 100, 1)

    cp_data = (
        date_filter(
            db.session.query(
                StudyMaterial.title,
                func.avg(UserProgress.progress_percentage).label('avg_prog')
            )
            .join(UserProgress, UserProgress.study_material_id == StudyMaterial.id),
            UserProgress.completion_date
        )
        .group_by(StudyMaterial.title)
        .order_by(StudyMaterial.title)
        .all()
    )
    course_labels = [row.title for row in cp_data]
    course_avg_progress = [round(row.avg_prog, 2) for row in cp_data]

    ts = (
        date_filter(
            db.session.query(
                func.date(UserScore.created_at).label('date'),
                func.avg(UserScore.score).label('avg_score')
            ),
            UserScore.created_at
        )
        .group_by(func.date(UserScore.created_at))
        .order_by(func.date(UserScore.created_at))
        .all()
    )
    ts_labels = [r.date.strftime('%Y-%m-%d') for r in ts]
    ts_avg_scores = [round(r.avg_score, 2) for r in ts]

    # Top users by exam score in range
    top_users_query = db.session.query(User, func.avg(UserScore.score).label('avg_score')) \
        .join(UserScore, UserScore.user_id == User.id) \
        .filter(User.deleted_at.is_(None))
    if start_date and end_date:
        top_users_query = top_users_query.filter(UserScore.created_at >= start_date, UserScore.created_at <= end_date)
    top_users = top_users_query.group_by(User).order_by(func.avg(UserScore.score).desc()).limit(5).all()

    # --- Advanced 3D Scatter Metrics ---
    users_all = User.query.filter(User.deleted_at.is_(None)).all()
    metrics = {
        "avg_exam_score": [],
        "total_time_spent": [],
        "completion_rate": [],
        "exams_taken": [],
        "avg_attempts_per_exam": [],
        "score_improvement": [],
        "last_activity_days_ago": [],
        "user_labels": [],
        "department": [],
    }
    for user in users_all:
        # Scores and progress
        scores = sorted(user.scores, key=lambda s: s.created_at)
        progresses = user.study_progress

        # Avg exam score
        avg_score = sum(s.score for s in scores) / len(scores) if scores else 0
        metrics["avg_exam_score"].append(round(avg_score, 2))

        # Total time spent
        time_spent = sum([getattr(sp, "time_spent", 0) for sp in progresses])
        metrics["total_time_spent"].append(round(time_spent, 2))

        # Completion rate
        courses_assigned = len(progresses)
        courses_completed = sum(1 for sp in progresses if getattr(sp, "progress_percentage", 0) >= 100)
        completion_rate = (courses_completed / courses_assigned * 100) if courses_assigned else 0
        metrics["completion_rate"].append(round(completion_rate, 2))

        # Exams taken
        exams_taken = len(scores)
        metrics["exams_taken"].append(exams_taken)

        # Avg attempts per exam (if attempt tracking)
        exam_ids = set()
        attempts = 0
        for s in scores:
            exam_ids.add(s.exam_id)
            attempts += getattr(s, "attempt", 1)
        avg_attempts = (attempts / len(exam_ids)) if exam_ids else 0
        metrics["avg_attempts_per_exam"].append(round(avg_attempts, 2))

        # Score improvement (last - first)
        if len(scores) >= 2:
            score_improvement = scores[-1].score - scores[0].score
        else:
            score_improvement = 0
        metrics["score_improvement"].append(round(score_improvement, 2))

        # Last activity days ago
        last_dates = [s.created_at for s in scores] + [sp.completion_date for sp in progresses if sp.completion_date]
        last_dates = [d for d in last_dates if d]
        if last_dates:
            last_activity = max(last_dates)
            days_ago = (today - last_activity.date()).days
        else:
            days_ago = None
        metrics["last_activity_days_ago"].append(days_ago if days_ago is not None else "")

        # Labels
        metrics["user_labels"].append(f"{user.first_name} {user.last_name}")
        metrics["department"].append(user.department.name if user.department else "N/A")

    axis_options = [
        {"key": "avg_exam_score", "label": "Avg Exam Score"},
        {"key": "total_time_spent", "label": "Total Time Spent"},
        {"key": "completion_rate", "label": "Completion Rate"},
        {"key": "exams_taken", "label": "Exams Taken"},
        {"key": "avg_attempts_per_exam", "label": "Avg Attempts per Exam"},
        {"key": "score_improvement", "label": "Score Improvement"},
        {"key": "last_activity_days_ago", "label": "Last Activity (days ago)"},
    ]
    # Default axes
    default_x = "avg_exam_score"
    default_y = "total_time_spent"
    default_z = "completion_rate"

    return render_template(
        'admin_analytics.html',
        period=period, periods=periods,
        total_users=total_users,
        active_users=active_users,
        avg_exam_score=round(avg_exam_score, 2),
        avg_course_progress=round(avg_course_progress, 2),
        exam_labels=exam_labels,
        exam_avg_scores=exam_avg_scores,
        passed_count=passed_count,
        failed_count=failed_count,
        pass_pct=pass_pct,
        fail_pct=fail_pct,
        course_labels=course_labels,
        course_avg_progress=course_avg_progress,
        ts_labels=ts_labels,
        ts_avg_scores=ts_avg_scores,
        top_users=top_users,
        start_date=start_date.strftime('%Y-%m-%d') if start_date else '',
        end_date=end_date.strftime('%Y-%m-%d') if end_date else '',
        axis_options=axis_options,
        metrics=metrics,
        default_x=default_x,
        default_y=default_y,
        default_z=default_z,
    )

@admin_routes.route('/admin/analytics/users')
@login_required
@admin_required
def analytics_user_list():
    # Filtering
    q = request.args.get('q', '').strip()
    dept_id = request.args.get('dept', '').strip()
    sort = request.args.get('sort', '')

    users_query = User.query
    if q:
        users_query = users_query.filter(
            (User.first_name.ilike(f'%{q}%')) |
            (User.last_name.ilike(f'%{q}%')) |
            (User.employee_email.ilike(f'%{q}%'))
        )
    if dept_id:
        users_query = users_query.filter(User.department_id == int(dept_id))

    users = users_query.order_by(User.last_name, User.first_name).all()
    # Gather stats
    user_stats = []
    for user in users:
        exams_taken = len(user.scores)
        avg_score = round(sum([s.score for s in user.scores]) / exams_taken, 2) if exams_taken else 0
        courses_taken = len(user.study_progress)
        avg_progress = round(sum([sp.progress_percentage for sp in user.study_progress]) / courses_taken, 2) if courses_taken else 0
        user_stats.append({
            'user': user,
            'exams_taken': exams_taken,
            'avg_score': avg_score,
            'courses_taken': courses_taken,
            'avg_progress': avg_progress
        })
    # Sorting
    if sort == 'score_desc':
        user_stats.sort(key=lambda x: x['avg_score'], reverse=True)
    elif sort == 'score_asc':
        user_stats.sort(key=lambda x: x['avg_score'])
    elif sort == 'progress_desc':
        user_stats.sort(key=lambda x: x['avg_progress'], reverse=True)
    elif sort == 'progress_asc':
        user_stats.sort(key=lambda x: x['avg_progress'])

    departments = Department.query.order_by(Department.name).all()
    return render_template('admin_analytics_users.html', user_stats=user_stats, departments=departments, search_query=q, dept_id=dept_id, sort=sort, departments_list=departments)

@admin_routes.route('/admin/analytics/user/<int:user_id>')
@login_required
@admin_required
def analytics_user_detail(user_id):
    user = User.query.get_or_404(user_id)
    # Exams: show all attempts, with date
    exam_scores = (
        db.session.query(Exam.title, UserScore.score, UserScore.created_at)
        .join(UserScore, UserScore.exam_id == Exam.id)
        .filter(UserScore.user_id == user_id)
        .order_by(UserScore.created_at)
        .all()
    )
    course_progress = (
        db.session.query(StudyMaterial.title, UserProgress.progress_percentage, UserProgress.completion_date)
        .join(UserProgress, UserProgress.study_material_id == StudyMaterial.id)
        .filter(UserProgress.user_id == user_id)
        .all()
    )
    exam_titles = [e[0] for e in exam_scores]
    exam_scores_list = [e[1] for e in exam_scores]
    exam_dates = [e[2].strftime('%Y-%m-%d') if e[2] else '' for e in exam_scores]
    course_titles = [c[0] for c in course_progress]
    course_percents = [c[1] for c in course_progress]
    # Timeline, sorted
    timeline = sorted(
        [(e[2], f"Exam: {e[0]}", e[1]) for e in exam_scores] +
        [(c[2], f"Course: {c[0]}", c[1]) for c in course_progress if c[2]],
        key=lambda x: x[0] or datetime.min
    )
    return render_template(
        'admin_analytics_user_detail.html',
        user=user,
        exam_titles=exam_titles,
        exam_scores=exam_scores_list,
        exam_dates=exam_dates,
        course_titles=course_titles,
        course_percents=course_percents,
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
        'departments','courses','exams','leaderboard','inactive_users','custom'
    ]
    Optional ?search= filter. For custom: Accepts ?type=custom&report_model=users&fields=id&fields=first_name, etc.
    """
    rpt_type = request.args.get('type')
    search   = request.args.get('search', '').strip()
    report_model = request.args.get('report_model')
    fields = request.args.getlist('fields')

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
            passed = UserScore.query.filter_by(user_id=u.id).filter(UserScore.score>=56).count()
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

    elif rpt_type == 'audit_logs':
        cw.writerow([
            'Record Type','Timestamp','User/Email',
            'Description','IP Address','User Agent'
        ])
        ev_q = Event.query
        if search:
            ev_q = ev_q.filter(Event.description.ilike(f'%{search}%'))
        for ev in ev_q.order_by(Event.date.desc()).all():
            ts = ev.date.strftime('%Y-%m-%d %H:%M:%S') if ev.date else ''
            cw.writerow(['EVENT', ts, '', ev.description or '', '', ''])
        fl_q = FailedLogin.query
        if search:
            fl_q = fl_q.filter(FailedLogin.email.ilike(f'%{search}%'))
        ts_col = None
        for col in ('timestamp','created_at','attempted_at','logged_at'):
            if hasattr(FailedLogin, col):
                ts_col = getattr(FailedLogin, col)
                break
        if ts_col is not None:
            fl_q = fl_q.order_by(ts_col.desc())
        for fl in fl_q.all():
            ts = None
            for col in (fl.timestamp if hasattr(fl, 'timestamp') else None,
                        getattr(fl, 'created_at', None),
                        getattr(fl, 'attempted_at', None),
                        getattr(fl, 'logged_at', None)):
                if col:
                    ts = col
                    break
            ts_str = ts.strftime('%Y-%m-%d %H:%M:%S') if ts else ''
            cw.writerow([
                'FAILED_LOGIN',
                ts_str,
                fl.email or '',
                '',
                fl.ip_address or '',
                fl.user_agent or ''
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
                u.department.name if u.department else '',
                u.join_date.strftime('%Y-%m-%d') if u.join_date else '',
            ])
        filename = 'users.csv'

    elif rpt_type == 'departments':
        cw.writerow(['Department','User Count','Avg Exam Score','Avg Progress','Completion Rate'])
        departments = Department.query.all()
        for d in departments:
            d_users = User.query.filter_by(department_id=d.id).all()
            ids = [u.id for u in d_users]
            if not ids:
                continue
            avg_score = db.session.query(func.avg(UserScore.score)).filter(UserScore.user_id.in_(ids)).scalar() or 0
            avg_progress = db.session.query(func.avg(UserProgress.progress_percentage)).filter(UserProgress.user_id.in_(ids)).scalar() or 0
            total_assigned = UserProgress.query.filter(UserProgress.user_id.in_(ids)).count()
            total_completed = UserProgress.query.filter(UserProgress.user_id.in_(ids), UserProgress.progress_percentage >= 100).count()
            completion_rate = (total_completed/total_assigned*100) if total_assigned else 0
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
            pass_rate = (pass_count/attempts*100) if attempts else 0
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
                u.department.name if u.department else '',
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
                    u.department.name if u.department else '',
                ])
        filename = 'inactive_users.csv'

    # 3. Fully Custom Report
    elif rpt_type == 'custom' and report_model and fields:
        REPORT_MODELS = {
            "users": {
                "model": User,
                "fields": {
                    "id": lambda u: u.id,
                    "first_name": lambda u: u.first_name,
                    "last_name": lambda u: u.last_name,
                    "employee_email": lambda u: u.employee_email,
                    "department": lambda u: u.department.name if u.department else '',
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
    # --- Read optional filter params ---
    start = request.args.get('start')  # e.g. '2025-05-01'
    end   = request.args.get('end')    # e.g. '2025-05-07'
    q     = request.args.get('q', '').strip()

    # --- 1) Query Events with date/text filters ---
    ev_q = Event.query
    if start:
        ev_q = ev_q.filter(Event.date >= start)
    if end:
        ev_q = ev_q.filter(Event.date <= end)
    if q:
        ev_q = ev_q.filter(Event.description.ilike(f'%{q}%'))
    try:
        events = ev_q.order_by(Event.date.desc()).limit(50).all()
    except Exception:
        events = []

    # --- 2) Identify the correct timestamp column on FailedLogin ---
    ts_col = None
    for col_name in ('timestamp', 'created_at', 'attempt_time', 'logged_at'):
        if hasattr(FailedLogin, col_name):
            ts_col = getattr(FailedLogin, col_name)
            break

    # --- 3) Query FailedLogin with same filters ---
    fl_q = FailedLogin.query
    if start and ts_col is not None:
        fl_q = fl_q.filter(ts_col >= start)
    if end and ts_col is not None:
        fl_q = fl_q.filter(ts_col <= end)
    if q:
        fl_q = fl_q.filter(FailedLogin.email.ilike(f'%{q}%'))
    try:
        if ts_col is not None:
            fl_q = fl_q.order_by(ts_col.desc())
        failed_logins = fl_q.limit(50).all()
    except Exception:
        failed_logins = []

    return render_template(
        'admin_audit_logs.html',
        events=events,
        failed_logins=failed_logins,
        filters={'start': start, 'end': end, 'q': q}
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
# 1) Summary: list users & count of wrong answers
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
# 2) Detail: either paginated or full list for one user
# ------------------------------------------------------------
@admin_routes.route('/incorrect_answers')
@login_required
@admin_required
def view_incorrect_answers():
    # 1) Grab parameters
    user_id  = request.args.get('user_id', type=int)
    page     = request.args.get('page', 1, type=int)
    per_page = 40

    # 2) Validate user_id
    if not user_id:
        flash("Please select a user first.", "warning")
        return redirect(url_for('admin_routes.incorrect_summary'))
    user = User.query.get_or_404(user_id)

    # 3) Build subquery: for each question, find the latest answered_at
    last_wrong_sq = (
        db.session.query(
            IncorrectAnswer.question_id,
            IncorrectAnswer.exam_id,
            IncorrectAnswer.special_paper,
            func.max(IncorrectAnswer.answered_at).label('last_wrong')
        )
        .filter(IncorrectAnswer.user_id == user_id)
        .group_by(IncorrectAnswer.question_id, IncorrectAnswer.exam_id, IncorrectAnswer.special_paper)
        .subquery('last_wrong_sq')
    )

    # 4) Join to get full details for only those “last wrong” rows
    detailed_q = (
        IncorrectAnswer.query  # Start with the Model.query
        .with_entities( # Specify the entities to select
            last_wrong_sq.c.last_wrong.label('answered_at'),
            db.case(
                [
                    (IncorrectAnswer.special_paper.isnot(None),
                     func.concat('Special Exam ', IncorrectAnswer.special_paper))
                ],
                else_=func.coalesce(Exam.title, 'Exam Not Found')
            ).label('exam_title'),
            IncorrectAnswer.special_paper,
            IncorrectAnswer.question_id.label('question_id_val'),
            func.coalesce(Question.question_text, 'Question Not Available').label('question_text'),
            IncorrectAnswer.user_answer,
            IncorrectAnswer.correct_answer
        )
        # Join IncorrectAnswer (the base of our query) with last_wrong_sq
        .join(
            last_wrong_sq,
            and_(
                IncorrectAnswer.question_id == last_wrong_sq.c.question_id,
                (IncorrectAnswer.exam_id == last_wrong_sq.c.exam_id) | \
                ((IncorrectAnswer.exam_id.is_(None)) & (last_wrong_sq.c.exam_id.is_(None))),
                (IncorrectAnswer.special_paper == last_wrong_sq.c.special_paper) | \
                ((IncorrectAnswer.special_paper.is_(None)) & (last_wrong_sq.c.special_paper.is_(None))),
                IncorrectAnswer.answered_at  == last_wrong_sq.c.last_wrong
            )
        )
        .filter(IncorrectAnswer.user_id == user_id) # Filter results for the specified user
        # Outerjoin to Question table, only if not a special paper and question_id might match
        .outerjoin(Question, and_(Question.id == IncorrectAnswer.question_id, IncorrectAnswer.special_paper.is_(None)))
        # Outerjoin to Exam table
        .outerjoin(Exam, Exam.id == IncorrectAnswer.exam_id)
        .order_by(desc(last_wrong_sq.c.last_wrong))
    )

    # 5) Paginate over unique questions
    pagination = detailed_q.paginate(page=page, per_page=per_page, error_out=False)
    records    = pagination.items

    # 6) Render
    return render_template(
        'incorrect_details.html',
        user       = user,
        records    = records,
        pagination = pagination
    )

@admin_routes.route('/incorrect_answers/clear', methods=['POST'])
@login_required
@admin_required
def clear_incorrect_answers():
    # 1) Read and validate the incoming user_id
    user_id = request.form.get('user_id', type=int)
    if not user_id:
        flash("No user specified to clear.", "warning")
        return redirect(url_for('admin_routes.incorrect_summary'))

    user = User.query.get_or_404(user_id)

    # 2) Bulk‐delete all that user’s incorrect answers
    deleted_count = (
        IncorrectAnswer.query
        .filter_by(user_id=user_id)
        .delete(synchronize_session=False)
    )
    db.session.commit()

    # 3) Feedback and redirect back to the detail view (now empty)
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