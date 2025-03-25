from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, abort
from flask_login import login_required, current_user
from extensions import db
from models import User, Designation, Event
from mongodb_operations import get_profile_picture, save_profile_picture, delete_profile_picture
from io import BytesIO
import imghdr
import logging

# Create Blueprint
profile_routes = Blueprint('profile_routes', __name__)

@profile_routes.route('/profile')
@login_required
def profile():
    """
    Display the profile page of the current user, including calendar events.
    """
    # Fetch user events
    calendar_events = Event.query.filter_by(user_id=current_user.id).all()

    # Fetch user's designation title if available
    designation_title = current_user.designation.title if current_user.designation else "Not Assigned"

    # Fetch user's profile picture from MongoDB
    profile_picture = get_profile_picture(current_user.id)

    # Prepare departments
    user_departments = current_user.departments.split(", ") if current_user.departments else []
    formatted_departments = ", ".join(user_departments)

    return render_template(
        'profile.html',
        user=current_user,
        calendar_events=calendar_events,
        designation_title=designation_title,
        profile_picture=profile_picture,
        formatted_departments=formatted_departments  
    )


@profile_routes.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    """
    Allow users to edit their profile information, including profile picture.
    """
    user = current_user  # Access the currently logged-in user

    if request.method == 'POST':
        try:
            # Retrieve form data
            user.first_name = request.form.get('first_name', user.first_name)
            user.last_name = request.form.get('last_name', user.last_name)
            user.employee_email = request.form.get('employee_email', user.employee_email)
            user.employee_id = request.form.get('employee_id', user.employee_id)
            user.phone_number = request.form.get('phone_number', user.phone_number)
            user.departments = request.form.getlist('departments')

            # Update designation by designation_id
            designation_id = request.form.get('designation_id')
            if designation_id:
                designation = Designation.query.get(designation_id)
                user.designation = designation

            # Handle profile picture upload
            if 'profile_picture' in request.files:
                file = request.files['profile_picture']
                if file:
                    # Validate image type and size
                    image_type = imghdr.what(file)
                    if image_type not in ['jpeg', 'png']:
                        flash("Only JPEG and PNG images are allowed.", "danger")
                        return redirect(url_for('profile_routes.edit_profile'))
                    file_data = file.read()
                    if len(file_data) > 5 * 1024 * 1024:  # 5MB limit
                        flash("Profile picture size exceeds the 5MB limit.", "danger")
                        return redirect(url_for('profile_routes.edit_profile'))
                    save_profile_picture(user.id, file_data)

            db.session.commit()  # Commit changes to the database
            flash('Profile updated successfully!', 'success')
            return redirect(url_for('profile_routes.profile'))
        except Exception as e:
            db.session.rollback()
            flash(f"An error occurred: {str(e)}", 'danger')
            return redirect(url_for('profile_routes.edit_profile'))

    # Fetch all designations for the dropdown in edit form
    designations = Designation.query.all()
    return render_template('edit_profile.html', user=user, designations=designations)


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


# Add event
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


# Edit event
@profile_routes.route('/edit_event/<int:event_id>', methods=['POST'])
@login_required
def edit_event(event_id):
    """
    Allow users to edit an existing event in their calendar.
    """
    event = Event.query.get_or_404(event_id)

    # Ensure the user can only edit their own events
    if event.user_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for('profile_routes.profile'))

    event.title = request.form['event_title']
    event.description = request.form['event_description']
    event.date = request.form['event_date']
    db.session.commit()
    flash("Event updated successfully!", "success")
    return redirect(url_for('profile_routes.profile'))


# Delete event
@profile_routes.route('/delete_event/<int:event_id>', methods=['POST'])
@login_required
def delete_event(event_id):
    """
    Allow users to delete an event from their calendar.
    """
    event = Event.query.get_or_404(event_id)

    # Ensure the user can only delete their own events
    if event.user_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for('profile_routes.profile'))

    db.session.delete(event)
    db.session.commit()
    flash("Event deleted successfully!", "success")
    return redirect(url_for('profile_routes.profile'))


# Route to serve the profile picture
@profile_routes.route('/profile_picture/<int:user_id>')
@login_required
def serve_profile_picture(user_id):
    """
    Serve the user's profile picture from MongoDB if available.
    """
    try:
        profile_picture = get_profile_picture(user_id)

        if profile_picture:
            # If image data is found, return it as a file
            return send_file(BytesIO(profile_picture), mimetype='image/jpeg')
        else:
            # If no profile picture is found, flash a warning and redirect
            flash("Profile picture not found.", "warning")
            return redirect(url_for('profile_routes.profile'))
    except Exception as e:
        # Log the error and redirect with a danger flash message
        logging.error(f"Error serving profile picture for user {user_id}: {e}")
        flash("Error retrieving profile picture.", "danger")
        return redirect(url_for('profile_routes.profile'))