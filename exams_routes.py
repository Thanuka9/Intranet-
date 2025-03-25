from flask import Blueprint, request, jsonify, render_template
from extensions import db
from models import Exam, Question, UserScore, User, Category, Level, Area, UserLevelProgress, Designation, LevelArea
from flask_login import current_user, login_required
from datetime import datetime, timezone, timedelta
from jinja2 import TemplateNotFound
import random
import requests
from models import SpecialExamRecord
import logging
from models import Exam, Question, UserScore, User, Category, StudyMaterial
from sqlalchemy.exc import SQLAlchemyError
from flask import redirect, flash, url_for

# Blueprint setup
exams_routes = Blueprint('exams_routes', __name__, url_prefix='/exams')

# -------------------------------
# Route to Create an Exam 
# -------------------------------
@exams_routes.route('/create', methods=['GET', 'POST'])
@login_required
def create_exam():
    """
    Route to create an exam.
    Accessible only to super admins.
    Ensures exam is linked to a Level, Area, and Category.
    Also allows setting a Minimum Designation Level for exam eligibility.
    """
    # -------------------------------
    # Authorization Check
    # -------------------------------
    if not getattr(current_user, 'is_super_admin', False):
        logging.warning("Unauthorized access attempt by user ID: %s", current_user.id)
        return jsonify({'error': 'Unauthorized access'}), 403

    # -------------------------------
    # GET Request: Render Exam Creation Form
    # -------------------------------
    if request.method == 'GET':
        try:
            # Fetch Levels, Categories, and Designation Levels from Dropdown Route
            response = requests.get(url_for('study_material_routes.get_dropdowns', _external=True))
            dropdown_data = response.json()

            # Fetch Courses from study_materials table
            courses = StudyMaterial.query.all()
            courses_data = [{"id": course.id, "title": course.title} for course in courses]

            return render_template(
                'upload_exam.html',
                levels=dropdown_data['levels'],
                categories=dropdown_data['categories'],
                designation_levels=dropdown_data['designations'],
                courses=courses_data
            )
        except Exception as e:
            logging.error(f"Error rendering exam creation form: {e}")
            return render_template('500.html', error="Failed to load the exam creation form."), 500

    # -------------------------------
    # POST Request: Handle Exam Creation
    # -------------------------------
    if request.method == 'POST':
        try:
            data = request.form
            title = data.get('title', '').strip()
            duration = data.get('duration', '').strip()
            level_id = data.get('level_id', '').strip()
            category_id = data.get('category_id', '').strip()
            course_id = data.get('course_id', '').strip()
            min_designation_level = data.get('minimum_designation_level', '').strip()

            # Validate form inputs
            if not all([title, duration, level_id, category_id, course_id, min_designation_level]):
                return jsonify({'error': 'All fields are required'}), 400

            if not duration.isdigit() or int(duration) <= 0:
                return jsonify({'error': 'Duration must be a positive integer'}), 400

            # Check if Level, Category, and Course exist
            level = Level.query.get(level_id)
            category = Category.query.get(category_id)
            course = StudyMaterial.query.get(course_id)
            designation = Designation.query.get(min_designation_level)

            if not level:
                return jsonify({'error': 'Selected level does not exist'}), 400

            if not category:
                return jsonify({'error': 'Selected category does not exist'}), 400

            if not course:
                return jsonify({'error': 'Selected course does not exist'}), 400

            if not designation:
                return jsonify({'error': 'Selected minimum designation level does not exist'}), 400

            # Check Level Eligibility based on User's Designation
            if not current_user.can_skip_level(level.level_number):
                logging.warning(f"User {current_user.id} attempted to create an exam for unauthorized level {level.level_number}")
                return jsonify({'error': 'You are not allowed to create exams for this level'}), 403

            # Create Exam
            exam = Exam(
                title=title,
                duration=int(duration),
                level_id=int(level_id),
                category_id=int(category_id),
                course_id=int(course_id),  # Link to the selected course
                minimum_designation_level=int(min_designation_level),
                created_by=current_user.id
            )
            db.session.add(exam)
            db.session.commit()

            logging.info(f"Exam '{title}' created successfully by user {current_user.id}")
            return jsonify({'message': 'Exam created successfully', 'exam_id': exam.id}), 201

        except SQLAlchemyError as db_error:
            logging.error(f"Database error creating exam: {db_error}")
            db.session.rollback()
            return jsonify({'error': 'Database error. Please try again later.'}), 500

        except Exception as e:
            logging.error(f"Unexpected error creating exam: {e}")
            db.session.rollback()
            return jsonify({'error': 'Failed to create exam. Please try again later.'}), 500

# -------------------------------
# Route to Add Questions to an Exam
# -------------------------------
@exams_routes.route('/<int:exam_id>/add_questions', methods=['POST'])
@login_required
def add_questions(exam_id):
    """
    Add questions to an existing exam.
    """
    if not getattr(current_user, 'is_super_admin', False):
        logging.warning(f"Unauthorized access by user {current_user.id} to add questions.")
        return jsonify({'error': 'Unauthorized access'}), 403

    try:
        # Validate exam existence
        exam = Exam.query.get(exam_id)
        if not exam:
            logging.error(f"Exam with ID {exam_id} not found.")
            return jsonify({'error': 'Exam not found'}), 404

        # Parse the form data into a dictionary
        data = request.form.to_dict(flat=False)
        questions = {}
        errors = []
        questions_to_add = []

        # Parse form data
        for key, value in data.items():
            if key.startswith("questions["):
                parts = key.split('][')
                question_index = parts[0].split('[')[1]
                field = parts[1].rstrip(']')
                questions.setdefault(question_index, {})[field] = value[0].strip()

        # Process and validate questions
        for question_index, question_data in questions.items():
            try:
                # Clean and validate inputs
                question_text = question_data.get('question_text', '').strip()
                choices_raw = question_data.get('choices', '').strip(' ,')
                correct_answer = question_data.get('correct_answer', '').strip(' "\'')
                category_id = question_data.get('category_id', '').strip()

                # Validate required fields
                if not all([question_text, choices_raw, correct_answer, category_id]):
                    errors.append(f"Question {question_index}: All fields are required")
                    continue

                # Parse and clean choices
                choices_list = [choice.strip(' "\'') for choice in choices_raw.split(',') if choice.strip()]
                choices_list = [c for c in choices_list if c]  # Remove empty strings
                
                if len(choices_list) < 2:
                    errors.append(f"Question {question_index}: At least 2 choices required")
                    continue

                # Check for duplicate choices (case-insensitive)
                unique_choices = {choice.lower() for choice in choices_list}
                if len(choices_list) != len(unique_choices):
                    errors.append(f"Question {question_index}: Duplicate choices found (case-insensitive)")
                    continue

                # Validate correct answer exists in choices
                if correct_answer not in choices_list:
                    errors.append(
                        f"Question {question_index}: Correct answer must match one of the choices. "
                        f"Got '{correct_answer}', Choices: {choices_list}"
                    )
                    continue

                # Validate category exists
                if not Category.query.get(category_id):
                    errors.append(f"Question {question_index}: Invalid category ID")
                    continue

                # Prepare the question object with proper string storage
                question = Question(
                    exam_id=exam_id,
                    question_text=question_text,
                    choices=','.join(choices_list),  # Convert list to comma-separated string
                    correct_answer=correct_answer,
                    category_id=int(category_id)
                )
                questions_to_add.append(question)

            except Exception as qe:
                logging.error(f"Error processing question {question_index}: {qe}")
                errors.append(f"Question {question_index}: Invalid input format")

        # Bulk insert valid questions
        if questions_to_add:
            try:
                db.session.bulk_save_objects(questions_to_add)
                db.session.commit()
                logging.info(f"Added {len(questions_to_add)} questions to Exam {exam_id}")
            except Exception as e:
                db.session.rollback()
                logging.error(f"Database error: {str(e)}")
                errors.append("Failed to save questions due to database error")

        # Return response
        if errors:
            return jsonify({
                'message': f"Processed with {len(errors)} errors",
                'success_count': len(questions_to_add),
                'errors': errors
            }), 207

        return jsonify({
            'message': f"Successfully added {len(questions_to_add)} questions",
            'exam_id': exam_id
        }), 201

    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Internal server error'}), 500
    
@exams_routes.route('/list', methods=['GET'])
@login_required
def list_exams():
    """
    Render the exam list page with user-specific exam status and retry information.
    Handles:
      - Designation-based skipping
      - Level prerequisites
      - Retry restrictions
      - Dynamic exam display for DB-based exams
      - Plus, adds two special exam papers (Paper 1 and Paper 2)
    """
    try:
        user_id = current_user.id
        now = datetime.utcnow()
        retry_period = timedelta(days=30)

        # -----------------------------
        # 1) Fetch normal (DB-based) Exams
        # -----------------------------
        exams = Exam.query.options(
            db.joinedload(Exam.level),
            db.joinedload(Exam.area),
            db.joinedload(Exam.category)
        ).all()

        processed_exams = []

        if exams:
            # Get completed courses from study_progress
            completed_courses = {
                progress.study_material_id
                for progress in current_user.study_progress
                if progress.progress_percentage >= 100
            }

            # Get exam scores
            exam_scores = {
                score.exam_id: score
                for score in UserScore.query.filter_by(user_id=user_id)
            }

            # Current user level
            current_level = current_user.get_current_level()

            for exam in exams:
                # 1) Minimum level check
                if exam.minimum_level and current_level < exam.minimum_level:
                    logging.info(f"User {current_user.id} does not meet the minimum level for Exam {exam.id}")
                    continue

                # 2) Check if user can skip exam based on designation
                if exam.is_skippable(current_user):
                    processed_exams.append({
                        'exam_id': exam.id,
                        'title': exam.title,
                        'duration': exam.duration,
                        'category': exam.category.name if exam.category else 'General',
                        'level': exam.level.level_number,
                        'status': 'Skipped',
                        'retry_date': None,
                        'can_retry': False
                    })
                    continue

                # 3) Check if user can even access this level
                if not current_user.designation.can_skip_level(exam.level.level_number) and exam.level.level_number > current_level:
                    logging.info(f"User {current_user.id} cannot access Level {exam.level.level_number}")
                    continue

                exam_data = {
                    'exam_id': exam.id,
                    'title': exam.title,
                    'duration': exam.duration,
                    'category': exam.category.name if exam.category else 'General',
                    'level': exam.level.level_number,
                    'status': 'Course not completed',
                    'retry_date': None,
                    'can_retry': False
                }

                # 4) Check course prerequisite
                if exam.course_id not in completed_courses:
                    exam_data['status'] = 'Prerequisite Course Not Completed'
                    processed_exams.append(exam_data)
                    continue

                # 5) Determine exam attempt status
                score_record = exam_scores.get(exam.id)
                if not score_record:
                    # Not attempted => Start Exam
                    exam_data.update({
                        'status': 'Start Exam',
                        'can_retry': True
                    })
                else:
                    # Already attempted
                    if score_record.score >= 56:
                        exam_data['status'] = 'Completed'
                    else:
                        retry_available_date = score_record.created_at + retry_period
                        exam_data['retry_date'] = retry_available_date.date().isoformat()
                        exam_data.update({
                            'status': ('Retry available' if now >= retry_available_date
                                       else f'Retry after {retry_available_date.strftime("%Y-%m-%d")}'),
                            'can_retry': now >= retry_available_date
                        })

                # 6) Update level progression
                update_level_progression(current_user.id, exam.id)
                processed_exams.append(exam_data)
        else:
            logging.info("No exams found in database")

        # ------------------------------------------------
        # 2) Add Special Exam Papers (Paper 1 & Paper 2)
        # ------------------------------------------------
        from models import SpecialExamRecord
        record = SpecialExamRecord.query.filter_by(user_id=user_id).first()

        def can_attempt_again(completed_at):
            if not completed_at:
                return True
            next_allowed = completed_at + retry_period
            return now >= next_allowed

        # ---------- Paper 1 ----------
        if record:
            if record.paper1_passed:
                p1_status = "Completed"
            elif record.paper1_completed_at:
                p1_status = (
                    "Retry available"
                    if can_attempt_again(record.paper1_completed_at)
                    else f"Retry after {(record.paper1_completed_at + retry_period).strftime('%Y-%m-%d')}"
                )
            else:
                p1_status = "Start Exam"
        else:
            p1_status = "Start Exam"

        paper1_data = {
            'exam_id': 9991,  # Arbitrary ID
            'title': 'Special Exam Paper 1',
            'category': 'Special',
            'duration': 45,
            'status': p1_status,
            'retry_date': (
                (record.paper1_completed_at + retry_period).date().isoformat()
                if record and record.paper1_completed_at and not can_attempt_again(record.paper1_completed_at)
                else None
            ),
            'can_retry': p1_status in ["Start Exam", "Retry available"],
            # Provide the route to use in the template
            'route': 'special_exams_routes.exam_paper1'
        }

        # ---------- Paper 2 ----------
        if record:
            if record.paper1_passed:
                p2_status = "Locked (Paper 1 passed)"
            else:
                if record.paper2_passed:
                    p2_status = "Completed"
                elif record.paper2_completed_at:
                    p2_status = (
                        "Retry available"
                        if can_attempt_again(record.paper2_completed_at)
                        else f"Retry after {(record.paper2_completed_at + retry_period).strftime('%Y-%m-%d')}"
                    )
                else:
                    p2_status = "Start Exam"
        else:
            p2_status = "Locked (Attempt Paper 1 first)"

        paper2_data = {
            'exam_id': 9992,
            'title': 'Special Exam Paper 2',
            'category': 'Special',
            'duration': 45,
            'status': p2_status,
            'retry_date': (
                (record.paper2_completed_at + retry_period).date().isoformat()
                if record and record.paper2_completed_at and not can_attempt_again(record.paper2_completed_at)
                else None
            ),
            'can_retry': p2_status in ["Start Exam", "Retry available"],
            'route': 'special_exams_routes.exam_paper2'
        }

        special_exams = [paper1_data, paper2_data]

        # ------------------------------------------------
        # 3) Render the exam list template
        # ------------------------------------------------
        return render_template(
            'exam_list.html',
            exams=processed_exams,
            special_exams=special_exams,
            message=f"Found {len(processed_exams)} regular exams"
        )

    except SQLAlchemyError as e:
        logging.critical(f"Database error in list_exams: {str(e)}")
        db.session.rollback()
        return render_template('500.html', error="Exam data unavailable"), 500
    except TemplateNotFound as e:
        logging.error(f"Missing template: {str(e)}")
        return "System error: Display template missing", 500
    except Exception as e:
        logging.error(f"Unexpected error in list_exams: {str(e)}")
        return render_template('500.html', error="Failed to load exam list"), 500

def update_level_progression(user_id, exam_id):
    """
    Update the user's level progression after an exam attempt.
    Handles:
      - Score and attempt tracking
      - Status updates for Level-Area completion
      - Level advancement logic
      - Designation-based skipping
    """
    try:
        exam = Exam.query.get(exam_id)
        user = User.query.get(user_id)

        if not exam or not user:
            logging.error("Invalid exam or user data.")
            return

        # Check if the user has already passed this area
        existing_progress = UserLevelProgress.query.filter_by(
            user_id=user_id,
            level_id=exam.level_id,
            area_id=exam.area_id
        ).first()

        # Fetch the latest score for this exam
        latest_score = UserScore.query.filter_by(
            user_id=user_id,
            exam_id=exam.id
        ).order_by(UserScore.created_at.desc()).first()

        # If no score found, user hasn't attempted this exam
        if not latest_score:
            return

        # Create progress if it doesn't exist
        if not existing_progress:
            existing_progress = UserLevelProgress(
                user_id=user_id,
                level_id=exam.level_id,
                area_id=exam.area_id,
                attempts=0,
                best_score=0,
                status='pending'
            )
            db.session.add(existing_progress)

        # Update attempt count and best score
        existing_progress.attempts += 1
        existing_progress.best_score = max(existing_progress.best_score or 0, latest_score.score)

        # Check if the exam is passed
        if latest_score.score >= 56:
            existing_progress.status = 'completed'

        db.session.commit()

        # Check for level completion
        if check_level_completion(user_id, exam.level_id):
            next_level = Level.query.filter_by(level_number=exam.level.level_number + 1).first()
            if next_level:
                user.current_level = next_level.level_number
                db.session.commit()
                flash(f"Congratulations! You have unlocked Level {next_level.level_number}", "success")

    except SQLAlchemyError as e:
        logging.error(f"Database error in update_level_progression: {str(e)}")
        db.session.rollback()
    except Exception as e:
        logging.error(f"Unexpected error in update_level_progression: {str(e)}")


def check_level_completion(user_id, level_id):
    """
    Check if the user has completed all areas in the given level.
    Takes into account:
    - Designation-based skips
    - Completion status in UserLevelProgress
    """
    # Fetch all required areas for the given level
    required_areas = LevelArea.query.filter_by(level_id=level_id).all()

    # Iterate through each required area to check completion status
    for area in required_areas:
        # Check if the user is allowed to skip this exam/area
        if current_user.can_skip_exam(area.required_exam_id):
            continue

        # Get progress record for this area
        progress = UserLevelProgress.query.filter_by(
            user_id=user_id,
            level_id=level_id,
            area_id=area.category_id
        ).first()

        # If no progress or status is not completed, return False
        if not progress or progress.status != 'completed':
            return False

    # All areas completed or skipped, return True
    return True

# -------------------------------
# Route to Submit an Exam
# -------------------------------

@exams_routes.route('/<int:exam_id>/submit', methods=['POST'])
@login_required
def submit_exam(exam_id):
    """
    Handle exam submission with strict pass/fail enforcement
    """
    try:
        # Fetch exam and validate
        exam = Exam.query.options(db.joinedload(Exam.category)).get_or_404(exam_id)
        submitted_answers = request.form.to_dict(flat=True)
        
        # Check existing score first
        existing_score = UserScore.query.filter_by(
            user_id=current_user.id, 
            exam_id=exam_id
        ).first()
        
        # Prevent retakes if already passed
        if existing_score and existing_score.score >= 56:
            flash('You have already passed this exam', 'info')
            return redirect(url_for('exams_routes.list_exams'))

        # Validate time parameters
        start_time = datetime.fromisoformat(submitted_answers.get('start_time'))
        end_time = datetime.now(timezone.utc)
        time_taken = (end_time - start_time).total_seconds() / 60
        
        if time_taken > exam.duration:
            flash('Exam duration exceeded', 'danger')
            return redirect(url_for('exams_routes.start_exam', exam_id=exam_id))

        # Get served questions from form
        served_question_ids = [int(qid) for qid in submitted_answers.get('served_questions', '').split(',') if qid]
        questions = Question.query.filter(Question.id.in_(served_question_ids)).all()
        
        if not questions:
            flash('No valid questions found for scoring', 'danger')
            return redirect(url_for('exams_routes.start_exam', exam_id=exam_id))

        # Calculate score
        max_score = len(questions)
        score_per_question = 100 / max_score if max_score > 0 else 0
        total_percentage = 0.0

        for question in questions:
            user_answer = submitted_answers.get(f'answers[{question.id}]', '').strip(' "\'').lower()
            correct_answer = question.correct_answer.strip(' "\'').lower()
            
            if user_answer == correct_answer:
                total_percentage += score_per_question

        # Round to 2 decimal places for display
        final_percentage = round(total_percentage, 2)
        grade = calculate_grade(final_percentage)
        passed = final_percentage >= 56

        # Handle score recording
        if existing_score:
            # Only update if previous score was failing and this is better
            if existing_score.score < 56 and final_percentage > existing_score.score:
                existing_score.score = final_percentage
        else:
            new_score = UserScore(
                user_id=current_user.id,
                exam_id=exam_id,
                category_id=exam.category_id,
                score=final_percentage,
                created_at=end_time  # Always use submission time for new attempts
            )
            db.session.add(new_score)

        db.session.commit()

        # Determine retry info
        retry_info = {}
        if not passed:
            attempt_date = existing_score.created_at if existing_score else end_time
            retry_date = (attempt_date + timedelta(days=30)).strftime('%Y-%m-%d')
            retry_info['retry_date'] = retry_date

        flash('Exam results processed', 'success' if passed else 'warning')
        return render_template(
            'exam_results.html',
            exam=exam,
            percentage=final_percentage,
            grade=grade,
            **retry_info
        )

    except SQLAlchemyError as e:
        db.session.rollback()
        logging.error(f"Database error: {str(e)}")
        flash('Failed to save results', 'danger')
        return redirect(url_for('exams_routes.start_exam', exam_id=exam_id))

    except ValueError as e:
        logging.error(f"Invalid data format: {str(e)}")
        flash('Invalid exam data', 'danger')
        return redirect(url_for('exams_routes.list_exams'))

    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        flash('Exam processing failed', 'danger')
        return redirect(url_for('exams_routes.list_exams'))

def calculate_grade(percentage):
    """Grade calculation with precise 56% pass mark"""
    if percentage >= 86:
        return 'A'
    elif percentage >= 76:
        return 'A-'
    elif percentage >= 66:
        return 'B+'
    elif percentage >= 56:  # Pass mark
        return 'B-'
    elif percentage >= 46:
        return 'C+'
    elif percentage >= 35:
        return 'C-'
    else:
        return 'F'

@exams_routes.route('/results', methods=['GET'])
@login_required
def exam_results():
    """
    Display user's exam results including both normal and special exams
    """
    try:
        # Normal exams
        results = UserScore.query.options(
            db.joinedload(UserScore.exam),
            db.joinedload(UserScore.category)
        ).filter_by(user_id=current_user.id).all()

        # Special exams
        special_record = db.session.execute(
            db.select(SpecialExamRecord).filter_by(user_id=current_user.id)
        ).scalar_one_or_none()

        combined_results = []

        # Add normal exams to results
        combined_results += [{
            'exam_title': r.exam.title if r.exam else 'Deleted Exam',
            'category': r.category.name if r.category else 'Uncategorized',
            'score': r.score,
            'date': r.created_at.strftime('%Y-%m-%d'),
            'passed': r.score >= 56
        } for r in results]

        # Add special exams if available
        if special_record:
            if special_record.paper1_score is not None:
                combined_results.append({
                    'exam_title': 'Special Exam Paper 1',
                    'category': 'Special',
                    'score': special_record.paper1_score,
                    'date': special_record.paper1_completed_at.strftime('%Y-%m-%d') if special_record.paper1_completed_at else 'N/A',
                    'passed': special_record.paper1_passed
                })
            if special_record.paper2_score is not None:
                combined_results.append({
                    'exam_title': 'Special Exam Paper 2',
                    'category': 'Special',
                    'score': special_record.paper2_score,
                    'date': special_record.paper2_completed_at.strftime('%Y-%m-%d') if special_record.paper2_completed_at else 'N/A',
                    'passed': special_record.paper2_passed
                })

        return render_template(
            'exam_results.html',
            results=combined_results,
            passed_count=sum(1 for r in combined_results if r['passed']),
            total_attempts=len(combined_results)
        )

    except Exception as e:
        logging.error(f"Error loading results: {str(e)}")
        flash('Could not load exam results', 'danger')
        return redirect(url_for('exams_routes.list_exams'))

# -------------------------------
# Start Exam Route
# -------------------------------
@exams_routes.route('/<int:exam_id>/start', methods=['GET'])
@login_required
def start_exam(exam_id):
    """
    Render exam page with HTML template
    """
    try:
        # Check existing passing score
        if UserScore.query.filter_by(user_id=current_user.id, exam_id=exam_id, score__gte=56).first():
            flash('You have already passed this exam', 'info')
            return redirect(url_for('exams_routes.list_exams'))

        exam = Exam.query.get_or_404(exam_id)

        # Check course prerequisite
        completed = any(p.study_material_id == exam.course_id 
                      for p in current_user.study_progress 
                      if p.progress_percentage >= 100)
        if not completed:
            flash('Complete the prerequisite course first', 'danger')
            return redirect(url_for('exams_routes.list_exams'))

        # Get and validate questions
        questions = Question.query.filter_by(exam_id=exam.id).all()
        if not questions:
            flash('This exam currently has no questions', 'warning')
            return redirect(url_for('exams_routes.list_exams'))

        # Select random questions
        selected = random.sample(questions, min(len(questions), 10))
        question_ids = [str(q.id) for q in selected]

        return render_template(
            'exam_page.html',
            exam=exam,
            questions=[{
                'id': q.id,
                'text': q.question_text,
                'choices': q.choices.split(',') if isinstance(q.choices, str) else q.choices
            } for q in selected],
            start_time=datetime.now(timezone.utc).isoformat(),
            served_questions=','.join(question_ids)
        )

    except Exception as e:
        logging.error(f"Exam start error: {str(e)}")
        flash('Could not start exam', 'danger')
        return redirect(url_for('exams_routes.list_exams'))

# -------------------------------
# Debug Start Exam Route
# -------------------------------
@exams_routes.route('/debug/start/<int:exam_id>', methods=['GET'])
def debug_start_exam(exam_id):
    """
    Debug route to test the start_exam functionality without login required.
    """
    try:
        # Fetch the exam
        exam = Exam.query.get_or_404(exam_id)
        questions = Question.query.filter_by(exam_id=exam.id).all()

        debug_data = {
            'exam': {
                'id': exam.id,
                'title': exam.title,
                'duration': exam.duration
            },
            'questions': []
        }

        for question in questions:
            # Parse choices string into a list
            if isinstance(question.choices, str):
                choices_list = question.choices.split(',')
                choices_list = [choice.strip() for choice in choices_list]
            else:
                choices_list = question.choices

            debug_data['questions'].append({
                'id': question.id,
                'text': question.question_text,
                'choices': choices_list
            })

        return jsonify(debug_data)

    except SQLAlchemyError as e:
        logging.error(f"Database error in debug_start_exam for Exam ID {exam_id}: {e}")
        return jsonify({'error': 'Database error occurred'}), 500

    except Exception as e:
        logging.error(f"Unexpected error in debug_start_exam for Exam ID {exam_id}: {e}")
        return jsonify({'error': 'Unexpected error occurred'}), 500

# -------------------------------
# Route to Fetch Dropdown Data for Exam Creation
# -------------------------------
@exams_routes.route('/get_exam_dropdowns', methods=['GET'])
@login_required
def get_exam_dropdowns():
    """
    Fetch Levels, Categories, Designation Levels, and Courses for Exam Creation.
    This route is called via AJAX to populate the dropdowns dynamically.
    """
    try:
        # Fetch Levels, Categories, Designation Levels, and Courses
        levels = Level.query.order_by(Level.level_number.asc()).all()
        categories = Category.query.order_by(Category.id.asc()).all()
        designations = Designation.query.order_by(Designation.id.asc()).all()
        courses = StudyMaterial.query.order_by(StudyMaterial.id.asc()).all()

        # Format the data as JSON
        dropdown_data = {
            "levels": [{"id": level.id, "level_number": level.level_number} for level in levels],
            "categories": [{"id": category.id, "name": category.name} for category in categories],
            "designations": [{"id": des.id, "title": des.title} for des in designations],
            "courses": [{"id": course.id, "title": course.title} for course in courses]
        }

        return jsonify(dropdown_data), 200
    except Exception as e:
        logging.error(f"Error fetching dropdown data for exams: {e}")
        return jsonify({'error': 'Failed to load dropdowns.'}), 500

