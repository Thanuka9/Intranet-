from flask import Blueprint, request, jsonify, render_template
from extensions import db
from models import Exam, Question, UserScore, User, Category, Level, Area, UserLevelProgress, Designation, LevelArea, StudyMaterial, ExamAccessRequest, IncorrectAnswer
from flask_login import current_user, login_required
from datetime import datetime, timezone, timedelta
from jinja2 import TemplateNotFound
import random
import requests
from models import SpecialExamRecord
import logging
from sqlalchemy.exc import SQLAlchemyError
from flask import session
from flask import redirect, flash, url_for
from utils.progress_utils import has_finished_study

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
            # Instead of an HTTP round‐trip, query Levels, Categories, and Designations directly:
            levels = Level.query.order_by(Level.level_number.asc()).all()
            categories = Category.query.order_by(Category.id.asc()).all()
            designations = Designation.query.order_by(Designation.id.asc()).all()

            dropdown_data = {
                'levels': [
                    {'id': lvl.id, 'level_number': lvl.level_number}
                    for lvl in levels
                ],
                'categories': [
                    {'id': cat.id, 'name': cat.name}
                    for cat in categories
                ],
                'designations': [
                    {'id': des.id, 'title': des.title}
                    for des in designations
                ]
            }

            # Fetch Areas and Courses from the database as before
            areas = Area.query.all()
            courses = StudyMaterial.query.all()

            return render_template(
                'upload_exam.html',
                levels             = dropdown_data['levels'],
                categories         = dropdown_data['categories'],
                areas              = [{'id': a.id, 'name': a.name} for a in areas],
                designation_levels = dropdown_data['designations'],
                courses            = [{"id": c.id, "title": c.title} for c in courses]
            )

        except Exception as e:
            logging.error(f"Error rendering exam creation form: {e}")
            return render_template('500.html', error="Failed to load the exam creation form."), 500

    # -------------------------------
    # POST Request: Handle Exam Creation
    # -------------------------------
    if request.method == 'POST':
        try:
            form = request.form
            # 1) Extract and validate all exam‐level fields
            title       = form.get('title', '').strip()
            duration    = form.get('duration', '').strip()
            level_id    = form.get('level_id', '').strip()
            category_id = form.get('category_id', '').strip()
            area_id     = form.get('area_id', '').strip()
            course_id   = form.get('course_id', '').strip()
            min_desig   = form.get('minimum_designation_level', '').strip()

            if not all([title, duration, level_id, category_id, area_id, course_id, min_desig]):
                return jsonify({'error': 'All fields are required'}), 400
            if not duration.isdigit() or int(duration) <= 0:
                return jsonify({'error': 'Duration must be a positive integer'}), 400

            # 2) Load your FK objects
            level       = Level.query.get(level_id)
            category    = Category.query.get(category_id)
            area        = Area.query.get(area_id)
            course      = StudyMaterial.query.get(course_id)
            designation = Designation.query.get(min_desig)

            for obj, name in [
                (level, 'Level'),
                (category, 'Category'),
                (area, 'Area'),
                (course, 'Course'),
                (designation, 'Designation')
            ]:
                if not obj:
                    return jsonify({'error': f'Selected {name} does not exist'}), 400

            # 3) Create & commit the Exam
            exam = Exam(
                title                     = title,
                duration                  = int(duration),
                level_id                  = level.id,
                area_id                   = area.id,
                category_id               = category.id,
                course_id                 = course.id,
                created_by                = current_user.id,
                minimum_level             = level.level_number,
                minimum_designation_level = designation.id
            )
            db.session.add(exam)
            db.session.commit()

            # 4) Return the URL where the front-end must POST the questions
            add_q_url = url_for('exams_routes.add_questions', exam_id=exam.id)

            return jsonify({
                'message': 'Exam created successfully',
                'exam_id': exam.id,
                'add_questions_url': add_q_url
            }), 201

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
    Add questions to an existing exam, allowing correct answers as text,
    numeric index ("2" → second choice), or letter ("B" → second choice).
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

        # Parse the form data into a dictionary of question entries
        data = request.form.to_dict(flat=False)
        questions = {}
        errors = []
        questions_to_add = []

        # Group fields by question index
        for key, value in data.items():
            if key.startswith("questions["):
                parts = key.split('][')
                question_index = parts[0].split('[')[1]
                field = parts[1].rstrip(']')
                questions.setdefault(question_index, {})[field] = value[0].strip()

        # Process and validate each question
        for question_index, qdata in questions.items():
            try:
                question_text      = qdata.get('question_text','').strip()
                choices_raw        = qdata.get('choices','').strip(' ,')
                raw_answer         = qdata.get('correct_answer','').strip(' "\'')
                category_id        = qdata.get('category_id','').strip()

                # Required fields check
                if not all([question_text, choices_raw, raw_answer, category_id]):
                    errors.append(f"Question {question_index}: All fields are required")
                    continue

                # Split and clean choice strings
                choices_list = [c.strip() for c in choices_raw.split(',') if c.strip()]
                if len(choices_list) < 2:
                    errors.append(f"Question {question_index}: At least 2 choices required")
                    continue

                # Duplicate check (case-insensitive)
                if len(choices_list) != len({c.lower() for c in choices_list}):
                    errors.append(f"Question {question_index}: Duplicate choices found (case-insensitive)")
                    continue

                # Normalize correct answer input
                corr = raw_answer.upper()
                # Numeric index: "2" → second choice
                if corr.isdigit():
                    idx = int(corr) - 1
                    if 0 <= idx < len(choices_list):
                        corr = choices_list[idx]
                    else:
                        errors.append(f"Question {question_index}: Answer index {raw_answer} out of range")
                        continue
                # Letter index: "B" → second choice
                elif len(corr) == 1 and 'A' <= corr <= 'Z':
                    idx = ord(corr) - ord('A')
                    if 0 <= idx < len(choices_list):
                        corr = choices_list[idx]

                # Final membership check
                if corr not in choices_list:
                    errors.append(
                        f"Question {question_index}: Correct answer must match one of the choices. "
                        f"Got '{raw_answer}', Choices: {choices_list}"
                    )
                    continue

                # Validate category exists
                if not Category.query.get(category_id):
                    errors.append(f"Question {question_index}: Invalid category ID")
                    continue

                # Create Question model instance
                q = Question(
                    exam_id        = exam_id,
                    question_text  = question_text,
                    choices        = ','.join(choices_list),
                    correct_answer = corr,
                    category_id    = int(category_id)
                )
                questions_to_add.append(q)

            except Exception as qe:
                logging.error(f"Error processing question {question_index}: {qe}")
                errors.append(f"Question {question_index}: Invalid input format")

        # Bulk insert valid questions
        if questions_to_add:
            try:
                db.session.bulk_save_objects(questions_to_add)
                db.session.commit()
                logging.info(f"Added {len(questions_to_add)} questions to Exam {exam_id}")
            except Exception as db_err:
                db.session.rollback()
                logging.error(f"Database error saving questions: {db_err}")
                errors.append("Failed to save questions due to database error")

        # Return response
        if errors:
            return jsonify({
                'message':       f"Processed with {len(errors)} errors",
                'success_count': len(questions_to_add),
                'errors':        errors
            }), 207

        return jsonify({
            'message': f"Successfully added {len(questions_to_add)} questions",
            'exam_id': exam_id
        }), 201

    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        db.session.rollback()
        return jsonify({'error': 'Internal server error'}), 500

    
# -------------------------------
# List Exams
# -------------------------------
@exams_routes.route('/list', methods=['GET'])
@login_required
def list_exams():
    """
    Build the exam dashboard for the current user.
    """
    try:
        user_id       = current_user.id
        now           = datetime.utcnow()
        retry_period  = timedelta(days=30)
        current_level = current_user.get_current_level()

        # ── Base queries ─────────────────────────────────────────────
        exams = (
            Exam.query
                .options(
                    db.joinedload(Exam.level),
                    db.joinedload(Exam.area),
                    db.joinedload(Exam.category)
                )
                .order_by(Exam.level_id.asc(), Exam.title.asc())
                .all()
        )
        exam_scores = {
            s.exam_id: s
            for s in UserScore.query
                                .filter_by(user_id=user_id)
                                .order_by(UserScore.created_at.desc())
                                .all()
        }

        processed_exams = []

        # ── Main loop for regular exams ──────────────────────────────
        for exam in exams:
            score_record   = exam_scores.get(exam.id)
            access_request = (
                ExamAccessRequest.query
                .filter_by(user_id=user_id, exam_id=exam.id)
                .order_by(ExamAccessRequest.requested_at.desc())
                .first()
            )

            study_complete = has_finished_study(user_id, exam.level_id, exam.area_id)
            can_skip       = exam.is_skippable(current_user)
            level_allowed  = (
                current_user.designation.can_skip_level(exam.level.level_number)
                or exam.level.level_number <= current_level
            )
            if not level_allowed:
                continue  # hidden by level gating

            # base payload
            exam_data = {
                'exam_id'     : exam.id,
                'title'       : exam.title,
                'duration'    : exam.duration,
                'category'    : exam.category.name if exam.category else 'General',
                'level'       : exam.level.level_number,
                'retry_date'  : None,
                'can_retry'   : False,
                'attempts'    : score_record.attempts if score_record else 0,
                'status'      : '',
                'can_request' : False,
                'route'       : 'exams_routes.start_exam'
            }

            # 1) Study requirement
            if not study_complete:
                if can_skip and not score_record:
                    exam_data['status'] = 'Skipped (optional)'
                else:
                    exam_data['status'] = 'Study Material Not Completed'
                    processed_exams.append(exam_data)
                    continue

            # 2) Access control on first attempt
            if not score_record:
                if (
                    not access_request
                    or access_request.status != 'approved'
                    or access_request.used
                ):
                    exam_data['status'] = 'Access Required'
                    if (
                        not access_request
                        or access_request.status == 'rejected'
                        or access_request.used
                    ):
                        exam_data['can_request'] = True
                    processed_exams.append(exam_data)
                    continue

            # 3) Score & retry logic
            if not score_record:
                exam_data.update({
                    'status'   : 'Start Exam',
                    'can_retry': True
                })
            else:
                next_try       = score_record.created_at + retry_period
                can_retry_now  = now >= next_try
                exam_data['retry_date'] = next_try.date().isoformat()

                if score_record.score >= 56:
                    # passed: cooldown before re-attempt
                    exam_data.update({
                        'status'   : 'Retry available' if can_retry_now else 'Passed',
                        'can_retry': can_retry_now
                    })
                else:
                    # failed: same cooldown logic
                    exam_data.update({
                        'status'   : 'Retry available' if can_retry_now else 'Failed',
                        'can_retry': can_retry_now
                    })

                # 3b) require new access request when retrying
                if exam_data['status'] == 'Retry available':
                    latest_req = (
                        ExamAccessRequest.query
                        .filter_by(user_id=user_id, exam_id=exam.id, status='approved')
                        .order_by(ExamAccessRequest.requested_at.desc())
                        .first()
                    )
                    if not latest_req or latest_req.used:
                        exam_data['status']     = 'Access Required'
                        exam_data['can_request'] = True
                        exam_data['can_retry']  = False

            # 4) Level promotion badge
            if exam_data['status'] == 'Passed' and check_level_completion(current_user.id, exam.level_id):
                exam_data['status'] = 'Level Completed'

            processed_exams.append(exam_data)

        # ── Special Exam Papers ───────────────────────────────────────
        record = SpecialExamRecord.query.filter_by(user_id=user_id).first()

        def can_attempt_special(completed_at):
            return not completed_at or now >= (completed_at + retry_period)

        # Paper 1
        if record and record.paper1_passed:
            next_try  = record.paper1_completed_at + retry_period
            p1_status = 'Retry available' if now >= next_try else 'Passed'
        elif record:
            if record.paper2_passed:
                p1_status = 'Locked (Paper 2 passed)'
            elif record.paper1_completed_at:
                p1_status = 'Retry available' if can_attempt_special(record.paper1_completed_at) else 'Failed'
            else:
                p1_status = 'Start Exam'
        else:
            p1_status = 'Start Exam'

        paper1_data = {
            'exam_id'    : 9991,
            'title'      : 'Special Exam Paper 1',
            'category'   : 'Special',
            'duration'   : 60,
            'status'     : p1_status,
            'retry_date' : (
                (record.paper1_completed_at + retry_period).date().isoformat()
                if record and record.paper1_completed_at else None
            ),
            'can_retry'  : p1_status in ('Start Exam', 'Retry available'),
            'attempts'   : getattr(record, 'paper1_attempts', 0) if record else 0,
            'route'      : 'special_exams_routes.exam_paper1'
        }

        # Paper 2
        if record and record.paper2_passed:
            next_try  = record.paper2_completed_at + retry_period
            p2_status = 'Retry available' if now >= next_try else 'Passed'
        elif record:
            if record.paper1_passed:
                p2_status = 'Locked (Paper 1 passed)'
            elif record.paper2_completed_at:
                p2_status = 'Retry available' if can_attempt_special(record.paper2_completed_at) else 'Failed'
            else:
                p2_status = 'Start Exam'
        else:
            p2_status = 'Start Exam'

        paper2_data = {
            'exam_id'    : 9992,
            'title'      : 'Special Exam Paper 2',
            'category'   : 'Special',
            'duration'   : 60,
            'status'     : p2_status,
            'retry_date' : (
                (record.paper2_completed_at + retry_period).date().isoformat()
                if record and record.paper2_completed_at else None
            ),
            'can_retry'  : p2_status in ('Start Exam', 'Retry available'),
            'attempts'   : getattr(record, 'paper2_attempts', 0) if record else 0,
            'route'      : 'special_exams_routes.exam_paper2'
        }

        # enforce access gating on special papers too
        for paper in (paper1_data, paper2_data):
            if paper['status'] in ('Start Exam', 'Retry available'):
                latest_req = (
                    ExamAccessRequest.query
                    .filter_by(user_id=user_id, exam_id=paper['exam_id'], status='approved')
                    .order_by(ExamAccessRequest.requested_at.desc())
                    .first()
                )
                if not latest_req or latest_req.used:
                    paper['status']     = 'Access Required'
                    paper['can_request'] = True
                    paper['can_retry']  = False

        unlocked_level = session.pop("new_level_unlocked", None)
        if current_user.designation_id != 1:
            unlocked_level = None

        return render_template(
            'exam_list.html',
            exams          = processed_exams,
            special_exams  = [paper1_data, paper2_data],
            message        = f"Found {len(processed_exams)} regular exams",
            unlocked_level = unlocked_level
        )

    except SQLAlchemyError as e:
        logging.critical(f"Database error in list_exams: {e}")
        db.session.rollback()
        return render_template('500.html', error="Exam data unavailable"), 500

    except TemplateNotFound as e:
        logging.error(f"Missing template: {e}")
        return "System error: Display template missing", 500

    except Exception as e:
        logging.error(f"Unexpected error in list_exams: {e}")
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
            return

        existing_progress = UserLevelProgress.query.filter_by(
            user_id=user_id,
            level_id=exam.level_id,
            area_id=exam.area_id
        ).first()

        latest_score = UserScore.query.filter_by(
            user_id=user_id,
            exam_id=exam.id
        ).order_by(UserScore.created_at.desc()).first()
        if not latest_score:
            return

        if not existing_progress:
            existing_progress = UserLevelProgress(
                user_id=user_id,
                level_id=exam.level_id,
                category_id=exam.category_id,
                area_id=exam.area_id,
                attempts=0,
                best_score=0,
                status='pending'
            )
            db.session.add(existing_progress)

        existing_progress.attempts += 1
        existing_progress.best_score = max(existing_progress.best_score or 0, latest_score.score)
        if latest_score.score >= 56:
            existing_progress.status = 'completed'

        db.session.commit()

        # If entire level done, advance user
        if check_level_completion(user_id, exam.level_id):
            next_level = Level.query.filter_by(level_number=exam.level.level_number+1).first()
            if next_level:
                user.current_level = next_level.level_number
                db.session.commit()
                flash(f"Congratulations! You have unlocked Level {next_level.level_number}", "success")

    except SQLAlchemyError as e:
        logging.error(f"Database error in update_level_progression: {e}")
        db.session.rollback()
    except Exception as e:
        logging.error(f"Unexpected error in update_level_progression: {e}")


def check_level_completion(user_id, level_id):
    """
    Returns True if the user has met all completion requirements for the given level:
      • 100% study completion for each LevelArea
      • Passing required exams, unless skipped by designation
    """
    try:
        user = User.query.get(user_id)
        if not user:
            return False

        # Only consider the areas tied to this level
        level_areas = LevelArea.query.filter_by(level_id=level_id).all()

        for la in level_areas:
            # 1) enforce 100% study completion
            if not has_finished_study(user_id, level_id, la.area_id):
                return False

            # 2) if an exam is required, enforce pass (unless skipped)
            if la.required_exam_id:
                if user.can_skip_exam(la.required_exam):
                    # designation allows skipping this exam
                    continue

                prog = UserLevelProgress.query.filter_by(
                    user_id=user_id,
                    level_id=level_id,
                    area_id=la.area_id,
                    status='completed'
                ).first()
                if not prog:
                    return False

        return True

    except Exception as e:
        logging.warning(f"Level completion check failed for user {user_id}, level {level_id}: {e}")
        return False

# ------------------------------------------------------------
# helper sits first, so linters see it before it’s used
# ------------------------------------------------------------
def calculate_grade(percentage: float) -> str:
    """Return letter grade; 56 % is the pass mark."""
    if percentage >= 86:
        return "A"
    elif percentage >= 76:
        return "A-"
    elif percentage >= 66:
        return "B+"
    elif percentage >= 56:
        return "B-"
    elif percentage >= 46:
        return "C+"
    elif percentage >= 35:
        return "C-"
    return "F"

# ------------------------------------------------------------
# Route to submit an exam
# ------------------------------------------------------------
@exams_routes.route("/<int:exam_id>/submit", methods=["POST"])
@login_required
def submit_exam(exam_id):
    """
    Score an exam attempt, enforce time + retake rules, and record the result,
    plus log any incorrect answers.
    """
    try:
        # 1) Load exam and form data
        exam      = Exam.query.options(db.joinedload(Exam.category)).get_or_404(exam_id)
        submitted = request.form.to_dict(flat=True)

        # 2) Block if user already passed
        existing = UserScore.query.filter_by(user_id=current_user.id, exam_id=exam_id).first()
        if existing and existing.score >= 56:
            flash("You have already passed this exam.", "info")
            return redirect(url_for("exams_routes.list_exams"))

        # 3) Check duration
        start_time = datetime.fromisoformat(submitted.get("start_time"))
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        end_time = datetime.now(timezone.utc)
        if (end_time - start_time).total_seconds() / 60 > exam.duration:
            flash("Exam duration exceeded.", "danger")
            return redirect(url_for("exams_routes.start_exam", exam_id=exam_id))

        # 4) Collect served questions
        served_ids = [int(qid) for qid in submitted.get("served_questions", "").split(",") if qid]
        questions  = Question.query.filter(Question.id.in_(served_ids)).all()
        if not questions:
            flash("No valid questions found for scoring.", "danger")
            return redirect(url_for("exams_routes.start_exam", exam_id=exam_id))

        # 5) Calculate score
        per_q = 100 / len(questions)
        score = round(sum(
            per_q for q in questions
            if submitted.get(f"answers[{q.id}]", "").strip(" \"'").lower()
               == q.correct_answer.strip(" \"'").lower()
        ), 2)
        passed = score >= 56
        grade  = calculate_grade(score)

        # 6) Save / update UserScore
        if existing:
            if existing.score < 56:
                if score > existing.score:
                    existing.score = score
                existing.attempts += 1
                existing.area_id  = exam.area_id
                existing.level_id = exam.level_id
        else:
            new_score = UserScore(
                user_id     = current_user.id,
                exam_id     = exam_id,
                area_id     = exam.area_id,
                level_id    = exam.level_id,
                category_id = exam.category_id,
                score       = score,
                attempts    = 1,
                created_at  = end_time
            )
            db.session.add(new_score)

        # ─── Record each incorrect answer ─────────────────────────
        # Clear any prior logs for this exam attempt
        IncorrectAnswer.query \
            .filter_by(
                user_id       = current_user.id,
                exam_id       = exam_id,
                special_paper = None
            ) \
            .delete(synchronize_session=False)

        # Insert a row for each wrong question
        for q in questions:
            submitted_ans = submitted.get(f"answers[{q.id}]", "").strip(" \"'").lower()
            correct_ans   = q.correct_answer.strip(" \"'").lower()
            if submitted_ans != correct_ans:
                db.session.add(IncorrectAnswer(
                    user_id        = current_user.id,
                    exam_id        = exam_id,
                    special_paper  = None,
                    question_id    = q.id,
                    user_answer    = submitted_ans,
                    correct_answer = correct_ans,
                    answered_at    = end_time
                ))

        # Commit both score + incorrect‐answer logs in one transaction
        db.session.commit()

        update_level_progression(current_user.id, exam_id)
        
        # 7) Flash result and go to dashboard
        flash(
            f"You scored {score:.2f}% ({grade}) on “{exam.title}”",
            "success" if passed else "warning"
        )
        return redirect(url_for("exams_routes.exam_results"))

    except SQLAlchemyError as e:
        db.session.rollback()
        logging.error(f"DB error in submit_exam: {e}")
        flash("Failed to save results.", "danger")
        return redirect(url_for("exams_routes.start_exam", exam_id=exam_id))

    except ValueError as ve:
        logging.error(f"Bad data in submit_exam: {ve}")
        flash("Invalid exam data.", "danger")
        return redirect(url_for("exams_routes.list_exams"))

    except Exception as ex:
        logging.error(f"Unexpected error in submit_exam: {ex}")
        flash("Exam processing failed.", "danger")
        return redirect(url_for("exams_routes.list_exams"))

# -------------------------------
# Exam Results 
# -------------------------------
@exams_routes.route("/results", methods=["GET"])
@login_required
def exam_results():
    """
    Results dashboard:
        • every regular exam (even if never attempted)
        • special papers 1 & 2
        • latest attempt determines status
    """
    try:
        retry_period = timedelta(days=30)

        # ---------- fetch latest attempt for each regular exam ----------
        latest_scores = {
            s.exam_id: s
            for s in (
                UserScore.query
                .filter_by(user_id=current_user.id)
                .order_by(UserScore.created_at.desc())      # latest first
            )
        }

        # include exam metadata so un‑attempted rows render
        all_exams = (
            Exam.query.options(
                db.joinedload(Exam.category)
            )
            .order_by(Exam.title.asc())
            .all()
        )

        normal_results = []
        for exam in all_exams:
            s = latest_scores.get(exam.id)          # None → never attempted
            if s:
                passed = s.score >= 56
                retry_date = None
                if not passed:
                    retry_date = (
                        (s.created_at + retry_period).strftime("%Y-%m-%d")
                        if s.created_at else None
                    )
                normal_results.append({
                    "exam_title": exam.title,
                    "category"  : exam.category.name if exam.category else "General",
                    "score"     : round(s.score, 2),
                    "attempts"  : s.attempts or 1,
                    "date"      : s.created_at.strftime("%Y-%m-%d") if s.created_at else "Unknown",
                    "passed"    : passed,
                    "retry_date": retry_date or "—"
                })
            else:
                # ---- Never attempted ----
                normal_results.append({
                    "exam_title": exam.title,
                    "category"  : exam.category.name if exam.category else "General",
                    "score"     : "—",
                    "attempts"  : 0,
                    "date"      : "—",
                    "passed"    : False,
                    "retry_date": "—",
                    "not_attempted": True          # flag for template if you want a grey badge
                })

        # ---------- special exam papers (latest‑attempt logic) ----------
        special_results = []
        record = (
            db.session.execute(
                db.select(SpecialExamRecord).filter_by(user_id=current_user.id)
            ).scalar_one_or_none()
        )

        def add_special_row(title, score, passed, completed_at):
            """
            Normalise output for each special paper.
            • If never attempted (completed_at is None) ⇒ Not Attempted.
            • Else show real score / pass / fail and retry date.
            """
            if completed_at:
                retry_date = (
                    (completed_at + retry_period).strftime("%Y-%m-%d")
                    if (not passed and completed_at) else "—"
                )
                special_results.append({
                    "exam_title": title,
                    "category"  : "Special",
                    "score"     : round(score, 2),
                    "attempts"  : 1,
                    "date"      : completed_at.strftime("%Y-%m-%d"),
                    "passed"    : passed,
                    "retry_date": retry_date
                })
            else:   # never taken
                special_results.append({
                    "exam_title": title,
                    "category"  : "Special",
                    "score"     : "—",
                    "attempts"  : 0,
                    "date"      : "—",
                    "passed"    : False,
                    "retry_date": "—"
                })

        if record:
            add_special_row(
                "Special Exam Paper 1",
                record.paper1_score or 0,
                record.paper1_passed,
                record.paper1_completed_at
            )
            add_special_row(
                "Special Exam Paper 2",
                record.paper2_score or 0,
                record.paper2_passed,
                record.paper2_completed_at
            )

        # ---------- combine + render ----------
        all_results  = normal_results + special_results
        passed_count = sum(1 for r in all_results if r["passed"])
        failed_count = sum(
            1 for r in all_results
            if (not r["passed"]) and r["score"] != "—"
        )
        not_attempted_count = sum(1 for r in all_results if r["score"] == "—")

        return render_template(
            "exam_results.html",
            results           = all_results,
            total_attempts    = passed_count + failed_count,   # attempted only
            passed_count      = passed_count,
            failed_count      = failed_count,
            not_attempted_cnt = not_attempted_count            # if you display it
        )

    except Exception as e:
        logging.error(f"Error loading results for user {current_user.id}: {e}")
        flash("Could not load exam results.", "danger")
        return redirect(url_for("exams_routes.list_exams"))

# -------------------------------
# Start Exam
# -------------------------------
@exams_routes.route("/<int:exam_id>/start", methods=["GET"])
@login_required
def start_exam(exam_id):
    """
    Serve the exam page after all eligibility checks.

    • honours ‘skippable’ exams (with ?force=true override)
    • blocks users below the exam’s minimum level
    • enforces a 30‑day cool‑down after each failed attempt
    • prevents retake after a passing score (≥ 56 %)
    • REFUSES start unless study material for this area + level is 100 % complete
    """
    try:
        exam = (
            Exam.query
            .options(db.joinedload(Exam.category))
            .get_or_404(exam_id)
        )
        force    = request.args.get("force", "").lower() == "true"
        now_utc  = datetime.utcnow()

        # ─── Prerequisite Study Check ──────────────────────────────
        if not has_finished_study(current_user.id, exam.level_id, exam.area_id):
            flash("Please finish the study material first.", "danger")
            return redirect(url_for("exams_routes.list_exams"))

        # ─── Access Approval Check ────────────────────────────────
        access_req = (
            ExamAccessRequest.query
            .filter_by(user_id=current_user.id, exam_id=exam_id, status='approved')
            .order_by(ExamAccessRequest.requested_at.desc())
            .first()
        )

        if not access_req:
            flash("You must request access and wait for admin approval before starting this exam.", "warning")
            return redirect(url_for("exams_routes.list_exams"))

        if access_req.used:
            flash("This access has already been used. Please request exam access again.", "info")
            return redirect(url_for("exams_routes.list_exams"))

        # ─── Optional Skip Gate ────────────────────────────────────
        if exam.is_skippable(current_user) and not force:
            flash(
                "You can skip this exam. Click “Take Anyway” if you’d still like to attempt it.",
                "info"
            )
           
        # ─── Level Requirement ─────────────────────────────────────
        if exam.minimum_level and current_user.get_current_level() < exam.minimum_level:
            flash("Your level is not high enough to take this exam yet.", "warning")
            return redirect(url_for("exams_routes.list_exams"))

        # ─── Cooldown Logic ────────────────────────────────────────
        last_score = (
            UserScore.query
            .filter_by(user_id=current_user.id, exam_id=exam_id)
            .order_by(UserScore.created_at.desc())
            .first()
        )
        if last_score:
            if last_score.score >= 56:
                flash("You have already passed this exam.", "info")
                return redirect(url_for("exams_routes.list_exams"))

            next_try = last_score.created_at + timedelta(days=30)
            if now_utc < next_try:
                flash(f"You can retry this exam after {next_try.strftime('%Y‑%m‑%d')}.", "warning")
                return redirect(url_for("exams_routes.list_exams"))

        # ─── Questions Load ────────────────────────────────────────
        questions = Question.query.filter_by(exam_id=exam.id).all()
        if not questions:
            flash("This exam has no questions yet.", "warning")
            return redirect(url_for("exams_routes.list_exams"))

        selected = random.sample(questions, min(len(questions), 20))

        # ─── Mark Access As Used ───────────────────────────────────
        access_req.used = True
        db.session.commit()

        return render_template(
            "exam_page.html",
            exam=exam,
            questions=[
                {
                    "id": q.id,
                    "text": q.question_text,
                    "choices": q.choices.split(","),
                }
                for q in selected
            ],
            start_time=now_utc.isoformat(),
            served_questions=",".join(str(q.id) for q in selected),
        )

    # ─── Generic error fallback ───
    except Exception as err:
        logging.exception(f"Exam start error: {err}")
        flash("Could not start exam.", "danger")
        return redirect(url_for("exams_routes.list_exams"))

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
    Fetch Levels, Categories, Designation Levels, Courses, and Areas for Exam Creation.
    This route is called via AJAX to populate the dropdowns dynamically.
    """
    try:
        # Query each table
        levels       = Level.query.order_by(Level.level_number.asc()).all()
        categories   = Category.query.order_by(Category.id.asc()).all()
        designations = Designation.query.order_by(Designation.id.asc()).all()
        courses      = StudyMaterial.query.order_by(StudyMaterial.id.asc()).all()
        areas        = Area.query.order_by(Area.name.asc()).all()

        # Format into JSON‑serializable lists
        dropdown_data = {
            "levels": [
                {"id": lvl.id, "level_number": lvl.level_number}
                for lvl in levels
            ],
            "categories": [
                {"id": cat.id, "name": cat.name}
                for cat in categories
            ],
            "designations": [
                {"id": des.id, "title": des.title}
                for des in designations
            ],
            "courses": [
                {"id": crs.id, "title": crs.title}
                for crs in courses
            ],
            "areas": [
                {"id": area.id, "name": area.name}
                for area in areas
            ]
        }

        return jsonify(dropdown_data), 200

    except Exception as e:
        logging.error(f"Error fetching dropdown data for exams: {e}")
        return jsonify({'error': 'Failed to load dropdowns.'}), 500


@exams_routes.route('/<int:exam_id>/request_access', methods=['POST'])
@login_required
def request_exam_access(exam_id):
    from models import ExamAccessRequest, Exam, UserScore

    exam = Exam.query.get_or_404(exam_id)

    if not has_finished_study(current_user.id, exam.level_id, exam.area_id):
        flash("Complete the course before requesting access.", "warning")
        return redirect(url_for('exams_routes.list_exams'))

    now = datetime.utcnow()

    # Get score record
    score_record = (
        UserScore.query
        .filter_by(user_id=current_user.id, exam_id=exam_id)
        .order_by(UserScore.created_at.desc())
        .first()
    )

    # Already passed
    if score_record and score_record.score >= 56:
        flash("You already passed this exam.", "info")
        return redirect(url_for('exams_routes.list_exams'))

    # Request history
    recent_requests = (
        ExamAccessRequest.query
        .filter_by(user_id=current_user.id, exam_id=exam_id)
        .order_by(ExamAccessRequest.requested_at.desc())
        .all()
    )

    latest = recent_requests[0] if recent_requests else None
    recent_count = sum(1 for r in recent_requests if r.requested_at > now - timedelta(days=1))

    if latest and latest.status == 'pending':
        flash("Access already requested and is pending approval.", "info")
        return redirect(url_for('exams_routes.list_exams'))

    if latest and latest.status == 'approved' and (not score_record or score_record.score < 56):
        flash("You must re-request access to retry this exam.", "warning")
        # Do not return here, continue to create new request

    if recent_count >= 3:
        flash("Too many requests in the past 24 hours. Try again later.", "warning")
        return redirect(url_for('exams_routes.list_exams'))

    # Submit new access request
    new_request = ExamAccessRequest(
        user_id=current_user.id,
        exam_id=exam_id,
        status='pending',
        requested_at=now
    )
    db.session.add(new_request)
    db.session.commit()

    flash("Access request sent to admin.", "success")
    print(f"[ACCESS] New access request submitted — user_id={current_user.id}, exam_id={exam_id}")

    return redirect(url_for('exams_routes.list_exams'))
