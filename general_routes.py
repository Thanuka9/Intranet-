from flask import Blueprint, render_template, session, logging, jsonify
from flask_login import login_required, current_user
from datetime import date
from models import (
    db,
    StudyMaterial,
    UserProgress,
    UserScore,
    SpecialExamRecord,
    Task              # ← make sure Task is imported
)

# Initialize Blueprint
general_routes = Blueprint('general_routes', __name__)

@general_routes.route('/home')
def home():
    return render_template('home.html')

@general_routes.route('/dashboard')
@login_required
def dashboard():
    user_name        = current_user.first_name
    user_designation = current_user.designation.title if current_user.designation else "Not Assigned"
    user_id          = current_user.id

    # — Learning Progress —
    current_progress = (
        db.session.query(StudyMaterial.title, UserProgress.progress_percentage)
          .join(UserProgress, StudyMaterial.id == UserProgress.study_material_id)
          .filter(UserProgress.user_id == user_id)
          .order_by(UserProgress.progress_percentage.desc())
          .first()
    )
    if current_progress:
        current_course, course_progress = current_progress
    else:
        current_course, course_progress = "No course in progress.", 0

    # — Last Exam Results (special or regular) —
    special_record = SpecialExamRecord.query.filter_by(user_id=user_id).first()
    user_score     = UserScore.query.filter_by(user_id=user_id).order_by(UserScore.created_at.desc()).first()

    if special_record:
        if special_record.paper1_completed_at:
            last_exam_title = "Special Exam Paper 1"
            last_exam_score = special_record.paper1_score
        elif special_record.paper2_completed_at:
            last_exam_title = "Special Exam Paper 2"
            last_exam_score = special_record.paper2_score
        else:
            last_exam_title = "Special Exam – Incomplete"
            last_exam_score = 0
    elif user_score:
        last_exam_title = user_score.exam.title if user_score.exam else "N/A"
        last_exam_score = user_score.score
    else:
        last_exam_title = "No exams completed yet."
        last_exam_score = 0

    # — Upcoming Deadlines: next 5 tasks due today or later —
    upcoming_tasks = (
        Task.query
            .filter(
                Task.assignees.contains(current_user),   # or however you link tasks→users
                Task.due_date >= date.today()
            )
            .order_by(Task.due_date.asc())
            .limit(5)
            .all()
    )

    return render_template(
        'dashboard.html',
        user_name        = user_name,
        user_role        = user_designation,
        current_course   = current_course,
        course_progress  = course_progress,
        last_exam_title  = last_exam_title,
        last_exam_score  = last_exam_score,
        upcoming_tasks   = upcoming_tasks
    )

@general_routes.route('/study_materials')
@login_required
def study_materials():
    try:
        materials = StudyMaterial.query.all()
        progress_data = []
        user_id = session.get('user_id')

        for material in materials:
            up = UserProgress.query.filter_by(
                study_material_id=material.id,
                user_id=user_id
            ).first()
            progress_data.append({
                'course_id': material.id,
                'progress_percentage': up.progress_percentage if up else 0
            })

        is_super_admin = session.get('is_super_admin', False)
        return render_template(
            'study_materials.html',
            materials=materials,
            progress_data=progress_data,
            is_super_admin=is_super_admin,
            user_role=session.get('role')
        )
    except Exception as e:
        logging.error(f"Error rendering study materials: {e}")
        return jsonify({'error': 'Failed to load study materials'}), 500

@general_routes.route('/client_materials')
@login_required
def client_materials():
    return render_template('client_materials.html')

@general_routes.route('/hr_management')
@login_required
def hr_management():
    return render_template('hr_management.html')
