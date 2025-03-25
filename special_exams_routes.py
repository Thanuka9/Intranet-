from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from datetime import datetime, timezone, timedelta
import logging
from extensions import db
from models import SpecialExamRecord

special_exams_routes = Blueprint('special_exams_routes', __name__, url_prefix='/special_exams')

def can_attempt_again(completed_at):
    if not completed_at:
        return True
    next_allowed = completed_at + timedelta(days=30)
    return datetime.now(timezone.utc) >= next_allowed

@special_exams_routes.route('/paper1', methods=['GET'])
@login_required
def exam_paper1():
    try:
        record = SpecialExamRecord.query.filter_by(user_id=current_user.id).first()
        if record and record.paper1_passed:
            flash("You have already passed Paper 1.", "info")
            return redirect(url_for('exams_routes.list_exams'))

        if record and record.paper1_completed_at and not can_attempt_again(record.paper1_completed_at):
            retry_date = (record.paper1_completed_at + timedelta(days=30)).strftime('%Y-%m-%d')
            flash(f"You can re-attempt Paper 1 after {retry_date}.", "info")
            return redirect(url_for('exams_routes.list_exams'))

        start_time = datetime.now(timezone.utc).isoformat()
        return render_template('exam_paper1.html', start_time=start_time)

    except Exception as e:
        logging.error(f"Error displaying Paper 1: {e}")
        flash("Could not load Paper 1.", "danger")
        return redirect(url_for('exams_routes.list_exams'))

@special_exams_routes.route('/paper1_submit', methods=['POST'])
@login_required
def submit_paper1():
    try:
        data = request.form.to_dict()
        start_time_str = data.get('start_time')
        end_time = datetime.now(timezone.utc)

        if not start_time_str:
            flash("Invalid form data.", "danger")
            return redirect(url_for('exams_routes.list_exams'))

        start_time = datetime.fromisoformat(start_time_str)
        time_spent = (end_time - start_time).total_seconds()

        correct_answers = {
            '1': 'd', '2': 'a', '3': 'c', '4': 'd', '5': 'b',
            '6': 'c', '7': 'c', '8': 'b', '9': 'd', '10': 'a',
            '11': 'c', '12': 'c', '13': 'a', '14': 'c', '15': 'b',
            '16': 'd', '17': 'c', '18': 'b', '19': 'c', '20': 'd',
            '21': 'b', '22': 'a', '23': 'b', '24': 'c', '25': 'c',
            '26': 'b', '27': 'c', '28': 'a', '29': 'b', '30': 'd',
            '31': 'a', '32': 'b', '33': 'd', '34': 'a', '35': 'b',
            '36': 'a', '37': 'c', '38': 'b', '39': 'd', '40': 'a'
        }

        total_questions = len(correct_answers)
        marks_per_question = 2.5
        user_score = 0
        for q_num, correct_ans in correct_answers.items():
            user_ans = data.get(f'answers[{q_num}]', '').lower()
            if user_ans == correct_ans:
                user_score += marks_per_question

        final_percentage = round(user_score, 2)
        passed = final_percentage >= 56

        record = SpecialExamRecord.query.filter_by(user_id=current_user.id).first()
        if not record:
            record = SpecialExamRecord(user_id=current_user.id)
            db.session.add(record)

        record.paper1_score = final_percentage
        record.paper1_passed = passed
        record.paper1_time_spent = int(time_spent)
        record.paper1_completed_at = end_time
        db.session.commit()

        return render_template(
            'exam_results.html',
            exam_title="Special Exam Paper 1",
            percentage=final_percentage,
            time_taken=int(time_spent // 60),
            correct=int(user_score / marks_per_question),
            total=total_questions,
            passed=passed
        )

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error submitting Paper 1: {e}")
        flash("Error processing Paper 1 submission.", "danger")
        return redirect(url_for('exams_routes.list_exams'))

@special_exams_routes.route('/paper2', methods=['GET'])
@login_required
def exam_paper2():
    try:
        record = SpecialExamRecord.query.filter_by(user_id=current_user.id).first()
        if not record:
            flash("Please attempt Paper 1 first.", "info")
            return redirect(url_for('exams_routes.list_exams'))

        if record.paper1_passed:
            flash("Paper 2 is locked because you passed Paper 1.", "info")
            return redirect(url_for('exams_routes.list_exams'))

        if record.paper2_completed_at and not can_attempt_again(record.paper2_completed_at):
            retry_date = (record.paper2_completed_at + timedelta(days=30)).strftime('%Y-%m-%d')
            flash(f"You can re-attempt Paper 2 after {retry_date}.", "info")
            return redirect(url_for('exams_routes.list_exams'))

        start_time = datetime.now(timezone.utc).isoformat()
        return render_template('exam_paper2.html', start_time=start_time)

    except Exception as e:
        logging.error(f"Error displaying Paper 2: {e}")
        flash("Could not load Paper 2.", "danger")
        return redirect(url_for('exams_routes.list_exams'))

@special_exams_routes.route('/paper2_submit', methods=['POST'])
@login_required
def submit_paper2():
    try:
        data = request.form.to_dict()
        start_time_str = data.get('start_time')
        end_time = datetime.now(timezone.utc)

        if not start_time_str:
            flash("Invalid form data.", "danger")
            return redirect(url_for('exams_routes.list_exams'))

        start_time = datetime.fromisoformat(start_time_str)
        time_spent = (end_time - start_time).total_seconds()

        correct_answers = {
            '1': 'c', '2': 'd', '3': 'a', '4': 'c', '5': 'a',
            '6': 'b', '7': 'b', '8': 'c', '9': 'd', '10': 'd',
            '11': 'b', '12': 'c', '13': 'a', '14': 'd', '15': 'a',
            '16': 'b', '17': 'c', '18': 'b', '19': 'b', '20': 'b',
            '21': 'b', '22': 'c', '23': 'b', '24': 'd', '25': 'c',
            '26': 'b', '27': 'b', '28': 'c', '29': 'b', '30': 'c',
            '31': 'a', '32': 'b', '33': 'c', '34': 'a', '35': 'b',
            '36': 'a', '37': 'd', '38': 'b', '39': 'b', '40': 'a'
        }

        total_questions = len(correct_answers)
        marks_per_question = 2.5
        user_score = 0
        for q_num, correct_ans in correct_answers.items():
            user_ans = data.get(f'answers[{q_num}]', '').lower()
            if user_ans == correct_ans:
                user_score += marks_per_question

        final_percentage = round(user_score, 2)
        passed = final_percentage >= 56

        record = SpecialExamRecord.query.filter_by(user_id=current_user.id).first()
        if not record:
            flash("Paper 1 not attempted; cannot submit Paper 2.", "danger")
            return redirect(url_for('exams_routes.list_exams'))

        if record.paper1_passed:
            flash("Paper 2 is locked because you passed Paper 1.", "info")
            return redirect(url_for('exams_routes.list_exams'))

        if record.paper2_completed_at and not can_attempt_again(record.paper2_completed_at):
            retry_date = (record.paper2_completed_at + timedelta(days=30)).strftime('%Y-%m-%d')
            flash(f"You can re-attempt Paper 2 after {retry_date}.", "info")
            return redirect(url_for('exams_routes.list_exams'))

        record.paper2_score = final_percentage
        record.paper2_passed = passed
        record.paper2_time_spent = int(time_spent)
        record.paper2_completed_at = end_time
        db.session.commit()

        return render_template(
            'exam_results.html',
            exam_title="Special Exam Paper 2",
            percentage=final_percentage,
            time_taken=int(time_spent // 60),
            correct=int(user_score / marks_per_question),
            total=total_questions,
            passed=passed
        )

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error submitting Paper 2: {e}")
        flash("Error processing Paper 2 submission.", "danger")
        return redirect(url_for('exams_routes.list_exams'))
