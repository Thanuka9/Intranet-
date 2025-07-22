from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from datetime import datetime, timezone, timedelta
import logging
from extensions import db
from models import SpecialExamRecord, ExamAccessRequest, IncorrectAnswer

special_exams_routes = Blueprint('special_exams_routes', __name__, url_prefix='/special_exams')

def can_attempt_again(completed_at):
    if not completed_at:
        return True
    # Ensure completed_at is timezone-aware UTC
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    next_allowed = completed_at + timedelta(days=30)
    return datetime.now(timezone.utc) >= next_allowed

@special_exams_routes.route('/paper1', methods=['GET'])
@login_required
def exam_paper1():
    try:
        # ── Require an approved, unused access request for Paper 1 ─────────
        ACCESS_ID = 9991
        access_req = (
            ExamAccessRequest.query
            .filter_by(
                user_id   = current_user.id,
                exam_id   = ACCESS_ID,
                status    = 'approved'
            )
            .order_by(ExamAccessRequest.requested_at.desc())
            .first()
        )
        if not access_req:
            flash("You need to request & receive approval before starting this special exam.", "warning")
            return redirect(url_for('exams_routes.list_exams'))
        if access_req.used:
            flash("That approval has already been used. Please request access again.", "info")
            return redirect(url_for('exams_routes.list_exams'))
        # ───────────────────────────────────────────────────────────────

        record = SpecialExamRecord.query.filter_by(user_id=current_user.id).first()

        # Lock out if either paper has already been passed
        if record and (record.paper1_passed or record.paper2_passed):
            flash("You have already passed one of the special papers.", "info")
            return redirect(url_for('exams_routes.list_exams'))

        # Cooldown check for Paper 1
        if record and record.paper1_completed_at and not can_attempt_again(record.paper1_completed_at):
            retry_date = (
                record.paper1_completed_at.replace(tzinfo=timezone.utc) + timedelta(days=30)
            ).strftime('%Y-%m-%d')
            flash(f"You can re-attempt Paper 1 after {retry_date}.", "info")
            return redirect(url_for('exams_routes.list_exams'))

        # OK to start
        start_time = datetime.now(timezone.utc).isoformat()

        # ── Mark this access request as used ─────────────────────────────
        access_req.used = True
        db.session.commit()
        # ───────────────────────────────────────────────────────────────

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
        if not start_time_str:
            flash("Invalid form data.", "danger")
            return redirect(url_for('exams_routes.list_exams'))

        start_time = datetime.fromisoformat(start_time_str)
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        end_time = datetime.now(timezone.utc)
        time_spent = (end_time - start_time).total_seconds()

        correct_answers = {
            '0': 'd',
            '1': 'a',
            '2': 'c',
            '3': 'd',
            '4': 'b',
            '5': 'c',
            '6': 'c',
            '7': 'b',
            '8': 'd',
            '9': 'a',
            '10': 'c',
            '11': 'c',
            '12': 'a',
            '13': 'c',
            '14': 'b',
            '15': 'd',
            '16': 'c',
            '17': 'b',
            '18': 'c',
            '19': 'd',
            '20': 'b',
            '21': 'a',
            '22': 'b',
            '23': 'c',
            '24': 'c',
            '25': 'b',
            '26': 'c',
            '27': 'a',
            '28': 'b',
            '29': 'd',
            '30': 'a',
            '31': 'b',
            '32': 'd',
            '33': 'a',
            '34': 'b',
            '35': 'a',
            '36': 'c',
            '37': 'b',
            '38': 'd',
            '39': 'a'
        }

        marks_per_question = 2.5
        user_score = sum(
            marks_per_question
            for q, ans in correct_answers.items()
            if data.get(f'answers[{q}]', '').lower() == ans
        )
        final_percentage = round(user_score, 2)
        passed = final_percentage >= 70

        record = SpecialExamRecord.query.filter_by(user_id=current_user.id).first()
        if not record:
            record = SpecialExamRecord(user_id=current_user.id)
            db.session.add(record)

        # ─── INCREMENT PAPER 1 ATTEMPTS ────────────────────────────
        record.paper1_attempts = (record.paper1_attempts or 0) + 1
        ACCESS_ID = 9991
        # ─── RECORD EACH WRONG ANSWER ───────────────────────────────
        for q, ans in correct_answers.items():
            raw = data.get(f'answers[{q}]', '').strip()
            if raw.lower() != ans:
                db.session.add(IncorrectAnswer(
                    user_id        = current_user.id,
                    exam_id        = None,
                    special_paper  = 'paper1',
                    question_id    = int(q),
                    user_answer    = raw,
                    correct_answer = ans,
                    answered_at    = end_time
                ))

        # cooldown re-check
        if record.paper1_completed_at and not can_attempt_again(record.paper1_completed_at):
            retry_date = (
                record.paper1_completed_at.replace(tzinfo=timezone.utc)
                + timedelta(days=30)
            ).strftime('%Y-%m-%d')
            flash(f"You can re-attempt Paper 1 after {retry_date}.", "info")
            return redirect(url_for('exams_routes.list_exams'))

        # save results
        record.paper1_score        = final_percentage
        record.paper1_passed       = passed
        record.paper1_time_spent   = int(time_spent)
        record.paper1_completed_at = end_time
        db.session.commit()

        flash(
            f"Special Paper 1 {'passed' if passed else 'completed'} with {final_percentage}%",
            'success' if passed else 'warning'
        )
        return redirect(url_for('exams_routes.exam_results'))

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error submitting Paper 1: {e}")
        flash("Error processing Paper 1 submission.", "danger")
        return redirect(url_for('exams_routes.list_exams'))


@special_exams_routes.route('/paper2', methods=['GET'])
@login_required
def exam_paper2():
    try:
        # ── Require an approved, unused access request for Paper 2 ─────────
        ACCESS_ID = 9992
        access_req = (
            ExamAccessRequest.query
            .filter_by(
                user_id   = current_user.id,
                exam_id   = ACCESS_ID,
                status    = 'approved'
            )
            .order_by(ExamAccessRequest.requested_at.desc())
            .first()
        )
        if not access_req:
            flash("You need to request & receive approval before starting this special exam.", "warning")
            return redirect(url_for('exams_routes.list_exams'))
        if access_req.used:
            flash("That approval has already been used. Please request access again.", "info")
            return redirect(url_for('exams_routes.list_exams'))
        # ───────────────────────────────────────────────────────────────

        record = SpecialExamRecord.query.filter_by(user_id=current_user.id).first()

        # Lock out if either paper passed
        if record and (record.paper1_passed or record.paper2_passed):
            flash("You have already passed one of the special papers.", "info")
            return redirect(url_for('exams_routes.list_exams'))

        # Cooldown check for Paper 2
        if record and record.paper2_completed_at and not can_attempt_again(record.paper2_completed_at):
            retry_date = (
                record.paper2_completed_at.replace(tzinfo=timezone.utc) + timedelta(days=30)
            ).strftime('%Y-%m-%d')
            flash(f"You can re-attempt Paper 2 after {retry_date}.", "info")
            return redirect(url_for('exams_routes.list_exams'))

        # OK to start
        start_time = datetime.now(timezone.utc).isoformat()

        # ── Mark this access request as used ─────────────────────────────
        access_req.used = True
        db.session.commit()
        # ───────────────────────────────────────────────────────────────

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
        if not start_time_str:
            flash("Invalid form data.", "danger")
            return redirect(url_for('exams_routes.list_exams'))

        start_time = datetime.fromisoformat(start_time_str)
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        end_time = datetime.now(timezone.utc)
        time_spent = (end_time - start_time).total_seconds()

        correct_answers = {
            '0':  'c',  # Q1
            '1':  'd',  # Q2
            '2':  'a',  # Q3
            '3':  'c',  # Q4
            '4':  'a',  # Q5
            '5':  'b',  # Q6
            '6':  'b',  # Q7
            '7':  'c',  # Q8
            '8':  'd',  # Q9
            '9':  'd',  # Q10
            '10': 'b',  # Q11
            '11': 'c',  # Q12
            '12': 'a',  # Q13
            '13': 'd',  # Q14
            '14': 'a',  # Q15
            '15': 'b',  # Q16
            '16': 'c',  # Q17
            '17': 'b',  # Q18
            '18': 'b',  # Q19
            '19': 'b',  # Q20
            '20': 'b',  # Q21
            '21': 'c',  # Q22
            '22': 'b',  # Q23
            '23': 'd',  # Q24
            '24': 'c',  # Q25
            '25': 'b',  # Q26
            '26': 'b',  # Q27
            '27': 'c',  # Q28
            '28': 'b',  # Q29
            '29': 'c',  # Q30
            '30': 'a',  # Q31
            '31': 'b',  # Q32
            '32': 'c',  # Q33
            '33': 'a',  # Q34
            '34': 'b',  # Q35
            '35': 'a',  # Q36
            '36': 'd',  # Q37
            '37': 'b',  # Q38
            '38': 'b',  # Q39
            '39': 'a'   # Q40
        }


        marks_per_question = 2.5
        user_score = sum(
            marks_per_question
            for q, ans in correct_answers.items()
            if data.get(f'answers[{q}]', '').lower() == ans
        )
        final_percentage = round(user_score, 2)
        passed = final_percentage >= 70

        record = SpecialExamRecord.query.filter_by(user_id=current_user.id).first()
        if not record:
            record = SpecialExamRecord(user_id=current_user.id)
            db.session.add(record)

        # ─── INCREMENT PAPER 2 ATTEMPTS ────────────────────────────
        record.paper2_attempts = (record.paper2_attempts or 0) + 1
        ACCESS_ID = 9992
        # ─── RECORD EACH WRONG ANSWER ───────────────────────────────
        for q, ans in correct_answers.items():
            raw = data.get(f'answers[{q}]', '').strip()
            if raw.lower() != ans:
                db.session.add(IncorrectAnswer(
                    user_id        = current_user.id,
                    exam_id        = None,
                    special_paper  = 'paper2',
                    question_id    = int(q),
                    user_answer    = raw,
                    correct_answer = ans,
                    answered_at    = end_time
                ))

        # cooldown re-check
        if record.paper2_completed_at and not can_attempt_again(record.paper2_completed_at):
            retry_date = (
                record.paper2_completed_at.replace(tzinfo=timezone.utc)
                + timedelta(days=30)
            ).strftime('%Y-%m-%d')
            flash(f"You can re-attempt Paper 2 after {retry_date}.", "info")
            return redirect(url_for('exams_routes.list_exams'))

        # save results
        record.paper2_score        = final_percentage
        record.paper2_passed       = passed
        record.paper2_time_spent   = int(time_spent)
        record.paper2_completed_at = end_time
        db.session.commit()

        flash(
            f"Special Paper 2 {'passed' if passed else 'completed'} with {final_percentage}%",
            'success' if passed else 'warning'
        )
        return redirect(url_for('exams_routes.exam_results'))

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error submitting Paper 2: {e}")
        flash("Error processing Paper 2 submission.", "danger")
        return redirect(url_for('exams_routes.list_exams'))


@special_exams_routes.route("/<int:exam_id>/request_access", methods=["POST"])
@login_required
def request_special_exam_access(exam_id):
    if exam_id not in [9991, 9992]:
        flash("Invalid special exam.", "danger")
        return redirect(url_for("exams_routes.list_exams"))

    # Find the most-recent request
    existing = (
        ExamAccessRequest.query
        .filter_by(user_id=current_user.id, exam_id=exam_id)
        .order_by(ExamAccessRequest.requested_at.desc())
        .first()
    )

    # Block only if there’s a pending or approved-but-unused request
    if existing and not existing.used and existing.status in ('pending', 'approved'):
        flash("You already have an open access request.", "info")
        return redirect(url_for("exams_routes.list_exams"))

    # Otherwise create a new request
    req = ExamAccessRequest(user_id=current_user.id, exam_id=exam_id)
    db.session.add(req)
    db.session.commit()

    flash("Access request sent to admin.", "success")
    return redirect(url_for("exams_routes.list_exams"))
