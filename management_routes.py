from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from datetime import date
from admin_routes import admin_required 
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
@admin_required
def compare_users():
    from models import Category, Level  # Add these to your top import block if not already there

    if request.method == 'POST':
        user1_id = request.form.get('user1')
        user2_id = request.form.get('user2')
        selected_level = request.form.get('level', type=int, default=1)

        if not user1_id or not user2_id:
            flash("Please select two users to compare.", "warning")
            return redirect(url_for('management_routes.compare_users'))

        user1 = User.query.get(user1_id)
        user2 = User.query.get(user2_id)

        if not user1 or not user2:
            flash("One or both selected users not found.", "danger")
            return redirect(url_for('management_routes.compare_users'))

        # Calculate overall average score
        user1_scores = UserScore.query.filter_by(user_id=user1.id).all()
        user2_scores = UserScore.query.filter_by(user_id=user2.id).all()
        user1_avg = average_score(user1_scores)
        user2_avg = average_score(user2_scores)

        # Work experience
        user1_work_exp = calculate_work_experience(user1)
        user2_work_exp = calculate_work_experience(user2)

        # Special Exam
        user1_sprec = SpecialExamRecord.query.filter_by(user_id=user1.id).first()
        user2_sprec = SpecialExamRecord.query.filter_by(user_id=user2.id).first()

        # Categories for Radar Chart
        categories = ["Billing", "Posting", "VOB", "Collection", "Denial Management"]
        performance_labels = []
        user1_cat_scores = []
        user2_cat_scores = []

        for name in categories:
            cat = Category.query.filter_by(name=name).first()
            performance_labels.append(name)

            for user, scores_list in [(user1, user1_cat_scores), (user2, user2_cat_scores)]:
                if cat:
                    scores = UserScore.query.filter_by(
                        user_id=user.id,
                        category_id=cat.id,
                        level_id=selected_level
                    ).all()
                    avg_score = sum(s.score for s in scores) / len(scores) if scores else 0
                    scores_list.append(round(avg_score, 2))
                else:
                    scores_list.append(0)

        levels = Level.query.order_by(Level.level_number).all()

        return render_template(
            'compare_users.html',
            user1=user1,
            user2=user2,
            user1_avg=user1_avg,
            user2_avg=user2_avg,
            user1_work_exp=user1_work_exp,
            user2_work_exp=user2_work_exp,
            user1_sprec=user1_sprec,
            user2_sprec=user2_sprec,
            performance_labels=performance_labels,
            user1_scores=user1_cat_scores,
            user2_scores=user2_cat_scores,
            levels=levels,
            selected_level=selected_level
        )

    else:
        users = User.query.all()
        levels = Level.query.order_by(Level.level_number).all()
        return render_template('compare_users_form.html', users=users, levels=levels)