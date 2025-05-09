from flask import (
    Blueprint, request, jsonify, render_template,
    redirect, url_for, session, flash
)
from flask_login import login_required, current_user
from models import (
    db,
    StudyMaterial, UserProgress, SubTopic,
    User, Designation, Exam, Question, UserScore,
    Category, Level, Area, UserLevelProgress,
    SpecialExamRecord, Client, LevelArea,
    Task, TaskDocument, FailedLogin, Event, Role
)
import logging
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from gridfs import GridFS
from pymongo import MongoClient
from functools import wraps
from sqlalchemy import func, or_
import io, csv
from bson import ObjectId
from flask import make_response


# --- MongoDB + GridFS setup ---
mongo_client = MongoClient('mongodb://localhost:27017/')
mongo_db     = mongo_client['collective_rcm']
grid_fs      = GridFS(mongo_db)

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

@admin_routes.route('/admin/analytics')
@login_required
@admin_required
def view_analytics():
    # 1) Period selection
    try:
        period = int(request.args.get('period', 30))
    except ValueError:
        period = 30
    if period not in (30, 60, 90):
        period = 30

    today      = datetime.utcnow().date()
    start_date = today - timedelta(days=period)

    # 2) Summary metrics
    total_users         = User.query.count()
    active_user_ids     = set([u for (u,) in db.session.query(UserScore.user_id).distinct()]
                              + [u for (u,) in db.session.query(UserProgress.user_id).distinct()])
    active_users        = len(active_user_ids)
    avg_exam_score      = db.session.query(func.avg(UserScore.score)).scalar() or 0
    avg_course_progress = db.session.query(func.avg(UserProgress.progress_percentage)).scalar() or 0

    # 3) Exam averages + table
    exam_data = (
        db.session.query(
            Exam.title,
            func.avg(UserScore.score).label('avg_score')
        )
        .join(UserScore, UserScore.exam_id == Exam.id)
        .group_by(Exam.title)
        .order_by(Exam.title)
        .all()
    )
    exam_labels       = [row.title for row in exam_data]
    exam_avg_scores   = [round(row.avg_score,2) for row in exam_data]

    # 4) Pass vs Fail counts + percentages
    passed_count = UserScore.query.filter(UserScore.score >= 56).count()
    failed_count = UserScore.query.filter(UserScore.score < 56).count()
    pf_total     = passed_count + failed_count or 1
    pass_pct     = round(passed_count / pf_total * 100, 1)
    fail_pct     = round(failed_count / pf_total * 100, 1)

    # 5) Course progress averages + table
    cp_data = (
        db.session.query(
            StudyMaterial.title,
            func.avg(UserProgress.progress_percentage).label('avg_prog')
        )
        .join(UserProgress, UserProgress.study_material_id == StudyMaterial.id)
        .group_by(StudyMaterial.title)
        .order_by(StudyMaterial.title)
        .all()
    )
    course_labels       = [row.title for row in cp_data]
    course_avg_progress = [round(row.avg_prog,2) for row in cp_data]

    # 6) Time-series of avg score
    ts = (
        db.session.query(
            func.date(UserScore.created_at).label('date'),
            func.avg(UserScore.score).label('avg_score')
        )
        .filter(UserScore.created_at >= start_date)
        .group_by(func.date(UserScore.created_at))
        .order_by(func.date(UserScore.created_at))
        .all()
    )
    ts_labels    = [r.date.strftime('%Y-%m-%d') for r in ts]
    ts_avg_scores= [round(r.avg_score,2) for r in ts]

    return render_template(
        'admin_analytics.html',
        # timeframe
        period=period, periods=[30,60,90],
        # summary
        total_users=total_users,
        active_users=active_users,
        avg_exam_score=round(avg_exam_score,2),
        avg_course_progress=round(avg_course_progress,2),
        # exam data
        exam_labels=exam_labels,
        exam_avg_scores=exam_avg_scores,
        # pass/fail
        passed_count=passed_count,
        failed_count=failed_count,
        pass_pct=pass_pct,
        fail_pct=fail_pct,
        # course progress
        course_labels=course_labels,
        course_avg_progress=course_avg_progress,
        # timeseries
        ts_labels=ts_labels,
        ts_avg_scores=ts_avg_scores,
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

# Delete User
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
        flash("Failed to delete user.", "error")
    return redirect(url_for('admin_routes.view_analytics'))

@admin_routes.route('/admin/reports/download')
@login_required
@admin_required
def download_report():
    """
    Download CSV for type in ['course_progress','exam_performance','special_exams','audit_logs']
    Optional ?search= filter.
    """
    rpt_type = request.args.get('type')
    search   = request.args.get('search', '').strip()

    si = io.StringIO()
    cw = csv.writer(si)

    if rpt_type == 'course_progress':
        cw.writerow(['User ID','Name','Total Courses','Avg Progress (%)'])
        users = User.query.order_by(User.join_date.desc()).all()
        for u in users:
            total = UserProgress.query.filter_by(user_id=u.id).count()
            avg   = db.session.query(func.avg(UserProgress.progress_percentage))\
                               .filter_by(user_id=u.id).scalar() or 0
            cw.writerow([u.id, f"{u.first_name} {u.last_name}", total, round(avg,2)])
        filename = 'course_progress.csv'

    elif rpt_type == 'exam_performance':
        cw.writerow(['User ID','Name','Total Attempts','Avg Score','Successful Attempts'])
        users = User.query.order_by(User.join_date.desc()).all()
        for u in users:
            total  = UserScore.query.filter_by(user_id=u.id).count()
            avg    = db.session.query(func.avg(UserScore.score))\
                                .filter_by(user_id=u.id).scalar() or 0
            passed = UserScore.query.filter_by(user_id=u.id)\
                                    .filter(UserScore.score>=56).count()
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
            # filter by user name or email
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
        # Combined Events + FailedLogins
        cw.writerow([
            'Record Type','Timestamp','User/Email',
            'Description','IP Address','User Agent'
        ])
        # --- Events ---
        ev_q = Event.query
        if search:
            ev_q = ev_q.filter(Event.description.ilike(f'%{search}%'))
        for ev in ev_q.order_by(Event.date.desc()).all():
            ts = ev.date.strftime('%Y-%m-%d %H:%M:%S') if ev.date else ''
            cw.writerow(['EVENT', ts, '', ev.description or '', '', ''])
        # --- Failed Logins ---
        fl_q = FailedLogin.query
        if search:
            fl_q = fl_q.filter(FailedLogin.email.ilike(f'%{search}%'))
        # determine timestamp column
        ts_col = None
        for col in ('timestamp','created_at','attempted_at','logged_at'):
            if hasattr(FailedLogin, col):
                ts_col = getattr(FailedLogin, col)
                break
        if ts_col is not None:
            fl_q = fl_q.order_by(ts_col.desc())
        for fl in fl_q.all():
            # pick whichever timestamp exists
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

    else:
        flash("Invalid report type.", "error")
        return redirect(url_for('admin_routes.generate_reports'))

    # send CSV
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    output.headers["Content-type"] = "text/csv"
    return output

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