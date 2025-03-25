from flask import Blueprint, render_template, session, logging, jsonify
from flask_login import login_required, current_user
from models import db, StudyMaterial, UserProgress, UserScore, User, Category, Level, Area, UserLevelProgress, SpecialExamRecord
from extensions import db
import logging

# Initialize Blueprint
general_routes = Blueprint('general_routes', __name__)

# Home route should also use the Blueprint
@general_routes.route('/home')
def home():
    return render_template('home.html')

# Dashboard route
@general_routes.route('/dashboard')
@login_required
def dashboard():
    user_name = current_user.first_name
    user_designation = current_user.designation.title if current_user.designation else "Not Assigned"
    user_id = current_user.id

    # Learning Progress: Get the current course and progress
    current_progress = (
        db.session.query(StudyMaterial.title, UserProgress.progress_percentage)
        .join(UserProgress, StudyMaterial.id == UserProgress.study_material_id)
        .filter(UserProgress.user_id == user_id)
        .order_by(UserProgress.progress_percentage.desc())
        .first()
    )

    if current_progress:
        current_course = current_progress[0]
        course_progress = current_progress[1]
    else:
        current_course = "No course in progress."
        course_progress = 0

    # Exam Results: Combine scores from SpecialExamRecord and UserScore
    special_record = SpecialExamRecord.query.filter_by(user_id=user_id).first()
    user_score = UserScore.query.filter_by(user_id=user_id).order_by(UserScore.created_at.desc()).first()

    if special_record:
        # Use SpecialExamRecord if it exists
        if special_record.paper1_completed_at:
            last_exam_title = "Special Exam Paper 1"
            last_exam_score = special_record.paper1_score
        elif special_record.paper2_completed_at:
            last_exam_title = "Special Exam Paper 2"
            last_exam_score = special_record.paper2_score
        else:
            last_exam_title = "Special Exam - Incomplete"
            last_exam_score = 0
        next_exam_date = "Not Scheduled"

    elif user_score:
        last_exam_title = user_score.exam.title if user_score.exam else "N/A"
        last_exam_score = user_score.score
        next_exam_date = "Not Scheduled"

    else:
        last_exam_title = "No exams completed yet."
        last_exam_score = 0
        next_exam_date = "Not Scheduled"

    return render_template(
        'dashboard.html',
        user_name=user_name,
        user_role=user_designation,
        current_course=current_course,
        course_progress=course_progress,
        last_exam_title=last_exam_title,
        last_exam_score=last_exam_score,
        next_exam_date=next_exam_date
    )

# Study Materials route
@general_routes.route('/study_materials', methods=['GET'])
@login_required
def study_materials():
    """
    Render the Study Materials dashboard.
    """
    try:
        # Fetch all study materials
        materials = StudyMaterial.query.all()

        # Prepare progress data for each course
        progress_data = []
        user_id = session.get('user_id')  # Ensure user_id is securely retrieved from session
        for material in materials:
            user_progress = UserProgress.query.filter_by(
                study_material_id=material.id,
                user_id=user_id
            ).first()

            progress_percentage = user_progress.progress_percentage if user_progress else 0
            progress_data.append({
                'course_id': material.id,
                'progress_percentage': progress_percentage
            })

        # Fetch the `is_super_admin` value from the session
        is_super_admin = session.get('is_super_admin', False)  # Default to False if not set

        # Pass study materials, progress data, and user role to the template
        return render_template(
            'study_materials.html',
            materials=materials,
            progress_data=progress_data,
            is_super_admin=is_super_admin,  # Pass is_super_admin to the template
            user_role=session.get('role')  # Pass the user role to the frontend
        )
    except Exception as e:
        logging.error(f"Error rendering study materials: {e}")
        return jsonify({'error': 'Failed to load study materials'}), 500


# Client Materials route
@general_routes.route('/client_materials')
@login_required
def client_materials():
    # Replace with actual logic for client materials
    return render_template('client_materials.html')

# HR Management route
@general_routes.route('/hr_management')
@login_required
def hr_management():
    # Replace with actual logic for HR management
    return render_template('hr_management.html')