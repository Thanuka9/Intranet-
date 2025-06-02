from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, abort
from flask_login import login_required, current_user
from extensions import db
from models import User, Designation, Event, Department, Client, UserScore, Category, Level, Role
from mongodb_operations import get_profile_picture, save_profile_picture, delete_profile_picture
from io import BytesIO
from datetime import datetime
import imghdr
import logging

profile_routes = Blueprint('profile_routes', __name__)

@profile_routes.route('/profile')
@login_required
def profile():
    """
    Display the profile page of the current user, including calendar events and performance graphs.
    Performance data is filtered by the selected exam level.
    """
    # Fetch user events
    calendar_events = Event.query.filter_by(user_id=current_user.id).all()

    # Get designation title (if available)
    designation_title = current_user.designation.title if current_user.designation else "Not Assigned"

    # Fetch user's profile picture from MongoDB
    profile_picture = get_profile_picture(current_user.id)

    # Use the department relationship
    user_departments = current_user.departments if current_user.departments else []


    # --- Determine selected exam level ---
    try:
        selected_level = int(request.args.get('level', 1))
    except ValueError:
        selected_level = 1

    # --- Compute Performance Data ---
    # Define the five categories to display on the graph
    performance_categories = ["Billing", "Posting", "VOB", "Collection", "Introduction"]
    performance_labels = []
    user_performance = []   # Current user's average score per category for this level
    overall_performance = []  # Overall average score per category for this level

    for cat_name in performance_categories:
        performance_labels.append(cat_name)
        # Find the category row from the Category table
        cat = Category.query.filter_by(name=cat_name).first()
        if cat:
            # Compute current user’s average score for this category and level
            scores = UserScore.query.filter_by(user_id=current_user.id, category_id=cat.id, level_id=selected_level).all()
            avg_user_score = sum(s.score for s in scores) / len(scores) if scores else 0
            user_performance.append(round(avg_user_score, 2))
            
            # Compute overall average for all users for this category and level
            all_scores = UserScore.query.filter_by(category_id=cat.id, level_id=selected_level).all()
            avg_overall = sum(s.score for s in all_scores) / len(all_scores) if all_scores else 0
            overall_performance.append(round(avg_overall, 2))
        else:
            user_performance.append(0)
            overall_performance.append(0)

    # Query all exam levels for the level selection dropdown
    levels = Level.query.order_by(Level.level_number).all()

    return render_template(
        'profile.html',
        user=current_user,
        calendar_events=calendar_events,
        designation_title=designation_title,
        profile_picture=profile_picture,
        user_departments=user_departments,
        performance_labels=performance_labels,
        user_performance=user_performance,
        average_performance=overall_performance,
        levels=levels,
        selected_level=selected_level
    )

@profile_routes.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    """
    Allow users to edit their profile information, including profile picture,
    department, designation, and clients.
    """
    user = current_user

    if request.method == 'POST':
        try:
            # Update personal info
            user.first_name     = request.form.get('first_name', user.first_name)
            user.last_name      = request.form.get('last_name', user.last_name)
            user.employee_email = request.form.get('employee_email', user.employee_email)
            user.employee_id    = request.form.get('employee_id', user.employee_id)
            user.phone_number   = request.form.get('phone_number', user.phone_number)
            
            # Update departments (multi-select)
            dept_ids = request.form.getlist('departments', type=int)
            user.departments = Department.query.filter(Department.id.in_(dept_ids)).all() if dept_ids else []

            # Update designation (single select)
            desig_id = request.form.get('designation_id', type=int)
            if desig_id:
                user.designation = Designation.query.get(desig_id)
            
            # Update clients (multi-select many-to-many)
            client_ids = request.form.getlist('clients', type=int)
            # load and assign in one go (empty list if nothing selected)
            user.clients = Client.query.filter(Client.id.in_(client_ids)).all()

            # Handle profile picture upload
            if 'profile_picture' in request.files:
                file = request.files['profile_picture']
                if file:
                    image_type = imghdr.what(file)
                    if image_type not in ('jpeg', 'png'):
                        flash("Only JPEG and PNG images are allowed.", "danger")
                        return redirect(url_for('profile_routes.edit_profile'))
                    data = file.read()
                    if len(data) > 5 * 1024 * 1024:
                        flash("Profile picture size exceeds the 5MB limit.", "danger")
                        return redirect(url_for('profile_routes.edit_profile'))
                    save_profile_picture(user.id, data)

            db.session.commit()
            flash('Profile updated successfully!', 'success')
            return redirect(url_for('profile_routes.profile'))

        except Exception as e:
            db.session.rollback()
            flash(f"An error occurred: {e}", 'danger')
            return redirect(url_for('profile_routes.edit_profile'))

    # GET request: fetch lists for dropdowns
    designations = Designation.query.all()
    departments  = Department.query.all()
    clients      = Client.query.all()
    return render_template(
        'edit_profile.html',
        user=user,
        designations=designations,
        departments=departments,
        clients=clients
    )


@profile_routes.route('/profile/delete_picture', methods=['POST'])
@login_required
def delete_profile_picture_handler():
    """
    Allow users to delete their profile picture.
    """
    try:
        result = delete_profile_picture(current_user.id)
        if result.get('status') == 'deleted':
            flash("Profile picture deleted successfully!", "success")
        else:
            flash(result.get('message', "Error deleting profile picture"), "info")
    except Exception as e:
        flash(f"Error deleting profile picture: {str(e)}", "danger")
    return redirect(url_for('profile_routes.profile'))

@profile_routes.route('/add_event', methods=['POST'])
@login_required
def add_event():
    """
    Allow users to add new events to their calendar.
    """
    title = request.form['event_title']
    description = request.form['event_description']
    event_date = request.form['event_date']

    new_event = Event(title=title, description=description, date=event_date, user_id=current_user.id)
    db.session.add(new_event)
    db.session.commit()
    flash("Event added successfully!", "success")
    return redirect(url_for('profile_routes.profile'))

@profile_routes.route('/edit_event/<int:event_id>', methods=['POST'])
def edit_event(event_id):
    event = Event.query.get_or_404(event_id)
    if event.user_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for('profile_routes.profile'))

    event.title = request.form['event_title']
    event.description = request.form['event_description']
    
    # Safely parse the date string
    date_str = request.form['event_date']  # e.g. '2025-04-18' or maybe '04/18/2025'
    try:
        # If your <input type="date"> uses yyyy-mm-dd, use '%Y-%m-%d'
        event.date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        # If you expect mm/dd/yyyy, use '%m/%d/%Y' instead
        try:
            event.date = datetime.strptime(date_str, '%m/%d/%Y').date()
        except ValueError:
            flash("Invalid date format. Please use YYYY-MM-DD or adjust your date field.", "danger")
            return redirect(url_for('profile_routes.profile'))

    db.session.commit()
    flash("Event updated successfully!", "success")
    return redirect(url_for('profile_routes.profile'))

@profile_routes.route('/delete_event/<int:event_id>', methods=['POST'])
@login_required
def delete_event(event_id):
    """
    Allow users to delete an event from their calendar.
    """
    event = Event.query.get_or_404(event_id)
    if event.user_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for('profile_routes.profile'))

    db.session.delete(event)
    db.session.commit()
    flash("Event deleted successfully!", "success")
    return redirect(url_for('profile_routes.profile'))

@profile_routes.route('/profile_picture/<int:user_id>')
@login_required
def serve_profile_picture(user_id):
    """
    Serve the user's profile picture from MongoDB if available.
    """
    try:
        profile_picture = get_profile_picture(user_id)
        if profile_picture:
            return send_file(BytesIO(profile_picture), mimetype='image/jpeg')
        else:
            flash("Profile picture not found.", "warning")
            return redirect(url_for('profile_routes.profile'))
    except Exception as e:
        logging.error(f"Error serving profile picture for user {user_id}: {e}")
        flash("Error retrieving profile picture.", "danger")
        return redirect(url_for('profile_routes.profile'))
