from flask import Blueprint, request, jsonify, render_template, redirect, url_for, session, flash
from flask_login import login_required, current_user
from models import (
    db,
    StudyMaterial,
    UserProgress,
    SubTopic,
    User,
    Designation,
    Exam,
    Question,
    UserScore,
    Category,
    Level,
    Area,
    UserLevelProgress,
    SpecialExamRecord,
    Client,
    LevelArea,
    Task,
    TaskDocument,
    FailedLogin,
    Event
)
import logging
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from gridfs import GridFS
from pymongo import MongoClient
from functools import wraps

# MongoDB Client and GridFS Initialization
mongo_client = MongoClient('mongodb://localhost:27017/')  # adjust connection string as needed
mongo_db = mongo_client['collective_rcm']
grid_fs = GridFS(mongo_db)

ALLOWED_EXTENSIONS = {'pdf', 'docx', 'ppt', 'xlsx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Initialize Blueprint
admin_routes = Blueprint('admin_routes', __name__)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Admin Authentication Middleware
def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        # Check if user is super admin or has an admin-designated role (example: designations with IDs 11, 12)
        is_super_admin = session.get('is_super_admin', False)
        user_designation = session.get('designation_id')
        if not is_super_admin and user_designation not in [11, 12]:
            flash("You do not have access to this page.", "error")
            logging.warning(f"Unauthorized access attempt to {request.path} by user.")
            return redirect(url_for('auth_routes.login'))
        return func(*args, **kwargs)
    return wrapper

@admin_routes.route('/admin', methods=['GET'])
@admin_required
def admin_dashboard():
    """
    Render the Admin Dashboard with:
      - All Courses
      - All Users
      - All Designations
      - All Exams (regular)
      - All SpecialExamRecords
    """
    try:
        courses = StudyMaterial.query.all()  
        users = User.query.all()             
        designations = Designation.query.all()  
        exams = Exam.query.all()
        special_exam_records = SpecialExamRecord.query.all()

        return render_template(
            'admin_dashboard.html',
            courses=courses,
            users=users,
            designations=designations,
            exams=exams,
            special_exam_records=special_exam_records
        )
    except Exception as e:
        logging.error(f"Error loading admin dashboard: {e}")
        return render_template('500.html'), 500

@admin_routes.route('/delete_course/<int:course_id>', methods=['POST'])
@admin_required
def delete_course(course_id):
    try:
        course = StudyMaterial.query.get_or_404(course_id)
        SubTopic.query.filter_by(study_material_id=course_id).delete()
        UserProgress.query.filter_by(study_material_id=course_id).delete()
        if course.files:
            for file_ref in course.files:
                file_id, _ = file_ref.split("|")
                try:
                    grid_fs.delete(file_id)
                    logging.info(f"Deleted file with ID {file_id} from MongoDB.")
                except Exception as e:
                    logging.error(f"Failed to delete file with ID {file_id}: {e}")
        db.session.delete(course)
        db.session.commit()
        flash("Course deleted successfully!", "success")
    except Exception as e:
        logging.error(f"Error deleting course ID {course_id}: {e}")
        flash("Failed to delete course. Please try again.", "error")
    return redirect(url_for('admin_routes.admin_dashboard'))

@admin_routes.route('/admin/reports', methods=['GET'])
@admin_required
def generate_reports():
    """
    Generate reports on course progress, user activity, and exam performance.
    Combines regular exam data (from UserScore) and special exam data (from SpecialExamRecord).
    """
    try:
        # --- Course Progress Data ---
        courses = StudyMaterial.query.all()
        course_progress_data = []
        for course in courses:
            total_users = UserProgress.query.filter_by(study_material_id=course.id).count()
            avg_completion = (
                db.session.query(db.func.avg(UserProgress.progress_percentage))
                .filter_by(study_material_id=course.id)
                .scalar() or 0
            )
            course_progress_data.append({
                'course_id': course.id,
                'course_title': course.title,
                'total_users': total_users,
                'avg_completion': round(avg_completion, 2)
            })

        # --- Regular Exam Performance Data (from UserScore) ---
        exams = Exam.query.all()
        exam_performance_data = []
        for exam in exams:
            total_attempts = UserScore.query.filter_by(exam_id=exam.id).count()
            avg_score = (
                db.session.query(db.func.avg(UserScore.score))
                .filter_by(exam_id=exam.id)
                .scalar() or 0
            )
            successful_attempts = UserScore.query.filter(
                (UserScore.exam_id == exam.id) & (UserScore.score >= 56)
            ).count()
            exam_performance_data.append({
                'exam_id': exam.id,
                'exam_title': exam.title,
                'total_attempts': total_attempts,
                'avg_score': round(avg_score, 2),
                'successful_attempts': successful_attempts
            })

        # --- Special Exam Data (from SpecialExamRecord) ---
        special_exam_data = []
        special_records = SpecialExamRecord.query.all()
        for record in special_records:
            special_exam_data.append({
                'user_id': record.user_id,
                'paper1_score': record.paper1_score,
                'paper1_passed': record.paper1_passed,
                'paper2_score': record.paper2_score,
                'paper2_passed': record.paper2_passed
            })

        logging.info("Reports generated successfully.")
        return render_template(
            'admin_reports.html',
            course_progress_data=course_progress_data,
            exam_performance_data=exam_performance_data,
            special_exam_data=special_exam_data
        )
    except Exception as e:
        logging.error(f"Error generating reports: {e}")
        return jsonify({'error': 'Failed to generate reports'}), 500

@admin_routes.route('/admin/set_restrictions', methods=['POST'])
@admin_required
def set_restrictions():
    try:
        course_id = request.form.get('course_id')
        restriction_level = request.form.get('restriction_level')  # This corresponds to a designation ID
        if not course_id or not restriction_level:
            return jsonify({'error': 'Course ID and restriction level are required.'}), 400
        restriction_level = int(restriction_level)
        if restriction_level < 1 or restriction_level > 12:
            return jsonify({'error': 'Invalid restriction level.'}), 400

        course = StudyMaterial.query.get(course_id)
        if course:
            course.restriction_level = restriction_level
            db.session.commit()
            return jsonify({'success': f'Restriction level updated for Course ID {course_id}.'}), 200
        return jsonify({'error': f'Course with ID {course_id} not found.'}), 404
    except Exception as e:
        logging.error(f"Error setting restrictions: {e}")
        return jsonify({'error': 'Failed to set restrictions'}), 500

@admin_routes.route('/admin/edit_course/<int:course_id>', methods=['POST'])
def edit_course(course_id):
    try:
        course = StudyMaterial.query.get(course_id)
        if not course:
            flash("Course not found.", "error")
            return redirect(url_for('admin_routes.admin_dashboard'))

        title = request.form.get('title')
        description = request.form.get('description')
        course_time = request.form.get('course_time')
        max_time = request.form.get('max_time')
        new_files = request.files.getlist('new_files')
        delete_files = request.form.getlist('delete_files')  # Files marked for deletion

        course.title = title
        course.description = description
        course.course_time = int(course_time)
        course.max_time = int(max_time)

        if delete_files:
            for file_id in delete_files:
                try:
                    grid_fs.delete(file_id)
                except Exception as e:
                    logging.error(f"Error deleting file {file_id}: {e}")
                    flash(f"Failed to delete file: {file_id}", "error")
            course.files = [
                file for file in course.files if file.split('|')[0] not in delete_files
            ]

        for file in new_files:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                mongo_file_id = grid_fs.put(file, filename=filename, content_type=file.content_type)
                course.files.append(f"{mongo_file_id}|{filename}")

        db.session.commit()
        flash("Course updated successfully.", "success")
        return redirect(url_for('admin_routes.admin_dashboard'))
    except Exception as e:
        logging.error(f"Error editing course {course_id}: {e}")
        flash("Failed to edit course. Please try again.", "error")
        return redirect(url_for('admin_routes.admin_dashboard'))

@admin_routes.route('/delete_exam/<int:exam_id>', methods=['POST'])
@admin_required
def delete_exam(exam_id):
    try:
        exam = Exam.query.get_or_404(exam_id)
        Question.query.filter_by(exam_id=exam_id).delete()
        UserScore.query.filter_by(exam_id=exam_id).delete()
        db.session.delete(exam)
        db.session.commit()
        flash("Exam deleted successfully!", "success")
        return redirect(url_for('admin_routes.admin_dashboard'))
    except Exception as e:
        logging.error(f"Error deleting exam ID {exam_id}: {e}")
        flash("Failed to delete exam. Please try again.", "error")
        return redirect(url_for('admin_routes.admin_dashboard'))

@admin_routes.route('/edit_exam/<int:exam_id>', methods=['POST'])
@admin_required
def edit_exam(exam_id):
    try:
        exam = Exam.query.get_or_404(exam_id)
        title = request.form.get('title')
        duration = request.form.get('duration')
        level = request.form.get('level')
        course_id = request.form.get('course_id')
        category_id = request.form.get('category_id')

        if not all([title, duration, level, course_id, category_id]):
            flash("All fields are required.", "error")
            return redirect(url_for('admin_routes.admin_dashboard'))

        exam.title = title
        exam.duration = int(duration)
        exam.level = level  # Depending on your implementation, update this relationship accordingly
        exam.course_id = int(course_id)
        exam.category_id = int(category_id)

        db.session.commit()
        flash("Exam updated successfully.", "success")
        return redirect(url_for('admin_routes.admin_dashboard'))
    except Exception as e:
        logging.error(f"Error editing exam ID {exam_id}: {e}")
        flash("Failed to edit exam. Please try again.", "error")
        return redirect(url_for('admin_routes.admin_dashboard'))

# NEW ROUTE: View Special Exam Record Details
@admin_routes.route('/admin/view_special_exam_record/<int:record_id>', methods=['GET'])
@admin_required
def view_special_exam_record(record_id):
    """
    Render a detailed view of a special exam record.
    This page should display detailed information about the special exam record.
    """
    try:
        record = SpecialExamRecord.query.get_or_404(record_id)
        return render_template('admin_view_special_exam.html', record=record)
    except Exception as e:
        logging.error(f"Error viewing special exam record {record_id}: {e}")
        flash("Failed to load special exam record.", "error")
        return redirect(url_for('admin_routes.admin_dashboard'))
