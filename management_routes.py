from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from datetime import date
import logging

from models import User, UserScore, SpecialExamRecord, db

management_routes = Blueprint('management_routes', __name__)

def calculate_work_experience(user):
    """
    Calculate approximate work experience (in years & months) 
    based on the user's join_date. Returns a string like '2 yr(s), 3 mo(s)'.
    """
    if not user.join_date:
        return "N/A"
    today = date.today()
    days_diff = (today - user.join_date).days  # user.join_date is a Date column
    years = days_diff // 365
    leftover_days = days_diff % 365
    months = leftover_days // 30
    return f"{years} yr(s), {months} mo(s)"

def average_score(scores):
    """
    Helper function to calculate average exam score for a user.
    If no scores, returns 0.
    """
    if not scores:
        return 0
    return round(sum(s.score for s in scores) / len(scores), 2)

@management_routes.route('/compare_users', methods=['GET', 'POST'])
@login_required
def compare_users():
    if request.method == 'POST':
        # Get selected user IDs from the form
        user1_id = request.form.get('user1')
        user2_id = request.form.get('user2')

        if not user1_id or not user2_id:
            flash("Please select two users to compare.", "warning")
            return redirect(url_for('management_routes.compare_users'))

        # Fetch users from the database
        user1 = User.query.get(user1_id)
        user2 = User.query.get(user2_id)

        if not user1 or not user2:
            flash("One or both selected users not found.", "danger")
            return redirect(url_for('management_routes.compare_users'))

        # Fetch exam scores for each user
        user1_scores = UserScore.query.filter_by(user_id=user1.id).all()
        user2_scores = UserScore.query.filter_by(user_id=user2.id).all()

        user1_avg = average_score(user1_scores)
        user2_avg = average_score(user2_scores)

        # Calculate work experience
        user1_work_exp = calculate_work_experience(user1)
        user2_work_exp = calculate_work_experience(user2)

        # Fetch SpecialExamRecord if it exists
        user1_sprec = SpecialExamRecord.query.filter_by(user_id=user1.id).first()
        user2_sprec = SpecialExamRecord.query.filter_by(user_id=user2.id).first()

        return render_template(
            'compare_users.html',
            user1=user1,
            user2=user2,
            user1_avg=user1_avg,
            user2_avg=user2_avg,
            user1_work_exp=user1_work_exp,
            user2_work_exp=user2_work_exp,
            user1_sprec=user1_sprec,
            user2_sprec=user2_sprec
        )
    else:
        # GET: Display a form to select two users for comparison
        users = User.query.all()
        return render_template('compare_users_form.html', users=users)
